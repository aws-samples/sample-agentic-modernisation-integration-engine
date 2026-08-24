"""Command service — builds and executes ATX CLI commands with SSE streaming and process management.

Streaming model (mirrors ATX Transform's "background work + tail the durable
record" shape, so the two agents do not diverge):

1. ``execute_analysis`` starts a **background worker task** and then simply
   tails the durable event record. The worker is not tied to the HTTP response,
   so a browser refresh cannot kill a running analysis.
2. The worker runs two concurrent producers — a stdout reader and a
   conversation-log tailer — which both feed **one** ``asyncio.Queue``. A single
   consumer drains that queue and appends each event to ``events.jsonl``.
   FIFO on one queue is the ordering guarantee: neither producer can stall the
   other, and the persisted order is exactly the emitted order.
3. Both live streaming (``POST /analyze``) and reconnect/replay
   (``GET /conversations/{id}/stream``) read the same ``events.jsonl``, so
   replayed and live views are identical by construction.

Event types on the wire:

- ``init``     — first event, carries ``conversation_id`` (BC-26).
- ``log``      — a line of the ATX conversation log. Primary console content,
                 streamed live while the analysis is still running.
- ``output``   — a line of ATX CLI stdout, de-noised (ANSI stripped, spinner and
                 box-drawing frames dropped). Secondary; kept because real
                 failures and the ``Conversation log:`` path only appear here.
- ``complete`` — analysis exited 0.
- ``error``    — analysis failed, was cancelled, or could not be started.
"""

import asyncio
import json
import os
import re
import signal
import subprocess
import uuid
from collections import deque
from collections.abc import AsyncGenerator, Callable, Coroutine
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import ANALYSIS_DEFINITIONS, settings
from services.storage_service import (
    EVENTS_FILENAME,
    get_record_dir,
    read_metadata,
    update_record,
    write_record,
)

# Track running processes: conversation_id → Popen.
#
# Liveness only. Every durable fact about a conversation lives in its own
# ``metadata.json`` (see ``services/storage_service``); these registries exist so a
# ``running`` record with nothing tracked can be recognised as the remains of a killed
# run and reconciled, which is the one thing disk cannot tell us (BC-49).
running_processes: dict[str, subprocess.Popen] = {}

# Track background worker tasks: conversation_id → Task
worker_tasks: dict[str, asyncio.Task] = {}

TERMINAL_EVENT_TYPES = ("complete", "error")

# Extensions worth surfacing in the Documentation tab. ATX documentation output is
# markdown; the rest cover the structured side-artifacts the CLI writes alongside it.
DOCUMENT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".json", ".csv", ".yaml", ".yml", ".html"})

# Matches the /file reader's cap — collecting something it would refuse to serve
# is pointless.
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024

# Poll interval for tailing the conversation log and the event record.
POLL_INTERVAL = 0.25

# ANSI CSI / OSC escape sequences emitted by the ATX CLI's progress rendering.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]")


# Continuation marker the ATX CLI uses for the lines below its spinner.
_PROGRESS_MARKER = "⋮"


def _is_spinner(char: str) -> bool:
    """True if ``char`` is a Braille spinner frame (⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ and friends).

    The whole Braille Patterns block is treated as a spinner: the ATX CLI uses it
    only for progress animation, never for content.
    """
    return 0x2800 <= ord(char) <= 0x28FF


