"""JSON persistence with TTL-based cleanup and path traversal protection."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from models import AnalysisListItem

# Valid analysis ID: alphanumeric, dash, underscore only
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

# 7-day TTL in seconds
_TTL_SECONDS: int = 7 * 24 * 60 * 60

# Maximum number of stored analyses
_MAX_ANALYSES: int = 50


class StorageManager:
    """JSON file persistence with TTL and capacity management."""

    def __init__(self, base_path: str = "/app/temp/analyses") -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _validate_id(self, analysis_id: str) -> None:
        """Validate analysis_id against path traversal attacks.

        Raises:
            ValueError: If analysis_id contains unsafe characters.
        """
        if not analysis_id or not _SAFE_ID_PATTERN.match(analysis_id):
            raise ValueError(
                f"Invalid analysis_id: must be alphanumeric, dash, or underscore only. "
                f"Got: {analysis_id!r}"
            )

    def _file_path(self, analysis_id: str) -> Path:
        """Return the storage file path for an analysis ID."""
        self._validate_id(analysis_id)
        return self.base_path / f"{analysis_id}.json"

    def save(self, analysis_id: str, data: dict) -> None:
        """Persist analysis data as JSON.

        Writes to a temp file first, then renames (atomic on POSIX).
        """
        self._validate_id(analysis_id)
        file_path = self._file_path(analysis_id)
        tmp_path = file_path.with_suffix(".json.tmp")

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(str(tmp_path), str(file_path))

    def load(self, analysis_id: str) -> dict | None:
        """Load analysis data from JSON.

        Returns:
            Parsed dict or None if not found.
        """
        self._validate_id(analysis_id)
        file_path = self._file_path(analysis_id)

        if not file_path.exists():
            return None

        with open(file_path, encoding="utf-8") as f:
            return json.load(f)

    def delete(self, analysis_id: str) -> bool:
        """Delete an analysis file.

        Returns:
            True if deleted, False if not found.
        """
        self._validate_id(analysis_id)
        file_path = self._file_path(analysis_id)

        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def list_analyses(self) -> list[AnalysisListItem]:
        """List all stored analyses with metadata.

        Returns:
            List of AnalysisListItem sorted by created_at (newest first).
        """
        items: list[AnalysisListItem] = []

        for file_path in self.base_path.glob("*.json"):
            try:
                with open(file_path, encoding="utf-8") as f:
                    data = json.load(f)

                analysis_id = data.get("analysis_id", file_path.stem)
                source_type = data.get("source_type", "upload")
                source_url = data.get("source_url")
                completed_at = data.get("completed_at", "")
                status = "completed" if completed_at else "processing"

                # Use completed_at or file modification time as created_at
                created_at = completed_at or time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(file_path.stat().st_mtime),
                )

                items.append(
                    AnalysisListItem(
                        analysis_id=analysis_id,
                        source_type=source_type,
                        source_url=source_url,
                        created_at=created_at,
                        status=status,
                    )
                )
            except (json.JSONDecodeError, OSError):
                # Skip corrupted or unreadable files
                continue

        # Sort newest first
        items.sort(key=lambda x: x.created_at, reverse=True)
        return items

    def cleanup(self) -> None:
        """Remove expired analyses (>7 days) and enforce 50-cap limit."""
        now = time.time()
        files_with_mtime: list[tuple[Path, float]] = []

        for file_path in self.base_path.glob("*.json"):
            try:
                mtime = file_path.stat().st_mtime
                files_with_mtime.append((file_path, mtime))
            except OSError:
                continue

        # Remove files older than 7 days
        remaining: list[tuple[Path, float]] = []
        for file_path, mtime in files_with_mtime:
            if now - mtime > _TTL_SECONDS:
                try:
                    file_path.unlink()
                except OSError:
                    pass
            else:
                remaining.append((file_path, mtime))

        # Enforce 50-analysis cap: delete oldest first
        if len(remaining) > _MAX_ANALYSES:
            remaining.sort(key=lambda x: x[1])  # oldest first
            excess = remaining[: len(remaining) - _MAX_ANALYSES]
            for file_path, _ in excess:
                try:
                    file_path.unlink()
                except OSError:
                    pass
