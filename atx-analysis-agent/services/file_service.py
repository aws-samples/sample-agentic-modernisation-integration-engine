"""File service — file browsing and content reading."""

from pathlib import Path


def browse_directory(base_path: str, relative_path: str = "") -> list[dict]:
    """Browse a directory and return its contents.

    Returns a list of file/directory info dicts.
    """
    target = Path(base_path)
    if relative_path:
        target = target / relative_path

    # Prevent path traversal
    try:
        target = target.resolve()
        base_resolved = Path(base_path).resolve()
        # is_relative_to compares path components, avoiding the prefix-collision
        # bypass of a string startswith (e.g. /app/storage-evil vs /app/storage).
        if not target.is_relative_to(base_resolved):
            raise ValueError("Path traversal detected")
    except (OSError, ValueError):
        raise ValueError("Invalid path")

    if not target.exists():
        raise FileNotFoundError(f"Path does not exist: {relative_path}")

    if not target.is_dir():
        raise ValueError(f"Path is not a directory: {relative_path}")

    entries = []
    try:
        for entry in sorted(target.iterdir()):
            stat = entry.stat()
            entries.append(
                {
                    "name": entry.name,
                    "path": str(entry.relative_to(base_resolved)),
                    "type": "directory" if entry.is_dir() else "file",
                    "size": stat.st_size if entry.is_file() else None,
                }
            )
    except PermissionError:
        pass

    return entries


def read_file_content(base_path: str, file_path: str) -> str:
    """Read file content with path traversal protection.

    Returns file content as string.
    """
    target = (Path(base_path) / file_path).resolve()
    base_resolved = Path(base_path).resolve()

    # Prevent path traversal. is_relative_to compares path components, avoiding
    # the prefix-collision bypass of a string startswith check.
    if not target.is_relative_to(base_resolved):
        raise ValueError("Path traversal detected")

    if not target.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if not target.is_file():
        raise ValueError(f"Not a file: {file_path}")

    # Read with size limit (10MB)
    if target.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("File too large (max 10MB)")

    return target.read_text(errors="replace")


def list_docs(conversation_dir: Path) -> list[dict]:
    """List documentation files for a conversation.

    Each entry carries ``storage_path`` — the path relative to the storage root —
    so a client can read the document through ``GET /file`` (which keeps the
    path-traversal check and size cap) instead of the listing needing its own
    reader.
    """
    docs_dir = conversation_dir / "docs"
    if not docs_dir.exists():
        return []

    docs = []
    for file_path in sorted(docs_dir.rglob("*")):
        if file_path.is_file():
            relative = file_path.relative_to(docs_dir).as_posix()
            docs.append(
                {
                    "name": file_path.name,
                    "path": relative,
                    "storage_path": f"{conversation_dir.name}/docs/{relative}",
                    "size": file_path.stat().st_size,
                }
            )
    return docs


def list_logs(conversation_dir: Path) -> list[dict]:
    """List log files for a conversation."""
    logs = []

    # Check for output.log
    log_file = conversation_dir / "output.log"
    if log_file.exists():
        logs.append(
            {
                "name": "output.log",
                "path": "output.log",
                "size": log_file.stat().st_size,
            }
        )

    # Check for any other .log files
    for file_path in conversation_dir.glob("*.log"):
        if file_path.name != "output.log":
            logs.append(
                {
                    "name": file_path.name,
                    "path": file_path.name,
                    "size": file_path.stat().st_size,
                }
            )

    return logs
