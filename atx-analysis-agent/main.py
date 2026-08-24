"""ATX Analysis Agent — FastAPI service for executing ATX CLI analysis with SSE streaming."""

import asyncio
import json

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from sse_starlette.sse import EventSourceResponse

from config import ANALYSIS_DEFINITIONS, settings
from services.command_service import (
    cancel_analysis,
    ensure_artifacts_collected,
    execute_analysis,
    generate_conversation_id,
    is_tracked,
    stream_events,
)
from services.conversation_id import is_valid_conversation_id
from services.file_service import browse_directory, list_docs, list_logs, read_file_content
from services.repository_service import prepare_repository, validate_repo_path
from services.storage_service import (
    get_conversation_dir,
    list_conversations,
    mark_interrupted,
    read_metadata,
    read_record,
)

app = FastAPI(
    title="ATX Analysis Agent",
    description="ATX CLI Analysis with SSE streaming and conversation management",
    version="0.1.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request Models ---


class AnalyzeRequest(BaseModel):
    """Request body for starting an analysis.

    Field names are part of the published contract (design.md — ATX Analysis
    Agent ``POST /analyze``). Note the deliberate asymmetry with ATX Transform,
    which uses ``repo_url``: this endpoint uses ``repository_url``.
    """

    model_config = ConfigDict(extra="forbid")

    repository_url: str
    branch: str | None = None
    analysis_type: str = "code-assessment"
    conversation_id: str | None = None
    pat_token: str | None = None


# --- Health ---


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


# --- Analysis ---


@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    """Start ATX CLI analysis with SSE streaming.

    First SSE event is always {"type": "init", "conversation_id": "..."}.

    Remote repository URLs are cloned to local storage before the ATX CLI is
    started, because ``atx custom def exec -p`` expects a local project path.
    """
    # Fast-fail validation so contract/URL errors surface as HTTP status codes
    # rather than as a mid-stream SSE error event.
    try:
        validate_repo_path(request.repository_url)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    conversation_id = request.conversation_id or generate_conversation_id()
    # A client-supplied id becomes a directory name under the storage root, so it is
    # rejected here rather than mid-stream.
    if not is_valid_conversation_id(conversation_id):
        raise HTTPException(status_code=400, detail=f"Invalid conversation_id: {conversation_id!r}")

    async def event_generator():
        # init must be the first event on the wire (BC-26), before the clone.
        yield {"data": json.dumps({"type": "init", "conversation_id": conversation_id})}

        try:
            repo_path = await asyncio.to_thread(
                prepare_repository,
                request.repository_url,
                conversation_id,
                request.branch,
                request.pat_token,
            )
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            yield {"data": json.dumps({"type": "error", "message": f"Repository preparation failed: {e}"})}
            return

        try:
            async for event_data in execute_analysis(
                conversation_id,
                request.analysis_type,
                repo_path,
                emit_init=False,
            ):
                yield {"data": event_data}
        except (OSError, ValueError) as e:
            # The record is persisted before the worker starts, and an analysis whose
            # record cannot exist would be unreachable the moment the stream closed.
            # Headers are already sent, so this can only be said on the wire.
            yield {"data": json.dumps({"type": "error", "message": f"Could not record this analysis: {e}"})}

    return EventSourceResponse(event_generator())


# --- Process Management ---


@app.post("/cancel/{conversation_id}")
async def cancel(conversation_id: str):
    """Cancel a running analysis, or reconcile one the agent restarted out from under.

    Process liveness is in-memory, so this endpoint used to 404 with "no running
    analysis" for a conversation left ``running`` by a restart — indistinguishable from
    an id that never existed, and the record stayed ``running`` forever. The persisted
    record is the authority on whether there is anything to act on (BC-49).
    """
    if cancel_analysis(conversation_id):
        return {"status": "cancelled", "conversation_id": conversation_id}

    record = read_record(conversation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Conversation not found: {conversation_id}")

    if record.get("status") == "running" and not is_tracked(conversation_id):
        mark_interrupted(conversation_id)
        return {"status": "interrupted", "conversation_id": conversation_id}

    raise HTTPException(status_code=404, detail=f"No running analysis found for: {conversation_id}")


# --- Conversations ---


@app.get("/conversations")
async def get_conversations():
    """List all conversations with metadata.

    Response shape: {"conversations": [{"conversation_id": str, "status": str, "created_at": str}]}
    """
    conversations = list_conversations()
    return {"conversations": conversations}


@app.get("/conversations/{conversation_id}/stream")
async def stream_conversation(conversation_id: str, request: Request):
    """Reconnect to a conversation: replay emitted events, then tail live ones.

    Mirrors ATX Transform's ``GET /conversations/{repo_id}/stream``:

    - 404 for an unknown conversation id.
    - Replays every persisted event with ``"replay": true`` (``init`` first, BC-26).
    - Continues live if the analysis is still running.
    - Always ends on a terminal ``complete`` / ``error`` event so the UI stops
      spinning — including when metadata says ``running`` but no work is tracked,
      which means the agent restarted and that analysis is dead.
    """
    conv_dir = get_conversation_dir(conversation_id)
    if not conv_dir:
        raise HTTPException(status_code=404, detail=f"Conversation not found: {conversation_id}")

    async def event_generator():
        saw_terminal = False

        async for event_json in stream_events(
            conversation_id,
            mark_replay=True,
            is_disconnected=request.is_disconnected,
        ):
            try:
                event_type = json.loads(event_json).get("type")
            except json.JSONDecodeError:
                event_type = None
            if event_type in ("complete", "error"):
                saw_terminal = True
            yield {"data": event_json}

        if saw_terminal:
            return

        # No terminal event on record. Either the analysis is genuinely dead
        # (agent restarted with a stale "running" metadata) or it never wrote
        # one; either way the client gets a terminal event rather than a
        # perpetual empty console.
        metadata = read_metadata(conversation_id)
        status = metadata.get("status", "unknown")

        if status == "running" and not is_tracked(conversation_id):
            mark_interrupted(conversation_id)
            yield {
                "data": json.dumps(
                    {
                        "type": "error",
                        "message": "Analysis was interrupted — the agent restarted while it was running.",
                    }
                )
            }
            return

        if status in ("failed", "error", "interrupted", "cancelled"):
            yield {"data": json.dumps({"type": "error", "message": f"Analysis {status}"})}
        else:
            yield {"data": json.dumps({"type": "complete", "conversation_id": conversation_id, "status": status})}

    return EventSourceResponse(event_generator())


@app.get("/conversations/{conversation_id}/docs")
async def get_conversation_docs(conversation_id: str):
    """List documentation files for a conversation."""
    conv_dir = get_conversation_dir(conversation_id)
    if not conv_dir:
        raise HTTPException(status_code=404, detail=f"Conversation not found: {conversation_id}")

    # Retry collection for conversations whose worker never got to it (agent
    # restart). No-op once docs/ is populated.
    ensure_artifacts_collected(conversation_id)

    docs = list_docs(conv_dir)
    status = read_metadata(conversation_id).get("status", "unknown")
    return {"docs": docs, "status": status}


@app.get("/conversations/{conversation_id}/logs")
async def get_conversation_logs(conversation_id: str):
    """List log files for a conversation."""
    conv_dir = get_conversation_dir(conversation_id)
    if not conv_dir:
        raise HTTPException(status_code=404, detail=f"Conversation not found: {conversation_id}")

    logs = list_logs(conv_dir)
    return {"logs": logs}


# --- File Browsing ---


@app.get("/browse")
async def browse(path: str = Query(default="", description="Relative path to browse")):
    """Browse files in the storage directory."""
    try:
        entries = browse_directory(settings.storage_path, path)
        return {"entries": entries}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/file")
async def get_file(path: str = Query(..., description="Relative file path to read")):
    """Read file content from storage."""
    try:
        content = read_file_content(settings.storage_path, path)
        return {"content": content, "path": path}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Analysis Definitions ---


@app.get("/analysis-definitions")
async def get_analysis_definitions():
    """List available ATX analysis definitions."""
    definitions = [{"key": key, "definition": definition} for key, definition in ANALYSIS_DEFINITIONS.items()]
    return {"definitions": definitions}
