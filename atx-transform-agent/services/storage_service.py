"""Storage service — transformation record persistence and retrieval.

Deliberately the same shape as ``atx-analysis-agent/services/storage_service.py``:
one ``metadata.json`` per unit of work inside that unit's own storage directory, and a
listing that is rebuilt by **scanning storage** rather than remembered in a process
variable. The two ATX agents must not diverge on how durable state works.

    <storage_path>/<repo_id>/metadata.json   ← the record
    <storage_path>/<repo_id>/repo/           ← post-transform working tree
    <storage_path>/<repo_id>/original/       ← pristine checkout (diff baseline)
    <storage_path>/<repo_id>/logs/output.log ← de-noised CLI output

Why disk is the only source of truth
------------------------------------
The record used to live in a module-level list, so a restart emptied
``GET /transformation-history`` and made ``/diff``, ``/diff-summary``, ``/download``,
``/pr-preview`` and ``/create-file-pr`` permanently 404 for transformations whose trees
were still sitting on disk. The inputs survived; the index did not.

There is **no in-memory cache of record state** here, and that is a design choice
rather than an omission. ``GET /conversations/{repo_id}/stream`` tails on the record's
persisted status with a 0.5 s poll:

    while (_get_record(repo_id) or {}).get("status") == "running":

A cache that missed the ``running`` → ``completed`` transition would hang that stream
forever; a cache that expired early would terminate it during the pre-launch clone
window — the exact bug that keying the loop on process liveness caused. Every read
therefore hits ``metadata.json``, and every write is atomic (temp file +
``os.replace``), so a poll landing mid-write reads either the old record or the new one
and never a truncated one. A ~200-byte JSON read twice a second is not a cost worth
buying a staleness class of bug for.

Backfill
--------
Transformations that ran before this module existed have trees but no
``metadata.json``. :func:`read_record` and :func:`list_records` reconstruct what is
derivable from the filesystem — ``repo_id`` from the directory name, ``created_at``
from directory mtime, which trees are present — and persist the reconstruction so the
repair happens once. Same "repair on read, idempotent" precedent as the analysis
agent's ``ensure_artifacts_collected``. Fields that cannot be recovered
(``repo_url``, ``branch``, ``transformation_type``) are left ``None`` and the record is
flagged ``backfilled``; nothing is guessed.

A directory with neither a readable ``metadata.json`` nor a ``repo/`` tree is **not**
listed: there is no diff to render, no archive to download, no repo URL to open a PR
against, and no evidence it was ever a transformation rather than scratch space. A row
whose every action 404s is worse than no row. A directory that *does* have metadata is
always listed, tree or not — a transformation that failed before cloning is real
history, and its recorded status and error explain the missing tree.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from services.repo_id import is_valid_repo_id

logger = logging.getLogger(__name__)

METADATA_FILENAME = "metadata.json"

#: Fields ``GET /transformation-history`` returns for each record (BC-33).
HISTORY_FIELDS = ("repo_id", "status", "created_at", "repo_url")

#: Fields that cannot be recovered from the filesystem alone. Present-but-null on a
#: backfilled record so a consumer can tell "unknown" from "not applicable".
UNRECOVERABLE_FIELDS = ("repo_url", "branch", "transformation_type")

#: Status for a record whose real status was never written down.
UNKNOWN_STATUS = "unknown"


def get_storage_path() -> Path:
    """Base storage directory. Not created — reads must not have side effects."""
    return Path(settings.storage_path)


def get_record_dir(repo_id: str, create: bool = False) -> Path | None:
    """Resolve a record's storage directory, or ``None`` if ``repo_id`` is unsafe.

    The identifier is validated and the resolved path is asserted to sit under the
    storage root, so a traversal attempt can never reach or create a directory.
    """
    if not is_valid_repo_id(repo_id):
        return None

    storage = get_storage_path()
    record_dir = storage / repo_id
    try:
        if not record_dir.resolve().is_relative_to(storage.resolve()):
            return None
    except OSError:
        return None

    if create:
        # A failure here means storage is unusable; it is raised rather than reported as
        # "invalid id" so the caller can answer honestly instead of silently accepting a
        # transformation that could never be recorded.
        record_dir.mkdir(parents=True, exist_ok=True)

    return record_dir


def _metadata_path(record_dir: Path) -> Path:
    return record_dir / METADATA_FILENAME


def _read_metadata(record_dir: Path) -> dict | None:
    """Parse ``metadata.json``, or ``None`` if absent/unreadable/not an object."""
    try:
        payload = json.loads(_metadata_path(record_dir).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_metadata(record_dir: Path, record: dict) -> None:
    """Write ``metadata.json`` atomically.

    The stream's tail loop re-reads this file every 0.5 s while a transformation runs,
    so a partially written file must never be observable: content goes to a temp file
    in the same directory and is moved into place with ``os.replace``, which is atomic
    on POSIX.

    Raises:
        OSError: If the record could not be written. Losing a record silently is what
            this module exists to prevent, so failures are not swallowed.
    """
    path = _metadata_path(record_dir)
    tmp_path = path.with_suffix(".json.tmp")
    try:
        tmp_path.write_text(json.dumps(record, indent=2))
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_record(record: dict) -> dict:
    """Persist a transformation record. Returns the record as persisted.

    Raises:
        ValueError: If ``repo_id`` is missing or malformed.
        OSError: If storage is not writable.
    """
    repo_id = record.get("repo_id")
    record_dir = get_record_dir(repo_id or "", create=True)
    if record_dir is None:
        raise ValueError(f"Invalid repo_id: {repo_id!r}")
    _write_metadata(record_dir, record)
    return record


def update_record(repo_id: str, **fields) -> dict | None:
    """Merge ``fields`` into a record and persist the result.

    Read-modify-write against disk rather than mutating a remembered object, so a
    status written by the background task is visible to the next poll of the stream's
    tail loop — including across a process restart.
    """
    record_dir = get_record_dir(repo_id)
    if record_dir is None or not record_dir.is_dir():
        logger.warning(f"Cannot update unknown transformation record {repo_id}")
        return None

    record = _read_metadata(record_dir) or {"repo_id": repo_id}
    record.update(fields)
    record["repo_id"] = repo_id
    try:
        _write_metadata(record_dir, record)
    except OSError as exc:
        # Loud, because a lost status write is what leaves a stream tailing `running`.
        logger.error(f"Failed to persist status update for {repo_id}: {exc}")
        return None
    return record


def _derived_created_at(record_dir: Path) -> str | None:
    """Approximate creation time from filesystem metadata."""
    try:
        mtime = record_dir.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def _backfill_record(repo_id: str, record_dir: Path) -> dict | None:
    """Reconstruct a record for a transformation that has a tree but no metadata.

    Returns ``None`` when the directory carries no transformed tree, i.e. when there is
    nothing a caller could do with the record. The reconstruction is persisted so the
    scan cost is paid once; failing to persist is not fatal (a read-only volume still
    gets a usable record, it just gets rebuilt next time).
    """
    if not (record_dir / "repo").is_dir():
        return None

    record: dict = {
        "repo_id": repo_id,
        "status": UNKNOWN_STATUS,
        "created_at": _derived_created_at(record_dir) or "",
        "backfilled": True,
        "created_at_source": "filesystem",
        "has_original": (record_dir / "original").is_dir(),
        "has_transformed_tree": True,
    }
    # Explicitly unknown, never guessed: the URL, branch and transformation type were
    # only ever in the request body and are not derivable from what is on disk.
    for field in UNRECOVERABLE_FIELDS:
        record[field] = None

    try:
        _write_metadata(record_dir, record)
    except OSError as exc:
        # Read-only storage still gets a usable record; it just gets rebuilt next read.
        logger.warning(f"Could not persist backfilled record {repo_id}: {exc}")
    else:
        logger.info(f"Backfilled transformation record from storage: {repo_id}")
    return record


def read_record(repo_id: str) -> dict | None:
    """Read a transformation record from disk, backfilling if needed.

    This is the only lookup path: it works identically before and after a restart.
    """
    record_dir = get_record_dir(repo_id)
    if record_dir is None or not record_dir.is_dir():
        return None

    record = _read_metadata(record_dir)
    if record is not None:
        record.setdefault("repo_id", repo_id)
        record.setdefault("status", UNKNOWN_STATUS)
        record.setdefault("created_at", "")
        return record

    return _backfill_record(repo_id, record_dir)


def list_records() -> list[dict]:
    """Rebuild the transformation listing by scanning storage.

    Newest first, by ``created_at`` — filesystem iteration order is not chronological
    and a ``uuid4`` prefix does not sort.
    """
    storage = get_storage_path()
    if not storage.is_dir():
        return []

    records: list[dict] = []
    try:
        entries = sorted(storage.iterdir())
    except OSError as exc:
        logger.error(f"Cannot scan transformation storage: {exc}")
        return []

    for entry in entries:
        if not entry.is_dir() or not is_valid_repo_id(entry.name):
            continue
        record = read_record(entry.name)
        if record is not None:
            records.append(record)

    records.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return records


def history_entry(record: dict) -> dict:
    """Project a record onto the ``/transformation-history`` response shape (BC-33)."""
    return {field: record.get(field) for field in HISTORY_FIELDS}


def mark_interrupted(repo_id: str) -> dict | None:
    """Reconcile a ``running`` status left behind by an agent restart.

    Liveness is in-memory, so a record that says ``running`` while nothing is tracked
    can only be the remains of a killed process — it will never report a terminal
    status of its own. Marking it lets the stream emit a terminal event instead of
    tailing a status that can no longer change. Mirrors the analysis agent's
    ``mark_interrupted``.
    """
    return update_record(
        repo_id,
        status="interrupted",
        completed_at=datetime.now(timezone.utc).isoformat(),
        error="Agent restarted while the transformation was running",
    )
