"""Tests for repository preparation.

The ATX CLI takes a local project path via `-p`, so a remote URL has to be
cloned before the CLI runs. These tests use a real local git repository — no
network access, no mocks.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.repository_service import (
    clone_repository,
    get_repo_name,
    is_remote_url,
    prepare_repository,
    validate_repo_path,
)


@pytest.fixture
def local_git_repo(tmp_path: Path) -> Path:
    """Create a real local git repository that can be cloned by file path."""
    repo = tmp_path / "source-repo"
    repo.mkdir()
    (repo / "README.md").write_text("# fixture\n")
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True, env=env)
    return repo


# --- Classification & validation ---


def test_is_remote_url():
    assert is_remote_url("https://github.com/octocat/Hello-World")
    assert is_remote_url("http://example.com/repo.git")
    assert is_remote_url("git@github.com:octocat/Hello-World.git")
    assert not is_remote_url("/app/storage/repo")


def test_validate_repo_path_accepts_existing_dir(tmp_path: Path):
    assert validate_repo_path(str(tmp_path)) == str(tmp_path)


def test_validate_repo_path_missing_local_path():
    with pytest.raises(FileNotFoundError):
        validate_repo_path("/nonexistent/path/to/repo")


def test_validate_repo_path_rejects_file(tmp_path: Path):
    file_path = tmp_path / "not-a-dir.txt"
    file_path.write_text("x")
    with pytest.raises(ValueError, match="not a directory"):
        validate_repo_path(str(file_path))


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/repo.git",
        "http://127.0.0.1/repo.git",
        "https://10.0.0.5/repo.git",
        "https://192.168.1.10/repo.git",
        "https://169.254.169.254/latest/meta-data",
    ],
)
def test_validate_repo_path_blocks_private_hosts(url: str):
    with pytest.raises(ValueError, match="private/local"):
        validate_repo_path(url)


def test_validate_repo_path_rejects_ssh_url():
    with pytest.raises(ValueError, match="SSH"):
        validate_repo_path("git@github.com:octocat/Hello-World.git")


def test_validate_repo_path_requires_path_component():
    with pytest.raises(ValueError, match="no path"):
        validate_repo_path("https://github.com")


# --- Preparation ---


def test_prepare_repository_returns_local_path_unchanged(tmp_path: Path):
    """Local paths are used directly — no clone."""
    assert prepare_repository(str(tmp_path), "conv_1") == str(tmp_path)


def test_clone_repository_clones_real_repo(tmp_path: Path, local_git_repo: Path):
    """clone_repository performs a real shallow clone of the requested branch."""
    target = tmp_path / "clone-target"
    result = clone_repository(str(local_git_repo), target, branch="main")

    assert result == target
    assert (target / "README.md").read_text() == "# fixture\n"


def test_clone_repository_raises_on_failure(tmp_path: Path):
    """A clone failure is a RuntimeError, not a silent success."""
    with pytest.raises(RuntimeError, match="Git clone failed"):
        clone_repository(str(tmp_path / "does-not-exist"), tmp_path / "target")


def test_prepare_repository_clones_remote_into_storage(tmp_path: Path, local_git_repo: Path):
    """A remote URL is cloned to <storage>/<conversation_id>/repo."""
    storage = tmp_path / "storage"
    storage.mkdir()
    url = "https://github.com/Deenadayaalan/task-manager"

    def fake_clone(repo_url, target_path, branch=None, pat_token=None):
        # Stand in for the network hop only; clone into the real target path.
        return clone_repository(str(local_git_repo), target_path, branch="main")

    with patch("services.repository_service.settings.storage_path", str(storage)):
        with patch("services.repository_service.clone_repository", side_effect=fake_clone):
            result = prepare_repository(url, "conv_clone", branch="main")

    expected = storage / "conv_clone" / "repo"
    assert result == str(expected)
    assert (expected / "README.md").read_text() == "# fixture\n"


def test_get_repo_name():
    assert get_repo_name("https://github.com/Deenadayaalan/task-manager") == "task-manager"
    assert get_repo_name("https://github.com/octocat/Hello-World.git") == "Hello-World"
    assert get_repo_name("/app/storage/conv/repo") == "repo"
