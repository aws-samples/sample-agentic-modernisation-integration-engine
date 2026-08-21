"""Tests for routes/ai_streaming.py — SSE endpoints and doc-analysis storage."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from state import AppState, app_state
from utils.storage_manager import StorageManager

client = TestClient(app)


# --- Fixtures ---


def _setup_storage_with_analysis() -> tuple[StorageManager, str]:
    """Create a temp StorageManager with a sample analysis."""
    tmp_dir = tempfile.mkdtemp()
    storage = StorageManager(base_path=tmp_dir)
    analysis_id = "test_20250101_120000"
    storage.save(
        analysis_id,
        {
            "analysis_id": analysis_id,
            "source_type": "github",
            "file_stats": [{"language": "Python", "count": 5, "total_lines": 500}],
            "ai_documentation": "# Generated Docs\nThis is AI documentation.",
            "ai_enrichment_status": "completed",
            "kiro_spec": "# Kiro Spec\n## Requirements\n- Feature A",
        },
    )
    app_state.storage_manager = storage
    return storage, analysis_id


def _teardown_storage() -> None:
    """Reset app state."""
    AppState.reset()


# --- SSE Endpoint Tests ---


class TestSSEEndpoints:
    """Test SSE streaming endpoints return correct content type."""

    def setup_method(self) -> None:
        self.storage, self.analysis_id = _setup_storage_with_analysis()

    def teardown_method(self) -> None:
        _teardown_storage()

    def test_documentation_sse_invalid_id(self) -> None:
        """POST /documentation with invalid ID returns 400."""
        response = client.post("/api/analysis/bad!id@here/documentation")
        assert response.status_code == 400

    def test_judge_sse_invalid_id(self) -> None:
        """POST /judge with invalid ID returns 400."""
        response = client.post("/api/analysis/bad!id@here/judge")
        assert response.status_code == 400

    def test_file_analysis_sse_invalid_id(self) -> None:
        """POST /file-analysis with invalid ID returns 400."""
        response = client.post("/api/analysis/bad!id/file-analysis")
        assert response.status_code == 400

    def test_kiro_cli_sse_invalid_id(self) -> None:
        """POST /kiro-cli with invalid ID returns 400."""
        response = client.post("/api/analysis/bad!id/kiro-cli")
        assert response.status_code == 400

    def test_documentation_sse_valid_id_accepted(self) -> None:
        """POST /documentation with valid analysis ID returns 200.

        NOTE: sse-starlette EventSourceResponse has a known asyncio event
        loop bug with pytest sync TestClient (AppStatus.should_exit_event
        binds to a single loop). Full SSE integration tested via acceptance.
        """
        response = client.post(
            f"/api/analysis/{self.analysis_id}/documentation",
            content=json.dumps({}),
        )
        assert response.status_code == 200


# --- Doc-Analysis Storage Tests ---


class TestDocAnalysisStorage:
    """Test doc-analysis CRUD endpoints."""

    def setup_method(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.storage, self.analysis_id = _setup_storage_with_analysis()
        # Patch the doc-analysis base path
        self._patch = patch("routes.ai_streaming._DOC_ANALYSIS_BASE", self.tmp_dir)
        self._patch.start()

    def teardown_method(self) -> None:
        self._patch.stop()
        _teardown_storage()

    def test_get_doc_analysis_no_data(self) -> None:
        """GET /doc-analysis returns 404 when no runs exist."""
        response = client.get(f"/api/analysis/{self.analysis_id}/doc-analysis")
        assert response.status_code == 404

    def test_list_runs_empty(self) -> None:
        """GET /doc-analysis/runs returns empty list when no runs exist."""
        response = client.get(f"/api/analysis/{self.analysis_id}/doc-analysis/runs")
        assert response.status_code == 200
        assert response.json() == {"runs": []}

    def test_create_and_get_doc_analysis(self) -> None:
        """Create a doc-analysis run and retrieve it."""
        # Manually create a run
        doc_dir = Path(self.tmp_dir) / self.analysis_id / "doc-analysis"
        doc_dir.mkdir(parents=True)
        run_data = {
            "timestamp": "20250101T120000Z",
            "analysis_id": self.analysis_id,
            "documentation": "# Hello\nWorld",
            "status": "completed",
        }
        (doc_dir / "20250101T120000Z.json").write_text(json.dumps(run_data))

        # GET latest
        response = client.get(f"/api/analysis/{self.analysis_id}/doc-analysis")
        assert response.status_code == 200
        data = response.json()
        assert data["documentation"] == "# Hello\nWorld"
        assert data["status"] == "completed"

    def test_list_runs(self) -> None:
        """GET /doc-analysis/runs lists timestamps."""
        doc_dir = Path(self.tmp_dir) / self.analysis_id / "doc-analysis"
        doc_dir.mkdir(parents=True)
        (doc_dir / "20250101T100000Z.json").write_text("{}")
        (doc_dir / "20250101T120000Z.json").write_text("{}")

        response = client.get(f"/api/analysis/{self.analysis_id}/doc-analysis/runs")
        assert response.status_code == 200
        runs = response.json()["runs"]
        assert len(runs) == 2
        # Sorted newest first
        assert runs[0] == "20250101T120000Z"

    def test_get_specific_run(self) -> None:
        """GET /doc-analysis/run/{ts} returns specific run."""
        doc_dir = Path(self.tmp_dir) / self.analysis_id / "doc-analysis"
        doc_dir.mkdir(parents=True)
        run_data = {"timestamp": "20250101T100000Z", "documentation": "first"}
        (doc_dir / "20250101T100000Z.json").write_text(json.dumps(run_data))

        response = client.get(
            f"/api/analysis/{self.analysis_id}/doc-analysis/run/20250101T100000Z"
        )
        assert response.status_code == 200
        assert response.json()["documentation"] == "first"

    def test_get_specific_run_not_found(self) -> None:
        """GET /doc-analysis/run/{ts} returns 404 for missing run."""
        response = client.get(
            f"/api/analysis/{self.analysis_id}/doc-analysis/run/99999999T000000Z"
        )
        assert response.status_code == 404

    def test_delete_doc_analysis(self) -> None:
        """DELETE /doc-analysis removes all runs."""
        doc_dir = Path(self.tmp_dir) / self.analysis_id / "doc-analysis"
        doc_dir.mkdir(parents=True)
        (doc_dir / "20250101T100000Z.json").write_text("{}")
        (doc_dir / "20250101T120000Z.json").write_text("{}")

        response = client.delete(f"/api/analysis/{self.analysis_id}/doc-analysis")
        assert response.status_code == 200
        assert "2" in response.json()["detail"]

        # Verify gone
        response = client.get(f"/api/analysis/{self.analysis_id}/doc-analysis/runs")
        assert response.json()["runs"] == []

    def test_delete_doc_analysis_not_found(self) -> None:
        """DELETE /doc-analysis returns 404 when no data exists."""
        response = client.delete(f"/api/analysis/{self.analysis_id}/doc-analysis")
        assert response.status_code == 404


# --- Kiro Spec Download ---


class TestKiroSpecDownload:
    """Test kiro-spec download endpoint."""

    def setup_method(self) -> None:
        self.storage, self.analysis_id = _setup_storage_with_analysis()

    def teardown_method(self) -> None:
        _teardown_storage()

    def test_download_kiro_spec(self) -> None:
        """POST /kiro-spec/download returns markdown file."""
        response = client.post(f"/api/analysis/{self.analysis_id}/kiro-spec/download")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"
        assert "attachment" in response.headers.get("content-disposition", "")
        assert "# Kiro Spec" in response.text

    def test_download_kiro_spec_not_found(self) -> None:
        """POST /kiro-spec/download returns 404 for missing analysis."""
        response = client.post("/api/analysis/nonexistent_123/kiro-spec/download")
        assert response.status_code == 404

    def test_download_kiro_spec_no_spec(self) -> None:
        """POST /kiro-spec/download returns 404 when no spec available."""
        # Save analysis without kiro_spec
        self.storage.save(
            "test_nospecs_123",
            {"analysis_id": "test_nospecs_123", "source_type": "upload"},
        )
        response = client.post("/api/analysis/test_nospecs_123/kiro-spec/download")
        assert response.status_code == 404
