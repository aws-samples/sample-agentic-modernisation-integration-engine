"""Transformation records must outlive the process that created them.

The record used to live in a module-level list, so restarting the agent emptied
``GET /transformation-history`` and turned ``/diff``, ``/diff-summary``, ``/download``,
``/pr-preview`` and ``/create-file-pr`` into permanent 404s for transformations whose
trees were still on disk — the data was there and unreachable.

These tests pin the durable behaviour: every record is a ``metadata.json`` beside its
trees (same shape as the analysis agent's per-conversation metadata), the listing is
rebuilt by scanning storage, status transitions reach disk, and pre-existing trees are
backfilled rather than lost.
"""

import io
import zipfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from main import app
from services import storage_service, transform_service


@pytest.fixture
def tmp_storage(tmp_path):
    """Point the transform agent's storage at a temporary directory."""
    storage = tmp_path / "storage"
    storage.mkdir()
    with patch("config.settings.storage_path", str(storage)):
        yield storage


def _simulate_restart() -> None:
    """Drop every scrap of in-process state, exactly as a container restart does.

    Anything the agent still answers afterwards is answered from disk.
    """
    getattr(main, "transformation_records", []).clear()
    transform_service.running_processes.clear()
    getattr(transform_service, "active_transformations", set()).clear()


@pytest.fixture(autouse=True)
def clean_process_state():
    _simulate_restart()
    yield
    _simulate_restart()


def _make_trees(storage, repo_id: str, original: dict[str, str], transformed: dict[str, str]) -> None:
    for tree_name, files in (("original", original), ("repo", transformed)):
        for rel_path, content in files.items():
            target = storage / repo_id / tree_name / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        (storage / repo_id / tree_name).mkdir(parents=True, exist_ok=True)


def _persist(repo_id: str, status: str = "completed", **extra) -> dict:
    """Persist a record the way ``POST /transform`` does."""
    record = {
        "repo_id": repo_id,
        "status": status,
        "created_at": "2025-01-01T00:00:00+00:00",
        "repo_url": "https://github.com/org/repo",
        "branch": "main",
        "transformation_type": "AWS/java-version-upgrade",
        **extra,
    }
    return storage_service.write_record(record)


# --- 1. Survival across a restart ---


def test_a_completed_transformation_survives_a_restart(tmp_storage):
    """History, diff, download and PR preview all still resolve with no in-memory state.

    Against the in-memory implementation every one of these is a 404.
    """
    repo_id = "restart01"
    _persist(repo_id)
    _make_trees(
        tmp_storage,
        repo_id,
        original={"pom.xml": "<version>8</version>\n"},
        transformed={"pom.xml": "<version>17</version>\n"},
    )

    _simulate_restart()

    with TestClient(app) as client:
        history = client.get("/transformation-history")
        diff = client.get(f"/diff/{repo_id}")
        summary = client.get(f"/diff-summary/{repo_id}")
        download = client.get(f"/download/{repo_id}")
        preview = client.get(f"/pr-preview/{repo_id}")

    assert history.status_code == 200
    assert [r["repo_id"] for r in history.json()["records"]] == [repo_id]

    assert diff.status_code == 200, diff.text
    assert diff.json()["files"][0]["filename"] == "pom.xml"
    assert summary.status_code == 200 and summary.json()["has_changes"] is True

    assert download.status_code == 200, download.text
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert archive.testzip() is None
        assert archive.read("pom.xml").decode() == "<version>17</version>\n"

    assert preview.status_code == 200, preview.text
    assert preview.json()["repo_id"] == repo_id


def test_history_is_rebuilt_by_scanning_storage(tmp_storage):
    """The listing comes from the filesystem, and keeps its documented shape (BC-33)."""
    _persist("scanolder", created_at="2025-01-01T00:00:00+00:00")
    _persist("scannewer", created_at="2025-06-01T00:00:00+00:00")

    _simulate_restart()

    with TestClient(app) as client:
        payload = client.get("/transformation-history").json()

    assert set(payload) == {"records"}
    assert [r["repo_id"] for r in payload["records"]] == ["scannewer", "scanolder"]
    for record in payload["records"]:
        assert set(record) == {"repo_id", "status", "created_at", "repo_url"}


# --- 2. Status transitions reach disk ---


def test_status_transition_is_readable_from_disk_after_the_fact(tmp_storage):
    """A record written ``running`` and later completed reads back as ``completed``."""
    repo_id = "transition1"
    _persist(repo_id, status="running")

    assert storage_service.read_record(repo_id)["status"] == "running"

    storage_service.update_record(
        repo_id,
        status="completed",
        completed_at="2025-01-01T00:05:00+00:00",
    )
    _simulate_restart()

    reread = storage_service.read_record(repo_id)
    assert reread["status"] == "completed"
    assert reread["completed_at"] == "2025-01-01T00:05:00+00:00"
    # Fields written at creation are not lost by the update.
    assert reread["repo_url"] == "https://github.com/org/repo"

    with TestClient(app) as client:
        record = client.get("/transformation-history").json()["records"][0]
    assert record["status"] == "completed"


