"""Streaming zip download of a transformation's output tree.

Source of truth is ``<storage_path>/<repo_id>/repo`` — the post-transform working
tree. ``_run_transform_background`` copies the pristine checkout to
``<storage_path>/<repo_id>/original`` *before* running the transformation, so both
trees survive; ``repo`` is the one the user wants.

Design notes
------------
*Streaming, not buffering.* The archive is produced incrementally through
:class:`_ZipStreamSink`, an unseekable sink that :mod:`zipfile` writes into and that
is drained after every chunk. Nothing larger than one read buffer plus one deflate
block is held at a time, so a 400 MB tree does not become a 400 MB response object.

*Bounded.* ``MAX_TOTAL_BYTES`` (500 MB uncompressed) is checked by walking the tree
before a single byte is streamed. Exceeding it raises :class:`TreeTooLargeError`,
which the endpoint turns into a 413 naming the limit — the alternative would be
either an OOM or a silently truncated archive that looks valid to the user.

*Path safety.* ``repo_id`` arrives from the URL, so it is validated by
:func:`services.repo_id.validate_repo_id` and the resolved tree is asserted to sit
under the storage root. Symlinks are resolved and any entry escaping the tree is
skipped rather than followed, so an archive can never carry content from outside the
transformation.
"""

import io
import logging
import zipfile
from collections.abc import Iterator
from pathlib import Path

from config import settings
from services.repo_id import InvalidRepoIdError, validate_repo_id

__all__ = [
    "InvalidRepoIdError",
    "TreeMissingError",
    "TreeTooLargeError",
    "archive_filename",
    "measure_tree",
    "resolve_tree",
    "stream_tree_zip",
    "validate_repo_id",
]

logger = logging.getLogger(__name__)

#: 500 MB of uncompressed content. Comfortably covers real Java repositories while
#: keeping a single request's work bounded on a shared agent.
MAX_TOTAL_BYTES = 500 * 1024 * 1024

#: Read granularity when feeding the compressor.
CHUNK_SIZE = 64 * 1024


class TreeMissingError(FileNotFoundError):
    """The transformation has no output tree (e.g. it failed before cloning)."""


class TreeTooLargeError(ValueError):
    """The output tree exceeds the download size cap."""

    def __init__(self, actual_bytes: int, limit_bytes: int = MAX_TOTAL_BYTES):
        self.actual_bytes = actual_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"Transformed tree is {actual_bytes / 1024 / 1024:.1f} MB uncompressed, "
            f"which exceeds the {limit_bytes // 1024 // 1024} MB download limit. "
            "Use the pull request flow or clone the branch instead."
        )


def resolve_tree(repo_id: str) -> Path:
    """Resolve the transformed working tree for ``repo_id``.

    Raises:
        InvalidRepoIdError: If the identifier is malformed.
        TreeMissingError: If no output tree exists.
    """
    validate_repo_id(repo_id)

    storage_root = Path(settings.storage_path).resolve()
    tree = (storage_root / repo_id / "repo").resolve()

    if not tree.is_relative_to(storage_root):
        raise InvalidRepoIdError(f"Invalid repo_id: {repo_id!r}")

    if not tree.is_dir():
        raise TreeMissingError(
            f"No transformed output for {repo_id}. The transformation may have failed before cloning the repository."
        )

    return tree


def _archive_members(tree: Path) -> list[tuple[Path, str]]:
    """Collect ``(absolute_path, arcname)`` pairs for everything to archive.

    Excludes ``.git`` and anything resolving outside ``tree``.
    """
    members: list[tuple[Path, str]] = []
    for path in sorted(tree.rglob("*")):
        if ".git" in path.relative_to(tree).parts:
            continue
        if not path.is_file():
            continue
        try:
            if not path.resolve().is_relative_to(tree):
                logger.warning(f"Skipping entry escaping the tree: {path}")
                continue
        except OSError:
            continue
        members.append((path, path.relative_to(tree).as_posix()))
    return members


def measure_tree(tree: Path) -> int:
    """Total uncompressed size of everything that would be archived."""
    total = 0
    for path, _ in _archive_members(tree):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


class _ZipStreamSink(io.RawIOBase):
    """An unseekable, drainable sink for :class:`zipfile.ZipFile`.

    ``zipfile`` writes data descriptors instead of rewriting local headers when the
    underlying file reports ``seekable() is False``, which is what makes a
    single-pass stream possible.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._offset = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def write(self, data) -> int:  # type: ignore[override]
        payload = bytes(data)
        self._buffer += payload
        self._offset += len(payload)
        return len(payload)

    def tell(self) -> int:
        return self._offset

    def drain(self) -> bytes:
        chunk = bytes(self._buffer)
        self._buffer.clear()
        return chunk


def stream_tree_zip(repo_id: str) -> Iterator[bytes]:
    """Yield a zip archive of the transformed tree, one buffered chunk at a time.

    Raises:
        InvalidRepoIdError: If the identifier is malformed.
        TreeMissingError: If no output tree exists.
        TreeTooLargeError: If the tree exceeds :data:`MAX_TOTAL_BYTES`.
    """
    tree = resolve_tree(repo_id)

    total_bytes = measure_tree(tree)
    if total_bytes > MAX_TOTAL_BYTES:
        raise TreeTooLargeError(total_bytes)

    members = _archive_members(tree)
    sink = _ZipStreamSink()

    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, arcname in members:
            info = zipfile.ZipInfo.from_file(path, arcname)
            try:
                with path.open("rb") as source, archive.open(info, "w") as target:
                    while True:
                        block = source.read(CHUNK_SIZE)
                        if not block:
                            break
                        target.write(block)
                        chunk = sink.drain()
                        if chunk:
                            yield chunk
            except OSError as exc:
                logger.warning(f"Skipping unreadable file {path}: {exc}")
                continue
            chunk = sink.drain()
            if chunk:
                yield chunk

    # Central directory, written on ZipFile.close().
    tail = sink.drain()
    if tail:
        yield tail


def archive_filename(repo_id: str) -> str:
    """Content-Disposition filename for a transformation's archive."""
    return f"transformed-{repo_id}.zip"
