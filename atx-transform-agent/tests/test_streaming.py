"""Streaming contract tests for the ATX Transform Agent.

The stream is consumed by the shared frontend SSE client, which discards SSE
``event:`` names and discriminates purely on the JSON ``type`` field (see
design.md → "SSE Event Protocol"). These tests pin that contract:

1. Every payload carries ``type``; log lines are ``output`` events with ``data``.
2. A completed run ends on ``complete``; a failed run ends on ``error`` with a
   human-readable ``message``.
3. Replayed payloads carry ``replay: true``; live ones do not.
4. stdout de-noising drops spinner/ANSI/box-drawing decoration while keeping
   real content, including genuinely repeated content.
"""

import json
import sys
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sse_starlette.sse import AppStatus

from main import app
from services import storage_service, transform_service
from services.stdout_filter import (
    StdoutFilter,
    despinner,
    is_noise,
    strip_ansi,
    visible_text,
)
from services.transform_service import get_log_path, run_transformation


@pytest.fixture
def tmp_storage(tmp_path):
    """Point the transform agent's storage at a temporary directory."""
    storage = tmp_path / "storage"
    storage.mkdir()
    with patch("config.settings.storage_path", str(storage)):
        with patch("services.transform_service.settings.storage_path", str(storage)):
            yield storage


@pytest.fixture(autouse=True)
def clean_registries():
    """Keep the in-memory liveness registries isolated.

    Records themselves live on disk (one ``metadata.json`` per transformation) and are
    isolated by the per-test ``tmp_storage``; only liveness is in-process.

    ``AppStatus.should_exit_event`` is a process-global asyncio primitive in
    sse_starlette; it must be reset or the second SSE test in a run binds it to a
    dead event loop.
    """
    transform_service.running_processes.clear()
    transform_service.active_transformations.clear()
    AppStatus.should_exit_event = None
    yield
    transform_service.running_processes.clear()
    transform_service.active_transformations.clear()
    AppStatus.should_exit_event = None


def _register(repo_id: str, status: str, **extra) -> dict:
    """Persist a transformation record the way ``POST /transform`` does.

    A ``running`` record is also marked active, because ``POST /transform`` marks it
    active in the request path before its background task starts — an untracked
    ``running`` record means "killed by a restart", not "still working".
    """
    record = storage_service.write_record(
        {
            "repo_id": repo_id,
            "status": status,
            "created_at": "2025-01-01T00:00:00+00:00",
            "repo_url": "https://github.com/org/repo",
            "branch": "main",
            "transformation_type": "java-upgrade",
            **extra,
        }
    )
    if status == "running":
        transform_service.mark_active(repo_id)
    return record


def _finish_after(repo_id: str, log_path, live_lines: list[str], delay: float = 0.6) -> threading.Thread:
    """Drive a running transformation to completion from another thread.

    Stands in for the background task: appends live output to the log, then writes the
    terminal status to ``metadata.json``. The stream's tail loop re-reads that file
    every 0.5 s, so this exercises the real running → completed handover across a
    thread boundary rather than a monkey-patched status read.
    """

    def worker() -> None:
        time.sleep(delay)
        with open(log_path, "a") as handle:
            for line in live_lines:
                handle.write(f"[2025-01-01T00:00:02+00:00] {line}\n")
        time.sleep(delay / 2)
        storage_service.update_record(
            repo_id,
            status="completed",
            completed_at="2025-01-01T00:00:03+00:00",
        )
        transform_service.clear_active(repo_id)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _sse_payloads(text: str) -> list[dict]:
    prefix = "data: "
    return [json.loads(line[len(prefix) :]) for line in text.splitlines() if line.startswith(prefix)]


# --- 1. Every payload carries a discriminator ---


def test_every_stream_payload_carries_a_type(tmp_storage):
    """The shared SSE client cannot see ``event:`` names — ``type`` is the contract.

    Regression pin: the previous implementation emitted ``{"line": ...}`` /
    ``{"status": ...}`` with the discriminator only in the SSE ``event:`` name,
    so ``event.type`` was never defined on the client and the terminal branch
    could not fire.
    """
    repo_id = "transform-typed"
    _register(repo_id, "completed")
    get_log_path(repo_id).write_text("[2025-01-01T00:00:00+00:00] Upgrading pom.xml\n")

    with TestClient(app) as client:
        response = client.get(f"/conversations/{repo_id}/stream")

    assert response.status_code == 200
    payloads = _sse_payloads(response.text)
    assert payloads, "stream emitted no payloads"
    for payload in payloads:
        assert "type" in payload, f"payload without a type discriminator: {payload}"


