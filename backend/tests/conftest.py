"""Shared pytest fixtures for backend tests."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from main import app
from state import app_state
from utils.progress_tracker import ProgressTracker
from utils.storage_manager import StorageManager


@pytest.fixture()
def mock_storage(tmp_path: Path) -> StorageManager:
    """StorageManager backed by a temporary directory."""
    return StorageManager(base_path=str(tmp_path / "analyses"))


@pytest.fixture()
def test_client(mock_storage: StorageManager) -> TestClient:
    """FastAPI TestClient with patched storage and progress tracker."""
    app_state.storage_manager = mock_storage
    app_state.progress_tracker = ProgressTracker()
    return TestClient(app)


@pytest.fixture()
def mock_bedrock() -> MagicMock:
    """MagicMock for boto3 bedrock-runtime client."""
    client = MagicMock()
    client.invoke_model.return_value = {
        "body": io.BytesIO(b'{"content": [{"text": "mock response"}]}'),
        "contentType": "application/json",
    }
    client.invoke_model_with_response_stream.return_value = {
        "body": iter([]),
    }
    return client


@pytest.fixture()
def sample_zip(tmp_path: Path) -> Path:
    """Create a temporary ZIP file with sample Python files for upload tests."""
    zip_path = tmp_path / "sample_project.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "src/main.py",
            "class App:\n    def run(self):\n        print('hello')\n",
        )
        zf.writestr(
            "src/utils.py",
            "import os\nimport sys\n\ndef helper():\n    return True\n",
        )
        zf.writestr(
            "requirements.txt",
            "fastapi==0.115.5\nuvicorn==0.32.1\n",
        )
    zip_path.write_bytes(buf.getvalue())
    return zip_path


@pytest.fixture()
def sample_analysis_data() -> dict:
    """Complete analysis result dict for testing storage and endpoints."""
    return {
        "analysis_id": "github_20250115_143022",
        "source_type": "github",
        "source_url": "https://github.com/example/repo",
        "branch_name": "main",
        "file_stats": [
            {"extension": ".py", "count": 10, "total_lines": 500, "total_size": 12000},
            {"extension": ".js", "count": 5, "total_lines": 200, "total_size": 6000},
        ],
        "folder_structure": {
            "name": "repo",
            "type": "directory",
            "children": [
                {
                    "name": "src",
                    "type": "directory",
                    "children": [
                        {"name": "main.py", "type": "file", "size": 1200},
                    ],
                },
            ],
        },
        "dependencies": [
            {
                "name": "fastapi",
                "version": "0.115.5",
                "ecosystem": "pip",
                "source_file": "requirements.txt",
            },
        ],
        "dependency_graph": {
            "nodes": [
                {"id": "fastapi", "label": "fastapi", "type": "pip", "metadata": {}},
            ],
            "links": [],
        },
        "upgrade_recommendations": [],
        "diagrams": {
            "class_diagram": "classDiagram\n  class App",
            "sequence_diagram": "",
            "integration_diagram": "",
        },
        "completed_at": "2025-01-15T14:30:22Z",
    }