def _is_decoration(char: str) -> bool:
    """True if ``char`` is spinner/box-drawing/block decoration rather than content."""
    if char.isspace() or _is_spinner(char):
        return True
    code = ord(char)
    # Box Drawing (2500–257F), Block Elements (2580–259F), Geometric Shapes (25A0–25FF)
    return 0x2500 <= code <= 0x25FF


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from ``text``."""
    return _ANSI_RE.sub("", text)


def visible_text(raw: str) -> str:
    """Reduce a raw stdout line to what a terminal would actually show.

    Strips ANSI escapes and honours carriage-return overwrites by keeping only
    the last segment written to the line.
    """
    clean = strip_ansi(raw).replace("\x08", "")
    segments = [segment for segment in clean.split("\r") if segment.strip()]
    return segments[-1].rstrip() if segments else ""


def despinner(text: str) -> tuple[str, bool]:
    """Split a spinner frame off the front of a progress line.

    Returns ``(text_without_spinner, was_a_progress_frame)``. The ATX CLI repaints
    its progress line once per animation frame; because the subprocess is read in
    universal-newline mode, each repaint arrives as a separate line. Identifying
    them lets the reader collapse the repaints into one event per state change.
    """
    stripped = text.lstrip()
    was_progress = False
    while stripped and _is_spinner(stripped[0]):
        stripped = stripped[1:].lstrip()
        was_progress = True
    return stripped, was_progress


def is_noise(text: str) -> bool:
    """True if ``text`` carries no readable content (spinner/banner/blank)."""
    stripped = text.strip()
    if not stripped:
        return True
    return all(_is_decoration(char) for char in stripped)


class StdoutFilter:
    """Stateful de-noiser for ATX CLI stdout.

    Call it with a raw stdout line; it returns the payload to emit as an
    ``output`` event, or ``None`` to drop the line.

    The CLI repaints a multi-line progress block (a spinner line plus one or more
    ``⋮`` continuation lines) many times per second. Because the subprocess is
    read in universal-newline mode, every repaint arrives as fresh lines. A short
    memory of recently emitted progress lines collapses the repaint cycle to one
    event per actual state change, while any non-progress line clears the memory
    so real content is never suppressed.
    """

    def __init__(self, memory: int = 6) -> None:
        self._recent: deque[str] = deque(maxlen=memory)

    def __call__(self, raw: str) -> str | None:
        visible = visible_text(raw)
        if not visible or is_noise(visible):
            return None

        content, was_spinner = despinner(visible)
        if not content:
            return None

        if not (was_spinner or content.lstrip().startswith(_PROGRESS_MARKER)):
            self._recent.clear()
            return visible

        if content in self._recent:
            return None
        self._recent.append(content)
        return content


def build_atx_command(analysis_type: str, repo_path: str) -> list[str]:
    """Build the ATX CLI command for the given analysis type.

    Command format: atx custom def exec -n <AWS-definition> -p <repo> -x -t
    """
    definition = ANALYSIS_DEFINITIONS.get(analysis_type)
    if not definition:
        raise ValueError(f"Unknown analysis type: {analysis_type}. Available: {list(ANALYSIS_DEFINITIONS.keys())}")

    return [
        settings.atx_binary,
        "custom",
        "def",
        "exec",
        "-n",
        definition,
        "-p",
        repo_path,
        "-x",
        "-t",
    ]


# --- Storage helpers ---


def get_conversation_storage_dir(conversation_id: str) -> Path:
    """Storage directory for a conversation (created if missing).

    Raises:
        ValueError: If ``conversation_id`` is malformed. Validation lives in
            ``services/conversation_id`` and is applied by the record store, so this
            path cannot escape the storage root.
        OSError: If storage is not writable.
    """
    storage_dir = get_record_dir(conversation_id, create=True)
    if storage_dir is None:
        raise ValueError(f"Invalid conversation_id: {conversation_id!r}")
    return storage_dir


def get_events_path(conversation_id: str) -> Path:
    """Path of the durable emitted-event record for a conversation."""
    return Path(settings.storage_path) / conversation_id / EVENTS_FILENAME


def _append_event(events_path: Path, event: dict) -> None:
    """Append one emitted event to the durable record."""
    try:
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
            handle.flush()
    except OSError:
        pass


def read_events(events_path: Path, offset: int) -> tuple[list[dict], int]:
    """Read complete event lines starting at byte ``offset``.

    Returns ``(events, new_offset)``. A partially written trailing line is left
    unconsumed so the offset never lands mid-record.
    """
    if not events_path.exists():
        return [], offset

    try:
        with events_path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return [], offset

    last_newline = data.rfind(b"\n")
    if last_newline == -1:
        return [], offset

    events: list[dict] = []
    for line in data[: last_newline + 1].splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return events, offset + last_newline + 1


# --- Process state ---


def is_running(conversation_id: str) -> bool:
    """True if the ATX CLI process for this conversation is still alive."""
    process = running_processes.get(conversation_id)
    return process is not None and process.poll() is None


def is_tracked(conversation_id: str) -> bool:
    """True if this agent instance is tracking work for this conversation.

    False after an agent restart, which is how a stale ``running`` status in
    metadata is detected.
    """
    if conversation_id in running_processes:
        return True
    task = worker_tasks.get(conversation_id)
    return task is not None and not task.done()


def is_active(conversation_id: str) -> bool:
    """True if the background worker for this conversation has not finished."""
    task = worker_tasks.get(conversation_id)
    return task is not None and not task.done()


# --- Producers ---


async def _read_stdout(
    process: subprocess.Popen,
    queue: asyncio.Queue,
    stdout_done: asyncio.Event,
    log_path_holder: dict[str, str],
    raw_lines: list[str],
    storage_dir: Path,
    metadata: dict,
) -> None:
    """Producer: read ATX CLI stdout, de-noise it, and enqueue ``output`` events.

    Also detects the ``Conversation log:`` path and records it in metadata
    immediately, so a reconnect (or restart) can locate the file mid-run.
    """
    loop = asyncio.get_running_loop()
    assert process.stdout is not None
    denoise = StdoutFilter()
    try:
        while True:
            line = await loop.run_in_executor(None, process.stdout.readline)
            if not line:
                if process.poll() is not None:
                    break
                await asyncio.sleep(0.05)
                continue

            raw = line.rstrip("\n")
            raw_lines.append(raw)
            clean = strip_ansi(raw)

            if "Conversation log:" in clean and "path" not in log_path_holder:
                candidate = clean.split("Conversation log:", 1)[1].strip()
                if candidate:
                    log_path_holder["path"] = candidate
                    metadata["conversation_log"] = candidate
                    # Read-modify-write against disk, so this lands without clobbering a
                    # status a concurrent cancel may already have written.
                    update_record(storage_dir.name, conversation_log=candidate)

            payload = denoise(raw)
            if payload is not None:
                await queue.put({"type": "output", "data": payload})
    finally:
        stdout_done.set()


async def _tail_conversation_log(
    queue: asyncio.Queue,
    stdout_done: asyncio.Event,
    log_path_holder: dict[str, str],
    storage_dir: Path,
) -> None:
    """Producer: tail the ATX conversation log and enqueue ``log`` events.

    Starts as soon as the log path is detected — it does not wait for process
    exit. Keeps tailing until the process has exited *and* the file yields no
    more content, so the tail end of the log is not lost to a race with exit.
    """
    # Wait for the stdout reader to discover the log path.
    while "path" not in log_path_holder:
        if stdout_done.is_set():
            if "path" not in log_path_holder:
                return
            break
        await asyncio.sleep(0.05)

    log_path = Path(log_path_holder["path"])
    if not log_path.is_absolute():
        log_path = storage_dir / log_path

    handle = None
    buffer = ""
    idle_passes = 0
    try:
        while True:
            read_any = False

            if handle is None and log_path.exists():
                try:
                    handle = log_path.open("r", encoding="utf-8", errors="replace")
                except OSError:
                    handle = None

            if handle is not None:
                while True:
                    chunk = handle.readline()
                    if not chunk:
                        break
                    read_any = True
                    buffer += chunk
                    if buffer.endswith("\n"):
                        await queue.put({"type": "log", "data": buffer.rstrip("\n")})
                        buffer = ""

            if stdout_done.is_set() and not read_any:
                # Process has exited and this pass found nothing new. Do one
                # extra pass before giving up so a final flush is not truncated.
                idle_passes += 1
                if idle_passes >= 2:
                    break
            elif read_any:
                idle_passes = 0

            await asyncio.sleep(POLL_INTERVAL)

        if buffer.strip():
            await queue.put({"type": "log", "data": buffer.rstrip("\n")})
    finally:
        if handle is not None:
            handle.close()


# --- Worker ---


async def _run_analysis_worker(
    conversation_id: str,
    analysis_type: str,
    repo_path: str,
    storage_dir: Path,
    metadata: dict,
) -> None:
    """Background worker: run the ATX CLI and persist every emitted event.

    Runs independently of any HTTP response, so a client disconnect (page
    refresh) does not interrupt the analysis.
    """
    events_path = storage_dir / EVENTS_FILENAME

    def fail(message: str) -> None:
        _append_event(events_path, {"type": "error", "message": message})
        metadata["status"] = "failed"
        metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
        update_record(
            conversation_id,
            status="failed",
            completed_at=metadata["completed_at"],
            error=message,
        )

    try:
        cmd = build_atx_command(analysis_type, repo_path)
    except ValueError as e:
        fail(str(e))
        return

    env = os.environ.copy()
    env["ATX_ANALYSIS_ID"] = conversation_id

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=str(storage_dir),
        )
    except FileNotFoundError:
        fail(f"ATX binary not found: {settings.atx_binary}")
        return
    except OSError as e:
        fail(f"Failed to start ATX CLI: {e}")
        return

    running_processes[conversation_id] = process

    queue: asyncio.Queue = asyncio.Queue()
    stdout_done = asyncio.Event()
    log_path_holder: dict[str, str] = {}
    raw_lines: list[str] = []

    stdout_task = asyncio.create_task(
        _read_stdout(process, queue, stdout_done, log_path_holder, raw_lines, storage_dir, metadata)
    )
    tail_task = asyncio.create_task(_tail_conversation_log(queue, stdout_done, log_path_holder, storage_dir))

    async def await_producers() -> None:
        await asyncio.gather(stdout_task, tail_task, return_exceptions=True)
        # Sentinel is enqueued only after both producers have stopped putting,
        # so nothing already in the queue can be lost.
        await queue.put(None)

    watcher = asyncio.create_task(await_producers())

    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            _append_event(events_path, event)

        await asyncio.to_thread(process.wait)
        return_code = process.returncode

        try:
            (storage_dir / "output.log").write_text("\n".join(raw_lines))
        except OSError:
            pass

        # Recorded so a caller can tell "produced no documentation" apart from
        # "collection never ran".
        metadata["artifacts_collected"] = _collect_artifacts(storage_dir, metadata)

        # A concurrent cancel may already have marked this conversation, so the terminal
        # status is decided against what is on disk rather than against this task's copy.
        persisted_status = read_metadata(conversation_id).get("status")
        if persisted_status == "cancelled":
            metadata["status"] = "cancelled"
        else:
            metadata["status"] = "completed" if return_code == 0 else "failed"
        metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
        metadata["return_code"] = return_code
        update_record(
            conversation_id,
            status=metadata["status"],
            completed_at=metadata["completed_at"],
            return_code=return_code,
            artifacts_collected=metadata["artifacts_collected"],
        )

        if return_code == 0:
            _append_event(events_path, {"type": "complete"})
        elif metadata["status"] == "cancelled":
            _append_event(events_path, {"type": "error", "message": "Analysis was cancelled"})
        else:
            _append_event(events_path, {"type": "error", "message": f"ATX CLI exited with code {return_code}"})

    except asyncio.CancelledError:
        for task in (stdout_task, tail_task, watcher):
            task.cancel()
        raise
    except Exception as e:  # noqa: BLE001 — worker must always terminate the stream
        fail(f"Unexpected error: {e}")
    finally:
        for task in (stdout_task, tail_task, watcher):
            if not task.done():
                task.cancel()
        running_processes.pop(conversation_id, None)


def start_analysis(
    conversation_id: str,
    analysis_type: str,
    repo_path: str,
) -> Path:
    """Create conversation storage, persist the ``init`` event, start the worker.

    Returns the conversation storage directory. The ``init`` event is always
    persisted first so replay (and BC-26) sees it as event number one.
    """
    storage_dir = get_conversation_storage_dir(conversation_id)

    metadata = {
        "conversation_id": conversation_id,
        "analysis_type": analysis_type,
        "repo_path": repo_path,
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # Persisted before the worker starts, so the conversation is findable from the first
    # moment it exists — including by a process that did not create it.
    write_record(metadata)

    events_path = storage_dir / EVENTS_FILENAME
    events_path.write_text("")
    _append_event(events_path, {"type": "init", "conversation_id": conversation_id})

    task = asyncio.create_task(_run_analysis_worker(conversation_id, analysis_type, repo_path, storage_dir, metadata))
    worker_tasks[conversation_id] = task
    return storage_dir


# --- Streaming ---


async def stream_events(
    conversation_id: str,
    skip_events: int = 0,
    mark_replay: bool = False,
    is_disconnected: Callable[[], Coroutine[Any, Any, bool]] | None = None,
) -> AsyncGenerator[str, None]:
    """Yield persisted events as JSON strings, then tail live ones.

    Args:
        conversation_id: Conversation to stream.
        skip_events: Number of leading persisted events to skip (used by
            ``POST /analyze``, which emits ``init`` itself before cloning).
        mark_replay: Add ``"replay": true`` to events that were already
            persisted when the stream was attached.
        is_disconnected: Optional client-disconnect probe.
    """
    events_path = get_events_path(conversation_id)
    offset = 0
    skipped = 0
    first_batch = True
    saw_terminal = False

    while True:
        events, offset = read_events(events_path, offset)

        for event in events:
            if skipped < skip_events:
                skipped += 1
                continue
            payload = {**event, "replay": True} if (mark_replay and first_batch) else event
            yield json.dumps(payload)
            if event.get("type") in TERMINAL_EVENT_TYPES:
                saw_terminal = True

        first_batch = False

        if saw_terminal:
            return

        if is_disconnected is not None and await is_disconnected():
            return

        if not is_active(conversation_id):
            # Worker finished (or never existed): drain whatever landed after
            # the last read, then stop rather than polling forever.
            events, offset = read_events(events_path, offset)
            for event in events:
                if skipped < skip_events:
                    skipped += 1
                    continue
                yield json.dumps(event)
                if event.get("type") in TERMINAL_EVENT_TYPES:
                    saw_terminal = True
            return

        await asyncio.sleep(POLL_INTERVAL)


async def execute_analysis(
    conversation_id: str,
    analysis_type: str,
    repo_path: str,
    emit_init: bool = True,
) -> AsyncGenerator[str, None]:
    """Start an analysis and stream its events.

    First event is always ``{"type": "init", "conversation_id": "..."}`` — unless
    ``emit_init`` is False, meaning the caller already emitted it (e.g. because
    it needed to clone the repository after opening the stream); the persisted
    record still carries it as event one.

    Subsequent events are ``log`` (ATX conversation log, live) and ``output``
    (de-noised stdout). The final event is ``complete`` or ``error``.
    """
    start_analysis(conversation_id, analysis_type, repo_path)

    async for event_json in stream_events(conversation_id, skip_events=0 if emit_init else 1):
        yield event_json


def cancel_analysis(conversation_id: str) -> bool:
    """Cancel a running analysis by sending SIGKILL to its process.

    Returns True if the process was found and killed, False otherwise. A False here
    means only "not running in *this* process" — reconciling a record left ``running``
    by a restart is the caller's job, since only the record says whether there is
    anything to reconcile.
    """
    process = running_processes.get(conversation_id)
    if process and process.poll() is None:
        try:
            os.kill(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            running_processes.pop(conversation_id, None)
            return False
        update_record(
            conversation_id,
            status="cancelled",
            cancelled_at=datetime.now(timezone.utc).isoformat(),
        )
        return True
    return False


def generate_conversation_id() -> str:
    """Generate a unique conversation ID."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    return f"atx_{timestamp}_{short_id}"