def test_log_lines_are_output_events_with_data(tmp_storage):
    """Log lines use ``{"type": "output", "data": ...}`` — not ``line``."""
    repo_id = "transform-output"
    _register(repo_id, "completed")
    get_log_path(repo_id).write_text(
        "[2025-01-01T00:00:00+00:00] Upgrading pom.xml\n[2025-01-01T00:00:01+00:00] Done\n"
    )

    with TestClient(app) as client:
        response = client.get(f"/conversations/{repo_id}/stream")

    lines = [p for p in _sse_payloads(response.text) if p["type"] == "output"]
    assert len(lines) == 2
    for payload in lines:
        assert "data" in payload
        assert "line" not in payload
    assert lines[0]["data"].endswith("Upgrading pom.xml")


# --- 2. Terminal events ---


def test_completed_transformation_ends_on_typed_complete(tmp_storage):
    repo_id = "transform-complete"
    _register(repo_id, "completed")
    get_log_path(repo_id).write_text("[2025-01-01T00:00:00+00:00] Done\n")

    with TestClient(app) as client:
        response = client.get(f"/conversations/{repo_id}/stream")

    terminal = _sse_payloads(response.text)[-1]
    assert terminal["type"] == "complete"
    assert terminal["status"] == "completed"


@pytest.mark.parametrize(
    ("status", "extra"),
    [
        ("failed", {}),
        ("error", {"error": "ATX binary not found"}),
    ],
)
def test_failed_transformation_ends_on_typed_error_with_message(tmp_storage, status, extra):
    """The ``error`` union member requires ``message`` — a bare status is not enough."""
    repo_id = f"transform-{status}"
    _register(repo_id, status, **extra)
    get_log_path(repo_id).write_text("[2025-01-01T00:00:00+00:00] boom\n")

    with TestClient(app) as client:
        response = client.get(f"/conversations/{repo_id}/stream")

    terminal = _sse_payloads(response.text)[-1]
    assert terminal["type"] == "error"
    assert isinstance(terminal.get("message"), str) and terminal["message"].strip()


def test_stream_unknown_repo_id_is_404(tmp_storage):
    with TestClient(app) as client:
        assert client.get("/conversations/nope/stream").status_code == 404


# --- 3. Replay flagging ---


def test_replayed_payloads_are_flagged_and_live_ones_are_not(tmp_storage):
    repo_id = "transform-replay"
    log_path = get_log_path(repo_id)
    log_path.write_text("[2025-01-01T00:00:00+00:00] stored line\n")

    _register(repo_id, "running")
    _finish_after(repo_id, log_path, ["live line"])

    with TestClient(app) as client:
        response = client.get(f"/conversations/{repo_id}/stream")

    payloads = _sse_payloads(response.text)
    outputs = [p for p in payloads if p["type"] == "output"]
    stored = [p for p in outputs if p["data"].endswith("stored line")]
    live = [p for p in outputs if p["data"].endswith("live line")]

    assert stored and all(p.get("replay") is True for p in stored)
    assert live and all("replay" not in p for p in live)
    assert payloads[-1]["type"] == "complete"


def test_stream_does_not_terminate_before_the_cli_starts(tmp_storage):
    """A just-started transformation must not get an immediate terminal event.

    Between ``POST /transform`` returning and the ATX CLI launching, the background
    task is still cloning, so nothing is registered in ``running_processes``.
    Keying the tail loop on that emitted ``complete`` with ``status: "running"``
    straight away, clearing the UI's in-progress state before any work happened.
    """
    repo_id = "transform-not-yet-started"
    log_path = get_log_path(repo_id)
    log_path.write_text("")

    _register(repo_id, "running")
    _finish_after(repo_id, log_path, ["cli finally produced a line"])

    assert not transform_service.is_running(repo_id), "precondition: no process registered yet"

    with TestClient(app) as client:
        response = client.get(f"/conversations/{repo_id}/stream")

    payloads = _sse_payloads(response.text)
    # The terminal event is reached only after the record left `running`, and the
    # output produced in the meantime is not lost.
    assert any(p["type"] == "output" for p in payloads)
    assert payloads[-1] == {"type": "complete", "status": "completed"}
    # No terminal payload may claim `running` — that is what a liveness-keyed loop emits
    # during the clone window.
    assert not any(p.get("status") == "running" for p in payloads)


def test_tail_loop_observes_a_status_transition_written_by_another_process(tmp_storage):
    """The 0.5 s poll must see ``running`` → ``completed`` through the persisted record.

    The status is written to ``metadata.json`` by the background task, not handed to the
    stream in memory. If the lookup ever cached, this hangs (missed transition) or
    terminates early (premature expiry) — so this pins the read path the tail loop
    depends on.
    """
    repo_id = "transform-transition"
    log_path = get_log_path(repo_id)
    log_path.write_text("")

    _register(repo_id, "running")
    _finish_after(repo_id, log_path, ["work happened"], delay=0.8)

    with TestClient(app) as client:
        response = client.get(f"/conversations/{repo_id}/stream")

    payloads = _sse_payloads(response.text)
    assert [p["data"] for p in payloads if p["type"] == "output"] == ["[2025-01-01T00:00:02+00:00] work happened"]
    assert payloads[-1] == {"type": "complete", "status": "completed"}
    assert storage_service.read_record(repo_id)["status"] == "completed"