def test_background_failure_details_are_persisted(tmp_storage):
    """``status``/``completed_at``/``error`` written by the background task hit disk."""
    repo_id = "failed0001"
    _persist(repo_id, status="running")

    storage_service.update_record(
        repo_id,
        status="error",
        error="Git clone failed",
        completed_at="2025-01-01T00:01:00+00:00",
    )
    _simulate_restart()

    record = storage_service.read_record(repo_id)
    assert record["status"] == "error"
    assert record["error"] == "Git clone failed"
    assert record["completed_at"] == "2025-01-01T00:01:00+00:00"


def test_metadata_writes_leave_no_partial_files(tmp_storage):
    """Writes are atomic, so a 0.5 s stream poll can never read a half-written record."""
    repo_id = "atomic0001"
    _persist(repo_id, status="running")
    storage_service.update_record(repo_id, status="completed")

    leftovers = list((tmp_storage / repo_id).glob("*.tmp"))
    assert leftovers == [], f"temp files left behind: {leftovers}"
    assert (tmp_storage / repo_id / "metadata.json").is_file()


# --- 3. Backfill of pre-existing transformations ---


def test_directory_with_a_tree_but_no_metadata_is_recovered(tmp_storage):
    """Transformations that predate persistence stay usable: history, diff, download."""
    repo_id = "legacy0001"
    _make_trees(
        tmp_storage,
        repo_id,
        original={"App.java": "class App {}\n"},
        transformed={"App.java": "class App2 {}\n"},
    )
    assert not (tmp_storage / repo_id / "metadata.json").exists()

    with TestClient(app) as client:
        history = client.get("/transformation-history")
        diff = client.get(f"/diff/{repo_id}")
        download = client.get(f"/download/{repo_id}")

    assert [r["repo_id"] for r in history.json()["records"]] == [repo_id]
    assert diff.status_code == 200
    assert diff.json()["files"][0]["filename"] == "App.java"
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert archive.read("App.java").decode() == "class App2 {}\n"


def test_backfill_marks_unrecoverable_fields_unknown_rather_than_guessing(tmp_storage):
    repo_id = "legacy0002"
    _make_trees(tmp_storage, repo_id, original={"a.txt": "old\n"}, transformed={"a.txt": "new\n"})

    record = storage_service.read_record(repo_id)

    assert record["repo_id"] == repo_id
    assert record["backfilled"] is True
    assert record["status"] == "unknown"
    # Derivable from the filesystem.
    assert record["created_at"], "created_at should come from filesystem timestamps"
    assert record["has_transformed_tree"] is True
    assert record["has_original"] is True
    # Not derivable — explicitly unknown, never invented.
    for field in ("repo_url", "branch", "transformation_type"):
        assert record[field] is None, f"{field} was fabricated: {record[field]!r}"


def test_pr_flow_refuses_a_backfilled_record_instead_of_inventing_a_repo_url(tmp_storage):
    repo_id = "legacy0003"
    _make_trees(tmp_storage, repo_id, original={"a.txt": "old\n"}, transformed={"a.txt": "new\n"})

    with TestClient(app) as client:
        preview = client.get(f"/pr-preview/{repo_id}")
        create = client.post(f"/create-file-pr/{repo_id}")

    for response in (preview, create):
        assert response.status_code == 400, response.text
        assert "repository URL" in response.json()["detail"]


def test_backfill_is_persisted_once_and_is_stable(tmp_storage):
    """Repair on read, idempotent — the second read is served from the written file."""
    repo_id = "legacy0004"
    _make_trees(tmp_storage, repo_id, original={"a.txt": "old\n"}, transformed={"a.txt": "new\n"})

    first = storage_service.read_record(repo_id)
    metadata_file = tmp_storage / repo_id / "metadata.json"
    assert metadata_file.is_file(), "backfill did not persist the reconstruction"

    second = storage_service.read_record(repo_id)
    assert second == first


def test_a_directory_without_a_transformed_tree_is_not_history(tmp_storage):
    """Nothing derivable, nothing to serve — so it is not listed and does not resolve.

    Listing it would produce a row whose diff, download and PR actions all 404.
    """
    orphan = tmp_storage / "orphan001" / "logs"
    orphan.mkdir(parents=True)
    (orphan / "output.log").write_text("[2025-01-01T00:00:00+00:00] boom\n")

    with TestClient(app) as client:
        history = client.get("/transformation-history")
        diff = client.get("/diff/orphan001")

    assert history.json()["records"] == []
    assert diff.status_code == 404


def test_a_recorded_transformation_with_no_tree_is_still_history(tmp_storage):
    """A run that failed before cloning is real history — its record explains itself."""
    repo_id = "notree001"
    _persist(repo_id, status="error", error="Git clone failed")

    with TestClient(app) as client:
        history = client.get("/transformation-history")
        download = client.get(f"/download/{repo_id}")

    assert [r["repo_id"] for r in history.json()["records"]] == [repo_id]
    assert history.json()["records"][0]["status"] == "error"
    # The record resolves; the tree genuinely does not exist.
    assert download.status_code == 404
    assert "failed before cloning" in download.json()["detail"]


# --- 4. Path safety is unchanged by reading from disk ---


@pytest.mark.parametrize("repo_id", ["..", "../etc", "a/b", ".hidden", "with space", ""])
def test_traversal_shaped_ids_never_resolve_to_a_record(tmp_storage, repo_id):
    assert storage_service.read_record(repo_id) is None
    assert storage_service.get_record_dir(repo_id) is None
