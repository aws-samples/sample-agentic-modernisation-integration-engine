"""Input validation, prompt injection detection, and sensitive data redaction."""

from __future__ import annotations

import re

# Maximum prompt length (characters)
MAX_PROMPT_LENGTH: int = 100_000

# 12 regex patterns for prompt injection detection
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an|the)\b", re.IGNORECASE),
    re.compile(r"pretend\s+(you\s+are|to\s+be)", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?(your|previous)", re.IGNORECASE),
    re.compile(r"override\s+(your\s+)?(instructions?|rules?)", re.IGNORECASE),
    re.compile(r"reveal\s+(your\s+)?(system|hidden|secret)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|above|prior)", re.IGNORECASE),
    re.compile(
        r"do\s+not\s+follow\s+(your|the)\s+(rules?|instructions?)", re.IGNORECASE
    ),
    re.compile(r"act\s+as\s+(a|an|if)\b", re.IGNORECASE),
    re.compile(
        r"bypass\s+(safety|content|security)\s*(filter|guard|restriction)?",
        re.IGNORECASE,
    ),
]

# Sensitive data redaction patterns
_REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # AWS access keys (AKIA...)
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    # GitHub personal access tokens (ghp_ or github_pat_)
    (re.compile(r"ghp_[A-Za-z0-9]{36,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{22,}"), "[REDACTED_GITHUB_TOKEN]"),
    # Generic passwords in key=value or key:value patterns
    (re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+"), "[REDACTED_PASSWORD]"),
    # API keys in common formats
    (re.compile(r"(?i)(api[_-]?key|apikey)\s*[=:]\s*\S+"), "[REDACTED_API_KEY]"),
    # Private keys (PEM format)
    (
        re.compile(
            r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----[\s\S]*?-----END\s+(RSA\s+)?PRIVATE\s+KEY-----"
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    # AWS secret access keys (40 char base64)
    (
        re.compile(
            r"(?i)(aws_secret_access_key|secret_key)\s*[=:]\s*[A-Za-z0-9/+=]{40}"
        ),
        "[REDACTED_AWS_SECRET]",
    ),
]


def check_injection(text: str) -> bool:
    """Check if text contains prompt injection patterns.

    Returns:
        True if injection detected, False otherwise.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return True
    return False


def redact_sensitive(text: str) -> str:
    """Redact sensitive data (keys, tokens, passwords) from text.

    Returns:
        Text with sensitive values replaced by redaction markers.
    """
    result = text
    for pattern, replacement in _REDACTION_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def validate_prompt_length(text: str) -> None:
    """Validate that prompt text does not exceed MAX_PROMPT_LENGTH.

    Raises:
        ValueError: If text length exceeds MAX_PROMPT_LENGTH.
    """
    if len(text) > MAX_PROMPT_LENGTH:
        raise ValueError(
            f"Prompt length ({len(text):,} chars) exceeds maximum "
            f"({MAX_PROMPT_LENGTH:,} chars)"
        )
