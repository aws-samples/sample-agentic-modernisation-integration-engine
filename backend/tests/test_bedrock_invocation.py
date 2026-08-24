"""Bedrock client timeouts, retry policy, and error triage.

The failure that motivated this module: the documentation-generation call needs
~75s of model time, botocore's default read timeout is 60s, and botocore's own
retries re-ran the same under-timed call four more times before giving up. So
the timeout must be explicit, and retries must only be spent where they can
change the outcome.
"""

from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from config import settings
from utils.bedrock import (
    BedrockCallError,
    BedrockUnavailableError,
    bedrock_failure_for,
    bedrock_runtime_client,
    classify_bedrock_error,
    invoke_with_retry,
)

ENDPOINT = (
    "https://bedrock-runtime.us-east-1.amazonaws.com/model/"
    "us.anthropic.claude-sonnet-4-5-20250929-v1%3A0/invoke"
)


def _client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "InvokeModel")


# ─── Explicit timeouts ────────────────────────────────────────────────────────


def test_client_sets_explicit_read_timeout_above_botocore_default() -> None:
    """The 60s botocore default is what broke enrichment; it must be overridden."""
    client = bedrock_runtime_client()

    assert client.meta.config.read_timeout == settings.BEDROCK_READ_TIMEOUT_SECONDS
    assert client.meta.config.read_timeout > 60
    assert (
        client.meta.config.connect_timeout == settings.BEDROCK_CONNECT_TIMEOUT_SECONDS
    )
    # Retries are handled by invoke_with_retry, not silently by botocore.
    # botocore normalises `max_attempts` to `total_max_attempts` on the client.
    retries = client.meta.config.retries
    assert retries.get("total_max_attempts", retries.get("max_attempts")) == 1


# ─── Retry policy ─────────────────────────────────────────────────────────────


def test_read_timeout_is_retried_with_exponential_backoff() -> None:
    """Exponential backoff over an explicitly widened policy: 5 attempts, 1s base.

    Deliberately wider than the shipped default (2 attempts) — the growth curve
    only shows up across several retries, and callers may pass a bigger budget.
    """
    delays: list[float] = []
    attempts = 0

    def always_timeout() -> str:
        nonlocal attempts
        attempts += 1
        raise ReadTimeoutError(endpoint_url=ENDPOINT)

    with pytest.raises(BedrockCallError) as caught:
        invoke_with_retry(
            always_timeout,
            description="text generation",
            max_attempts=5,
            base_delay=1.0,
            sleep=delays.append,
        )

    assert attempts == 5
    assert delays == [1.0, 2.0, 4.0, 8.0]
    assert caught.value.failure.kind == "read_timeout"


def test_default_budget_is_two_attempts_bounding_worst_case_wall_clock() -> None:
    """The shipped default is one retry, and that bounds time-to-`failed`.

    The budget exists to cap how long a hung Bedrock can look alive before the
    analysis reports `failed`. That bound is
    `BEDROCK_MAX_ATTEMPTS × BEDROCK_READ_TIMEOUT_SECONDS` plus backoff, so the
    two settings are pinned together here rather than separately.
    """
    assert settings.BEDROCK_MAX_ATTEMPTS == 2
    assert settings.BEDROCK_READ_TIMEOUT_SECONDS == 300

    delays: list[float] = []
    attempts = 0

    def always_timeout() -> str:
        nonlocal attempts
        attempts += 1
        raise ReadTimeoutError(endpoint_url=ENDPOINT)

    with pytest.raises(BedrockCallError) as caught:
        # No max_attempts / base_delay override: this is the shipped default.
        invoke_with_retry(
            always_timeout, description="documentation generation", sleep=delays.append
        )

    # Two attempts means exactly one retry, so backoff degenerates to one delay.
    assert attempts == settings.BEDROCK_MAX_ATTEMPTS
    assert delays == [settings.BEDROCK_RETRY_BASE_DELAY_SECONDS]

    worst_case_seconds = (
        settings.BEDROCK_MAX_ATTEMPTS * settings.BEDROCK_READ_TIMEOUT_SECONDS
    ) + sum(delays)
    # Two full read timeouts plus the single backoff delay — ~601s, ten minutes.
    assert worst_case_seconds == 601.0
    assert worst_case_seconds < 11 * 60

    # And the message a caller sees reflects the same budget it just spent.
    assert "attempted up to 2 times" in caught.value.failure.message


