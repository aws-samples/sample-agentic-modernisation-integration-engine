"""AI enrichment status classification.

The design authority ("AI Enrichment Status Semantics") separates two outcomes
that are easy to conflate:

- `failed`  — an exception was raised during enrichment
- `skipped` — Bedrock unavailable, or enrichment deliberately not attempted

Collapsing both into `skipped` makes a Bedrock read timeout indistinguishable
from "we chose not to run the AI step", which is what made a real, actionable
failure read to a user as a no-op. These tests pin the distinction, the
specificity of `ai_enrichment_error`, and the guarantee that the deterministic
analysis results survive either outcome.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError

from services.code_parser_service import CodeParserService
from state import app_state
from utils.storage_manager import StorageManager

ANALYSIS_ID = "github_20260805_120000"


def _deterministic_result() -> dict[str, Any]:
    """A phase-1 analysis result, as the pipeline stores it before enrichment."""
    return {
        "analysis_id": ANALYSIS_ID,
        "source_type": "github",
        "source_url": "https://github.com/example/repo",
        "file_stats": [
            {"extension": ".py", "count": 3, "total_lines": 90, "total_size": 2100}
        ],
        "folder_structure": {"name": "repo", "type": "directory", "children": []},
        "dependencies": [
            {
                "name": "fastapi",
                "version": "0.115.5",
                "ecosystem": "pip",
                "source_file": "requirements.txt",
            }
        ],
        "dependency_graph": {"nodes": [{"id": "fastapi"}], "links": []},
        "upgrade_recommendations": [],
        "diagrams": {
            "class_diagram": "classDiagram\n  class App",
            "sequence_diagram": "",
            "integration_diagram": "",
        },
        "completed_at": "2026-08-05T12:00:00Z",
    }


def _assert_deterministic_results_survived(stored: dict[str, Any]) -> None:
    """Enrichment outcome must never cost us the deterministic analysis."""
    assert stored["file_stats"] == _deterministic_result()["file_stats"]
    assert stored["folder_structure"] == _deterministic_result()["folder_structure"]
    assert stored["dependencies"] == _deterministic_result()["dependencies"]
    assert stored["dependency_graph"] == _deterministic_result()["dependency_graph"]
    assert stored["diagrams"] == _deterministic_result()["diagrams"]
    assert stored["completed_at"] == "2026-08-05T12:00:00Z"


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    """Real on-disk storage, so we assert on what actually persists."""
    previous = app_state.storage_manager
    manager = StorageManager(base_path=str(tmp_path / "analyses"))
    app_state.storage_manager = manager
    yield manager
    app_state.storage_manager = previous


def _read_timeout() -> ReadTimeoutError:
    return ReadTimeoutError(
        endpoint_url=(
            "https://bedrock-runtime.us-east-1.amazonaws.com/model/"
            "us.anthropic.claude-sonnet-4-5-20250929-v1%3A0/invoke"
        )
    )


def _access_denied() -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "You don't have access to the model with the specified model ID.",
            }
        },
        "InvokeModel",
    )


# ─── failed: an exception was raised during enrichment ────────────────────────


def test_read_timeout_is_failed_not_skipped(
    storage: StorageManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Bedrock read timeout is a failure, not a deliberate skip."""
    service = CodeParserService()
    monkeypatch.setattr(
        service,
        "_invoke_bedrock",
        lambda *a, **kw: (_ for _ in ()).throw(_read_timeout()),
    )

    result = _deterministic_result()
    service._run_ai_enrichment(ANALYSIS_ID, result)

    stored = storage.load(ANALYSIS_ID)
    assert stored is not None
    assert stored["ai_enrichment_status"] == "failed"
    _assert_deterministic_results_survived(stored)


