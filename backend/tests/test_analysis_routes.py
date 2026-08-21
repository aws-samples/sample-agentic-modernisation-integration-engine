"""Tests for analysis routes and transformation management endpoints."""

import io
import zipfile

from fastapi.testclient import TestClient

from main import app
from state import app_state
from utils.progress_tracker import ProgressTracker
from utils.storage_manager import StorageManager

client = TestClient(app)


def _ensure_state() -> None:
    """Ensure app state is initialized for tests."""
    if not app_state.storage_manager:
        app_state.storage_manager = StorageManager(base_path="/tmp/test_analyses")
    if not app_state.progress_tracker:
        app_state.progress_tracker = ProgressTracker()


# --- Analysis Endpoints ---


def test_list_analyses_returns_envelope():
    """GET /api/analyses returns {analyses: [...]}."""
    _ensure_state()
    response = client.get("/api/analyses")
    assert response.status_code == 200
    data = response.json()
    assert "analyses" in data
    assert isinstance(data["analyses"], list)


def test_analysis_status_not_found():
    """GET /api/analysis/{id}/status returns 404 for unknown ID."""
    _ensure_state()
    response = client.get("/api/analysis/nonexistent_id/status")
    assert response.status_code == 404


def test_analysis_summary_not_found():
    """GET /api/analysis/{id}/summary returns 404 for unknown ID."""
    _ensure_state()
    response = client.get("/api/analysis/nonexistent_id/summary")
    assert response.status_code == 404


def test_analysis_file_stats_not_found():
    """GET /api/analysis/{id}/file-stats returns 404 for unknown ID."""
    _ensure_state()
    response = client.get("/api/analysis/nonexistent_id/file-stats")
    assert response.status_code == 404


def test_analysis_delete_not_found():
    """DELETE /api/analysis/{id} returns 404 for unknown ID."""
    _ensure_state()
    response = client.delete("/api/analysis/nonexistent_id")
    assert response.status_code == 404


def test_invalid_analysis_id_rejected():
    """Analysis IDs with path traversal are rejected."""
    _ensure_state()
    response = client.get("/api/analysis/../etc/passwd/status")
    # The path won't match the route due to slashes, but let's test
    # an ID with special chars that does match.
    response = client.get("/api/analysis/bad..id/status")
    assert response.status_code == 400


def test_upload_rejects_non_zip():
    """POST /api/analyze/upload rejects non-ZIP files."""
    _ensure_state()
    response = client.post(
        "/api/analyze/upload",
        files={"file": ("test.txt", b"not a zip", "text/plain")},
    )
    assert response.status_code == 400
    assert "ZIP" in response.json()["detail"]


def test_upload_accepts_zip():
    """POST /api/analyze/upload accepts a ZIP file and returns analysis_id."""
    _ensure_state()
    # Create a minimal ZIP in memory.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.py", "print('hello')")
    buf.seek(0)

    response = client.post(
        "/api/analyze/upload",
        files={"file": ("test.zip", buf.read(), "application/zip")},
    )
    assert response.status_code == 200
    data = response.json()
    assert "analysis_id" in data
    assert data["status"] == "processing"


def test_github_analysis_requires_url():
    """POST /api/analyze/github requires repo_url."""
    _ensure_state()
    response = client.post("/api/analyze/github", json={"repo_url": ""})
    assert response.status_code == 400


# --- Storage Round-Trip ---


def test_analysis_endpoints_with_stored_data():
    """Verify all GET endpoints return correct envelopes for stored data."""
    _ensure_state()
    storage = app_state.storage_manager
    assert storage is not None

    analysis_id = "test_roundtrip_001"
    storage.save(
        analysis_id,
        {
            "analysis_id": analysis_id,
            "source_type": "upload",
            "file_stats": [
                {"extension": ".py", "count": 5, "total_lines": 100, "total_size": 2000}
            ],
            "folder_structure": {"name": "root", "type": "directory", "children": []},
            "dependencies": [
                {
                    "name": "fastapi",
                    "version": "0.115.5",
                    "ecosystem": "pip",
                    "source_file": "requirements.txt",
                }
            ],
            "dependency_graph": {
                "nodes": [
                    {"id": "fastapi", "label": "fastapi", "type": "pip", "metadata": {}}
                ],
                "links": [],
            },
            "upgrade_recommendations": [],
            "diagrams": {
                "class_diagram": "classDiagram",
                "sequence_diagram": "sequenceDiagram",
                "integration_diagram": "graph TD",
            },
            "ai_documentation": "# Generated Docs",
            "ai_enrichment_status": "completed",
            "completed_at": "2025-01-01T00:00:00Z",
        },
    )

    # Status (completed in storage).
    r = client.get(f"/api/analysis/{analysis_id}/status")
    assert r.status_code == 200
    assert r.json()["status"] == "completed"

    # Summary.
    r = client.get(f"/api/analysis/{analysis_id}/summary")
    assert r.status_code == 200
    assert r.json()["analysis_id"] == analysis_id

    # File stats.
    r = client.get(f"/api/analysis/{analysis_id}/file-stats")
    assert r.status_code == 200
    assert "file_stats" in r.json()

    # Folder structure.
    r = client.get(f"/api/analysis/{analysis_id}/folder-structure")
    assert r.status_code == 200
    assert "folder_structure" in r.json()

    # Dependencies.
    r = client.get(f"/api/analysis/{analysis_id}/dependencies")
    assert r.status_code == 200
    assert "dependencies" in r.json()

    # Dependency graph.
    r = client.get(f"/api/analysis/{analysis_id}/dependency-graph")
    assert r.status_code == 200
    graph = r.json()["dependency_graph"]
    assert "nodes" in graph
    assert "links" in graph

    # Upgrade recommendations.
    r = client.get(f"/api/analysis/{analysis_id}/upgrade-recommendations")
    assert r.status_code == 200
    assert "upgrade_recommendations" in r.json()

    # Diagrams.
    r = client.get(f"/api/analysis/{analysis_id}/diagrams")
    assert r.status_code == 200
    assert "diagrams" in r.json()

    # Mermaid raw.
    r = client.get(f"/api/analysis/{analysis_id}/mermaid")
    assert r.status_code == 200
    assert "class_diagram" in r.json()

    # Documentation.
    r = client.get(f"/api/analysis/{analysis_id}/documentation")
    assert r.status_code == 200
    assert r.json()["documentation"] == "# Generated Docs"
    assert r.json()["ai_enrichment_status"] == "completed"

    # Delete.
    r = client.delete(f"/api/analysis/{analysis_id}")
    assert r.status_code == 200

    # Verify deleted.
    r = client.get(f"/api/analysis/{analysis_id}/summary")
    assert r.status_code == 404


# --- Transformation Management ---


def test_transformation_crud():
    """Full CRUD cycle for transformation definitions."""
    _ensure_state()

    # List — initially may be empty or have prior data.
    r = client.get("/api/transformations/definitions")
    assert r.status_code == 200
    assert "definitions" in r.json()

    # Create.
    r = client.post(
        "/api/transformations/definitions",
        json={"name": "Test Transform", "description": "A test"},
    )
    assert r.status_code == 201
    created = r.json()
    assert created["name"] == "Test Transform"
    assert "id" in created
    def_id = created["id"]

    # Update.
    r = client.put(
        f"/api/transformations/definitions/{def_id}",
        json={"name": "Updated Transform", "published": True},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Updated Transform"
    assert r.json()["published"] is True

    # Delete.
    r = client.delete(f"/api/transformations/definitions/{def_id}")
    assert r.status_code == 200

    # Delete again → 404.
    r = client.delete(f"/api/transformations/definitions/{def_id}")
    assert r.status_code == 404


def test_documentation_endpoint_reports_failed_status(
    test_client, mock_storage
) -> None:
    """A failed enrichment must reach the client as `failed`, not `skipped`.

    The endpoint used to default an absent status to "skipped", so a failure was
    indistinguishable from a deliberate no-op at the API boundary too.
    """
    analysis_id = "github_20260805_090000"
    mock_storage.save(
        analysis_id,
        {
            "analysis_id": analysis_id,
            "source_type": "github",
            "file_stats": [],
            "ai_enrichment_status": "failed",
            "ai_enrichment_error": "AI documentation generation failed — timed out",
            "completed_at": "2026-08-05T09:00:00Z",
        },
    )

    r = test_client.get(f"/api/analysis/{analysis_id}/documentation")
    assert r.status_code == 200
    assert r.json()["ai_enrichment_status"] == "failed"
    assert r.json()["documentation"] == ""

    # The recorded cause travels with the full stored object.
    summary = test_client.get(f"/api/analysis/{analysis_id}/summary").json()
    assert "timed out" in summary["ai_enrichment_error"]


def test_documentation_endpoint_does_not_invent_a_skip(
    test_client, mock_storage
) -> None:
    """An analysis with no recorded enrichment outcome must not claim a skip."""
    analysis_id = "github_20260805_091500"
    mock_storage.save(
        analysis_id,
        {
            "analysis_id": analysis_id,
            "source_type": "github",
            "file_stats": [],
            "completed_at": "2026-08-05T09:15:00Z",
        },
    )

    body = test_client.get(f"/api/analysis/{analysis_id}/documentation").json()
    assert body["ai_enrichment_status"] != "skipped"
    assert set(body.keys()) == {"documentation", "ai_enrichment_status"}
