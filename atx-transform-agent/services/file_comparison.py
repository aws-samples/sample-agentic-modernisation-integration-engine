"""File comparison service — paired line-by-line diffs for the transform results page.

Response contract (``GET /diff/{repo_id}``)
-------------------------------------------
Per changed file::

    {
      "filename": "src/main/java/App.java",   # what the renderer keys its file list on
      "status": "added" | "modified" | "deleted",
      "category": "source" | "documentation",
      "lines": [
        {"type": "added" | "removed" | "unchanged",
         "content": "...",
         "old_line_number": 12 | None,
         "new_line_number": 13 | None},
        ...
      ],
      "truncated": true            # only when the file's lines were capped
    }

``filename``/``lines[]`` is what ``EnhancedFileComparison`` actually consumes (via
``AtxTransformPage``'s snake_case → camelCase normalisation). The previous payload
returned ``path`` plus ``before``/``after``/``diff``, so the page's ``file.filename``
was always undefined (rendered "unknown") and ``file.lines`` was never an array
(rendered as zero lines) — every file looked empty and nothing complained. Pairing
the lines here with ``difflib`` rather than parsing unified-diff text in TypeScript
keeps the line numbering authoritative on one side.

``before``/``after``/``diff`` are gone: they were three copies of the same content and
are redundant once ``lines[]`` exists.

Categories
----------
A transformation can change source, generate documentation, or both, and those answer
different questions. An analysis-type definition such as
``AWS/comprehensive-codebase-analysis`` writes an ``ATXDocumentation/`` tree and edits
no source at all — a correct, complete result that nonetheless reads as "where is my
code?" when presented as an undifferentiated list of 32 markdown files.

So every entry carries a ``category``:

* ``documentation`` — the ``ATXDocumentation/`` tree, plus any other *added* markdown.
  This is output the CLI generated, not a change to the codebase under review.
* ``source`` — everything else, including a *modified* markdown file, because editing
  a README that already existed is a change to the repository.

``get_diff_summary`` reports both counts (and their line totals) separately, so a caller
can say "0 source files changed, 32 documentation files generated" instead of implying
the source edits went missing. Both categories stay in the payload and stay viewable —
this is a grouping, not a filter.

Unchanged files are excluded from the per-file payload — they are the bulk of any
repository and the renderer has nothing to show for them. They are still counted by
``get_diff_summary``, which is why ``status`` is retained.

Bounds
------
Rendering a diff is a review surface, not a bulk export, so the payload is bounded:

* ``MAX_LINES_PER_FILE`` (2000) — a longer file is truncated and flagged.
* ``MAX_TOTAL_LINES`` (20000) — once the whole response reaches this, further files
  are still listed (name + status) with an empty ``lines[]`` and ``truncated: true``.
* ``MAX_CHANGED_FILES`` (300) — files beyond this are omitted entirely and counted in
  the top-level ``omitted_files``.
* ``MAX_FILE_BYTES`` (500_000, unchanged) — per-file read cap.

The whole tree is available losslessly from ``GET /download/{repo_id}``, so
truncation here costs nothing that cannot be recovered.
"""

import difflib
import logging
from pathlib import Path, PurePosixPath

from config import settings

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 500_000
MAX_LINES_PER_FILE = 2_000
MAX_TOTAL_LINES = 20_000
MAX_CHANGED_FILES = 300

#: Directory the ATX CLI writes generated documentation into.
DOCUMENTATION_DIR = "ATXDocumentation"
DOCUMENTATION_SUFFIXES = {".md", ".markdown"}

CATEGORY_SOURCE = "source"
CATEGORY_DOCUMENTATION = "documentation"


def classify_category(filename: str, status: str) -> str:
    """Classify a changed file as generated documentation or a source edit.

    Args:
        filename: POSIX-style path relative to the repository root.
        status: ``"added"``, ``"modified"``, ``"deleted"`` or ``"unchanged"``.

    Returns:
        ``"documentation"`` for the ``ATXDocumentation/`` tree and for newly added
        markdown; ``"source"`` for everything else.

    A *modified* markdown file is source: the repository already had it, and editing
    it is a change to the codebase. Only newly written markdown counts as generated
    output. The directory is matched on any path component, not just the first, so a
    tree written inside a sub-project (``backend/ATXDocumentation/...``) is recognised.
    """
    path = PurePosixPath(filename)
    if DOCUMENTATION_DIR in path.parts:
        return CATEGORY_DOCUMENTATION
    if status == "added" and path.suffix.lower() in DOCUMENTATION_SUFFIXES:
        return CATEGORY_DOCUMENTATION
    return CATEGORY_SOURCE


