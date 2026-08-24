"""Tests for main.py — health, CORS, exception handlers."""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_returns_healthy():
    """GET /health returns 200 with status=healthy."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_not_found_returns_404():
    """Undefined route returns 404."""
    response = client.get("/nonexistent")
    assert response.status_code == 404


def test_cors_headers_present():
    """Preflight request returns CORS headers."""
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers
