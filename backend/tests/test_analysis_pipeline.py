"""Integration tests — Analysis pipeline: upload, status, file-stats, dependencies, delete."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from state import app_state
from utils.progress_tracker import ProgressTracker
from utils.storage_manager import StorageManager


def _ensure_state() -> None:
    """Ensure app state is initialized for tests."""
    if not app_state.storage_manager:
        app_state.storage_manager = StorageManager(base_path="/tmp/test_pipeline")
    if not app_state.progress_tracker:
        app_state.progress_tracker = ProgressTracker()


client = TestClient(app)


def _create_sample_zip() -> bytes:
    """Create a minimal valid ZIP file in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("src/main.py", "class App:\n    def run(self):\n        pass\n")
        zf.writestr("requirements.txt", "fastapi==0.115.5\n")
    buf.seek(0)
    return buf.read()


def _seed_analysis(analysis_id: str) -> None:
    """Store a complete analysis record for endpoint testing."""
    storage = app_state.storage_manager
    assert storage is not None
    storage.save(
        analysis_id,
        {
            "analysis_id": analysis_id,
            "source_type": "upload",
            "file_stats": [
                {"extension": ".py", "count": 3, "total_lines": 75, "total_size": 1500}
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
            "dependency_graph": {"nodes": [], "links": []},
            "completed_at": "2025-01-01T00:00:00Z",
        },
    )


class TestUploadReturnsId:
    """POST /api/analyze/upload returns an analysis_id."""

    @patch("services.code_parser_service.CodeParserService.analyze_zip")
    def test_upload_valid_zip_returns_analysis_id(self, mock_analyze):
        """Uploading a valid ZIP file returns analysis_id and processing status."""
        _ensure_state()
        mock_analyze.return_value = None  # Skip actual pipeline
        zip_bytes = _create_sample_zip()
        response = client.post(
            "/api/analyze/upload",
            files={"file": ("project.zip", zip_bytes, "application/zip")},
        )
        assert response.status_code == 200
        data = response.json()
        assert "analysis_id" in data
        assert data["status"] == "processing"
        assert data["analysis_id"].startswith("upload_")


class TestAnalysisStatus:
    """GET /api/analysis/{id}/status returns progress info."""

    def test_status_for_stored_analysis(self):
        """Completed analysis returns status=completed, progress=100."""
        _ensure_state()
        analysis_id = "pipeline_status_test"
        _seed_analysis(analysis_id)
        response = client.get(f"/api/analysis/{analysis_id}/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["progress"] == 100


class TestFileStats:
    """GET /api/analysis/{id}/file-stats returns file statistics."""

    def test_file_stats_returns_array(self):
        """Returns {"file_stats": [...]} envelope."""
        _ensure_state()
        analysis_id = "pipeline_filestats_test"
        _seed_analysis(analysis_id)
        response = client.get(f"/api/analysis/{analysis_id}/file-stats")
        assert response.status_code == 200
        data = response.json()
        assert "file_stats" in data
        assert isinstance(data["file_stats"], list)
        assert len(data["file_stats"]) > 0


class TestDependencies:
    """GET /api/analysis/{id}/dependencies returns dependency list."""

    def test_dependencies_returns_array(self):
        """Returns {"dependencies": [...]} envelope."""
        _ensure_state()
        analysis_id = "pipeline_deps_test"
        _seed_analysis(analysis_id)
        response = client.get(f"/api/analysis/{analysis_id}/dependencies")
        assert response.status_code == 200
        data = response.json()
        assert "dependencies" in data
        assert isinstance(data["dependencies"], list)
        assert len(data["dependencies"]) > 0


class TestFolderStructure:
    """GET /api/analysis/{id}/folder-structure returns folder tree."""

    def test_folder_structure_returns_object(self):
        """Returns {"folder_structure": {...}} envelope."""
        _ensure_state()
        analysis_id = "pipeline_folder_test"
        _seed_analysis(analysis_id)
        response = client.get(f"/api/analysis/{analysis_id}/folder-structure")
        assert response.status_code == 200
        data = response.json()
        assert "folder_structure" in data
        assert isinstance(data["folder_structure"], dict)


class TestDeleteAnalysis:
    """DELETE /api/analysis/{id} removes analysis and subsequent GET returns 404."""

    def test_delete_then_404(self):
        """Delete an analysis, then verify it returns 404."""
        _ensure_state()
        analysis_id = "pipeline_delete_test"
        _seed_analysis(analysis_id)

        # Delete.
        response = client.delete(f"/api/analysis/{analysis_id}")
        assert response.status_code == 200

        # Verify 404 after deletion.
        response = client.get(f"/api/analysis/{analysis_id}/summary")
        assert response.status_code == 404
