"""Prompt template directory resolution.

Prompt templates ship inside the backend package (`backend/prompts/`) so they are
always present in the container image, where the Dockerfile copies the contents
of `backend/` into `/app` (making the templates available at `/app/prompts`).

Resolution is candidate-based rather than counting `.parent` hops, so the same
code works in the local dev layout, the container layout, and any future layout
where the templates are mounted elsewhere via the `PROMPTS_DIR` env var.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# backend/utils/prompt_paths.py → backend/
BACKEND_ROOT = Path(__file__).resolve().parent.parent


def candidate_prompt_dirs() -> list[Path]:
    """Return the prompt directories to probe, in priority order."""
    candidates: list[Path] = []

    override = os.getenv("PROMPTS_DIR")
    if override:
        candidates.append(Path(override))

    candidates.extend(
        [
            # Authoritative location: shipped with the backend package.
            BACKEND_ROOT / "prompts",
            # Legacy repo-root layout (local checkouts predating the move).
            BACKEND_ROOT.parent / "prompts",
            # Container layout, in case the backend package is not the WORKDIR.
            Path("/app/prompts"),
        ]
    )

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def resolve_prompts_dir() -> Path:
    """Return the first existing prompt directory.

    Falls back to the authoritative location if none exist, so callers always
    get a usable path for error messages and globbing.
    """
    for candidate in candidate_prompt_dirs():
        if candidate.is_dir():
            return candidate
    return BACKEND_ROOT / "prompts"


def find_prompt_file(name: str) -> tuple[Path | None, list[Path]]:
    """Locate a prompt template by name.

    Args:
        name: Template name without the `.md` extension.

    Returns:
        Tuple of (path or None if not found, list of paths that were tried).
    """
    tried: list[Path] = []
    for directory in candidate_prompt_dirs():
        path = directory / f"{name}.md"
        tried.append(path)
        if path.is_file():
            return path, tried
    return None, tried
