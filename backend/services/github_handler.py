"""GitHub handler — clone repositories with PAT and timeout, SSRF protection."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from urllib.parse import urlparse

import git

logger = logging.getLogger(__name__)

# Allowed hosts for SSRF protection.
_ALLOWED_HOSTS: set[str] = {
    "github.com",
    "www.github.com",
    "gitlab.com",
    "bitbucket.org",
}

# Private IP ranges to block (SSRF protection).
_PRIVATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^10\."),
    re.compile(r"^172\.(1[6-9]|2\d|3[01])\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^127\."),
    re.compile(r"^0\."),
    re.compile(r"^localhost$", re.IGNORECASE),
]

# Default clone timeout in seconds.
_DEFAULT_TIMEOUT: int = 120


class GitHubHandler:
    """Handles git clone operations with PAT authentication and SSRF protection."""

    def __init__(self, base_path: str = "/app/shared_repos") -> None:
        self.base_path = base_path
        os.makedirs(self.base_path, exist_ok=True)

    def clone(
        self,
        repo_url: str,
        branch: str = "main",
        pat_token: str = "",
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> str:
        """Clone a repository and return the local path.

        Args:
            repo_url: Git repository URL.
            branch: Branch to clone.
            pat_token: Personal access token for private repos.
            timeout: Clone timeout in seconds.

        Returns:
            Path to the cloned repository.

        Raises:
            ValueError: If the URL is invalid or blocked by SSRF protection.
            RuntimeError: If clone fails.
        """
        self._validate_url(repo_url)

        clone_url = self._build_clone_url(repo_url, pat_token)
        dest_dir = self._make_dest_dir(repo_url)

        try:
            git.Repo.clone_from(
                clone_url,
                dest_dir,
                branch=branch,
                depth=1,
                kill_after_timeout=timeout,
            )
            logger.info("Cloned %s (branch: %s) to %s", repo_url, branch, dest_dir)
            return dest_dir
        except git.GitCommandError as exc:
            # Clean up on failure.
            if os.path.exists(dest_dir):
                shutil.rmtree(dest_dir, ignore_errors=True)
            raise RuntimeError(f"Git clone failed: {exc}") from exc

    def _validate_url(self, url: str) -> None:
        """Validate URL against SSRF attacks."""
        parsed = urlparse(url)

        if parsed.scheme not in ("https", "http"):
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")

        hostname = parsed.hostname or ""

        # Check against private IP patterns.
        for pattern in _PRIVATE_PATTERNS:
            if pattern.match(hostname):
                raise ValueError(f"URL points to a private/local address: {hostname}")

        # Optionally restrict to known git hosts.
        if hostname and hostname not in _ALLOWED_HOSTS:
            # Allow any non-private host (enterprise GitHub, etc.)
            logger.info("Non-standard git host: %s", hostname)

    def _build_clone_url(self, repo_url: str, pat_token: str) -> str:
        """Inject PAT token into HTTPS URL if provided."""
        if not pat_token:
            return repo_url

        parsed = urlparse(repo_url)
        if parsed.scheme != "https":
            return repo_url

        # Insert token as basic auth: https://<token>@github.com/...
        authed_url = f"https://{pat_token}@{parsed.hostname}{parsed.path}"
        return authed_url

    def _make_dest_dir(self, repo_url: str) -> str:
        """Create a unique destination directory for the clone."""
        parsed = urlparse(repo_url)
        repo_name = parsed.path.rstrip("/").split("/")[-1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]

        dest_dir = tempfile.mkdtemp(prefix=f"{repo_name}_", dir=self.base_path)
        # mkdtemp creates the dir; remove it so git clone can create it.
        os.rmdir(dest_dir)
        return dest_dir

    def cleanup(self, path: str) -> None:
        """Remove a cloned repository directory."""
        if os.path.exists(path) and path.startswith(self.base_path):
            shutil.rmtree(path, ignore_errors=True)