def get_file_diff(repo_id: str) -> dict:
    """Generate paired line-by-line diffs for a completed transformation.

    Args:
        repo_id: Unique identifier for the transformation.

    Returns:
        ``{"repo_id": str, "files": [...], "truncated": bool, "omitted_files": int}``
        where each file carries ``filename``, ``status``, ``category`` and ``lines``.
        Only changed files appear; unchanged files are counted by
        :func:`get_diff_summary`.
    """
    statuses = _collect_statuses(repo_id)
    if statuses.get("error"):
        return {"repo_id": repo_id, "files": [], "error": statuses["error"], "truncated": False, "omitted_files": 0}

    changed = [entry for entry in statuses["entries"] if entry["status"] != "unchanged"]

    files: list[dict] = []
    total_lines = 0
    truncated = False
    omitted = 0

    for entry in changed:
        if len(files) >= MAX_CHANGED_FILES:
            omitted += 1
            truncated = True
            continue

        if total_lines >= MAX_TOTAL_LINES:
            # Keep the file visible in the file list, but stop shipping content.
            files.append(
                {
                    "filename": entry["filename"],
                    "status": entry["status"],
                    "category": entry["category"],
                    "lines": [],
                    "truncated": True,
                }
            )
            truncated = True
            continue

        budget = min(MAX_LINES_PER_FILE, MAX_TOTAL_LINES - total_lines)
        lines, file_truncated = _build_lines(entry["before"], entry["after"], entry["status"], budget)
        total_lines += len(lines)
        truncated = truncated or file_truncated

        payload = {
            "filename": entry["filename"],
            "status": entry["status"],
            "category": entry["category"],
            "lines": lines,
        }
        if file_truncated:
            payload["truncated"] = True
        files.append(payload)

    return {"repo_id": repo_id, "files": files, "truncated": truncated, "omitted_files": omitted}


def get_diff_summary(repo_id: str) -> dict:
    """Summarise a transformation's changes.

    Counts every file the comparison saw — including unchanged ones, which the diff
    payload omits — plus added/removed line totals so the results page header can
    report them.

    The same changed files are also counted per category, so a caller can distinguish
    "this run generated documentation and touched no code" from "this run's source
    changes are missing". Both counts are always present, including when one is zero.

    Args:
        repo_id: Unique identifier for the transformation.

    Returns:
        Summary dict with per-status file counts, line counts, and the per-category
        breakdown (``source_files_changed``, ``documentation_files_changed``,
        ``changed_by_category``).
    """
    statuses = _collect_statuses(repo_id)
    if statuses.get("error"):
        return {
            "repo_id": repo_id,
            "total_files": 0,
            "changed_files": 0,
            "added": 0,
            "modified": 0,
            "deleted": 0,
            "unchanged": 0,
            "additions": 0,
            "deletions": 0,
            "source_files_changed": 0,
            "documentation_files_changed": 0,
            "changed_by_category": _empty_category_totals(),
            "has_changes": False,
            "error": statuses["error"],
        }

    entries = statuses["entries"]
    added = sum(1 for e in entries if e["status"] == "added")
    modified = sum(1 for e in entries if e["status"] == "modified")
    deleted = sum(1 for e in entries if e["status"] == "deleted")
    unchanged = sum(1 for e in entries if e["status"] == "unchanged")

    additions = 0
    deletions = 0
    by_category = _empty_category_totals()
    for entry in entries:
        if entry["status"] == "unchanged":
            continue
        # Line counts are computed without the payload caps: a truncated view must
        # not understate how much actually changed.
        lines, _ = _build_lines(entry["before"], entry["after"], entry["status"], None)
        file_additions = sum(1 for line in lines if line["type"] == "added")
        file_deletions = sum(1 for line in lines if line["type"] == "removed")
        additions += file_additions
        deletions += file_deletions

        bucket = by_category[entry["category"]]
        bucket["files"] += 1
        bucket["additions"] += file_additions
        bucket["deletions"] += file_deletions

    return {
        "repo_id": repo_id,
        "total_files": len(entries),
        "changed_files": added + modified + deleted,
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unchanged": unchanged,
        "additions": additions,
        "deletions": deletions,
        "source_files_changed": by_category[CATEGORY_SOURCE]["files"],
        "documentation_files_changed": by_category[CATEGORY_DOCUMENTATION]["files"],
        "changed_by_category": by_category,
        "has_changes": (added + modified + deleted) > 0,
    }


