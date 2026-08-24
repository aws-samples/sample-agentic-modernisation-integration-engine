"""Repository service — validates and prepares repository paths for ATX analysis.

The ATX CLI is invoked as ``atx custom def exec ... -p <project_path>``, where
``-p`` must point at a **local** project directory. A remote URL therefore has
to be cloned to local disk before the CLI is started.

SSRF posture mirrors ``backend/services/github_handler.py``: https/http only,
private/link-local hosts rejected, PAT injected as basic-auth userinfo.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from config import settings

logger = logging.getLogger(__name__)

# Prefixes that mark a value as a remote repository URL rather than a local path.
_REMOTE_PREFIXES = ("http://", "https://", "git@")

# Schemes we are willing to clone from.
_ALLOWED_SCHEMES = {"http", "https"}

# Private / loopback / link-local ranges blocked for SSRF protection.
_PRIVATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^10\."),
    re.compile(r"^172\.(1[6-9]|2\d|3[01])\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^169\.254\."),
    re.compile(r"^127\."),
    re.compile(r"^0\."),
    re.compile(r"^localhost$", re.IGNORECASE),
    re.compile(r"^\[?::1\]?$"),
]

# Clone timeout in seconds.
_CLONE_TIMEOUT = 180


def is_remote_url(repo_url: str) -> bool:
    """Return True when the value looks like a remote repository URL."""
    return repo_url.startswith(_REMOTE_PREFIXES)


def validate_repo_path(repo_url: str) -> str:
    """Validate that a repository path/URL is usable for ATX analysis.

    For local paths: checks the directory exists.
    For remote URLs: applies scheme/host validation (SSRF protection).

    Returns the value unchanged. Use :func:`prepare_repository` to obtain a
    local path suitable for the ATX CLI's ``-p`` flag.

    Raises:
        FileNotFoundError: local path does not exist.
        ValueError: local path is not a directory, or the URL is unusable.
    """
    if is_remote_url(repo_url):
        _validate_remote_url(repo_url)
        return repo_url

    # Local path branch. Resolve to an absolute path and reject raw parent
    # traversal segments so a value like "../../etc" cannot be used to probe
    # arbitrary locations (CWE-22). Legitimate absolute local project dirs
    # remain usable.
    if ".." in Path(repo_url).parts:
        raise ValueError(f"Repository path must not contain '..': {repo_url}")
    path = Path(repo_url).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Repository path does not exist: {repo_url}")
    if not path.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo_url}")
    return repo_url


def prepare_repository(
    repo_url: str,
    conversation_id: str,
    branch: str | None = None,
    pat_token: str | None = None,
) -> str:
    """Return a local project directory for ATX analysis, cloning if needed.

    Local paths are validated and returned as-is. Remote URLs are cloned into
    ``<storage_path>/<conversation_id>/repo``.

    Raises:
        FileNotFoundError: local path does not exist.
        ValueError: path/URL is unusable (bad scheme, private host, SSH URL).
        RuntimeError: the clone failed.
    """
    validate_repo_path(repo_url)

    if not is_remote_url(repo_url):
        return repo_url

    target_path = Path(settings.storage_path) / conversation_id / "repo"
    if target_path.exists():
        shutil.rmtree(target_path, ignore_errors=True)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    return str(clone_repository(repo_url, target_path, branch, pat_token))


def clone_repository(
    repo_url: str,
    target_path: Path,
    branch: str | None = None,
    pat_token: str | None = None,
) -> Path:
    """Shallow-clone ``repo_url`` into ``target_path`` and return that path.

    Follows the same shape as ``atx-transform-agent`` ``clone_repository``:
    ``git clone --depth 1 [--branch <branch>] <url> <target>`` with the PAT
    injected as basic-auth userinfo for private repositories.
    """
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd.extend(["--branch", branch])

    token = pat_token or settings.github_pat or None
    cmd.extend([_build_clone_url(repo_url, token), str(target_path)])

    logger.info("Cloning %s (branch: %s) to %s", repo_url, branch or "default", target_path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_CLONE_TIMEOUT)
    except FileNotFoundError as exc:
        raise RuntimeError("git is not available in this environment") from exc
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(target_path, ignore_errors=True)
        raise RuntimeError(f"Git clone timed out after {_CLONE_TIMEOUT}s: {repo_url}") from exc

    if result.returncode != 0:
        shutil.rmtree(target_path, ignore_errors=True)
        raise RuntimeError(f"Git clone failed: {_redact(result.stderr.strip(), token)}")

    return target_path


def get_repo_name(repo_url: str) -> str:
    """Extract a short repository name from URL or path."""
    # Strip trailing slashes and .git suffix
    cleaned = repo_url.rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    return os.path.basename(cleaned) or "unknown"


# --- Internal helpers ---


def _validate_remote_url(repo_url: str) -> None:
    """Reject URLs we cannot or should not clone (SSRF protection)."""
    if repo_url.startswith("git@"):
        raise ValueError("SSH repository URLs are not supported; use an https:// URL")

    parsed = urlparse(repo_url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")

    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError(f"Repository URL has no host: {repo_url}")

    for pattern in _PRIVATE_PATTERNS:
        if pattern.match(hostname):
            raise ValueError(f"URL points to a private/local address: {hostname}")

    if not parsed.path.strip("/"):
        raise ValueError(f"Repository URL has no path: {repo_url}")


def _build_clone_url(repo_url: str, pat_token: str | None) -> str:
    """Inject the PAT as basic-auth userinfo for https URLs."""
    if not pat_token:
        return repo_url

    parsed = urlparse(repo_url)
    if parsed.scheme != "https":
        return repo_url

    return f"https://{pat_token}@{parsed.hostname}{parsed.path}"


def _redact(message: str, pat_token: str | None) -> str:
    """Remove a PAT from git output before it reaches logs or clients."""
    if pat_token and pat_token in message:
        return message.replace(pat_token, "***")
    return message
