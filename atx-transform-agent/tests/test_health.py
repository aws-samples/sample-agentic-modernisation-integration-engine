"""Tests for ATX Transform Agent health and basic endpoints."""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_endpoint():
    """GET /health returns healthy status."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_transformation_history_empty():
    """GET /transformation-history returns records format with empty list initially."""
    response = client.get("/transformation-history")
    assert response.status_code == 200
    data = response.json()
    assert "records" in data
    assert isinstance(data["records"], list)


def test_transformations_list():
    """GET /transformations returns available transformation definitions."""
    response = client.get("/transformations")
    assert response.status_code == 200
    data = response.json()
    assert "definitions" in data
    assert isinstance(data["definitions"], list)
    # Should have at least the AWS managed transformations
    assert len(data["definitions"]) > 0


def test_diff_not_found():
    """GET /diff/{repo_id} returns 404 for non-existent repo_id."""
    response = client.get("/diff/nonexistent-id")
    assert response.status_code == 404


def test_diff_summary_not_found():
    """GET /diff-summary/{repo_id} returns 404 for non-existent repo_id."""
    response = client.get("/diff-summary/nonexistent-id")
    assert response.status_code == 404


def test_pr_preview_not_found():
    """GET /pr-preview/{repo_id} returns 404 for non-existent repo_id."""
    response = client.get("/pr-preview/nonexistent-id")
    assert response.status_code == 404


def test_create_pr_not_found():
    """POST /create-file-pr/{repo_id} returns 404 for non-existent repo_id."""
    response = client.post("/create-file-pr/nonexistent-id")
    assert response.status_code == 404


def test_branches_requires_repo_url():
    """GET /branches requires repo_url query parameter."""
    response = client.get("/branches")
    assert response.status_code == 422  # Validation error


def test_stream_not_found():
    """GET /conversations/{repo_id}/stream returns 404 for non-existent repo_id."""
    response = client.get("/conversations/nonexistent-id/stream")
    assert response.status_code == 404
