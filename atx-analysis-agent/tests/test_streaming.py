"""Streaming behaviour tests for the ATX Analysis Agent.

Covers the four properties that make the console usable and a refresh
survivable:

1. The ATX conversation log is tailed **concurrently** — log lines reach the
   client while the CLI process is still running, not in one lump after exit.
2. stdout is de-noised: ANSI escapes, spinner frames and box-drawing banners are
   dropped, real content is kept.
3. ``events.jsonl`` replays the emitted stream verbatim, in order, flagged
   ``replay: true``.
4. ``GET /conversations/{id}/stream`` 404s on an unknown id, and a stale
   ``running`` metadata with no tracked process yields a terminal event instead
   of hanging.
"""

import asyncio
import json
import sys
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sse_starlette.sse import AppStatus

from main import app
from services import command_service
from services.command_service import (
    StdoutFilter,
    despinner,
    execute_analysis,
    is_noise,
    is_running,
    read_events,
    stream_events,
    strip_ansi,
    visible_text,
)


@pytest.fixture
def tmp_storage(tmp_path):
    """Point every storage consumer at a temporary directory."""
    storage = tmp_path / "storage"
    storage.mkdir()
    with patch("config.settings.storage_path", str(storage)):
        with patch("services.storage_service.settings.storage_path", str(storage)):
            with patch("services.command_service.settings.storage_path", str(storage)):
                yield storage


@pytest.fixture(autouse=True)
def clean_process_registry():
    """Keep module-level registries isolated per test.

    ``AppStatus.should_exit_event`` is a process-global asyncio primitive in
    sse_starlette; it must be reset or the second SSE test in a run binds it to
    a dead event loop.
    """
    command_service.running_processes.clear()
    command_service.worker_tasks.clear()
    AppStatus.should_exit_event = None
    yield
    command_service.running_processes.clear()
    command_service.worker_tasks.clear()
    AppStatus.should_exit_event = None


def _fake_atx_command(log_path, lines, hold_seconds, noisy=True):
    """A stand-in for the ATX CLI.

    Announces a conversation log path, writes ``lines`` to it one at a time with
    a pause between each, then stays alive for ``hold_seconds`` so a test can
    observe that the log was streamed *before* the process exited.
    """
    script = f"""
import sys, time
log_path = {str(log_path)!r}
lines = {lines!r}
noisy = {noisy!r}
print("ATX CLI starting", flush=True)
print("Conversation log: " + log_path, flush=True)
with open(log_path, "w") as handle:
    for line in lines:
        if noisy:
            sys.stdout.write("\\u001b[36m\\u28cb\\u001b[0m working\\r")
            sys.stdout.flush()
        handle.write(line + "\\n")
        handle.flush()
        time.sleep(0.2)
if noisy:
    print("\\u250c\\u2500\\u2500\\u2500\\u2510", flush=True)
    print("", flush=True)
time.sleep({hold_seconds})
print("ATX CLI finished", flush=True)
"""
    return [sys.executable, "-c", script]


# --- 1. Concurrent tail ---


@pytest.mark.asyncio
async def test_conversation_log_is_tailed_before_process_exit(tmp_storage):
    """Log lines must arrive while the CLI is still running.

    This is the regression pin for the original implementation, which read the
    conversation log only *after* the stdout loop broke on process exit. There,
    every ``log`` event was emitted with the process already dead, so
    ``logs_while_running`` would be empty and this test fails.
    """
    conversation_id = "atx_tail_live"
    log_path = tmp_storage / "conversation.log"
    agent_lines = ["agent: reading pom.xml", "agent: calling tool fs_read", "agent: writing assessment"]
    cmd = _fake_atx_command(log_path, agent_lines, hold_seconds=3.0)

    logs_while_running: list[str] = []
    logs_after_exit: list[str] = []

    with patch("services.command_service.build_atx_command", return_value=cmd):
        async for event_json in execute_analysis(conversation_id, "code-assessment", str(tmp_storage)):
            event = json.loads(event_json)
            if event["type"] == "log":
                if is_running(conversation_id):
                    logs_while_running.append(event["data"])
                else:
                    logs_after_exit.append(event["data"])

    assert logs_while_running, (
        "no conversation-log lines were streamed while the process was alive — "
        "the log is being read after exit instead of tailed concurrently"
    )
    # Every line still arrives exactly once, in order.
    assert logs_while_running + logs_after_exit == agent_lines


