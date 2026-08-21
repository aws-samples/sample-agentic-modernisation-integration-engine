"""Authentication routes — login, config, user info."""

from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from jose import jwt

from config import settings
from models import AuthConfig, LoginRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Ephemeral signing key for disabled-mode dummy tokens. Auth is bypassed entirely
# in disabled mode, so this token is never validated; a fresh random key per process
# avoids shipping a hard-coded secret while keeping disabled-mode login working.
_DISABLED_MODE_SIGNING_KEY = secrets.token_urlsafe(32)

# ---------------------------------------------------------------------------
# Auth mode detection (shared with security.py)
# ---------------------------------------------------------------------------


def _detect_auth_mode() -> str:
    """Detect authentication mode from environment variables."""
    if settings.AUTH_DISABLED:
        return "disabled"
    if settings.LOCAL_AUTH_SECRET:
        return "local"
    if settings.COGNITO_USER_POOL_ID and settings.COGNITO_CLIENT_ID:
        return "cognito"
    return "disabled"


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse | JSONResponse:
    """Authenticate user and return JWT token (local mode only).

    In local mode, accepts any username/password and issues a HS256 JWT.
    In cognito mode, returns 400 (use Cognito hosted UI).
    In disabled mode, returns a dummy token.
    """
    mode = _detect_auth_mode()

    if mode == "disabled":
        # Return a dummy token when auth is disabled
        token = _create_local_token(body.username, "admin")
        return TokenResponse(access_token=token)

    if mode == "cognito":
        return JSONResponse(
            status_code=400,
            content={"detail": "Use Cognito hosted UI for authentication"},
        )

    # Local mode — issue JWT
    # In a real app, validate credentials against a user store.
    # For local dev, accept any username/password.
    token = _create_local_token(body.username, "admin")
    return TokenResponse(access_token=token)


# ---------------------------------------------------------------------------
# GET /api/auth/config
# ---------------------------------------------------------------------------


@router.get("/config", response_model=AuthConfig)
async def get_auth_config() -> AuthConfig:
    """Return current auth mode configuration."""
    mode = _detect_auth_mode()
    return AuthConfig(
        mode=mode,
        cognito_user_pool_id=settings.COGNITO_USER_POOL_ID if mode == "cognito" else "",
        cognito_client_id=settings.COGNITO_CLIENT_ID if mode == "cognito" else "",
    )


# ---------------------------------------------------------------------------
# GET /api/auth/user
# ---------------------------------------------------------------------------


@router.get("/user")
async def get_current_user(request: Request) -> dict:
    """Return current user info from JWT claims."""
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"detail": "Not authenticated"},
        )
    return {
        "sub": user.get("sub", ""),
        "role": user.get("role", "user"),
        "username": user.get("sub", ""),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_local_token(username: str, role: str) -> str:
    """Create a local HS256 JWT token.

    Signs with LOCAL_AUTH_SECRET when configured (local mode, where the token is
    later verified in security.py). In disabled mode no secret is set and the token
    is never validated, so it is signed with an ephemeral per-process key rather
    than a hard-coded fallback.
    """
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + 86400,  # 24 hours
    }
    signing_key = settings.LOCAL_AUTH_SECRET or _DISABLED_MODE_SIGNING_KEY
    return jwt.encode(payload, signing_key, algorithm="HS256")
