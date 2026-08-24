"""ATX Transform Agent — Code transformation service with streaming output."""

import asyncio
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from config import settings
from services import storage_service, transform_service
from services.docker_service import prepare_repository
from services.download_service import (
    InvalidRepoIdError,
    TreeMissingError,
    TreeTooLargeError,
    archive_filename,
    stream_tree_zip,
    validate_repo_id,
)
from services.file_comparison import get_diff_summary, get_file_diff
from services.github_pr_service import create_pr, get_pr_preview, list_branches
from services.plan_context_defaults import resolve_configuration
from services.transform_service import get_log_path, run_transformation
from services.transformation_validation import (
    resolve_definition_name,
    validate_transformation_type,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="ATX Transform Agent",
    description="Code transformation with ATX CLI, streaming logs, and GitHub PR creation.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Pydantic Models ---


class TransformRequest(BaseModel):
    repo_url: str
    branch: str | None = None
    transformation_type: str
    configuration: str | None = None

    @field_validator("transformation_type")
    @classmethod
    def _validate_transformation_type(cls, value: str) -> str:
        """Reject identifiers the ATX control plane would reject, before the CLI runs.

        ``transformation_type`` is passed verbatim to ``atx custom def exec -n``, where
        it becomes the ``resource`` parameter. Without this check a display name such as
        ``"Java Version Upgrade"`` is accepted here, the caller gets a 200, and the
        failure only appears minutes later as an opaque AWS ``ValidationException`` in
        the log tail. A value constraint on an already-declared field — no change to the
        request's field names.
        """
        return validate_transformation_type(value)


class TransformResponse(BaseModel):
    repo_id: str
    status: str


# --- Helper ---


def _get_record(repo_id: str) -> dict | None:
    """Look up a transformation record.

    Resolved from ``<storage>/<repo_id>/metadata.json`` on every call — no in-memory
    index, so a record found before a restart is found identically after one, and the
    0.5 s poll in the stream's tail loop can never observe a stale status. See
    ``services/storage_service`` for why there is deliberately no cache here.
    """
    return storage_service.read_record(repo_id)


def _require_repo_url(record: dict) -> str:
    """Repo URL for a PR flow, or a 400 explaining why there isn't one.

    A backfilled record (a transformation that predates record persistence) has no
    ``repo_url``: it was only ever in the original request body. Refusing is the honest
    answer — guessing a remote to push a branch to is not.
    """
    repo_url = record.get("repo_url")
    if not repo_url:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Transformation {record.get('repo_id')} has no recorded repository URL "
                "(it was recovered from storage), so a pull request cannot be created. "
                "Download the transformed tree instead."
            ),
        )
    return repo_url


