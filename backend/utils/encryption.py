"""Token encryption utilities — base64 for dev, Secrets Manager for prod."""

from __future__ import annotations

import base64
import logging

from config import settings

logger = logging.getLogger(__name__)

# Attempt to import boto3 for production mode
_secrets_client = None
if settings.AGENTCORE_MODE:
    try:
        import boto3

        _secrets_client = boto3.client(
            "secretsmanager", region_name=settings.AWS_REGION
        )
    except Exception:
        logger.warning(
            "Failed to initialize Secrets Manager client; "
            "falling back to base64 encoding."
        )


def encrypt_token(token: str) -> str:
    """Encrypt a token for storage.

    In dev mode (AGENTCORE_MODE=false): base64 encode.
    In prod mode (AGENTCORE_MODE=true): use AWS Secrets Manager if available,
    otherwise fall back to base64.

    Args:
        token: The plaintext token to encrypt.

    Returns:
        The encrypted/encoded token string.
    """
    if settings.AGENTCORE_MODE and _secrets_client:
        try:
            response = _secrets_client.put_secret_value(
                SecretId=f"code-insights/tokens/{_token_hash(token)}",
                SecretString=token,
            )
            return f"sm://{response['ARN']}"
        except Exception:
            logger.warning("Secrets Manager put failed; falling back to base64.")

    return base64.b64encode(token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted: str) -> str:
    """Decrypt a stored token.

    In dev mode: base64 decode.
    In prod mode: retrieve from Secrets Manager if ARN format, else base64.

    Args:
        encrypted: The encrypted/encoded token string.

    Returns:
        The plaintext token.
    """
    if encrypted.startswith("sm://") and _secrets_client:
        try:
            arn = encrypted[5:]  # Strip "sm://" prefix
            response = _secrets_client.get_secret_value(SecretId=arn)
            return response["SecretString"]
        except Exception:
            logger.warning("Secrets Manager get failed; attempting base64 decode.")

    return base64.b64decode(encrypted.encode("utf-8")).decode("utf-8")


def _token_hash(token: str) -> str:
    """Generate a short hash for token identification in Secrets Manager."""
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
