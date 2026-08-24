"""Bedrock invocation support: explicit timeouts, retries, and error triage.

Three concerns live here because they are inseparable in practice:

1. **Timeouts.** botocore's default read timeout is 60s. A Claude Sonnet call
   asking for 4096 output tokens against a real analysis context takes ~75s, so
   the default turns a working call into a hard failure. The read timeout is set
   explicitly (`BEDROCK_READ_TIMEOUT_SECONDS`) rather than inherited.

2. **Retries.** botocore's own retries cannot help a call that needs more
   wall-clock time than the per-attempt timeout allows — each attempt fails the
   same way. So botocore retries are disabled and retries are done here, per the
   design's "Retry Strategies" (exponential backoff, base delay 1s), and only
   for errors that a retry can actually survive. A denied model or an invalid
   request fails on the first attempt instead of burning minutes. The budget is
   deliberately small — `BEDROCK_MAX_ATTEMPTS` defaults to 2, one retry —
   because the worst case before a `failed` verdict is that many attempts times
   the full read timeout, plus backoff.

3. **Triage.** A read timeout, a denied model, an expired credential and a
   throttle each demand a different operator action, so the failure carries a
   classification and a message that names the action.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    NoRegionError,
    PartialCredentialsError,
    ReadTimeoutError,
)

from config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Error codes that a retry cannot fix — they describe the request or the
# account, not a transient condition.
_PERMANENT_CLIENT_CODES = frozenset(
    {
        "AccessDeniedException",
        "ValidationException",
        "ResourceNotFoundException",
        "UnrecognizedClientException",
        "InvalidSignatureException",
        "ExpiredTokenException",
        "ExpiredToken",
        "InvalidClientTokenId",
        "SerializationException",
    }
)

# Error codes worth retrying: capacity and rate conditions that commonly clear.
_TRANSIENT_CLIENT_CODES = frozenset(
    {
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "ServiceQuotaExceededException",
        "InternalServerException",
        "ModelTimeoutException",
        "ModelNotReadyException",
    }
)


@dataclass(frozen=True)
class BedrockFailure:
    """A classified Bedrock failure with an operator-actionable message."""

    kind: str
    """Short machine-readable class, e.g. `read_timeout`, `access_denied`."""
    message: str
    """Human-readable cause plus the action it implies."""
    retryable: bool
    unavailable: bool
    """True when the call could not be attempted (credentials/region/endpoint)."""


class BedrockUnavailableError(RuntimeError):
    """Bedrock cannot be reached or called at all — nothing was attempted.

    Distinct from a call that was attempted and failed: this maps to the
    `skipped` enrichment status ("Bedrock unavailable"), not to `failed`.
    """


class BedrockCallError(RuntimeError):
    """A Bedrock call was attempted and failed, carrying its classification."""

    def __init__(self, failure: BedrockFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


def bedrock_failure_for(exc: BaseException) -> BedrockFailure:
    """Return the classification of a Bedrock error, already-classified or not."""
    if isinstance(exc, BedrockCallError):
        return exc.failure
    return classify_bedrock_error(exc)


def classify_bedrock_error(exc: BaseException) -> BedrockFailure:
    """Classify a Bedrock exception into a cause, an action, and retryability.

    Args:
        exc: The exception raised by an `invoke_model` call.

    Returns:
        A BedrockFailure naming the cause specifically enough to act on.
    """
    model = settings.BEDROCK_MODEL_ID
    region = settings.AWS_REGION

    if isinstance(exc, ReadTimeoutError):
        return BedrockFailure(
            kind="read_timeout",
            message=(
                f"Bedrock request timed out: the model did not return a full "
                f"response within the {settings.BEDROCK_READ_TIMEOUT_SECONDS}s read "
                f"timeout (model {model}, region {region}). The call was attempted "
                f"up to {settings.BEDROCK_MAX_ATTEMPTS} times. Raise "
                f"BEDROCK_READ_TIMEOUT_SECONDS or lower the requested output size."
            ),
            retryable=True,
            unavailable=False,
        )

    if isinstance(exc, (NoCredentialsError, PartialCredentialsError)):
        return BedrockFailure(
            kind="no_credentials",
            message=(
                "Bedrock unavailable: no complete AWS credentials could be resolved "
                "(checked environment, shared config, and instance role). Set "
                "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, or attach a role."
            ),
            retryable=False,
            unavailable=True,
        )

    if isinstance(exc, NoRegionError):
        return BedrockFailure(
            kind="no_region",
            message=(
                "Bedrock unavailable: no AWS region is configured. Set AWS_REGION "
                "to a region where the model is available."
            ),
            retryable=False,
            unavailable=True,
        )

    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError)):
        return BedrockFailure(
            kind="endpoint_unreachable",
            message=(
                f"Bedrock unavailable: could not connect to the bedrock-runtime "
                f"endpoint in {region}. Check network egress, proxy settings, and "
                f"that AWS_REGION is correct."
            ),
            retryable=True,
            unavailable=True,
        )

    if isinstance(exc, ConnectionClosedError):
        return BedrockFailure(
            kind="connection_closed",
            message=(
                f"Bedrock connection closed before the response completed "
                f"(model {model}, region {region})."
            ),
            retryable=True,
            unavailable=False,
        )

    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "") or ""
        detail = exc.response.get("Error", {}).get("Message", "") or str(exc)

        if code == "AccessDeniedException":
            return BedrockFailure(
                kind="access_denied",
                message=(
                    f"Bedrock access denied for model {model} in {region}: {detail} "
                    f"Grant bedrock:InvokeModel on this model and confirm model "
                    f"access is enabled for the account in this region."
                ),
                retryable=False,
                unavailable=False,
            )
        if code in ("ExpiredTokenException", "ExpiredToken", "InvalidClientTokenId"):
            return BedrockFailure(
                kind="expired_credentials",
                message=(
                    f"Bedrock rejected the AWS credentials as expired or invalid "
                    f"({code}): {detail} Refresh the credentials in the "
                    f"environment configuration."
                ),
                retryable=False,
                unavailable=False,
            )
        if code == "ValidationException":
            return BedrockFailure(
                kind="validation_error",
                message=(
                    f"Bedrock rejected the request as invalid ({code}): {detail} "
                    f"Check BEDROCK_MODEL_ID ({model}) and the request payload."
                ),
                retryable=False,
                unavailable=False,
            )
        if code == "ResourceNotFoundException":
            return BedrockFailure(
                kind="model_not_found",
                message=(
                    f"Bedrock model {model} was not found in {region}: {detail} "
                    f"Check BEDROCK_MODEL_ID and AWS_REGION."
                ),
                retryable=False,
                unavailable=False,
            )
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return BedrockFailure(
                kind="throttled",
                message=(
                    f"Bedrock throttled the request ({code}) for model {model} in "
                    f"{region}: {detail} Request a quota increase or retry later."
                ),
                retryable=True,
                unavailable=False,
            )

        retryable = (
            code in _TRANSIENT_CLIENT_CODES or code not in _PERMANENT_CLIENT_CODES
        )
        return BedrockFailure(
            kind=f"client_error:{code or 'unknown'}",
            message=(
                f"Bedrock call failed ({code or 'unknown error'}) for model {model} "
                f"in {region}: {detail}"
            ),
            retryable=retryable,
            unavailable=False,
        )

    if isinstance(exc, BotoCoreError):
        return BedrockFailure(
            kind="botocore_error",
            message=(
                f"Bedrock call failed ({type(exc).__name__}) for model {model} in "
                f"{region}: {exc}"
            ),
            retryable=True,
            unavailable=False,
        )

    return BedrockFailure(
        kind="unexpected_error",
        message=(
            f"Unexpected error while calling Bedrock model {model} in {region} "
            f"({type(exc).__name__}): {exc}"
        ),
        retryable=False,
        unavailable=False,
    )


def bedrock_runtime_client() -> Any:
    """Build a bedrock-runtime client with explicit, generous timeouts.

    botocore's internal retries are disabled: retrying an under-timed call
    cannot succeed, and `invoke_with_retry` handles the cases where a retry
    genuinely helps.

    Raises:
        BedrockUnavailableError: If the client cannot be constructed at all.
    """
    try:
        return boto3.client(
            "bedrock-runtime",
            region_name=settings.AWS_REGION,
            config=Config(
                read_timeout=settings.BEDROCK_READ_TIMEOUT_SECONDS,
                connect_timeout=settings.BEDROCK_CONNECT_TIMEOUT_SECONDS,
                # `total_max_attempts` (not `max_attempts`, which botocore reads
                # as a retry count on top of the first try) so exactly one
                # request goes out per invoke_with_retry attempt.
                retries={"total_max_attempts": 1, "mode": "standard"},
            ),
        )
    except (NoRegionError, NoCredentialsError, PartialCredentialsError) as exc:
        raise BedrockUnavailableError(classify_bedrock_error(exc).message) from exc


def invoke_with_retry(
    call: Callable[[], T],
    *,
    description: str,
    max_attempts: int | None = None,
    base_delay: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run a Bedrock call with exponential backoff on retryable failures.

    Per the design's retry strategy: exponential backoff with base delay 1s,
    over `BEDROCK_MAX_ATTEMPTS` attempts. Non-retryable failures (denied model,
    invalid request, expired credentials) raise immediately — retrying them only
    delays an honest error.

    At the default budget of 2 attempts the exponential backoff degenerates to a
    single `base_delay` pause: n attempts produce n-1 delays. That is expected,
    not a bug. The backoff stays general because the policy is configurable —
    `max_attempts` here and `BEDROCK_MAX_ATTEMPTS` in settings both widen it,
    and the growth curve has to hold when they do.

    Args:
        call: Zero-argument callable performing the Bedrock request.
        description: What is being generated, used in log and error text.
        max_attempts: Override for `BEDROCK_MAX_ATTEMPTS`.
        base_delay: Override for `BEDROCK_RETRY_BASE_DELAY_SECONDS`.
        sleep: Injectable sleep, so tests do not wait.

    Returns:
        Whatever `call` returns.

    Raises:
        BedrockUnavailableError: The call could never be attempted.
        BedrockCallError: The call was attempted and failed; the attached
            `failure` names the cause and the operator action.
    """
    attempts = max_attempts or settings.BEDROCK_MAX_ATTEMPTS
    delay_base = (
        base_delay
        if base_delay is not None
        else settings.BEDROCK_RETRY_BASE_DELAY_SECONDS
    )
    last: BedrockFailure | None = None

    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as exc:  # Classified below; nothing swallowed.
            failure = classify_bedrock_error(exc)
            last = failure

            if not failure.retryable or attempt == attempts:
                logger.warning(
                    "Bedrock %s failed on attempt %d/%d [%s]: %s",
                    description,
                    attempt,
                    attempts,
                    failure.kind,
                    failure.message,
                )
                if failure.unavailable:
                    raise BedrockUnavailableError(failure.message) from exc
                raise BedrockCallError(failure) from exc

            delay = delay_base * (2 ** (attempt - 1))
            logger.warning(
                "Bedrock %s attempt %d/%d failed [%s]; retrying in %.1fs: %s",
                description,
                attempt,
                attempts,
                failure.kind,
                delay,
                failure.message,
            )
            sleep(delay)

    # Unreachable: the loop either returns or raises.
    raise BedrockCallError(
        last
        or BedrockFailure(
            kind="unknown",
            message=f"{description} failed for an unknown reason.",
            retryable=False,
            unavailable=False,
        )
    )