def test_retry_returns_the_first_success() -> None:
    """A transient timeout that clears must not cost the caller the result.

    Runs on the default budget, so the one retry it keeps is exactly the one
    that has to work — this is the case the retry is retained for.
    """
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise ReadTimeoutError(endpoint_url=ENDPOINT)
        return "generated text"

    assert (
        invoke_with_retry(
            flaky, description="text generation", base_delay=0.0, sleep=lambda _: None
        )
        == "generated text"
    )
    assert calls["n"] == 2


def test_access_denied_fails_on_the_first_attempt() -> None:
    """Retrying a denied model only delays an honest error."""
    attempts = 0

    def denied() -> str:
        nonlocal attempts
        attempts += 1
        raise _client_error("AccessDeniedException", "no access to model")

    with pytest.raises(BedrockCallError) as caught:
        invoke_with_retry(denied, description="text generation", sleep=lambda _: None)

    assert attempts == 1
    assert caught.value.failure.kind == "access_denied"
    assert caught.value.failure.retryable is False


def test_missing_credentials_raises_unavailable_not_call_error() -> None:
    """Nothing was attempted, so this is "unavailable", not a failed call."""

    def no_creds() -> str:
        raise NoCredentialsError()

    with pytest.raises(BedrockUnavailableError):
        invoke_with_retry(no_creds, description="text generation", sleep=lambda _: None)


def test_successful_invocation_parses_model_text() -> None:
    """The retry wrapper must not change the happy-path result."""
    client = MagicMock()
    client.invoke_model.return_value = {
        "body": MagicMock(
            read=lambda: json.dumps({"content": [{"text": "hi"}]}).encode()
        )
    }

    def call() -> Any:
        response = client.invoke_model(modelId="m", body="{}")
        return json.loads(response["body"].read())["content"][0]["text"]

    assert invoke_with_retry(call, description="text generation") == "hi"


@given(
    max_attempts=st.integers(min_value=1, max_value=6),
    base_delay=st.floats(min_value=0.1, max_value=4.0, allow_nan=False),
)
@hyp_settings(max_examples=40, deadline=None)
def test_backoff_is_exponential_for_any_policy(
    max_attempts: int, base_delay: float
) -> None:
    """Property: n attempts produce n-1 strictly increasing base*2^(i-1) delays."""
    delays: list[float] = []

    def always_timeout() -> str:
        raise ReadTimeoutError(endpoint_url=ENDPOINT)

    with pytest.raises(BedrockCallError):
        invoke_with_retry(
            always_timeout,
            description="text generation",
            max_attempts=max_attempts,
            base_delay=base_delay,
            sleep=delays.append,
        )

    assert len(delays) == max_attempts - 1
    assert delays == [base_delay * (2**i) for i in range(max_attempts - 1)]
    assert all(b > a for a, b in zip(delays, delays[1:]))


# ─── Triage ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "kind", "must_mention"),
    [
        (ReadTimeoutError(endpoint_url=ENDPOINT), "read_timeout", "timed out"),
        (_client_error("AccessDeniedException"), "access_denied", "access denied"),
        (_client_error("ExpiredTokenException"), "expired_credentials", "expired"),
        (_client_error("ValidationException"), "validation_error", "invalid"),
        (_client_error("ThrottlingException"), "throttled", "throttled"),
        (_client_error("ResourceNotFoundException"), "model_not_found", "not found"),
        (NoCredentialsError(), "no_credentials", "credentials"),
        (
            EndpointConnectionError(endpoint_url=ENDPOINT),
            "endpoint_unreachable",
            "could not connect",
        ),
    ],
)
def test_each_cause_is_named_distinctly(
    exc: Exception, kind: str, must_mention: str
) -> None:
    """A timeout, a denied model and an absent credential are three actions."""
    failure = classify_bedrock_error(exc)

    assert failure.kind == kind
    assert must_mention in failure.message.lower()


def test_failures_carry_an_operator_action() -> None:
    """Every classified message must say what to do, not just what broke."""
    action_words = re.compile(
        r"set |raise |grant |refresh |check |request |lower |attach ", re.IGNORECASE
    )
    for exc in (
        ReadTimeoutError(endpoint_url=ENDPOINT),
        _client_error("AccessDeniedException"),
        _client_error("ValidationException"),
        NoCredentialsError(),
        EndpointConnectionError(endpoint_url=ENDPOINT),
    ):
        message = classify_bedrock_error(exc).message
        assert action_words.search(message), message


def test_already_classified_errors_are_not_reclassified() -> None:
    """Unwrapping must preserve the specific cause through the call stack."""
    original = classify_bedrock_error(_client_error("AccessDeniedException"))

    assert bedrock_failure_for(BedrockCallError(original)) == original
