"""Integration tests — API core: health, CORS, auth config, analyses list, 404, prompts, storage."""

from __future__ import annotations

from fastapi.testclient import TestClient

from main import app
from state import app_state
from utils.progress_tracker import ProgressTracker
from utils.storage_manager import StorageManager


def _ensure_state() -> None:
    """Ensure app state is initialized for tests."""
    if not app_state.storage_manager:
        app_state.storage_manager = StorageManager(base_path="/tmp/test_api_core")
    if not app_state.progress_tracker:
        app_state.progress_tracker = ProgressTracker()


client = TestClient(app)


class TestHealthEndpoint:
    """Health check endpoint tests."""

    def test_health_returns_200_and_healthy(self):
        """GET /health returns 200 with {"status": "healthy"}."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


class TestCORS:
    """CORS middleware tests."""

    def test_cors_headers_on_cross_origin_request(self):
        """Cross-origin preflight request returns appropriate CORS headers."""
        response = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers


class TestAuthConfig:
    """Auth configuration endpoint tests."""

    def test_auth_config_returns_200_with_mode(self):
        """GET /api/auth/config returns 200 with a 'mode' field."""
        response = client.get("/api/auth/config")
        assert response.status_code == 200
        data = response.json()
        assert "mode" in data
        assert data["mode"] in ("disabled", "local", "cognito")


class TestAnalysesList:
    """Analyses list endpoint tests."""

    def test_analyses_list_returns_200(self):
        """GET /api/analyses returns 200 with analyses array."""
        _ensure_state()
        response = client.get("/api/analyses")
        assert response.status_code == 200
        data = response.json()
        assert "analyses" in data
        assert isinstance(data["analyses"], list)


class TestNotFound:
    """404 handling tests."""

    def test_nonexistent_analysis_returns_404(self):
        """GET /api/analysis/nonexistent/summary returns 404."""
        _ensure_state()
        response = client.get("/api/analysis/nonexistent/summary")
        assert response.status_code == 404

    def test_invalid_path_returns_404(self):
        """Any invalid path returns 404."""
        response = client.get("/api/completely-invalid-path-xyz")
        assert response.status_code == 404


class TestPromptLibrary:
    """Prompt library endpoint tests."""

    def test_prompts_endpoint_responds(self):
        """GET /api/prompts returns 200 or 404 (acceptable either way)."""
        response = client.get("/api/prompts")
        assert response.status_code in (200, 404)


class TestStorageStats:
    """Storage stats endpoint tests."""

    def test_storage_stats_responds(self):
        """GET /api/storage/stats returns 200 or 404 (acceptable either way)."""
        response = client.get("/api/storage/stats")
        assert response.status_code in (200, 404)
