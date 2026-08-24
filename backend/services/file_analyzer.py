"""File analyzer service — directory walk, language classification, file stats."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class FileTypeStat:
    """Statistics for a single file extension."""

    extension: str
    count: int = 0
    total_lines: int = 0
    total_size: int = 0


@dataclass
class FolderNode:
    """Recursive tree representation of a directory structure."""

    name: str
    type: str = "directory"  # "directory" or "file"
    children: list["FolderNode"] = field(default_factory=list)
    size: int | None = None


class FileAnalyzer:
    """Walks directories, classifies files by extension, produces stats and tree."""

    # Extensions to skip during analysis.
    _SKIP_DIRS: set[str] = {
        ".git",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".idea",
        ".vs",
        "bin",
        "obj",
        "target",
        "build",
        "dist",
    }

    def analyze(self, root_path: str) -> tuple[list[FileTypeStat], FolderNode]:
        """Analyze a directory and return file stats and folder tree.

        Args:
            root_path: Path to the root directory to analyze.

        Returns:
            Tuple of (file_stats, folder_structure).
        """
        stats: dict[str, FileTypeStat] = {}
        folder_tree = self._build_tree(root_path, stats)
        return sorted(stats.values(), key=lambda s: s.count, reverse=True), folder_tree

    def _build_tree(
        self,
        path: str,
        stats: dict[str, FileTypeStat],
    ) -> FolderNode:
        """Recursively build a folder tree and accumulate file stats."""
        name = os.path.basename(path) or path
        node = FolderNode(name=name, type="directory")

        try:
            entries = sorted(os.listdir(path))
        except PermissionError:
            return node

        for entry in entries:
            if entry in self._SKIP_DIRS:
                continue

            full_path = os.path.join(path, entry)

            if os.path.isdir(full_path):
                child = self._build_tree(full_path, stats)
                node.children.append(child)
            elif os.path.isfile(full_path):
                ext = os.path.splitext(entry)[1].lower() or "(no ext)"
                try:
                    size = os.path.getsize(full_path)
                except OSError:
                    size = 0

                line_count = self._count_lines(full_path)

                if ext not in stats:
                    stats[ext] = FileTypeStat(extension=ext)
                stats[ext].count += 1
                stats[ext].total_lines += line_count
                stats[ext].total_size += size

                file_node = FolderNode(name=entry, type="file", size=size)
                node.children.append(file_node)

        return node

    def _count_lines(self, file_path: str) -> int:
        """Count lines in a file. Returns 0 for binary/unreadable files."""
        try:
            with open(file_path, "rb") as f:
                # Quick binary check — look for null bytes in first 8KB.
                chunk = f.read(8192)
                if b"\x00" in chunk:
                    return 0

            with open(file_path, encoding="utf-8", errors="ignore") as f:
                return sum(1 for _ in f)
        except (OSError, UnicodeDecodeError):
            return 0
