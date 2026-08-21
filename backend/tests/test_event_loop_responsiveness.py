"""Event-loop responsiveness regression tests.

The analysis pipeline is entirely synchronous work: a GitPython clone, the
Tree-sitter parse walk, and two large Bedrock calls. If any of that runs on the
uvicorn event loop, the whole application stops answering for the duration of
an analysis (~2 minutes) — the POST response body never flushes and every
subsequent request queues behind it.

These tests pin the property rather than the symptom. Each one starts real
analysis work with the blocking step stubbed to park a thread, then asserts two
things while that work is still in flight:

  1. the event loop regained control (a second request is served, or a
     concurrent coroutine keeps running), and
  2. the blocking step is executing on a worker thread, not the loop thread.

Covers steering `#acceptance-tests` Test 12 scenario 3 (frontend remains
responsive during AI enrichment).
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from agents.llm_judge import SCORING_DIMENSIONS
from main import app
from state import app_state
from utils.progress_tracker import ProgressTracker
from utils.storage_manager import StorageManager

# How long a stubbed blocking call holds its thread. Long enough that a blocked
# event loop is unmistakable, short enough to keep the suite fast.
_BLOCK_SECONDS = 1.5

# Work done concurrently with the blocking step must finish in milliseconds, not
# wait out the full block.
_MAX_CONCURRENT_LATENCY = 0.5

# Bound on how long we wait to observe the blocking work starting. Exceeding the
# block duration means the loop was frozen for the whole block.
_START_TIMEOUT = _BLOCK_SECONDS + 1.0


class _BlockingProbe:
    """Records the thread a stubbed blocking call runs on, then parks there."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.thread_ident: int | None = None

    def block(self) -> None:
        self.thread_ident = threading.get_ident()
        self.entered.set()
        self.release.wait(timeout=_BLOCK_SECONDS)


async def _await_start(probe: _BlockingProbe) -> None:
    """Yield to the loop until the blocking work reports that it started."""
    deadline = time.monotonic() + _START_TIMEOUT
    while not probe.entered.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)


def _init_state(tmp_path: Path) -> None:
    app_state.storage_manager = StorageManager(base_path=str(tmp_path / "analyses"))
    app_state.progress_tracker = ProgressTracker()


# --- GitHub analysis entrypoint ---


