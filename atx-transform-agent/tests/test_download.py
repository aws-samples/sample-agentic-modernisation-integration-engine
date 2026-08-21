"""Download contract tests for ``GET /download/{repo_id}``.

The results page's changed-files view is the review surface; the download is the
artefact and must be the *whole* transformed tree. These tests pin: content fidelity
against the on-disk tree, ``.git`` exclusion, the size cap, traversal rejection, and
the missing/unknown cases.
"""

import io
import zipfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from services import download_service, storage_service
from services.download_service import (
    InvalidRepoIdError,
    TreeMissingError,
    TreeTooLargeError,
    resolve_tree,
    stream_tree_zip,
)


@pytest.fixture
def tmp_storage(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    with patch("config.settings.storage_path", str(storage)):
        yield storage


def _register(repo_id: str, status: str = "completed") -> dict:
    """Persist a transformation record the way ``POST /transform`` does."""
    return storage_service.write_record(
        {
            "repo_id": repo_id,
            "status": status,
            "created_at": "2025-01-01T00:00:00+00:00",
            "repo_url": "https://github.com/org/repo",
            "branch": "main",
            "transformation_type": "AWS/java-version-upgrade",
        }
    )


def _make_tree(storage, repo_id: str, files: dict[str, str]):
    tree = storage / repo_id / "repo"
    for rel_path, content in files.items():
        target = tree / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return tree


# --- Content fidelity ---


def test_download_streams_a_zip_of_the_whole_transformed_tree(tmp_storage):
    repo_id = "dl-full"
    _register(repo_id)
    files = {
        "pom.xml": "<project/>\n",
        "src/main/java/App.java": "class App {}\n",
        "src/test/java/AppTest.java": "class AppTest {}\n",
        "README.md": "docs\n",
    }
    _make_tree(tmp_storage, repo_id, files)

    with TestClient(app) as client:
        response = client.get(f"/download/{repo_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert f'filename="transformed-{repo_id}.zip"' in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None, "archive is corrupt"
        assert set(archive.namelist()) == set(files)
        for name, content in files.items():
            assert archive.read(name).decode() == content


def test_download_excludes_git_but_keeps_everything_else(tmp_storage):
    repo_id = "dl-git"
    _register(repo_id)
    _make_tree(
        tmp_storage,
        repo_id,
        {
            "src/App.java": "class App {}\n",
            ".gitignore": "target/\n",
            ".git/HEAD": "ref: refs/heads/main\n",
            ".git/objects/ab/cdef": "binary-ish\n",
        },
    )

    with TestClient(app) as client:
        response = client.get(f"/download/{repo_id}")

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()

    assert not any(name.startswith(".git/") for name in names)
    # .gitignore is repository content, not git metadata.
    assert ".gitignore" in names
    assert "src/App.java" in names


def test_download_is_streamed_not_buffered(tmp_storage):
    """The archive must arrive in multiple chunks, never as one in-memory blob."""
    repo_id = "dl-stream"
    _register(repo_id)
    # Incompressible-ish content across several files so deflate cannot collapse it
    # into a single small chunk.
    payload = "".join(f"line {i} {'x' * 80}\n" for i in range(4000))
    _make_tree(tmp_storage, repo_id, {f"f{i}.txt": payload for i in range(4)})

    chunks = list(stream_tree_zip(repo_id))

    assert len(chunks) > 1, "download did not stream incrementally"
    with zipfile.ZipFile(io.BytesIO(b"".join(chunks))) as archive:
        assert archive.testzip() is None
        assert archive.read("f0.txt").decode() == payload


# --- Bounds ---


def test_download_refuses_an_over_cap_tree_and_names_the_limit(tmp_storage):
    repo_id = "dl-toobig"
    _register(repo_id)
    _make_tree(tmp_storage, repo_id, {"big.bin": "x" * 4096})

    with patch.object(download_service, "MAX_TOTAL_BYTES", 1024):
        with pytest.raises(TreeTooLargeError) as excinfo:
            list(stream_tree_zip(repo_id))

        assert "exceeds" in str(excinfo.value)
        assert "download limit" in str(excinfo.value)

        with TestClient(app) as client:
            response = client.get(f"/download/{repo_id}")

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert "MB download limit" in detail, detail


# --- Failure modes ---


def test_download_404s_on_unknown_repo_id(tmp_storage):
    with TestClient(app) as client:
        assert client.get("/download/unknown123").status_code == 404


def test_download_404s_when_the_tree_is_missing(tmp_storage):
    """A transformation that failed before cloning has no tree to download."""
    repo_id = "dl-notree"
    _register(repo_id, status="error")

    with TestClient(app) as client:
        response = client.get(f"/download/{repo_id}")

    assert response.status_code == 404
    assert "failed before cloning" in response.json()["detail"]


@pytest.mark.parametrize(
    "repo_id",
    ["../etc", "..", "a/../../b", "foo/bar", ".hidden", "with space", "semi;colon", ""],
)
def test_download_rejects_traversal_and_malformed_ids(repo_id):
    with pytest.raises(InvalidRepoIdError):
        resolve_tree(repo_id)


def test_download_endpoint_rejects_a_traversal_attempt(tmp_storage):
    """A traversal attempt is refused before any filesystem access.

    Two shapes, two rejection points, neither of which produces an archive:

    * ``%2E%2E`` decodes to a dot segment that still matches the route, so it reaches
      the handler and is rejected as a malformed identifier — before ``resolve_tree``
      touches the disk.
    * ``%2F`` decodes to a separator, so the single-segment route never matches and
      routing answers 404.
    """
    _register("legit12345")

    with TestClient(app) as client:
        dot_segment = client.get("/download/%2E%2E")
        encoded_slash = client.get("/download/..%2F..%2Fetc")

    assert dot_segment.status_code == 400
    assert "Invalid repo_id" in dot_segment.json()["detail"]

    assert encoded_slash.status_code == 404
    assert encoded_slash.headers.get("content-type", "").startswith("application/json")
    assert "zip" not in encoded_slash.headers.get("content-type", "")


def test_symlink_escaping_the_tree_is_not_archived(tmp_storage):
    repo_id = "dl-symlink"
    _register(repo_id)
    tree = _make_tree(tmp_storage, repo_id, {"App.java": "class App {}\n"})
    secret = tmp_storage.parent / "secret.txt"
    secret.write_text("do not ship me\n")
    (tree / "escape.txt").symlink_to(secret)

    with TestClient(app) as client:
        response = client.get(f"/download/{repo_id}")

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()

    assert "escape.txt" not in names
    assert "App.java" in names


def test_resolve_tree_raises_tree_missing_for_absent_output(tmp_storage):
    with pytest.raises(TreeMissingError):
        resolve_tree("absent123")