@pytest.mark.asyncio
async def test_tail_captures_lines_written_just_before_exit(tmp_storage):
    """The final log lines are not truncated by the race with process exit."""
    conversation_id = "atx_tail_end"
    log_path = tmp_storage / "conversation.log"
    agent_lines = ["first", "second", "last-line-before-exit"]
    cmd = _fake_atx_command(log_path, agent_lines, hold_seconds=0.0, noisy=False)

    logs: list[str] = []
    with patch("services.command_service.build_atx_command", return_value=cmd):
        async for event_json in execute_analysis(conversation_id, "code-assessment", str(tmp_storage)):
            event = json.loads(event_json)
            if event["type"] == "log":
                logs.append(event["data"])

    assert logs == agent_lines


# --- 2. stdout de-noising ---


def test_strip_ansi_removes_escape_sequences():
    assert strip_ansi("\x1b[36mhello\x1b[0m") == "hello"
    assert strip_ansi("\x1b[2K\x1b[1Gprogress") == "progress"


def test_visible_text_honours_carriage_return_overwrite():
    assert visible_text("⠋ working\r⠙ working\rDone: 12 files") == "Done: 12 files"
    assert visible_text("\x1b[36m⠹\x1b[0m scanning\r") == "⠹ scanning"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        "⠋",
        "⠋ ⠙ ⠹",
        "┌────────────┐",
        "│            │",
        "└────────────┘",
        "━━━━━━━━━━",
        "▁▂▃▄▅▆▇█",
    ],
)
def test_is_noise_drops_spinner_and_box_drawing(line):
    assert is_noise(line) is True


@pytest.mark.parametrize(
    "line",
    [
        "Conversation log: /app/storage/x/conversation.log",
        "│ Analysing repository",
        "⠋ Reading pom.xml",
        "ERROR: credentials not found",
        "Done: 12 files",
    ],
)
def test_is_noise_keeps_real_content(line):
    assert is_noise(line) is False


def test_stdout_filter_collapses_the_real_atx_progress_repaint():
    """The ATX CLI repaints a spinner line plus a `⋮` continuation line.

    Captured from a live run: without collapsing, this pair alternates forever and
    buries the useful output. Real content must still pass through untouched.
    """
    denoise = StdoutFilter()
    raw_lines = ["📝 Conversation log: /app/x/conversation.log"]
    for frame in "⠋⠙⠹⠸⠼⠴⠦⠧":
        raw_lines.append(f"\x1b[36m{frame}\x1b[0m Thinking... (ctrl + c to terminate transformation)")
        raw_lines.append("  ⋮ ~0.05 agent min")
    raw_lines.append("⠏ Thinking... (ctrl + c to terminate transformation)")
    raw_lines.append("  ⋮ ~0.06 agent min")
    raw_lines.append("✅ Analysis complete")

    emitted = [payload for payload in (denoise(line) for line in raw_lines) if payload is not None]

    assert emitted == [
        "📝 Conversation log: /app/x/conversation.log",
        "Thinking... (ctrl + c to terminate transformation)",
        "⋮ ~0.05 agent min",
        "⋮ ~0.06 agent min",
        "✅ Analysis complete",
    ]


def test_stdout_filter_keeps_repeated_real_content():
    """Deduplication is scoped to progress repaints, not to genuine output."""
    denoise = StdoutFilter()
    lines = ["ERROR: credentials not found", "ERROR: credentials not found"]
    assert [denoise(line) for line in lines] == lines


