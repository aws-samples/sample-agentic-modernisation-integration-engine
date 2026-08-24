"""GitHub PR creation service using Personal Access Token."""

import logging
import re
from pathlib import Path

import httpx

from config import settings

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"

#: A single GitHub path segment (owner or repo name): letters, digits, and the
#: limited punctuation GitHub allows, no separators or dot-segments.
_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")

#: A safe git branch/ref name. Deliberately excludes a leading "-" (which git
#: would treat as an option — argument injection), whitespace, and shell/ref
#: metacharacters. Segments joined by single "/" are allowed (e.g.
#: "atx-transform/<id>").
_GIT_REF_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]{0,200}")


def _validate_git_ref(ref: str) -> str:
    """Return ``ref`` if it is a safe git branch name, else raise ValueError.

    Guards the subprocess git calls (CWE-78 argument injection): a ref starting
    with "-" or containing metacharacters must never reach the git argv, even
    though the commands are already list-form (no shell).
    """
    if not ref or ".." in ref or not _GIT_REF_RE.fullmatch(ref):
        raise ValueError(f"Unsafe git ref: {ref!r}")
    return ref


def _redact_url(value: str) -> str:
    """Redact userinfo (e.g. an embedded PAT) from a URL before logging.

    ``https://<token>@github.com/...`` becomes ``https://[REDACTED]@github.com/...``
    (CWE-532). Non-URL values are returned unchanged.
    """
    if "://" in value and "@" in value:
        proto, rest = value.split("://", 1)
        return f"{proto}://[REDACTED]@{rest.split('@', 1)[1]}"
    return value


def _redact_cmd(cmd: list[str]) -> str:
    """Render a git argv for logging with any credential-bearing URL redacted."""
    return " ".join(_redact_url(part) for part in cmd)


def _get_headers() -> dict[str, str]:
    """Get GitHub API headers with PAT authentication."""
    if not settings.github_pat:
        raise ValueError("GitHub PAT is not configured (set ATX_TRANSFORM_GITHUB_PAT)")
    return {
        "Authorization": f"Bearer {settings.github_pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _parse_repo_url(repo_url: str) -> tuple[str, str]:
    """Extract owner and repo name from a GitHub URL.

    Args:
        repo_url: GitHub repository URL (https://github.com/owner/repo or similar).

    Returns:
        Tuple of (owner, repo_name).
    """
    # Strip .git suffix and trailing slashes
    url = repo_url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]

    parts = url.split("/")
    # Expect: ['https:', '', 'github.com', 'owner', 'repo']
    if len(parts) < 5:
        raise ValueError(f"Invalid GitHub URL: {repo_url}")

    owner = parts[-2]
    repo = parts[-1]
    # Validate the extracted segments (CWE-918 / defense-in-depth): owner and
    # repo are interpolated into the api.github.com request path, so they must
    # be single, well-formed GitHub path segments — no slashes, no dots that
    # could alter the request target.
    if not _SEGMENT_RE.fullmatch(owner) or not _SEGMENT_RE.fullmatch(repo):
        raise ValueError(f"Invalid GitHub URL: {repo_url}")
    return owner, repo


def get_pr_preview(repo_id: str, repo_url: str, branch: str | None = None) -> dict:
    """Preview PR information before creation.

    Args:
        repo_id: Unique transformation identifier.
        repo_url: GitHub repository URL.
        branch: Target branch for the PR.

    Returns:
        Dict with PR preview info (title, body, base branch, head branch).
    """
    owner, repo_name = _parse_repo_url(repo_url)
    head_branch = f"atx-transform/{repo_id}"
    base_branch = branch or "main"

    return {
        "repo_id": repo_id,
        "owner": owner,
        "repo": repo_name,
        "title": f"ATX Transform: {repo_id}",
        "body": f"Automated code transformation applied by ATX Transform Agent.\n\nTransformation ID: `{repo_id}`",
        "head": head_branch,
        "base": base_branch,
        "url_preview": f"https://github.com/{owner}/{repo_name}/compare/{base_branch}...{head_branch}",
    }


