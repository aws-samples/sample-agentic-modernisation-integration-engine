"""Every agent that calls Bedrock must use the shared client factory.

Two agents built their bedrock-runtime client with a bare
`boto3.client("bedrock-runtime", region_name=...)`, inheriting botocore's 60s
default read timeout *and* botocore's default internal retries — the exact pair
that made AI enrichment fail five times in a row, where a 75.1s
`documentation-generation` call could never complete and botocore silently
re-ran it until 5.5 minutes had passed.

So the assertions here are about configuration, not about the model:

  1. the client each agent builds carries the explicit read timeout, above the
     60s default, and has botocore's own retries disabled;
  2. a retryable failure is retried on the shared policy;
  3. a non-retryable failure (a denied model) costs exactly one attempt;
  4. a failure that survives its retries reaches the SSE consumer as a terminal
     `error` event whose `message` names the cause and the operator action.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from agents.kiro_specs_agent import KiroSpecsAgent
from agents.llm_judge import LLMJudge
from config import settings
from utils.storage_manager import StorageManager

ENDPOINT = (
    "https://bedrock-runtime.us-east-1.amazonaws.com/model/"
    "us.anthropic.claude-sonnet-4-5-20250929-v1%3A0/invoke"
)


def _client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "InvokeModel")


def _bedrock_response(text: str) -> dict[str, Any]:
    return {
        "body": MagicMock(
            read=lambda: json.dumps({"content": [{"text": text}]}).encode()
        )
    }


def _drain(stream: AsyncGenerator[dict[str, Any], None]) -> list[dict[str, Any]]:
    async def run() -> list[dict[str, Any]]:
        return [event async for event in stream]

    return asyncio.run(run())


@pytest.fixture
def judge() -> LLMJudge:
    return LLMJudge()


@pytest.fixture
def specs_agent(tmp_path) -> KiroSpecsAgent:
    storage = StorageManager(base_path=str(tmp_path / "analyses"))
    storage.save(
        "github_20250101_120000",
        {
            "analysis_id": "github_20250101_120000",
            "source_type": "github",
            "source_url": "https://github.com/example/repo",
            "file_stats": [
                {"extension": ".py", "count": 1, "total_lines": 10, "total_size": 100}
            ],
            "folder_structure": {"name": "root", "type": "directory", "children": []},
            "dependencies": [],
            "parsed_files": [
                {
                    "filename": "main.py",
                    "classes": [
                        {
                            "name": "App",
                            "line_number": 1,
                            "methods": ["run"],
                            "parent_classes": [],
                        }
                    ],
                    "methods": [],
                    "imports": [],
                    "complexity": 1,
                    "language": "python",
                    "line_count": 10,
                }
            ],
        },
    )
    return KiroSpecsAgent(storage)


# ─── Explicit timeouts, per agent ─────────────────────────────────────────────


def _assert_configured(client: Any) -> None:
    config = client.meta.config
    assert config.read_timeout == settings.BEDROCK_READ_TIMEOUT_SECONDS
    assert config.read_timeout > 60, (
        "the agent inherited botocore's 60s default read timeout; a long-form "
        "generation cannot complete inside it"
    )
    assert config.connect_timeout == settings.BEDROCK_CONNECT_TIMEOUT_SECONDS
    retries = config.retries or {}
    assert retries.get("total_max_attempts", retries.get("max_attempts")) == 1, (
        "botocore's internal retries are still on; they compound with "
        "invoke_with_retry and silently re-run an under-timed call"
    )


def test_judge_client_sets_explicit_read_timeout(judge: LLMJudge) -> None:
    """LLMJudge survives on small outputs today, not on correct configuration."""
    _assert_configured(judge._bedrock_client)


def test_specs_agent_client_sets_explicit_read_timeout(
    specs_agent: KiroSpecsAgent,
) -> None:
    """KiroSpecsAgent asks for 8192 tokens while streaming — the most exposed."""
    _assert_configured(specs_agent._bedrock_client)


# ─── Retry policy, per agent ──────────────────────────────────────────────────


@patch("utils.bedrock.settings.BEDROCK_RETRY_BASE_DELAY_SECONDS", 0.0)
def test_judge_retries_a_retryable_failure_on_the_shared_policy(
    judge: LLMJudge,
) -> None:
    """A read timeout gets the shared budget's one retry, then reports."""
    client = MagicMock()
    client.invoke_model.side_effect = ReadTimeoutError(endpoint_url=ENDPOINT)
    judge._client = client

    result = judge.score_dimension("some documentation", "accuracy")

    assert client.invoke_model.call_count == settings.BEDROCK_MAX_ATTEMPTS
    assert "timed out" in result["error"].lower()


@patch("utils.bedrock.settings.BEDROCK_RETRY_BASE_DELAY_SECONDS", 0.0)
def test_judge_recovers_when_the_retry_succeeds(judge: LLMJudge) -> None:
    """The retry is kept for the transient case; it must return the result."""
    client = MagicMock()
    client.invoke_model.side_effect = [
        ReadTimeoutError(endpoint_url=ENDPOINT),
        _bedrock_response('{"score": 9, "justification": "clear"}'),
    ]
    judge._client = client

    result = judge.score_dimension("some documentation", "accuracy")

    assert client.invoke_model.call_count == 2
    assert result["score"] == 9
    assert "error" not in result


