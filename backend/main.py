"""Code Transformation Engine — FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config import settings
from middleware.auth_routes import router as auth_router
from middleware.security import AuditLogMiddleware, AuthMiddleware, limiter
from middleware.transformation_management import router as transformation_router
from models import HealthResponse
from routes.ai_streaming import router as ai_streaming_router
from routes.analysis import router as analysis_router
from routes.aux import router as aux_router
from routes.security_fix import router as security_fix_router
from utils.progress_tracker import ProgressTracker
from utils.storage_manager import StorageManager

logger = logging.getLogger(__name__)


# --- Lifespan ---


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize application state on startup."""
    from state import app_state

    app_state.storage_manager = StorageManager()
    app_state.progress_tracker = ProgressTracker()
    yield


# --- Application Factory ---

app = FastAPI(
    title="Code Transformation Engine",
    description="AI-powered code transformation and modernization platform",
    version="0.1.0",
    lifespan=lifespan,
)

# --- Rate Limiter (slowapi) ---

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- Middleware Stack (order: rate limiter → audit → auth → CORS) ---
# NOTE: Starlette processes middleware in REVERSE registration order.
# Register in reverse: CORS first (processed last), then Auth, then Audit.
# Rate limiter is handled via slowapi's decorator/state, not as BaseHTTPMiddleware.

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)
app.add_middleware(AuditLogMiddleware)

# --- Exception Handlers ---


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    """Handle ValueError as 400 Bad Request."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(FileNotFoundError)
async def file_not_found_handler(
    _request: Request, exc: FileNotFoundError
) -> JSONResponse:
    """Handle FileNotFoundError as 404 Not Found."""
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def generic_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Handle uncaught exceptions as 500 Internal Server Error."""
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# --- Health Endpoint ---


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="healthy")


# --- Register Route Modules ---

app.include_router(auth_router)
app.include_router(analysis_router)
app.include_router(ai_streaming_router)
app.include_router(aux_router)
app.include_router(security_fix_router)
app.include_router(transformation_router)