def test_despinner_identifies_progress_frames():
    assert despinner("⠋ Thinking...") == ("Thinking...", True)
    assert despinner("⠙  Reading pom.xml") == ("Reading pom.xml", True)
    assert despinner("Done: 12 files") == ("Done: 12 files", False)


@pytest.mark.asyncio
async def test_repeated_spinner_frames_collapse_to_one_event(tmp_storage):
    """A repainted progress line yields one event per state change, not per frame."""
    conversation_id = "atx_spinner"
    log_path = tmp_storage / "conversation.log"
    frames = "".join(f'sys.stdout.write("{ch} Thinking...\\r"); sys.stdout.flush()\n' for ch in "⠋⠙⠹⠸⠼⠴")
    script = f"""
import sys
print("Conversation log: {log_path}", flush=True)
open({str(log_path)!r}, "w").close()
{frames}
sys.stdout.write("⠧ Writing assessment\\r"); sys.stdout.flush()
print("Done", flush=True)
"""
    cmd = [sys.executable, "-c", script]

    outputs: list[str] = []
    with patch("services.command_service.build_atx_command", return_value=cmd):
        async for event_json in execute_analysis(conversation_id, "code-assessment", str(tmp_storage)):
            event = json.loads(event_json)
            if event["type"] == "output":
                outputs.append(event["data"])

    assert outputs.count("Thinking...") == 1
    assert "Writing assessment" in outputs
    assert "Done" in outputs
    assert not any(any(ch in line for ch in "⠋⠙⠹⠸⠼⠴⠧") for line in outputs)


@pytest.mark.asyncio
async def test_stdout_events_are_filtered_and_log_events_dominate(tmp_storage):
    """Emitted ``output`` events carry no spinner/ANSI/banner noise."""
    conversation_id = "atx_filter"
    log_path = tmp_storage / "conversation.log"
    cmd = _fake_atx_command(log_path, ["agent: step one", "agent: step two"], hold_seconds=0.0)

    outputs: list[str] = []
    logs: list[str] = []
    with patch("services.command_service.build_atx_command", return_value=cmd):
        async for event_json in execute_analysis(conversation_id, "code-assessment", str(tmp_storage)):
            event = json.loads(event_json)
            if event["type"] == "output":
                outputs.append(event["data"])
            elif event["type"] == "log":
                logs.append(event["data"])

    assert logs == ["agent: step one", "agent: step two"]
    assert "ATX CLI starting" in outputs
    assert any(line.startswith("Conversation log:") for line in outputs)
    for line in outputs:
        assert "\x1b" not in line
        assert not is_noise(line)
    assert "┌───┐" not in outputs


# --- 3. events.jsonl persistence and replay ---


@pytest.mark.asyncio
async def test_events_jsonl_replays_in_original_order_with_replay_flag(tmp_storage):
    """Replay re-sends the emitted stream verbatim, in order, flagged replay."""
    conversation_id = "atx_replay"
    log_path = tmp_storage / "conversation.log"
    cmd = _fake_atx_command(log_path, ["agent: alpha", "agent: beta"], hold_seconds=0.0, noisy=False)

    live: list[dict] = []
    with patch("services.command_service.build_atx_command", return_value=cmd):
        async for event_json in execute_analysis(conversation_id, "code-assessment", str(tmp_storage)):
            live.append(json.loads(event_json))

    replayed = [json.loads(e) async for e in stream_events(conversation_id, mark_replay=True)]

    # Replay is the emitted stream verbatim — same records, same order — with
    # only the replay marker added.
    assert all(e["replay"] is True for e in replayed)
    assert [{k: v for k, v in e.items() if k != "replay"} for e in replayed] == live
    assert live[0]["type"] == "init"
    assert live[0]["conversation_id"] == conversation_id
    assert live[-1]["type"] == "complete"
    assert [e["data"] for e in live if e["type"] == "log"] == ["agent: alpha", "agent: beta"]