def test_read_timeout_error_names_the_cause(
    storage: StorageManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ai_enrichment_error` must be specific enough to act on."""
    service = CodeParserService()
    monkeypatch.setattr(
        service,
        "_invoke_bedrock",
        lambda *a, **kw: (_ for _ in ()).throw(_read_timeout()),
    )

    result = _deterministic_result()
    service._run_ai_enrichment(ANALYSIS_ID, result)

    error = storage.load(ANALYSIS_ID)["ai_enrichment_error"]
    assert "timed out" in error.lower()
    # A timeout and a denied model demand different operator actions, so the
    # message must say which one happened and what to do about it.
    assert "BEDROCK_READ_TIMEOUT_SECONDS" in error


def test_access_denied_is_failed_and_distinguishable(
    storage: StorageManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A denied model must not read like a timeout or a skip."""
    service = CodeParserService()
    monkeypatch.setattr(
        service,
        "_invoke_bedrock",
        lambda *a, **kw: (_ for _ in ()).throw(_access_denied()),
    )

    result = _deterministic_result()
    service._run_ai_enrichment(ANALYSIS_ID, result)

    stored = storage.load(ANALYSIS_ID)
    assert stored["ai_enrichment_status"] == "failed"
    error = stored["ai_enrichment_error"]
    assert "access denied" in error.lower()
    assert "model access" in error.lower()
    assert "timed out" not in error.lower()
    _assert_deterministic_results_survived(stored)


def test_successful_summary_survives_a_failing_documentation_call(
    storage: StorageManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Output already paid for must be kept when a later call fails."""
    service = CodeParserService()
    calls: list[str] = []

    def fake_invoke(prompt: str, context: dict) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return "# Executive Summary\nReal summary text."
        raise _read_timeout()

    monkeypatch.setattr(service, "_invoke_bedrock", fake_invoke)

    result = _deterministic_result()
    service._run_ai_enrichment(ANALYSIS_ID, result)

    stored = storage.load(ANALYSIS_ID)
    assert stored["ai_enrichment_status"] == "failed"
    assert stored["ai_summary"] == "# Executive Summary\nReal summary text."
    assert "documentation" in stored["ai_enrichment_error"].lower()
    _assert_deterministic_results_survived(stored)


# ─── skipped: not attempted ───────────────────────────────────────────────────


def test_deliberate_skip_is_skipped(
    storage: StorageManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SKIP_AI_ENRICHMENT=true means enrichment was deliberately not attempted."""
    from config import settings

    monkeypatch.setattr(settings, "SKIP_AI_ENRICHMENT", True)
    service = CodeParserService()

    def fail_if_called(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("Bedrock must not be invoked when enrichment is skipped")

    monkeypatch.setattr(service, "_invoke_bedrock", fail_if_called)

    result = _deterministic_result()
    service._run_ai_enrichment(ANALYSIS_ID, result)

    stored = storage.load(ANALYSIS_ID)
    assert stored["ai_enrichment_status"] == "skipped"
    assert "SKIP_AI_ENRICHMENT" in stored["ai_enrichment_error"]
    assert "ai_summary" not in stored
    _assert_deterministic_results_survived(stored)


def test_missing_credentials_is_skipped_as_bedrock_unavailable(
    storage: StorageManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No resolvable credentials means Bedrock is unavailable, not that it failed."""
    service = CodeParserService()
    monkeypatch.setattr(
        service,
        "_invoke_bedrock",
        lambda *a, **kw: (_ for _ in ()).throw(NoCredentialsError()),
    )

    result = _deterministic_result()
    service._run_ai_enrichment(ANALYSIS_ID, result)

    stored = storage.load(ANALYSIS_ID)
    assert stored["ai_enrichment_status"] == "skipped"
    assert "credential" in stored["ai_enrichment_error"].lower()
    _assert_deterministic_results_survived(stored)


# ─── completed: unchanged behaviour ───────────────────────────────────────────


def test_successful_enrichment_still_completes(
    storage: StorageManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The happy path keeps reporting `completed` with no error field."""
    service = CodeParserService()
    monkeypatch.setattr(service, "_invoke_bedrock", lambda prompt, ctx: "AI text")

    result = _deterministic_result()
    service._run_ai_enrichment(ANALYSIS_ID, result)

    stored = storage.load(ANALYSIS_ID)
    assert stored["ai_enrichment_status"] == "completed"
    assert "ai_enrichment_error" not in stored
    assert stored["ai_summary"] == "AI text"
    assert stored["ai_documentation"] == "AI text"
    _assert_deterministic_results_survived(stored)
