"""AI streaming routes — SSE endpoints for documentation, judge, file-analysis, kiro-cli.

Also includes doc-analysis storage endpoints (runs, history, download).

Response envelopes:
  POST /api/analysis/{id}/documentation → SSE stream (text/event-stream)
  POST /api/analysis/{id}/judge         → SSE stream (text/event-stream)
  POST /api/analysis/{id}/file-analysis → SSE stream (text/event-stream)
  POST /api/analysis/{id}/kiro-cli      → SSE stream (text/event-stream)
  GET  /api/analysis/{id}/doc-analysis      → latest doc-analysis result
  GET  /api/analysis/{id}/doc-analysis/runs → list of run timestamps
  GET  /api/analysis/{id}/doc-analysis/run/{ts} → specific run
  DELETE /api/analysis/{id}/doc-analysis    → delete all doc-analysis data
  POST /api/analysis/{id}/kiro-spec/download → .md file download
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from state import app_state

router = APIRouter(prefix="/api", tags=["ai-streaming"])

# Valid analysis ID: alphanumeric, dash, underscore only.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

# Base path for doc-analysis storage
_DOC_ANALYSIS_BASE = os.environ.get("DOC_ANALYSIS_PATH", "/app/temp/analyses")

# Kiro CLI agent URL
_KIRO_CLI_URL = os.environ.get("KIRO_CLI_AGENT_URL", "http://kiro-cli-agent:8007")

# Timeout for kiro-cli proxy
_KIRO_CLI_TIMEOUT = 120.0


def _validate_analysis_id(analysis_id: str) -> None:
    """Validate analysis_id against injection."""
    if not analysis_id or not _SAFE_ID.match(analysis_id):
        raise HTTPException(status_code=400, detail="Invalid analysis ID")


def _get_analysis_or_404(analysis_id: str) -> dict:
    """Load analysis from storage or raise 404."""
    _validate_analysis_id(analysis_id)
    storage = app_state.storage_manager
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    data = storage.load(analysis_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return data


def _doc_analysis_dir(analysis_id: str) -> Path:
    """Return the doc-analysis storage directory for an analysis.

    analysis_id is validated by _validate_analysis_id (_SAFE_ID blocks path
    separators and dots), and the resolved path is confirmed to stay within
    the base directory as defense-in-depth against path traversal (CWE-22).
    """
    base = Path(_DOC_ANALYSIS_BASE).resolve()
    target = (base / analysis_id / "doc-analysis").resolve()
    if not target.is_relative_to(base):
        raise HTTPException(status_code=400, detail="Invalid analysis ID")
    return target


async def _load_off_loop(storage: Any, analysis_id: str) -> dict | None:
    """Read a stored analysis without blocking the event loop.

    Called from inside SSE async generators, which must keep yielding events —
    they cannot be declared `def` to get FastAPI's threadpool, so blocking work
    has to be pushed to a worker thread explicitly. Analysis JSON runs to many
    megabytes for a large repository, so parsing it on the loop stalls every
    other request.
    """
    if not storage:
        return None
    return await run_in_threadpool(storage.load, analysis_id)


# --- Request models ---


class StreamingRequestBody(BaseModel):
    """Optional body for SSE streaming endpoints."""

    judge_feedback: str = ""
    mode: str = ""
    file_path: str = ""
    extra: dict[str, Any] = {}


# --- SSE Streaming Endpoints ---


async def _documentation_stream(
    analysis_id: str, body: StreamingRequestBody
) -> AsyncGenerator[dict[str, str], None]:
    """Generate SSE events for documentation generation."""
    conversation_id = str(uuid.uuid4())
    yield {
        "event": "message",
        "data": json.dumps({"type": "init", "conversation_id": conversation_id}),
    }

    # Try to load the agent
    try:
        from agents import DocAnalysisAgent

        if DocAnalysisAgent is None:
            raise ImportError("DocAnalysisAgent not available")
    except ImportError:
        yield {
            "event": "message",
            "data": json.dumps(
                {
                    "type": "error",
                    "message": "DocAnalysisAgent not available. Agent module not yet implemented.",
                }
            ),
        }
        return

    # Load analysis data for context
    storage = app_state.storage_manager
    data = await _load_off_loop(storage, analysis_id)
    if data is None:
        yield {
            "event": "message",
            "data": json.dumps({"type": "error", "message": "Analysis not found"}),
        }
        return

    try:
        agent = DocAnalysisAgent(storage_manager=storage)
        async for event in agent.generate_documentation(analysis_id, data):
            yield {"event": "message", "data": json.dumps(event)}

        # Save doc-analysis result — file write, so keep it off the loop.
        await run_in_threadpool(
            _save_doc_analysis_run, analysis_id, data.get("ai_documentation", "")
        )

        yield {
            "event": "message",
            "data": json.dumps(
                {
                    "type": "complete",
                    "conversation_id": conversation_id,
                    "status": "completed",
                }
            ),
        }
    except Exception as exc:
        yield {
            "event": "message",
            "data": json.dumps({"type": "error", "message": str(exc)}),
        }


async def _judge_stream(
    analysis_id: str, body: StreamingRequestBody
) -> AsyncGenerator[dict[str, str], None]:
    """Generate SSE events for LLM Judge evaluation."""
    conversation_id = str(uuid.uuid4())
    yield {
        "event": "message",
        "data": json.dumps({"type": "init", "conversation_id": conversation_id}),
    }

    try:
        from agents import LLMJudge

        if LLMJudge is None:
            raise ImportError("LLMJudge not available")
    except ImportError:
        yield {
            "event": "message",
            "data": json.dumps(
                {
                    "type": "error",
                    "message": "LLMJudge not available. Agent module not yet implemented.",
                }
            ),
        }
        return

    storage = app_state.storage_manager
    data = await _load_off_loop(storage, analysis_id)
    if data is None:
        yield {
            "event": "message",
            "data": json.dumps({"type": "error", "message": "Analysis not found"}),
        }
        return

    try:
        judge = LLMJudge(storage_manager=storage)
        async for event in judge.evaluate(
            analysis_id, data, feedback=body.judge_feedback
        ):
            yield {"event": "message", "data": json.dumps(event)}

        yield {
            "event": "message",
            "data": json.dumps(
                {
                    "type": "complete",
                    "conversation_id": conversation_id,
                    "status": "completed",
                }
            ),
        }
    except Exception as exc:
        yield {
            "event": "message",
            "data": json.dumps({"type": "error", "message": str(exc)}),
        }


async def _file_analysis_stream(
    analysis_id: str, body: StreamingRequestBody
) -> AsyncGenerator[dict[str, str], None]:
    """Generate SSE events for per-file AI analysis (security or transform mode)."""
    conversation_id = str(uuid.uuid4())
    yield {
        "event": "message",
        "data": json.dumps({"type": "init", "conversation_id": conversation_id}),
    }

    try:
        from agents import DocAnalysisAgent

        if DocAnalysisAgent is None:
            raise ImportError("DocAnalysisAgent not available")
    except ImportError:
        yield {
            "event": "message",
            "data": json.dumps(
                {
                    "type": "error",
                    "message": "File analysis agent not available. Agent module not yet implemented.",
                }
            ),
        }
        return

    storage = app_state.storage_manager
    data = await _load_off_loop(storage, analysis_id)
    if data is None:
        yield {
            "event": "message",
            "data": json.dumps({"type": "error", "message": "Analysis not found"}),
        }
        return

    try:
        agent = DocAnalysisAgent(storage_manager=storage)
        async for event in agent.analyze_file(
            analysis_id, data, file_path=body.file_path, mode=body.mode
        ):
            yield {"event": "message", "data": json.dumps(event)}

        yield {
            "event": "message",
            "data": json.dumps(
                {
                    "type": "complete",
                    "conversation_id": conversation_id,
                    "status": "completed",
                }
            ),
        }
    except Exception as exc:
        yield {
            "event": "message",
            "data": json.dumps({"type": "error", "message": str(exc)}),
        }


async def _kiro_cli_stream(
    analysis_id: str, body: StreamingRequestBody
) -> AsyncGenerator[dict[str, str], None]:
    """Proxy SSE events from the Kiro CLI Agent (port 8007)."""
    conversation_id = str(uuid.uuid4())
    yield {
        "event": "message",
        "data": json.dumps({"type": "init", "conversation_id": conversation_id}),
    }

    try:
        async with httpx.AsyncClient(timeout=_KIRO_CLI_TIMEOUT) as client:
            payload = {
                "analysis_id": analysis_id,
                "conversation_id": conversation_id,
                **body.extra,
            }
            async with client.stream(
                "POST",
                f"{_KIRO_CLI_URL}/generate",
                json=payload,
                timeout=_KIRO_CLI_TIMEOUT,
            ) as response:
                if response.status_code != 200:
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {
                                "type": "error",
                                "message": f"Kiro CLI Agent returned {response.status_code}",
                            }
                        ),
                    }
                    return

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        yield {"event": "message", "data": data_str}
                    elif line.startswith("data:"):
                        data_str = line[5:]
                        yield {"event": "message", "data": data_str}

        yield {
            "event": "message",
            "data": json.dumps(
                {
                    "type": "complete",
                    "conversation_id": conversation_id,
                    "status": "completed",
                }
            ),
        }
    except httpx.ConnectError:
        yield {
            "event": "message",
            "data": json.dumps(
                {"type": "error", "message": "Kiro CLI Agent not reachable"}
            ),
        }
    except httpx.TimeoutException:
        yield {
            "event": "message",
            "data": json.dumps(
                {"type": "error", "message": "Kiro CLI Agent request timed out"}
            ),
        }
    except Exception as exc:
        yield {
            "event": "message",
            "data": json.dumps({"type": "error", "message": str(exc)}),
        }


# --- SSE Route Handlers ---


@router.post("/analysis/{analysis_id}/documentation", response_model=None)
async def stream_documentation(
    analysis_id: str, request: Request
) -> EventSourceResponse:
    """Stream AI documentation generation via SSE."""
    _validate_analysis_id(analysis_id)
    body = await _parse_body(request)
    return EventSourceResponse(_documentation_stream(analysis_id, body))


@router.post("/analysis/{analysis_id}/judge", response_model=None)
async def stream_judge(analysis_id: str, request: Request) -> EventSourceResponse:
    """Stream LLM Judge evaluation via SSE."""
    _validate_analysis_id(analysis_id)
    body = await _parse_body(request)
    return EventSourceResponse(_judge_stream(analysis_id, body))


@router.post("/analysis/{analysis_id}/file-analysis", response_model=None)
async def stream_file_analysis(
    analysis_id: str, request: Request
) -> EventSourceResponse:
    """Stream per-file AI analysis via SSE."""
    _validate_analysis_id(analysis_id)
    body = await _parse_body(request)
    return EventSourceResponse(_file_analysis_stream(analysis_id, body))


@router.post("/analysis/{analysis_id}/kiro-cli", response_model=None)
async def stream_kiro_cli(analysis_id: str, request: Request) -> EventSourceResponse:
    """Proxy SSE stream to Kiro CLI Agent."""
    _validate_analysis_id(analysis_id)
    body = await _parse_body(request)
    return EventSourceResponse(_kiro_cli_stream(analysis_id, body))


# --- Doc-Analysis Storage Endpoints ---


@router.get("/analysis/{analysis_id}/doc-analysis")
async def get_doc_analysis(analysis_id: str) -> dict:
    """Get the latest doc-analysis result."""
    _validate_analysis_id(analysis_id)
    doc_dir = _doc_analysis_dir(analysis_id)

    if not doc_dir.exists():
        raise HTTPException(status_code=404, detail="No doc-analysis data found")

    runs = sorted(doc_dir.glob("*.json"), reverse=True)
    if not runs:
        raise HTTPException(status_code=404, detail="No doc-analysis runs found")

    with open(runs[0], encoding="utf-8") as f:
        return json.load(f)


@router.get("/analysis/{analysis_id}/doc-analysis/runs")
async def list_doc_analysis_runs(analysis_id: str) -> dict:
    """List all doc-analysis run timestamps."""
    _validate_analysis_id(analysis_id)
    doc_dir = _doc_analysis_dir(analysis_id)

    if not doc_dir.exists():
        return {"runs": []}

    runs = sorted(doc_dir.glob("*.json"), reverse=True)
    timestamps = [f.stem for f in runs]
    return {"runs": timestamps}


@router.get("/analysis/{analysis_id}/doc-analysis/run/{timestamp}")
async def get_doc_analysis_run(analysis_id: str, timestamp: str) -> dict:
    """Get a specific doc-analysis run by timestamp."""
    _validate_analysis_id(analysis_id)

    # Validate timestamp format (prevent path traversal). The character class
    # is linear-time (no nested quantifiers, so not ReDoS-prone), and we also
    # reject any ".." sequence and confirm the resolved file stays in doc_dir.
    if not re.fullmatch(r"[A-Za-z0-9_\-:.]{1,64}", timestamp) or ".." in timestamp:
        raise HTTPException(status_code=400, detail="Invalid timestamp format")

    doc_dir = _doc_analysis_dir(analysis_id)
    file_path = (doc_dir / f"{timestamp}.json").resolve()
    if not file_path.is_relative_to(doc_dir):
        raise HTTPException(status_code=400, detail="Invalid timestamp format")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Doc-analysis run not found")

    with open(file_path, encoding="utf-8") as f:
        return json.load(f)


@router.delete("/analysis/{analysis_id}/doc-analysis")
async def delete_doc_analysis(analysis_id: str) -> dict:
    """Delete all doc-analysis data for an analysis."""
    _validate_analysis_id(analysis_id)
    doc_dir = _doc_analysis_dir(analysis_id)

    if not doc_dir.exists():
        raise HTTPException(status_code=404, detail="No doc-analysis data found")

    deleted = 0
    for file_path in doc_dir.glob("*.json"):
        file_path.unlink()
        deleted += 1

    # Remove the directory if empty
    try:
        doc_dir.rmdir()
    except OSError:
        pass

    return {"detail": f"Deleted {deleted} doc-analysis run(s)"}


@router.post("/analysis/{analysis_id}/kiro-spec/download")
async def download_kiro_spec(analysis_id: str) -> Response:
    """Download generated Kiro spec as a .md file."""
    _validate_analysis_id(analysis_id)

    # Try to load from doc-analysis or main analysis
    storage = app_state.storage_manager
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    data = storage.load(analysis_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    # Look for kiro_spec in the analysis data
    spec_content = data.get("kiro_spec", "")
    if not spec_content:
        # Try doc-analysis directory
        doc_dir = _doc_analysis_dir(analysis_id)
        if doc_dir.exists():
            runs = sorted(doc_dir.glob("*.json"), reverse=True)
            for run_file in runs:
                try:
                    with open(run_file, encoding="utf-8") as f:
                        run_data = json.load(f)
                    spec_content = run_data.get("kiro_spec", "")
                    if spec_content:
                        break
                except (json.JSONDecodeError, OSError):
                    continue

    if not spec_content:
        raise HTTPException(
            status_code=404, detail="No Kiro spec available for this analysis"
        )

    return Response(
        content=spec_content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="kiro-spec-{analysis_id}.md"'
        },
    )


# --- Helpers ---


async def _parse_body(request: Request) -> StreamingRequestBody:
    """Parse optional JSON body from request."""
    try:
        raw = await request.body()
        if raw:
            data = json.loads(raw)
            return StreamingRequestBody(**data)
    except (json.JSONDecodeError, Exception):
        pass
    return StreamingRequestBody()


def _save_doc_analysis_run(analysis_id: str, documentation: str) -> None:
    """Save a doc-analysis run result to filesystem."""
    doc_dir = _doc_analysis_dir(analysis_id)
    doc_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    result = {
        "timestamp": timestamp,
        "analysis_id": analysis_id,
        "documentation": documentation,
        "status": "completed",
    }

    file_path = doc_dir / f"{timestamp}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