@pytest.mark.asyncio
async def test_read_events_ignores_partially_written_trailing_line(tmp_storage):
    """A half-written record is not consumed, so the offset never splits one."""
    events_path = tmp_storage / "events.jsonl"
    events_path.write_text('{"type": "init", "conversation_id": "a"}\n{"type": "log", "dat')

    events, offset = read_events(events_path, 0)
    assert [e["type"] for e in events] == ["init"]

    events_path.write_text('{"type": "init", "conversation_id": "a"}\n{"type": "log", "data": "x"}\n')
    events, offset = read_events(events_path, offset)
    assert [e["type"] for e in events] == ["log"]


@pytest.mark.asyncio
async def test_conversation_log_path_recorded_in_metadata_during_run(tmp_storage):
    """The log path lands in metadata as soon as stdout announces it."""
    conversation_id = "atx_meta_path"
    log_path = tmp_storage / "conversation.log"
    cmd = _fake_atx_command(log_path, ["agent: one"], hold_seconds=2.0, noisy=False)

    metadata_path = tmp_storage / conversation_id / "metadata.json"
    seen_mid_run = False

    with patch("services.command_service.build_atx_command", return_value=cmd):
        async for event_json in execute_analysis(conversation_id, "code-assessment", str(tmp_storage)):
            event = json.loads(event_json)
            if event["type"] == "log" and is_running(conversation_id):
                metadata = json.loads(metadata_path.read_text())
                if metadata.get("conversation_log") == str(log_path):
                    assert metadata["status"] == "running"
                    seen_mid_run = True

    assert seen_mid_run, "conversation_log was not written to metadata while the analysis was running"


# --- 4. Stream endpoint ---


@pytest.mark.asyncio
async def test_stream_not_found(tmp_storage):
    """GET /conversations/{id}/stream returns 404 for an unknown conversation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/conversations/nonexistent-id/stream")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stream_replays_finished_conversation(tmp_storage):
    """A finished conversation replays through the HTTP endpoint."""
    conversation_id = "atx_http_replay"
    conv_dir = tmp_storage / conversation_id
    conv_dir.mkdir()
    (conv_dir / "metadata.json").write_text(
        json.dumps({"conversation_id": conversation_id, "status": "completed", "created_at": "2025-01-01T00:00:00Z"})
    )
    (conv_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "init", "conversation_id": conversation_id}),
                json.dumps({"type": "output", "data": "ATX CLI starting"}),
                json.dumps({"type": "log", "data": "agent: hello"}),
                json.dumps({"type": "complete"}),
            ]
        )
        + "\n"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/conversations/{}/stream".format(conversation_id))

    assert response.status_code == 200
    events = [json.loads(line[len("data: ") :]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["init", "output", "log", "complete"]
    assert all(e["replay"] is True for e in events)


@pytest.mark.asyncio
async def test_stream_reconciles_stale_running_metadata(tmp_storage):
    """`running` metadata with no tracked process yields a terminal event."""
    conversation_id = "atx_stale"
    conv_dir = tmp_storage / conversation_id
    conv_dir.mkdir()
    (conv_dir / "metadata.json").write_text(
        json.dumps({"conversation_id": conversation_id, "status": "running", "created_at": "2025-01-01T00:00:00Z"})
    )
    (conv_dir / "events.jsonl").write_text(json.dumps({"type": "init", "conversation_id": conversation_id}) + "\n")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await asyncio.wait_for(client.get("/conversations/{}/stream".format(conversation_id)), timeout=10)

    assert response.status_code == 200
    events = [json.loads(line[len("data: ") :]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[0]["type"] == "init"
    assert events[-1]["type"] == "error"
    assert "interrupted" in events[-1]["message"].lower()

    metadata = json.loads((conv_dir / "metadata.json").read_text())
    assert metadata["status"] == "interrupted"