def ensure_artifacts_collected(conversation_id: str) -> int:
    """Collect artifacts for a conversation if ``docs/`` has nothing in it yet.

    Collection normally runs once, at the end of the worker. That single chance is
    lost if the agent restarts mid-run — the CLI's output is still on disk but
    nothing ever copies it in. Retrying on read closes that gap; it is a no-op
    once ``docs/`` is populated.

    Returns the number of files collected by this call (0 if nothing was needed
    or nothing was found).
    """
    storage_dir = Path(settings.storage_path) / conversation_id
    if not storage_dir.is_dir():
        return 0

    docs_dir = storage_dir / "docs"
    if docs_dir.is_dir() and any(path.is_file() for path in docs_dir.rglob("*")):
        return 0

    return _collect_artifacts(storage_dir, read_metadata(conversation_id))


def _atx_run_dir(metadata: dict, storage_dir: Path) -> Path | None:
    """Derive the ATX CLI's own run directory from the recorded conversation log.

    The CLI prints ``Conversation log: <run_dir>/logs/<ts>-conversation.log`` and
    ``_read_stdout`` already parses that line into ``metadata["conversation_log"]``.
    The run directory is therefore two levels up from the log file — no second
    stdout-parsing mechanism is needed to find it.
    """
    recorded = metadata.get("conversation_log")
    if not recorded:
        return None

    log_path = Path(recorded)
    if not log_path.is_absolute():
        # Same relative-path handling as the log tailer.
        log_path = storage_dir / log_path

    run_dir = log_path.parent.parent
    return run_dir if run_dir != log_path.parent else None


