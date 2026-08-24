"""Health check and basic endpoint tests for ATX Analysis Agent."""

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.fixture
def tmp_storage(tmp_path):
    """Provide a temporary storage directory for tests."""
    with patch("config.settings.storage_path", str(tmp_path)):
        with patch("services.storage_service.settings.storage_path", str(tmp_path)):
            with patch("services.command_service.settings.storage_path", str(tmp_path)):
                yield tmp_path


@pytest.mark.asyncio
async def test_health():
    """GET /health returns healthy status."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "healthy"}


@pytest.mark.asyncio
async def test_analysis_definitions():
    """GET /analysis-definitions returns available definitions."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/analysis-definitions")
    assert response.status_code == 200
    data = response.json()
    assert "definitions" in data
    assert len(data["definitions"]) > 0
    # Check code-assessment definition exists
    keys = [d["key"] for d in data["definitions"]]
    assert "code-assessment" in keys


@pytest.mark.asyncio
async def test_conversations_empty(tmp_storage):
    """GET /conversations returns empty list when no conversations exist."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/conversations")
    assert response.status_code == 200
    data = response.json()
    assert "conversations" in data
    assert isinstance(data["conversations"], list)


@pytest.mark.asyncio
async def test_conversations_with_data(tmp_storage):
    """GET /conversations returns conversation list with correct fields."""
    # Create a conversation directory with metadata
    conv_dir = tmp_storage / "atx_20250101_120000_abc12345"
    conv_dir.mkdir()
    metadata = {
        "conversation_id": "atx_20250101_120000_abc12345",
        "status": "completed",
        "created_at": "2025-01-01T12:00:00+00:00",
    }
    (conv_dir / "metadata.json").write_text(json.dumps(metadata))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/conversations")
    assert response.status_code == 200
    data = response.json()
    assert len(data["conversations"]) == 1
    conv = data["conversations"][0]
    assert "conversation_id" in conv
    assert "status" in conv
    assert "created_at" in conv
    assert conv["conversation_id"] == "atx_20250101_120000_abc12345"
    assert conv["status"] == "completed"


@pytest.mark.asyncio
async def test_conversation_docs_not_found(tmp_storage):
    """GET /conversations/{id}/docs returns 404 for non-existent conversation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/conversations/nonexistent/docs")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_conversation_logs_not_found(tmp_storage):
    """GET /conversations/{id}/logs returns 404 for non-existent conversation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/conversations/nonexistent/logs")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancel_not_found():
    """POST /cancel/{id} returns 404 when no running process."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/cancel/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_browse_empty(tmp_storage):
    """GET /browse returns entries for storage root."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/browse")
    assert response.status_code == 200
    data = response.json()
    assert "entries" in data
    assert isinstance(data["entries"], list)


@pytest.mark.asyncio
async def test_file_not_found(tmp_storage):
    """GET /file returns 404 for non-existent file."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/file", params={"path": "nonexistent.txt"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_analyze_invalid_repo():
    """POST /analyze with non-existent local path returns 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/analyze",
            json={"repository_url": "/nonexistent/path/to/repo", "analysis_type": "code-assessment"},
        )
    assert response.status_code == 400
