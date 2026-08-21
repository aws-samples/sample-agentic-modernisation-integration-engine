"""Security middleware — AuthMiddleware, AuditLogMiddleware, rate limiter."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import Request, Response
from jose import JWTError, jwt
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate Limiter (slowapi)
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------

PUBLIC_PATHS: list[str] = [
    "/health",
    "/docs",
    "/openapi.json",
    "/api/auth/login",
    "/api/auth/config",
]

# ---------------------------------------------------------------------------
# JWKS cache for Cognito RS256
# ---------------------------------------------------------------------------

_jwks_cache: dict[str, Any] = {}


def _get_cognito_jwks() -> dict[str, Any]:
    """Fetch and cache Cognito JWKS keys."""
    if _jwks_cache.get("keys"):
        return _jwks_cache

    jwks_url = (
        f"https://cognito-idp.{settings.AWS_REGION}.amazonaws.com/"
        f"{settings.COGNITO_USER_POOL_ID}/.well-known/jwks.json"
    )
    try:
        resp = httpx.get(jwks_url, timeout=5.0)
        resp.raise_for_status()
        _jwks_cache.update(resp.json())
    except Exception:
        logger.warning("Failed to fetch Cognito JWKS from %s", jwks_url)
    return _jwks_cache


# ---------------------------------------------------------------------------
# Auth mode detection
# ---------------------------------------------------------------------------


def _detect_auth_mode() -> str:
    """Detect authentication mode from environment variables.

    Priority: disabled > local > cognito.
    """
    if settings.AUTH_DISABLED:
        return "disabled"
    if settings.LOCAL_AUTH_SECRET:
        return "local"
    if settings.COGNITO_USER_POOL_ID and settings.COGNITO_CLIENT_ID:
        return "cognito"
    # Default to disabled if nothing configured
    return "disabled"


# ---------------------------------------------------------------------------
# JWT validation helpers
# ---------------------------------------------------------------------------


def _validate_local_token(token: str) -> dict[str, Any]:
    """Validate a JWT signed with LOCAL_AUTH_SECRET (HS256)."""
    payload = jwt.decode(
        token,
        settings.LOCAL_AUTH_SECRET,
        algorithms=["HS256"],
    )
    return payload


def _validate_cognito_token(token: str) -> dict[str, Any]:
    """Validate a JWT signed by Cognito (RS256 via JWKS)."""
    jwks = _get_cognito_jwks()
    if not jwks.get("keys"):
        raise JWTError("JWKS keys not available")

    # Decode header to find the key id
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")

    # Find matching key
    rsa_key: dict[str, Any] = {}
    for key in jwks["keys"]:
        if key.get("kid") == kid:
            rsa_key = key
            break

    if not rsa_key:
        raise JWTError("Unable to find matching JWKS key")

    issuer = (
        f"https://cognito-idp.{settings.AWS_REGION}.amazonaws.com/"
        f"{settings.COGNITO_USER_POOL_ID}"
    )

    payload = jwt.decode(
        token,
        rsa_key,
        algorithms=["RS256"],
        audience=settings.COGNITO_CLIENT_ID,
        issuer=issuer,
    )
    return payload


# ---------------------------------------------------------------------------
# AuthMiddleware
# ---------------------------------------------------------------------------


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT authentication middleware with 3-mode detection.

    Modes:
    - disabled: all requests pass through (AUTH_DISABLED=true)
    - local: HS256 JWT validation using LOCAL_AUTH_SECRET
    - cognito: RS256 JWT validation via Cognito JWKS
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process authentication for incoming requests."""
        mode = _detect_auth_mode()

        # Disabled mode — pass all requests
        if mode == "disabled":
            request.state.user = {"sub": "anonymous", "role": "admin"}
            return await call_next(request)

        # Check if path is public
        path = request.url.path
        if (
            path in PUBLIC_PATHS
            or path.startswith("/docs")
            or path.startswith("/redoc")
        ):
            return await call_next(request)

        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid authorization header"},
            )

        token = auth_header[7:]  # Strip "Bearer "

        try:
            if mode == "local":
                claims = _validate_local_token(token)
            elif mode == "cognito":
                claims = _validate_cognito_token(token)
            else:
                return JSONResponse(
                    status_code=500,
                    content={"detail": "Unknown auth mode"},
                )
        except JWTError as e:
            logger.warning("JWT validation failed: %s", e)
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
            )

        # Set user claims on request state
        request.state.user = claims
        return await call_next(request)


# ---------------------------------------------------------------------------
# AuditLogMiddleware
# ---------------------------------------------------------------------------


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Audit log middleware — logs each request with method, path, status, duration."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Log request details after processing."""
        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000

        logger.info(
            "audit: method=%s path=%s status=%d duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
