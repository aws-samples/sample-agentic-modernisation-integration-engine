"""Artifact collection and documentation serving.

Ground truth these tests encode, established by inspecting a real completed run
in the container (``AWS/comprehensive-codebase-analysis``):

- the CLI writes ``ATXDocumentation/`` **into the project path** it was handed —
  the cloned repo at ``<storage>/<id>/repo`` — not into the process cwd;
- it mirrors that tree, plus an ``artifacts/`` directory, under its own run
  directory ``~/.aws/atx/custom/<run_id>/``, which is derivable from the
  ``conversation_log`` path already recorded in ``metadata.json``;
- neither location is ``<storage>/<id>/artifacts`` or
  ``<storage>/<id>/ATXDocumentation``, so the original candidate list collected
  nothing and ``docs/`` stayed empty.

Everything collected is copied *into* the conversation's ``docs/`` dir, so the
documents served still resolve under the storage root and ``GET /file`` keeps its
path-traversal protection.
"""

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from config import settings
from main import app
from services.command_service import _collect_artifacts, ensure_artifacts_collected
from services.file_service import list_docs

CONVERSATION_ID = "atx_20250101_120000_abcd1234"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Point the agent at a temporary storage root."""
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setattr(settings, "storage_path", str(root))
    return root


def _make_completed_run(
    storage: Path,
    home: Path,
    *,
    conversation_id: str = CONVERSATION_ID,
    with_repo_docs: bool = True,
    with_run_dir_docs: bool = True,
) -> Path:
    """Lay out a finished analysis exactly as the real CLI leaves it on disk."""
    storage_dir = storage / conversation_id
    repo_path = storage_dir / "repo"
    repo_path.mkdir(parents=True)
    (storage_dir / "docs").mkdir()

    run_dir = home / ".aws" / "atx" / "custom" / "20250101_120005_deadbeef"
    log_path = run_dir / "logs" / "2025-01-01T12-00-05-conversation.log"
    _write(log_path, "agent: done\n")

    if with_repo_docs:
        _write(repo_path / "ATXDocumentation" / "README.md", "# ATX Documentation\n")
        _write(
            repo_path / "ATXDocumentation" / "architecture" / "system-overview.md",
            "# System Overview\n\n| Component | Role |\n| --- | --- |\n| api | edge |\n",
        )

    if with_run_dir_docs:
        # The CLI mirrors the same tree under its own run directory.
        _write(run_dir / "ATXDocumentation" / "README.md", "# ATX Documentation\n")
        _write(run_dir / "artifacts" / "validation_summary.md", "# Validation Summary\n")
        _write(run_dir / "artifacts" / "worklog.log", "not a document\n")

    metadata = {
        "conversation_id": conversation_id,
        "analysis_type": "code-assessment",
        "repo_path": str(repo_path),
        "status": "completed",
        "created_at": "2025-01-01T12:00:00+00:00",
        "conversation_log": str(log_path),
        "return_code": 0,
    }
    (storage_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
    return storage_dir


# --- Collection ---


def test_collects_documentation_written_into_the_cloned_repo(storage, tmp_path):
    """ATXDocumentation/ under the project path is collected into docs/.

    This is the fault: the CLI writes there, the old candidate list did not look
    there, so docs/ stayed empty for every completed analysis.
    """
    storage_dir = _make_completed_run(storage, tmp_path / "home", with_run_dir_docs=False)
    metadata = json.loads((storage_dir / "metadata.json").read_text())

    collected = _collect_artifacts(storage_dir, metadata)

    assert collected == 2
    docs_dir = storage_dir / "docs"
    assert (docs_dir / "README.md").read_text() == "# ATX Documentation\n"
    assert "System Overview" in (docs_dir / "architecture" / "system-overview.md").read_text()


def test_collects_documentation_from_the_cli_run_directory(storage, tmp_path):
    """The run dir is derived from the recorded conversation_log path."""
    storage_dir = _make_completed_run(storage, tmp_path / "home", with_repo_docs=False)
    metadata = json.loads((storage_dir / "metadata.json").read_text())

    _collect_artifacts(storage_dir, metadata)

    docs_dir = storage_dir / "docs"
    assert (docs_dir / "README.md").exists()
    assert (docs_dir / "validation_summary.md").read_text() == "# Validation Summary\n"


def test_mirrored_trees_are_not_collected_twice(storage, tmp_path):
    """The repo copy and the run-dir mirror produce one docs/ entry per path."""
    storage_dir = _make_completed_run(storage, tmp_path / "home")
    metadata = json.loads((storage_dir / "metadata.json").read_text())

    _collect_artifacts(storage_dir, metadata)

    names = sorted(
        p.relative_to(storage_dir / "docs").as_posix() for p in (storage_dir / "docs").rglob("*") if p.is_file()
    )
    assert names == [
        "README.md",
        "architecture/system-overview.md",
        "validation_summary.md",
    ]


def test_still_collects_from_the_storage_root_candidates(storage, tmp_path):
    """The original two candidate directories remain supported."""
    storage_dir = _make_completed_run(storage, tmp_path / "home", with_repo_docs=False, with_run_dir_docs=False)
    _write(storage_dir / "ATXDocumentation" / "project-overview.md", "# Project Overview\n")
    _write(storage_dir / "artifacts" / "summary.md", "# Summary\n")
    metadata = json.loads((storage_dir / "metadata.json").read_text())

    _collect_artifacts(storage_dir, metadata)

    assert (storage_dir / "docs" / "project-overview.md").exists()
    assert (storage_dir / "docs" / "summary.md").exists()


def test_no_artifacts_produced_leaves_docs_empty(storage, tmp_path):
    """A run that produced nothing collects nothing — no invented content."""
    storage_dir = _make_completed_run(storage, tmp_path / "home", with_repo_docs=False, with_run_dir_docs=False)
    metadata = json.loads((storage_dir / "metadata.json").read_text())

    assert _collect_artifacts(storage_dir, metadata) == 0
    assert list((storage_dir / "docs").rglob("*")) == []


def test_collection_is_idempotent(storage, tmp_path):
    """Re-running collection does not duplicate or re-copy."""
    storage_dir = _make_completed_run(storage, tmp_path / "home")
    metadata = json.loads((storage_dir / "metadata.json").read_text())

    first = _collect_artifacts(storage_dir, metadata)
    second = _collect_artifacts(storage_dir, metadata)

    assert first == 3
    assert second == 0


def test_ensure_artifacts_collected_backfills_an_empty_docs_dir(storage, tmp_path):
    """A conversation whose worker never collected is repaired on read."""
    _make_completed_run(storage, tmp_path / "home")

    assert ensure_artifacts_collected(CONVERSATION_ID) == 3
    # Second call is a no-op now that docs/ is populated.
    assert ensure_artifacts_collected(CONVERSATION_ID) == 0


# --- Serving ---


def test_docs_listing_carries_a_storage_path_the_file_reader_accepts(storage, tmp_path):
    """Each doc entry points at a path under the storage root, for GET /file."""
    storage_dir = _make_completed_run(storage, tmp_path / "home")
    _collect_artifacts(storage_dir, json.loads((storage_dir / "metadata.json").read_text()))

    docs = list_docs(storage_dir)

    assert docs, "expected collected documents to be listed"
    for doc in docs:
        assert doc["storage_path"] == f"{CONVERSATION_ID}/docs/{doc['path']}"
        assert (storage / doc["storage_path"]).is_file()


@pytest.mark.asyncio
async def test_docs_endpoint_content_is_readable_through_the_file_endpoint(storage, tmp_path):
    """End-to-end API path: /docs lists it, /file returns renderable markdown."""
    _make_completed_run(storage, tmp_path / "home")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listing = await client.get(f"/conversations/{CONVERSATION_ID}/docs")
        assert listing.status_code == 200
        body = listing.json()
        docs = body["docs"]
        assert [doc["name"] for doc in docs], "completed analysis with ATXDocumentation on disk returned no documents"
        assert body["status"] == "completed"

        target = next(doc for doc in docs if doc["name"] == "system-overview.md")
        content = await client.get("/file", params={"path": target["storage_path"]})

    assert content.status_code == 200
    assert content.json()["content"].startswith("# System Overview")


@pytest.mark.asyncio
async def test_docs_endpoint_reports_status_for_a_run_with_no_documentation(storage, tmp_path):
    """An empty listing is accompanied by the status that explains it."""
    _make_completed_run(storage, tmp_path / "home", with_repo_docs=False, with_run_dir_docs=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/conversations/{CONVERSATION_ID}/docs")

    assert response.status_code == 200
    assert response.json() == {"docs": [], "status": "completed"}


@pytest.mark.asyncio
async def test_docs_endpoint_rejects_traversal_out_of_the_storage_root(storage, tmp_path):
    """Collected-in-place means nothing served needs to escape the storage root."""
    _make_completed_run(storage, tmp_path / "home")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/file", params={"path": "../etc/passwd"})

    assert response.status_code in (400, 404)


def test_symlinked_artifact_dir_is_not_followed(storage, tmp_path):
    """A committed ATXDocumentation symlink to a dir outside the repo is refused.

    CWE-22 symlink traversal: an attacker's cloned repo could point
    ATXDocumentation at /etc (or another conversation's storage). The collector
    must not read through it.
    """
    storage_dir = _make_completed_run(storage, tmp_path / "home", with_repo_docs=False, with_run_dir_docs=False)
    metadata = json.loads((storage_dir / "metadata.json").read_text())

    # Sensitive tree outside the repo, then ATXDocumentation -> it.
    secret_dir = tmp_path / "outside"
    _write(secret_dir / "secret.md", "# top secret\n")
    repo_path = Path(metadata["repo_path"])
    (repo_path / "ATXDocumentation").symlink_to(secret_dir, target_is_directory=True)

    collected = _collect_artifacts(storage_dir, metadata)

    assert collected == 0
    assert not (storage_dir / "docs" / "secret.md").exists()


def test_file_reached_through_symlinked_parent_is_not_collected(storage, tmp_path):
    """A regular file under a symlinked *sub*directory resolves outside and is skipped."""
    storage_dir = _make_completed_run(storage, tmp_path / "home", with_repo_docs=False, with_run_dir_docs=False)
    metadata = json.loads((storage_dir / "metadata.json").read_text())

    secret_dir = tmp_path / "outside"
    _write(secret_dir / "secret.md", "# top secret\n")
    repo_path = Path(metadata["repo_path"])
    docs = repo_path / "ATXDocumentation"
    _write(docs / "README.md", "# real doc\n")
    # A subdirectory that is a symlink out of the repo.
    (docs / "sneaky").symlink_to(secret_dir, target_is_directory=True)

    collected = _collect_artifacts(storage_dir, metadata)

    # The legitimate README is collected; the symlinked-out file is not.
    assert (storage_dir / "docs" / "README.md").exists()
    assert not (storage_dir / "docs" / "sneaky" / "secret.md").exists()
    assert collected == 1