def _run_transform_background(
    repo_id: str,
    repo_url: str,
    branch: str | None,
    transformation_type: str,
    configuration: str | None,
) -> None:
    """Background task: clone repo and run transformation.

    Every status mutation goes through ``update_record``, so it lands in
    ``metadata.json`` rather than in a dict that dies with the process. The stream's
    tail loop reads that file, which is how it sees ``running`` → ``completed``.
    """
    try:
        # Prepare repository (clone)
        repo_path = prepare_repository(repo_url, repo_id, branch)

        # Optionally save original for diff
        original_path = Path(settings.storage_path) / repo_id / "original"
        if repo_path.exists() and not original_path.exists():
            shutil.copytree(repo_path, original_path, ignore=shutil.ignore_patterns(".git"))

        # Run transformation
        exit_code = run_transformation(repo_id, transformation_type, repo_path, configuration)

        storage_service.update_record(
            repo_id,
            status="completed" if exit_code == 0 else "failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            exit_code=exit_code,
        )

    except Exception as e:
        logger.error(f"Transformation {repo_id} failed: {e}")
        storage_service.update_record(
            repo_id,
            status="error",
            error=str(e),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        # Liveness only. Dropped last so a status write is always already on disk
        # before this transformation stops counting as tracked.
        transform_service.clear_active(repo_id)


# --- Endpoints ---


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/transform", response_model=TransformResponse)
async def start_transform(request: TransformRequest, background_tasks: BackgroundTasks):
    """Start a code transformation.

    Accepts repo_url, branch, transformation_type, and optional configuration.
    Returns immediately with repo_id and status 'running'.
    Transformation runs in background.
    """
    repo_id = str(uuid.uuid4())[:12]

    # Some definitions (AWS/java-version-upgrade) cannot run non-interactively without
    # `-g additionalPlanContext=...`; the agent supplies a default for those. Both the
    # effective value and its origin are recorded, because a target version chosen by the
    # agent and never written down would be indistinguishable from one the user asked for.
    # Resolution is pure, so this record and the command the background task builds
    # cannot disagree.
    resolved_configuration = resolve_configuration(request.transformation_type, request.configuration)

    # Persisted before the response is returned, so a client that attaches to the
    # stream immediately finds the record — and so it is still there after a restart.
    # If it cannot be persisted the request is refused: a transformation whose record
    # does not exist is one whose results are unreachable, which is the bug this
    # replaced.
    try:
        storage_service.write_record(
            {
                "repo_id": repo_id,
                "status": "running",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "repo_url": request.repo_url,
                "branch": request.branch,
                "transformation_type": request.transformation_type,
                "configuration": resolved_configuration.value,
                "configuration_source": resolved_configuration.source,
            }
        )
    except OSError as e:
        logger.error(f"Cannot persist transformation record: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Transformation storage is not writable ({settings.storage_path}); cannot start a transformation.",
        )
    transform_service.mark_active(repo_id)

    background_tasks.add_task(
        _run_transform_background,
        repo_id,
        request.repo_url,
        request.branch,
        request.transformation_type,
        request.configuration,
    )

    return TransformResponse(repo_id=repo_id, status="running")


@app.get("/transformation-history")
async def get_transformation_history():
    """Get list of transformation execution records.

    Returns {"records": [...]} format (BC-33).

    Rebuilt by scanning storage on every call, so the history a user sees is the set of
    transformations that actually exist on disk — not the subset this process happens to
    remember. Records with no ``metadata.json`` are reconstructed from their trees;
    ``repo_url`` is null for those rather than invented.
    """
    return {"records": [storage_service.history_entry(r) for r in storage_service.list_records()]}


def _load_custom_definitions() -> list[dict]:
    """Read custom transformation definitions from ``settings.transformations_path``.

    Each file may hold either a single definition object or a list of them — the
    backend's definition CRUD writes one ``definitions.json`` containing a list, so
    appending the parsed payload directly produced a *nested list* inside
    ``definitions`` and every custom entry rendered as a blank, unusable dropdown row.
    Flatten one level and drop anything that is not an object.
    """
    definitions: list[dict] = []
    custom_path = Path(settings.transformations_path)
    if not custom_path.exists():
        return definitions

    for f in sorted(custom_path.glob("*.json")):
        try:
            payload = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning(f"Skipping invalid transformation file: {f}")
            continue

        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if isinstance(entry, dict):
                definitions.append(entry)
            else:
                logger.warning(f"Skipping non-object transformation entry in {f}: {entry!r}")

    return definitions


@app.get("/transformations")
async def get_transformations():
    """Get available transformation definitions (AWS managed + custom).

    Every definition is annotated with ``atx_definition_name``: the identifier the ATX
    CLI accepts, or null when the record has none. AWS-managed and custom records keep
    the identifier in different fields (``id`` vs ``name``), so resolving it here — at
    the only place that knows where each record came from — keeps callers from having to
    guess, and keeps a display label from ever being sent as an identifier.
    """
    # Load AWS managed transformations
    aws_managed_path = Path(__file__).parent / "data" / "aws_managed_transformations.json"
    aws_managed = []
    if aws_managed_path.exists():
        aws_managed = json.loads(aws_managed_path.read_text())

    definitions = [
        {**d, "atx_definition_name": resolve_definition_name(d)} for d in aws_managed + _load_custom_definitions()
    ]
    return {"definitions": definitions}


@app.get("/diff/{repo_id}")
async def get_diff(repo_id: str):
    """Get line-by-line diff for a completed transformation."""
    record = _get_record(repo_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Transformation {repo_id} not found")
    return get_file_diff(repo_id)


@app.get("/diff-summary/{repo_id}")
async def get_diff_summary_endpoint(repo_id: str):
    """Get summary of changes from a transformation."""
    record = _get_record(repo_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Transformation {repo_id} not found")
    return get_diff_summary(repo_id)


@app.get("/download/{repo_id}")
async def download_transformed_tree(repo_id: str):
    """Stream the whole transformed working tree as a zip archive.

    The changed-files view is the review surface; this is the artefact. Everything
    under ``<storage>/<repo_id>/repo`` is included except ``.git``.

    ``repo_id`` is validated before the record lookup so a traversal attempt is
    reported as a bad request rather than being masked as "not found".
    """
    try:
        validate_repo_id(repo_id)
    except InvalidRepoIdError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not _get_record(repo_id):
        raise HTTPException(status_code=404, detail=f"Transformation {repo_id} not found")

    try:
        # Resolve eagerly: a generator's first exception would otherwise surface
        # mid-stream, after a 200 and response headers had already been sent.
        chunks = stream_tree_zip(repo_id)
        first_chunk = next(chunks, b"")
    except TreeMissingError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except TreeTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))

    def body():
        if first_chunk:
            yield first_chunk
        yield from chunks

    filename = archive_filename(repo_id)
    return StreamingResponse(
        body(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/create-file-pr/{repo_id}")
async def create_file_pr(repo_id: str):
    """Create a GitHub Pull Request with the transformation changes."""
    record = _get_record(repo_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Transformation {repo_id} not found")

    try:
        result = create_pr(repo_id, _require_repo_url(record), record.get("branch"))
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/pr-preview/{repo_id}")
async def pr_preview(repo_id: str):
    """Preview PR info before creation."""
    record = _get_record(repo_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Transformation {repo_id} not found")

    try:
        return get_pr_preview(repo_id, _require_repo_url(record), record.get("branch"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/branches")
async def get_branches(repo_url: str = Query(..., description="GitHub repository URL")):
    """List branches for a GitHub repository."""
    try:
        branches = list_branches(repo_url)
        return {"branches": branches}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _output_event(line: str, replay: bool) -> dict:
    """Wrap one stored log line as an SSE ``output`` payload.

    ``type`` is the discriminator, not the SSE ``event:`` name: the shared frontend
    client (``streamSSE``) parses the ``data:`` payload and discards ``event:``
    lines, so a name-only discriminator never reaches the consumer. Emitted on the
    default ``message`` event name, exactly as the analysis agent does.

    The channel is ``output`` (not ``log``) because this is captured ATX CLI stdout;
    ``log`` is reserved for the ATX conversation log. ``data`` carries the stored
    line verbatim, timestamp prefix included — that prefix is the documented
    ``output.log`` format and is useful in the transform console.
    """
    payload: dict = {"type": "output", "data": line.rstrip("\n")}
    if replay:
        # Live payloads carry no replay key at all, per the SSEEvent union.
        payload["replay"] = True
    return {"data": json.dumps(payload)}


@app.get("/conversations/{repo_id}/stream")
async def stream_logs(repo_id: str, request: Request):
    """SSE endpoint: replays stored log lines, tails live if still running.

    Payload shapes follow design.md → "SSE Event Protocol" (the same contract the
    ATX Analysis stream uses):

    - Log lines → ``{"type": "output", "data": <line>, "replay": true}`` (replay
      flag present only on already-stored lines).
    - Success terminal → ``{"type": "complete", "status": <status>}``.
    - Failure terminal → ``{"type": "error", "message": <reason>}``.

    Every stream ends on a terminal event so the UI's "in progress" state clears.
    """
    record = _get_record(repo_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Transformation {repo_id} not found")

    # A persisted `running` status with no work tracked in this process can only be the
    # remains of a run killed by an agent restart: nothing will ever write its terminal
    # status. Reconcile it now, so the tail loop below has a status that can change.
    # Note this is keyed on *tracking*, not on `is_running()` — during the pre-launch
    # clone window there is no subprocess yet, and terminating on that was the bug the
    # status-keyed loop fixed.
    if record.get("status") == "running" and not transform_service.is_tracked(repo_id):
        logger.warning(f"Transformation {repo_id} was left running by a restart — marking interrupted")
        storage_service.mark_interrupted(repo_id)

    async def event_generator():
        log_path = get_log_path(repo_id)
        lines_sent = 0

        # Replay existing lines
        if log_path.exists():
            with open(log_path) as f:
                for line in f:
                    if await request.is_disconnected():
                        return
                    yield _output_event(line, replay=True)
                    lines_sent += 1

        # Tail live while the transformation has not reached a final status.
        #
        # The loop condition is the *record status*, not `is_running()`. Between
        # `POST /transform` returning and the ATX CLI actually launching, the
        # background task is still cloning, so nothing is registered in
        # `running_processes` and `is_running()` is False. Keying the loop on that
        # made a freshly started transformation terminate its stream immediately
        # with `complete` while the status was still `running` — the frontend would
        # clear "in progress" before any work had happened. The status is set to
        # completed/failed/error by the background task, including on exception.
        while (_get_record(repo_id) or {}).get("status") == "running":
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.5)

            if log_path.exists():
                with open(log_path) as f:
                    new_lines = f.readlines()[lines_sent:]
                for line in new_lines:
                    yield _output_event(line, replay=False)
                    lines_sent += 1

        # Final flush of any remaining lines
        if log_path.exists():
            with open(log_path) as f:
                new_lines = f.readlines()[lines_sent:]
            for line in new_lines:
                yield _output_event(line, replay=False)

        # Terminal event — the frontend clears "in progress" on this.
        current_record = _get_record(repo_id)
        status = (current_record or {}).get("status", "unknown")
        if status in ("error", "failed", "interrupted"):
            # The error union member requires `message`; a bare status is not enough.
            reason = (current_record or {}).get("error") or f"Transformation {status}"
            yield {"data": json.dumps({"type": "error", "message": reason})}
        else:
            yield {"data": json.dumps({"type": "complete", "status": status})}

    return EventSourceResponse(event_generator())


# --- Exception Handlers ---


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(FileNotFoundError)
async def not_found_handler(request: Request, exc: FileNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})