def test_github_analysis_does_not_block_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GitHub analysis in flight must not stop the server answering requests."""
    _init_state(tmp_path)
    probe = _BlockingProbe()

    from services.code_parser_service import CodeParserService
    from services.github_handler import GitHubHandler

    def _init_handler(self, base_path: str = "") -> None:
        # The real __init__ mkdirs /app/shared_repos, absent outside the container.
        self.base_path = str(tmp_path / "repos")

    def _blocking_clone(self, repo_url, branch="main", pat_token="", **kwargs) -> str:
        probe.block()
        return str(tmp_path)

    monkeypatch.setattr(GitHubHandler, "__init__", _init_handler)
    monkeypatch.setattr(GitHubHandler, "clone", _blocking_clone)
    monkeypatch.setattr(
        CodeParserService,
        "analyze_directory",
        lambda self, *a, **k: None,
    )

    async def scenario() -> None:
        loop_thread = threading.get_ident()
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            started = time.perf_counter()
            post_task = asyncio.create_task(
                client.post(
                    "/api/analyze/github",
                    json={
                        "repo_url": "https://github.com/example/repo",
                        "branch": "main",
                    },
                )
            )

            await _await_start(probe)
            assert probe.entered.is_set(), "background analysis never started"

            health = await client.get("/health")
            analyses = await client.get("/api/analyses")
            # Measured from the POST, so a loop frozen for the whole blocking
            # window cannot pass no matter when it hands control back.
            served_after = time.perf_counter() - started

            probe.release.set()
            post_response = await post_task

        assert post_response.status_code == 200
        assert post_response.json()["status"] == "processing"

        assert health.status_code == 200
        assert analyses.status_code == 200
        assert served_after < _MAX_CONCURRENT_LATENCY, (
            f"/health and /api/analyses took {served_after:.2f}s to be served "
            f"after the analysis started; the event loop was blocked"
        )

        assert probe.thread_ident is not None
        assert (
            probe.thread_ident != loop_thread
        ), "the repository clone ran on the event loop thread"

    asyncio.run(scenario())


# --- ZIP upload entrypoint ---


def test_zip_upload_analysis_does_not_block_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_zip: Path
) -> None:
    """A ZIP-upload analysis in flight must not stop the server answering."""
    _init_state(tmp_path)
    probe = _BlockingProbe()

    from services.code_parser_service import CodeParserService

    def _blocking_analyze_zip(self, zip_path: str, analysis_id: str) -> None:
        probe.block()

    monkeypatch.setattr(CodeParserService, "analyze_zip", _blocking_analyze_zip)

    async def scenario() -> None:
        loop_thread = threading.get_ident()
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            started = time.perf_counter()
            post_task = asyncio.create_task(
                client.post(
                    "/api/analyze/upload",
                    files={
                        "file": (
                            "sample.zip",
                            sample_zip.read_bytes(),
                            "application/zip",
                        )
                    },
                )
            )

            await _await_start(probe)
            assert probe.entered.is_set(), "background analysis never started"

            health = await client.get("/health")
            served_after = time.perf_counter() - started

            probe.release.set()
            post_response = await post_task

        assert post_response.status_code == 200
        assert health.status_code == 200
        assert served_after < _MAX_CONCURRENT_LATENCY, (
            f"/health took {served_after:.2f}s to be served after the analysis "
            f"started; the event loop was blocked"
        )
        assert probe.thread_ident is not None
        assert (
            probe.thread_ident != loop_thread
        ), "the ZIP pipeline ran on the event loop thread"

    asyncio.run(scenario())


# --- SSE streaming generators ---


def _drive_stream(
    make_stream, probe: _BlockingProbe
) -> tuple[int, float, int, list[dict]]:
    """Consume an SSE async generator while a heartbeat coroutine runs.

    Returns (loop_thread_ident, seconds_until_loop_regained_control,
    heartbeat_ticks_during_block, collected_events).
    """
    result: dict = {}

    async def scenario() -> None:
        result["loop_thread"] = threading.get_ident()
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        events: list[dict] = []

        async def drain() -> None:
            async for event in make_stream():
                events.append(event)

        heartbeat_task = asyncio.create_task(heartbeat())
        started = time.perf_counter()
        drain_task = asyncio.create_task(drain())

        await _await_start(probe)
        # Time from kicking off the stream to the loop being able to observe
        # that the blocking call had started. If the blocking call ran on the
        # loop, this cannot be less than the full block duration.
        result["regained"] = time.perf_counter() - started

        ticks_at_entry = ticks
        await asyncio.sleep(0.2)
        result["ticks"] = ticks - ticks_at_entry

        probe.release.set()
        await drain_task
        heartbeat_task.cancel()
        result["events"] = events

    asyncio.run(scenario())
    return (
        result["loop_thread"],
        result["regained"],
        result["ticks"],
        result["events"],
    )


def test_documentation_stream_does_not_block_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bedrock calls inside the SSE generator must run off the loop.

    An async generator cannot be declared `def` — it has to keep yielding SSE
    events — so the blocking Bedrock call has to be handed to a worker thread
    explicitly. Between yields the loop must stay free.
    """
    from agents.doc_analysis_agent import DocAnalysisAgent

    storage = StorageManager(base_path=str(tmp_path / "analyses"))
    analysis_id = "github_20250101_120000"
    storage.save(
        analysis_id,
        {
            "analysis_id": analysis_id,
            "source_type": "github",
            "file_stats": [
                {"extension": ".py", "count": 1, "total_lines": 10, "total_size": 100}
            ],
            "folder_structure": {"name": "root", "type": "directory", "children": []},
            "dependencies": [],
            "parsed_files": [],
        },
    )

    probe = _BlockingProbe()

    def _blocking_invoke(self, prompt: str, max_tokens: int = 4096) -> str:
        probe.block()
        return "# Generated documentation"

    monkeypatch.setattr(DocAnalysisAgent, "_invoke_model", _blocking_invoke)

    agent = DocAnalysisAgent(storage)
    loop_thread, regained, ticks, events = _drive_stream(
        lambda: agent.generate_documentation(analysis_id), probe
    )

    assert regained < _MAX_CONCURRENT_LATENCY, (
        f"the event loop was frozen for {regained:.2f}s after the documentation "
        f"stream started the Bedrock call"
    )
    assert (
        ticks >= 5
    ), f"event loop only advanced {ticks} times during the blocking Bedrock call"
    assert probe.thread_ident is not None
    assert (
        probe.thread_ident != loop_thread
    ), "the Bedrock call ran on the event loop thread"
    assert any(e.get("type") == "content" for e in events)
    assert any(e.get("type") == "complete" for e in events)


