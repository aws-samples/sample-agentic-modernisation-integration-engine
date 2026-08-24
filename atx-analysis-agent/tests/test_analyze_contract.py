"""Request-contract tests for POST /analyze.

The ATX Analysis Agent's request body is `{ repository_url, branch,
analysis_type, conversation_id?, pat_token? }` (design.md — Agent Service
Interfaces). ATX Transform deliberately uses `repo_url` instead; these tests
pin the asymmetry so a rename cannot pass silently.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from main import AnalyzeRequest, app

# --- Model-level contract ---


def test_model_accepts_documented_field_set():
    """AnalyzeRequest accepts every documented field."""
    request = AnalyzeRequest(
        repository_url="https://github.com/octocat/Hello-World",
        branch="main",
        analysis_type="code-assessment",
        conversation_id="atx_20250101_120000_abc12345",
        pat_token="ghp_example",
    )
    assert request.repository_url == "https://github.com/octocat/Hello-World"
    assert request.branch == "main"
    assert request.analysis_type == "code-assessment"
    assert request.conversation_id == "atx_20250101_120000_abc12345"
    assert request.pat_token == "ghp_example"


def test_model_requires_only_repository_url():
    """Optional fields default; analysis_type defaults to code-assessment."""
    request = AnalyzeRequest(repository_url="https://github.com/octocat/Hello-World")
    assert request.analysis_type == "code-assessment"
    assert request.branch is None
    assert request.conversation_id is None
    assert request.pat_token is None


def test_model_rejects_repo_url_field_name():
    """`repo_url` is the ATX Transform field name and must be rejected here."""
    with pytest.raises(ValidationError) as exc_info:
        AnalyzeRequest(repo_url="https://github.com/octocat/Hello-World")

    errors = exc_info.value.errors()
    assert any(e["type"] == "missing" and e["loc"] == ("repository_url",) for e in errors)


# --- Endpoint-level contract ---


@pytest.mark.asyncio
async def test_analyze_with_repo_url_returns_422():
    """POST /analyze using the wrong field name is a validation error."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/analyze",
            json={"repo_url": "https://github.com/octocat/Hello-World", "analysis_type": "code-assessment"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_analyze_with_repository_url_is_not_422():
    """The documented field set passes validation (400 here — path doesn't exist)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/analyze",
            json={
                "repository_url": "/nonexistent/path/to/repo",
                "branch": "main",
                "analysis_type": "code-assessment",
            },
        )
    assert response.status_code != 422
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_analyze_rejects_private_host_url():
    """SSRF posture: private/loopback hosts are rejected before any clone."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/analyze",
            json={"repository_url": "http://127.0.0.1/internal/repo.git"},
        )
    assert response.status_code == 400
    assert "private" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_analyze_rejects_ssh_url():
    """SSH URLs cannot be cloned in this container — surfaced as 400, not a hang."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/analyze",
            json={"repository_url": "git@github.com:octocat/Hello-World.git"},
        )
    assert response.status_code == 400
