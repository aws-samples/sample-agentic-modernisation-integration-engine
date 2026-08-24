"""Docker-in-Docker or git clone fallback for repository preparation."""

import logging
import shutil
import subprocess
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


def is_docker_available() -> bool:
    """Check if Docker is available in the environment."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def clone_repository(repo_url: str, target_path: Path, branch: str | None = None) -> Path:
    """Clone a repository using git (fallback when Docker is not available).

    Args:
        repo_url: The Git repository URL.
        target_path: The local directory to clone into.
        branch: Optional branch to checkout.

    Returns:
        Path to the cloned repository.
    """
    target_path.mkdir(parents=True, exist_ok=True)

    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd.extend(["--branch", branch])

    # Inject PAT for private repos if available
    authenticated_url = repo_url
    if settings.github_pat and "github.com" in repo_url:
        authenticated_url = repo_url.replace(
            "https://github.com",
            f"https://{settings.github_pat}@github.com",
        )

    cmd.extend([authenticated_url, str(target_path)])

    logger.info(f"Cloning repository to {target_path}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        raise RuntimeError(f"Git clone failed: {result.stderr}")

    return target_path


def prepare_repository(repo_url: str, repo_id: str, branch: str | None = None) -> Path:
    """Prepare a repository for transformation.

    Attempts Docker-in-Docker first, falls back to git clone.

    Args:
        repo_url: The Git repository URL.
        repo_id: Unique identifier for this transformation.
        branch: Optional branch to checkout.

    Returns:
        Path to the prepared repository.
    """
    repo_path = Path(settings.storage_path) / repo_id / "repo"

    if repo_path.exists():
        shutil.rmtree(repo_path)

    if is_docker_available():
        logger.info("Docker available — using Docker-in-Docker for repo preparation")
        # Docker-based clone with isolation
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{repo_path}:/workspace",
                    "alpine/git:latest",
                    "clone",
                    "--depth",
                    "1",
                    *(["--branch", branch] if branch else []),
                    repo_url,
                    "/workspace",
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            return repo_path
        except (subprocess.TimeoutExpired, RuntimeError) as e:
            logger.warning(f"Docker clone failed, falling back to git: {e}")

    # Fallback: direct git clone
    return clone_repository(repo_url, repo_path, branch)