def test_judge_denied_model_fails_on_the_first_attempt(judge: LLMJudge) -> None:
    """Retrying a denied model only delays an honest error."""
    client = MagicMock()
    client.invoke_model.side_effect = _client_error(
        "AccessDeniedException", "no access to model"
    )
    judge._client = client

    result = judge.score_dimension("some documentation", "accuracy")

    assert client.invoke_model.call_count == 1
    assert "access denied" in result["error"].lower()
    assert "grant bedrock:invokemodel" in result["error"].lower()


@patch("utils.bedrock.settings.BEDROCK_RETRY_BASE_DELAY_SECONDS", 0.0)
def test_specs_agent_retries_a_retryable_failure_on_the_shared_policy(
    specs_agent: KiroSpecsAgent,
) -> None:
    """Spec generation gets the same budget, not botocore's silent one."""
    client = MagicMock()
    client.invoke_model.side_effect = ReadTimeoutError(endpoint_url=ENDPOINT)
    specs_agent._client = client

    result = specs_agent.generate_component_spec("github_20250101_120000", "main.py")

    assert client.invoke_model.call_count == settings.BEDROCK_MAX_ATTEMPTS
    assert "timed out" in result["error"].lower()
    assert result["file_path"] == "main.py"


def test_specs_agent_denied_model_fails_on_the_first_attempt(
    specs_agent: KiroSpecsAgent,
) -> None:
    """A non-retryable failure costs exactly one attempt."""
    client = MagicMock()
    client.invoke_model.side_effect = _client_error("AccessDeniedException")
    specs_agent._client = client

    result = specs_agent.generate_component_spec("github_20250101_120000", "main.py")

    assert client.invoke_model.call_count == 1
    assert "access denied" in result["error"].lower()


# ─── Terminal SSE error events ────────────────────────────────────────────────


@patch("utils.bedrock.settings.BEDROCK_RETRY_BASE_DELAY_SECONDS", 0.0)
def test_judge_stream_ends_with_an_error_event_naming_the_cause(
    judge: LLMJudge,
) -> None:
    """A failure after retries must not be swallowed into a bogus score.

    Scoring a dimension 0 because the call never returned, then averaging it
    into an `overall_score` and reporting `completed`, tells the caller the
    evaluation succeeded and the documentation is bad.
    """
    client = MagicMock()
    client.invoke_model.side_effect = ReadTimeoutError(endpoint_url=ENDPOINT)
    judge._client = client

    events = _drain(judge.evaluate_streaming("some documentation text"))

    assert events[-1]["type"] == "error"
    message = events[-1]["message"]
    assert "timed out" in message.lower()
    # The cause has to be actionable, per the platform SSE `error` contract.
    assert str(settings.BEDROCK_READ_TIMEOUT_SECONDS) in message
    assert not any(e["type"] == "complete" for e in events)


@patch("utils.bedrock.settings.BEDROCK_RETRY_BASE_DELAY_SECONDS", 0.0)
def test_judge_stream_error_event_names_a_denied_model(judge: LLMJudge) -> None:
    """The terminal event distinguishes a denied model from a timeout."""
    client = MagicMock()
    client.invoke_model.side_effect = _client_error("AccessDeniedException")
    judge._client = client

    events = _drain(judge.evaluate_streaming("some documentation text"))

    assert events[-1]["type"] == "error"
    assert "access denied" in events[-1]["message"].lower()


@patch("utils.bedrock.settings.BEDROCK_RETRY_BASE_DELAY_SECONDS", 0.0)
def test_specs_stream_ends_with_an_error_event_naming_the_cause(
    specs_agent: KiroSpecsAgent,
) -> None:
    """`generate_specs_streaming` must report the classified cause, not a stub."""
    client = MagicMock()
    client.invoke_model.side_effect = ReadTimeoutError(endpoint_url=ENDPOINT)
    specs_agent._client = client

    events = _drain(specs_agent.generate_specs_streaming("github_20250101_120000"))

    assert events[-1]["type"] == "error"
    message = events[-1]["message"]
    assert "timed out" in message.lower()
    assert str(settings.BEDROCK_READ_TIMEOUT_SECONDS) in message
    assert not any(e["type"] == "complete" for e in events)


@patch("utils.bedrock.settings.BEDROCK_RETRY_BASE_DELAY_SECONDS", 0.0)
def test_specs_stream_completes_on_the_happy_path(
    specs_agent: KiroSpecsAgent,
) -> None:
    """The migration must not change the successful stream's event sequence."""
    client = MagicMock()
    client.invoke_model.return_value = _bedrock_response(
        "# Requirements\n\nThe system shall parse files.\n\n"
        "# Design\n\nA layered architecture with parsers and services.\n\n"
        "# Tasks\n\n- [ ] 1. Implement the parser\n"
    )
    specs_agent._client = client

    events = _drain(specs_agent.generate_specs_streaming("github_20250101_120000"))

    assert [e["type"] for e in events if e["type"] in ("content", "complete")] == [
        "content",
        "complete",
    ]
    assert events[-1]["status"] == "completed"
    assert not any(e["type"] == "error" for e in events)