def _artifact_source_dirs(storage_dir: Path, metadata: dict) -> list[Path]:
    """Ordered directories the ATX CLI may have written documents to.

    Verified in-container for ``AWS/comprehensive-codebase-analysis``: the CLI
    writes ``ATXDocumentation/`` **into the project path** it was given (the
    cloned repo, not the process cwd) and mirrors it, plus an ``artifacts/``
    directory, under its own run directory ``~/.aws/atx/custom/<run_id>/``.
    Neither location is the storage root, which is why the original two
    candidates found nothing. They are kept as candidates anyway — they cost one
    ``exists()`` each and cover any future CLI that does write relative to cwd.

    Documentation directories are ordered ahead of ``artifacts/`` so that when the
    same relative filename appears in both, the documentation copy is the one
    served.
    """
    candidates = [storage_dir / "ATXDocumentation"]

    repo_path = metadata.get("repo_path")
    if repo_path:
        candidates.append(Path(repo_path) / "ATXDocumentation")

    run_dir = _atx_run_dir(metadata, storage_dir)
    if run_dir is not None:
        candidates.append(run_dir / "ATXDocumentation")

    candidates.append(storage_dir / "artifacts")
    if run_dir is not None:
        candidates.append(run_dir / "artifacts")

    return candidates


def _collect_artifacts(storage_dir: Path, metadata: dict | None = None) -> int:
    """Copy ATX documentation artifacts into the conversation's ``docs/`` dir.

    Artifacts are produced outside the storage root, so they are **copied in**
    rather than served from where the CLI left them: everything the API serves
    then still resolves under the storage root and the ``/file`` reader's
    path-traversal protection stays intact.

    Relative structure below each source directory is preserved. The first source
    to provide a given relative path wins, so mirrored copies of the same tree do
    not produce duplicate entries.

    Returns the number of files collected.
    """
    metadata = metadata or {}
    docs_dir = storage_dir / "docs"

    # ``docs/`` is created only when there is something to put in it. This runs on read
    # (``ensure_artifacts_collected``), and an unconditional mkdir gave every directory
    # it was pointed at a payload marker — which promoted scratch directories such as
    # ``<storage>/repos`` into the conversation listing.
    collected = 0
    for artifact_dir in _artifact_source_dirs(storage_dir, metadata):
        if not artifact_dir.is_dir():
            continue
        # The source tree may be an untrusted cloned repo. Refuse an artifact
        # dir that is itself a symlink, and resolve its real location once so we
        # can confirm every file read stays inside it (CWE-22 symlink traversal:
        # e.g. a committed ATXDocumentation -> /etc symlink).
        if artifact_dir.is_symlink():
            continue
        artifact_root = artifact_dir.resolve()
        for file_path in sorted(artifact_dir.rglob("*")):
            if not file_path.is_file() or file_path.is_symlink():
                continue
            # Confirm the resolved source stays within the resolved artifact dir,
            # so a symlinked *parent* directory cannot pull in files from
            # outside the repo (regular files under a symlinked dir pass the
            # per-file is_symlink() check above).
            try:
                if not file_path.resolve().is_relative_to(artifact_root):
                    continue
            except (OSError, ValueError):
                continue
            if file_path.suffix.lower() not in DOCUMENT_SUFFIXES:
                continue
            try:
                if file_path.stat().st_size > MAX_ARTIFACT_BYTES:
                    # Larger than the /file reader will ever serve.
                    continue
                dest = docs_dir / file_path.relative_to(artifact_dir)
                # Defence-in-depth (CWE-22): confirm the destination resolves
                # within docs_dir before writing, so an unexpected relative
                # component in the source tree cannot escape the docs root.
                if not dest.resolve().is_relative_to(docs_dir.resolve()):
                    continue
                if dest.exists():
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)  # creates docs/ too
                dest.write_bytes(file_path.read_bytes())
                collected += 1
            except OSError:
                continue

    return collected