def test_a_run_left_running_by_a_restart_terminates_instead_of_tailing_forever(tmp_storage):
    """A persisted ``running`` status with no work tracked is reconciled, not tailed.

    Liveness lives in memory, so after a restart nothing will ever write this record's
    terminal status. Without reconciliation the tail loop would poll a status that can
    no longer change and the UI would sit in "in progress" indefinitely.
    """
    repo_id = "transform-orphaned"
    get_log_path(repo_id).write_text("[2025-01-01T00:00:00+00:00] partial work\n")

    _register(repo_id, "running")
    # The restart: the record survives on disk, the liveness registries do not.
    transform_service.active_transformations.clear()
    transform_service.running_processes.clear()

    with TestClient(app) as client:
        response = client.get(f"/conversations/{repo_id}/stream")

    payloads = _sse_payloads(response.text)
    assert any(p["type"] == "output" for p in payloads)
    assert payloads[-1]["type"] == "error"
    assert "restart" in payloads[-1]["message"].lower()
    assert storage_service.read_record(repo_id)["status"] == "interrupted"


# --- 4. stdout de-noising ---


def test_strip_ansi_removes_escape_sequences():
    assert strip_ansi("\x1b[36mhello\x1b[0m") == "hello"
    assert strip_ansi("\x1b[2K\x1b[1Gprogress") == "progress"


def test_visible_text_honours_carriage_return_overwrite():
    assert visible_text("⠋ working\r⠙ working\rDone: 12 files") == "Done: 12 files"


@pytest.mark.parametrize(
    "line",
    ["", "   ", "⠋", "⠋ ⠙ ⠹", "┌────────────┐", "│            │", "━━━━━━━━━━", "▁▂▃▄▅▆▇█"],
)
def test_is_noise_drops_decoration(line):
    assert is_noise(line) is True


@pytest.mark.parametrize(
    "line",
    [
        "│ Region: us-east-1 │",
        "⠋ Upgrading pom.xml",
        "ERROR: credentials not found",
        "Done: 12 files",
    ],
)
def test_is_noise_keeps_real_content(line):
    assert is_noise(line) is False


def test_despinner_identifies_progress_frames():
    assert despinner("⠋ Thinking...") == ("Thinking...", True)
    assert despinner("Done: 12 files") == ("Done: 12 files", False)


def test_stdout_filter_collapses_progress_repaints():
    denoise = StdoutFilter()
    raw_lines = ["📝 Conversation log: /app/x/conversation.log"]
    for frame in "⠋⠙⠹⠸⠼⠴⠦⠧":
        raw_lines.append(f"\x1b[36m{frame}\x1b[0m Thinking... (ctrl + c to terminate transformation)")
        raw_lines.append("  ⋮ ~0.05 agent min")
    raw_lines.append("⠏ Thinking... (ctrl + c to terminate transformation)")
    raw_lines.append("  ⋮ ~0.06 agent min")
    raw_lines.append("✅ Transformation complete")

    emitted = [payload for payload in (denoise(line) for line in raw_lines) if payload is not None]

    assert emitted == [
        "📝 Conversation log: /app/x/conversation.log",
        "Thinking... (ctrl + c to terminate transformation)",
        "⋮ ~0.05 agent min",
        "⋮ ~0.06 agent min",
        "✅ Transformation complete",
    ]


def test_stdout_filter_keeps_repeated_real_content():
    denoise = StdoutFilter()
    lines = ["ERROR: credentials not found", "ERROR: credentials not found"]
    assert [denoise(line) for line in lines] == lines


def test_denoising_is_applied_when_the_log_is_written(tmp_storage, tmp_path):
    """Filtering happens at write time, so output.log is the de-noised record.

    The stream is a faithful tail of that file, which keeps replay and live views
    identical by construction.
    """
    repo_id = "transform-denoise"
    _register(repo_id, "running")
    script = r"""
import sys
print("Upgrading pom.xml", flush=True)
for frame in "\u280b\u2819\u2839\u2838":
    sys.stdout.write("\u001b[36m" + frame + "\u001b[0m Thinking...\r")
    sys.stdout.flush()
print("\u250c\u2500\u2500\u2500\u2510", flush=True)
print("", flush=True)
print("ERROR: boom", flush=True)
print("ERROR: boom", flush=True)
"""
    cmd = [sys.executable, "-c", script]

    with patch("services.transform_service.build_atx_command", return_value=cmd):
        exit_code = run_transformation(repo_id, "java-upgrade", tmp_path)

    assert exit_code == 0
    stored = [line for line in get_log_path(repo_id).read_text().splitlines() if line]
    content = [line.split("] ", 1)[1] for line in stored]

    assert "Upgrading pom.xml" in content
    assert content.count("Thinking...") == 1, f"progress repaints were not collapsed: {content}"
    assert content.count("ERROR: boom") == 2, "genuinely repeated content must not be suppressed"
    for line in content:
        assert "\x1b" not in line
        assert not is_noise(line)