def create_pr(repo_id: str, repo_url: str, branch: str | None = None) -> dict:
    """Create a GitHub Pull Request with transformation changes.

    Pushes transformed code to a new branch and creates a PR.

    Args:
        repo_id: Unique transformation identifier.
        repo_url: GitHub repository URL.
        branch: Target branch for the PR base.

    Returns:
        Dict with PR details (url, number, state).
    """
    owner, repo_name = _parse_repo_url(repo_url)
    base_branch = branch or "main"
    head_branch = f"atx-transform/{repo_id}"
    repo_path = Path(settings.storage_path) / repo_id / "repo"

    if not repo_path.exists():
        raise FileNotFoundError(f"Repository path not found for {repo_id}")

    # Push changes to the new branch
    _push_branch(repo_path, head_branch, repo_url)

    # Create the PR via GitHub API
    pr_data = {
        "title": f"ATX Transform: {repo_id}",
        "body": (
            f"Automated code transformation applied by ATX Transform Agent.\n\n"
            f"**Transformation ID:** `{repo_id}`\n\n"
            f"Please review the changes and merge if satisfactory."
        ),
        "head": head_branch,
        "base": base_branch,
    }

    response = httpx.post(
        f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/pulls",
        headers=_get_headers(),
        json=pr_data,
        timeout=30,
    )

    if response.status_code == 201:
        pr = response.json()
        return {
            "repo_id": repo_id,
            "pr_url": pr["html_url"],
            "pr_number": pr["number"],
            "state": pr["state"],
            "created": True,
        }
    else:
        error_detail = (
            response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        )
        raise RuntimeError(
            f"GitHub PR creation failed ({response.status_code}): " f"{error_detail.get('message', response.text)}"
        )


def list_branches(repo_url: str) -> list[dict]:
    """List branches for a GitHub repository.

    Args:
        repo_url: GitHub repository URL.

    Returns:
        List of branch info dicts.
    """
    owner, repo_name = _parse_repo_url(repo_url)

    response = httpx.get(
        f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/branches",
        headers=_get_headers(),
        timeout=15,
    )

    if response.status_code == 200:
        branches = response.json()
        return [{"name": b["name"], "protected": b.get("protected", False)} for b in branches]
    else:
        logger.warning(f"Failed to list branches: {response.status_code}")
        return []


def _push_branch(repo_path: Path, branch_name: str, repo_url: str) -> None:
    """Push local changes to a new remote branch.

    Args:
        repo_path: Local repository path.
        branch_name: Name of the new branch to push.
        repo_url: Remote repository URL.
    """
    import subprocess

    # Validate the branch name before it reaches the git argv (CWE-78): reject a
    # leading "-" or any ref metacharacter so it cannot be interpreted as a git
    # option or otherwise manipulate the command.
    branch_name = _validate_git_ref(branch_name)

    # Configure push URL with PAT
    push_url = repo_url
    if settings.github_pat and "github.com" in repo_url:
        push_url = repo_url.replace(
            "https://github.com",
            f"https://{settings.github_pat}@github.com",
        )

    commands = [
        ["git", "checkout", "-b", branch_name],
        ["git", "add", "-A"],
        ["git", "commit", "-m", f"ATX Transform: automated code transformation ({branch_name})"],
        # "--" ends option parsing so the URL/branch cannot be read as flags.
        ["git", "push", "--", push_url, branch_name],
    ]

    for cmd in commands:
        result = subprocess.run(cmd, cwd=str(repo_path), capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            # git add or commit may fail if no changes — that's okay for add
            if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                continue
            # Redact any embedded PAT from both the command and git's stderr
            # before logging (CWE-532): the push URL carries the token.
            safe_stderr = result.stderr
            if settings.github_pat and settings.github_pat in safe_stderr:
                safe_stderr = safe_stderr.replace(settings.github_pat, "[REDACTED]")
            logger.warning(f"Git command failed: {_redact_cmd(cmd)} — {safe_stderr}")