def test_judge_stream_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLMJudge scores five dimensions serially; each call must be off-loop."""
    from agents.llm_judge import LLMJudge

    probe = _BlockingProbe()
    thread_idents: list[int] = []

    def _blocking_invoke(self, prompt: str, max_tokens: int = 2048) -> str:
        thread_idents.append(threading.get_ident())
        if not probe.entered.is_set():
            probe.block()
        return '{"score": 8, "justification": "fine"}'

    monkeypatch.setattr(LLMJudge, "_invoke_model", _blocking_invoke)

    judge = LLMJudge()
    loop_thread, regained, ticks, events = _drive_stream(
        lambda: judge.evaluate_streaming("some documentation text"), probe
    )

    assert regained < _MAX_CONCURRENT_LATENCY, (
        f"the event loop was frozen for {regained:.2f}s after the judge stream "
        f"started scoring"
    )
    assert (
        ticks >= 5
    ), f"event loop only advanced {ticks} times during the blocking scoring call"
    assert thread_idents, "judge never invoked the model"
    assert (
        loop_thread not in thread_idents
    ), "at least one scoring call ran on the event loop thread"
    assert any(e.get("type") == "complete" for e in events)


# --- Supplemental: pin the exact regression shape ---


def test_background_task_callables_are_not_coroutine_functions() -> None:
    """`BackgroundTasks` callables must be plain `def` so FastAPI threadpools them.

    Weaker than the concurrency tests above, but it pins the precise regression:
    re-declaring either entrypoint `async def` puts the whole pipeline back on
    the event loop.
    """
    from routes.analysis import _run_github_analysis
    from services.code_parser_service import CodeParserService

    assert not inspect.iscoroutinefunction(_run_github_analysis)
    assert not inspect.isasyncgenfunction(_run_github_analysis)
    assert not inspect.iscoroutinefunction(CodeParserService.analyze_zip)
    assert not inspect.iscoroutinefunction(CodeParserService.analyze_directory)


def test_judge_retry_backoff_does_not_sleep_on_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry wrapper's backoff sleep must run on the worker thread too.

    Moving the Bedrock call off the loop is not enough once retries exist: if the
    nesting is inverted — a retry loop on the loop awaiting a threadpooled call —
    the backoff `time.sleep` lands on the loop and freezes the application for
    the length of the backoff, which is exactly the freeze the threadpool move
    was meant to remove. So what gets threadpooled has to be the call *plus* its
    retries.
    """
    import json as _json
    from unittest.mock import MagicMock

    import utils.bedrock
    from agents.llm_judge import LLMJudge
    from botocore.exceptions import ReadTimeoutError

    endpoint = "https://bedrock-runtime.us-east-1.amazonaws.com/model/m/invoke"

    # `invoke_with_retry` binds `time.sleep` as a default argument, so the sleep
    # cannot be intercepted to read its thread. Measured instead: real backoff
    # elapses while the heartbeat keeps ticking. A backoff on the loop cannot
    # produce both.
    backoff = 0.3
    monkeypatch.setattr(
        utils.bedrock.settings, "BEDROCK_RETRY_BASE_DELAY_SECONDS", backoff
    )
    expected_backoff = backoff * len(SCORING_DIMENSIONS)

    call_threads: list[int] = []

    class _FlakyClient:
        """Fails the first attempt of every scoring call, then succeeds."""

        def __init__(self) -> None:
            self.calls = 0

        def invoke_model(self, **kwargs: object) -> dict:
            call_threads.append(threading.get_ident())
            self.calls += 1
            if self.calls % 2 == 1:
                raise ReadTimeoutError(endpoint_url=endpoint)
            payload = {"content": [{"text": '{"score": 8, "justification": "ok"}'}]}
            return {"body": MagicMock(read=lambda: _json.dumps(payload).encode())}

    judge = LLMJudge()
    judge._client = _FlakyClient()

    result: dict = {}

    async def scenario() -> None:
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        heartbeat_task = asyncio.create_task(heartbeat())
        started = time.perf_counter()
        events = [
            event async for event in judge.evaluate_streaming("documentation text")
        ]
        result["elapsed"] = time.perf_counter() - started
        heartbeat_task.cancel()

        result["loop_thread"] = threading.get_ident()
        result["ticks"] = ticks
        result["events"] = events

    asyncio.run(scenario())

    # Five dimensions, each retried once: the shared policy's backoff really ran.
    assert (
        result["elapsed"] >= expected_backoff
    ), f"only {result['elapsed']:.2f}s elapsed; the retries did not back off"
    assert len(call_threads) == 2 * len(SCORING_DIMENSIONS)
    assert result["loop_thread"] not in call_threads

    # With ~1.5s of backoff and a 10ms heartbeat, a responsive loop ticks well
    # over a hundred times; a loop sleeping through the backoff ticks near zero.
    min_ticks = int(expected_backoff / 0.01 * 0.5)
    assert result["ticks"] >= min_ticks, (
        f"event loop only advanced {result['ticks']} times during "
        f"{result['elapsed']:.2f}s of retry backoff; the backoff slept on the "
        f"loop, so the retry loop is outside the threadpool instead of inside it"
    )
    assert any(e.get("type") == "complete" for e in result["events"])