def _empty_category_totals() -> dict:
    """Zeroed per-category totals.

    Both keys always exist: "no source files changed" is a fact worth stating, and a
    missing key would let a caller render it as unknown instead.
    """
    return {
        CATEGORY_SOURCE: {"files": 0, "additions": 0, "deletions": 0},
        CATEGORY_DOCUMENTATION: {"files": 0, "additions": 0, "deletions": 0},
    }


def _collect_statuses(repo_id: str) -> dict:
    """Walk the original/transformed trees once and classify every file."""
    base_path = Path(settings.storage_path) / repo_id
    original_path = base_path / "original"
    transformed_path = base_path / "repo"

    if not transformed_path.exists():
        return {"entries": [], "error": "Transformation output not found"}

    if original_path.exists():
        entries = _compare_directories(original_path, transformed_path)
    else:
        entries = _list_as_added(transformed_path)

    return {"entries": entries, "error": None}


def _compare_directories(original: Path, transformed: Path) -> list[dict]:
    """Compare two directory trees, returning one classified entry per file."""
    original_files = {p.relative_to(original) for p in original.rglob("*") if p.is_file() and ".git" not in p.parts}
    transformed_files = {
        p.relative_to(transformed) for p in transformed.rglob("*") if p.is_file() and ".git" not in p.parts
    }

    entries = []
    for rel_path in sorted(original_files | transformed_files):
        filename = rel_path.as_posix()
        orig_file = original / rel_path
        trans_file = transformed / rel_path

        if rel_path in original_files and rel_path not in transformed_files:
            entries.append(_entry(filename, "deleted", _safe_read(orig_file), None))
        elif rel_path not in original_files and rel_path in transformed_files:
            entries.append(_entry(filename, "added", None, _safe_read(trans_file)))
        else:
            before = _safe_read(orig_file)
            after = _safe_read(trans_file)
            status = "unchanged" if before == after else "modified"
            entries.append(_entry(filename, status, before, after))

    return entries


def _entry(filename: str, status: str, before: str | None, after: str | None) -> dict:
    """One classified file, with its category derived from path and status."""
    return {
        "filename": filename,
        "status": status,
        "category": classify_category(filename, status),
        "before": before,
        "after": after,
    }


def _list_as_added(directory: Path) -> list[dict]:
    """Classify every file in a directory as 'added' (no original to compare)."""
    entries = []
    for file_path in sorted(directory.rglob("*")):
        if file_path.is_file() and ".git" not in file_path.parts:
            entries.append(_entry(file_path.relative_to(directory).as_posix(), "added", None, _safe_read(file_path)))
    return entries


def _build_lines(
    before: str | None,
    after: str | None,
    status: str,
    budget: int | None,
) -> tuple[list[dict], bool]:
    """Pair up before/after into renderable diff lines.

    ``budget`` caps how many lines are returned (``None`` = uncapped, used for the
    summary's line counts). Returns ``(lines, truncated)``.

    For a modified file the full file is emitted — unchanged lines included — because
    the renderer collapses runs of unchanged lines into "show N unchanged lines"
    sections and needs them to do so.
    """
    before_lines = (before or "").splitlines()
    after_lines = (after or "").splitlines()

    lines: list[dict] = []

    if status == "added":
        for index, content in enumerate(after_lines, start=1):
            lines.append({"type": "added", "content": content, "old_line_number": None, "new_line_number": index})
    elif status == "deleted":
        for index, content in enumerate(before_lines, start=1):
            lines.append({"type": "removed", "content": content, "old_line_number": index, "new_line_number": None})
    else:
        matcher = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for offset in range(i2 - i1):
                    lines.append(
                        {
                            "type": "unchanged",
                            "content": before_lines[i1 + offset],
                            "old_line_number": i1 + offset + 1,
                            "new_line_number": j1 + offset + 1,
                        }
                    )
                continue
            # 'replace' emits the removed block then the added block, so a modified
            # file always yields both line types.
            if tag in ("delete", "replace"):
                for index in range(i1, i2):
                    lines.append(
                        {
                            "type": "removed",
                            "content": before_lines[index],
                            "old_line_number": index + 1,
                            "new_line_number": None,
                        }
                    )
            if tag in ("insert", "replace"):
                for index in range(j1, j2):
                    lines.append(
                        {
                            "type": "added",
                            "content": after_lines[index],
                            "old_line_number": None,
                            "new_line_number": index + 1,
                        }
                    )

    if budget is not None and len(lines) > budget:
        return lines[:budget], True
    return lines, False


def _safe_read(path: Path) -> str | None:
    """Safely read a file, returning None if binary or unreadable."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        # Skip large binary-looking files
        if len(content) > MAX_FILE_BYTES:
            return f"[File too large: {len(content)} chars]"
        return content
    except Exception:
        return None
