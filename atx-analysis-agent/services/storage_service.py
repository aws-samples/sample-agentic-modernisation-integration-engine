"""Storage service — conversation record persistence and retrieval.

Deliberately the same shape as ``atx-transform-agent/services/storage_service.py``:
one ``metadata.json`` per unit of work inside that unit's own storage directory, and a
listing that is rebuilt by **scanning storage** rather than remembered in a process
variable. The two ATX agents must not diverge on how durable state works (BC-61).

    <storage_path>/<conversation_id>/metadata.json   ← the record
    <storage_path>/<conversation_id>/events.jsonl    ← durable emitted-event record
    <storage_path>/<conversation_id>/repo/           ← cloned project tree
    <storage_path>/<conversation_id>/docs/           ← collected ATX documentation
    <storage_path>/<conversation_id>/output.log      ← raw CLI stdout

Why disk is the only source of truth
------------------------------------
Unlike the transform agent, this agent never held its index in a module-level dict —
the listing already scanned storage. The divergence was in the record and the record
store, and it produced the same family of symptoms: rows in the listing whose every
action was empty or a 404, and conversations whose real state was on disk but was
reported as ``unknown`` forever.

There is **no in-memory cache of record state**, and that is a design choice rather than
an omission. Every consumer that gates on a conversation existing — ``/conversations``,
``/conversations/{id}/stream``, ``/docs``, ``/logs``, ``/cancel`` — reads the record from
disk, so what is found before a restart is found identically after one. The stream's
tail loop polls the *persisted* status; a cache that missed a terminal transition would
hang that poller forever, and one that expired early would terminate it before the
record existed.

Every write is atomic — temp file in the same directory plus ``os.replace`` — so a
reader landing mid-write sees the old record or the new one, never a truncated one. That
matters concretely here: the previous plain ``write_text`` meant a concurrent read could
parse-fail and silently degrade a real ``completed`` status to ``unknown``.

Backfill
--------
A conversation stranded by a restart has trees but no readable ``metadata.json``.
:func:`read_record` reconstructs what is derivable from the filesystem —
``conversation_id`` from the directory name, ``created_at`` from directory mtime, which
trees are present, and ``repo_path`` when the cloned tree exists — and persists the
reconstruction so the repair happens once. Same "repair on read, idempotent" precedent
as ``ensure_artifacts_collected``. Recovering ``repo_path`` is what lets that artifact
repair then find the ``ATXDocumentation/`` tree the CLI wrote into the clone.

Fields that only ever existed in the ``POST /analyze`` request body — ``repository_url``,
``branch``, ``analysis_type`` — plus ``conversation_log``, which only ever appeared on
CLI stdout, are left **present and ``None``**. Nothing is guessed; present-but-null
distinguishes "unknown" from "not applicable".

Listing rules
-------------
- A directory that has metadata is **always listed**, payload or not. A run that failed
  during repository preparation is real history, and its recorded status and error
  explain the absent tree.
- A directory with neither readable metadata nor any conversation payload is **not
  listed**. A live example: ``<storage>/repos`` is scratch space for cloned projects, not
  a conversation, and listing it produced a row with no stream, no logs and no documents
  — every action on it empty or 404.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from services.conversation_id import is_valid_conversation_id

logger = logging.getLogger(__name__)

METADATA_FILENAME = "metadata.json"
EVENTS_FILENAME = "events.jsonl"

#: Fields ``GET /conversations`` returns for each conversation. The envelope uses
#: ``conversation_id``, never ``id``.
LISTING_FIELDS = ("conversation_id", "status", "created_at")

#: Fields that cannot be recovered from the filesystem alone. Present-but-null on a
#: backfilled record so a consumer can tell "unknown" from "not applicable".
UNRECOVERABLE_FIELDS = ("repository_url", "branch", "analysis_type", "conversation_log")

#: Status for a record whose real status was never written down.
UNKNOWN_STATUS = "unknown"


def get_storage_path() -> Path:
    """Base storage directory. Not created — reads must not have side effects."""
    return Path(settings.storage_path)


def get_record_dir(conversation_id: str, create: bool = False) -> Path | None:
    """Resolve a conversation's storage directory, or ``None`` if the id is unsafe.

    The identifier is validated through the shared module and the resolved path is
    asserted to sit under the storage root with ``is_relative_to`` — a path
    relationship, not a string prefix — so a traversal attempt can never reach or
    create a directory. Existence is **not** checked; use :func:`get_conversation_dir`
    for the "must already exist" form.
    """
    if not is_valid_conversation_id(conversation_id):
        return None

    storage = get_storage_path()
    record_dir = storage / conversation_id
    try:
        if not record_dir.resolve().is_relative_to(storage.resolve()):
            return None
    except OSError:
        return None

    if create:
        # A failure here means storage is unusable; it is raised rather than reported as
        # "invalid id" so the caller can answer honestly instead of silently accepting an
        # analysis that could never be recorded.
        record_dir.mkdir(parents=True, exist_ok=True)

    return record_dir


def get_conversation_dir(conversation_id: str) -> Path | None:
    """Storage directory for an **existing** conversation, else ``None``.

    This is what the ``/conversations/{id}/*`` routes gate on, and it reads the
    filesystem every time, so it answers identically before and after a restart.
    """
    record_dir = get_record_dir(conversation_id)
    if record_dir is None or not record_dir.is_dir():
        return None
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

    Readers poll this file — the stream's tail loop, ``/docs`` for its status, the
    listing scan — so a partially written file must never be observable: content goes
    to a temp file in the same directory and is moved into place with ``os.replace``,
    which is atomic on POSIX.

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
    """Persist a conversation record. Returns the record as persisted.

    Raises:
        ValueError: If ``conversation_id`` is missing or malformed.
        OSError: If storage is not writable.
    """
    conversation_id = record.get("conversation_id")
    record_dir = get_record_dir(conversation_id or "", create=True)
    if record_dir is None:
        raise ValueError(f"Invalid conversation_id: {conversation_id!r}")
    _write_metadata(record_dir, record)
    return record


def update_record(conversation_id: str, **fields) -> dict | None:
    """Merge ``fields`` into a record and persist the result.

    Read-modify-write against disk rather than mutating a remembered object, so a
    status written by the background worker is visible to the next poll of the stream's
    tail loop — including across a process restart.
    """
    record_dir = get_conversation_dir(conversation_id)
    if record_dir is None:
        logger.warning(f"Cannot update unknown conversation record {conversation_id}")
        return None

    record = _read_metadata(record_dir) or {"conversation_id": conversation_id}
    record.update(fields)
    record["conversation_id"] = conversation_id
    try:
        _write_metadata(record_dir, record)
    except OSError as exc:
        # Loud, because a lost status write is what leaves a stream tailing `running`.
        logger.error(f"Failed to persist status update for {conversation_id}: {exc}")
        return None
    return record


def _derived_created_at(record_dir: Path) -> str | None:
    """Approximate creation time from filesystem metadata."""
    try:
        mtime = record_dir.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def _has_payload(record_dir: Path) -> bool:
    """True if the directory holds something a conversation endpoint could serve.

    An **empty** ``docs/`` is deliberately not payload: artifact collection creates that
    directory as a side effect of a read, so counting it would let probing an arbitrary
    directory promote it into the listing.
    """
    if (record_dir / EVENTS_FILENAME).is_file():
        return True
    if (record_dir / "output.log").is_file():
        return True
    if (record_dir / "repo").is_dir():
        return True
    docs_dir = record_dir / "docs"
    return docs_dir.is_dir() and any(path.is_file() for path in docs_dir.rglob("*"))


def _backfill_record(conversation_id: str, record_dir: Path) -> dict | None:
    """Reconstruct a record for a conversation with payload but no readable metadata.

    Returns ``None`` when the directory carries no conversation payload, i.e. when there
    is nothing a caller could do with the record. The reconstruction is persisted so the
    scan cost is paid once; failing to persist is not fatal (a read-only volume still
    gets a usable record, it just gets rebuilt next time).
    """
    if not _has_payload(record_dir):
        return None

    repo_dir = record_dir / "repo"
    has_repo = repo_dir.is_dir()

    record: dict = {
        "conversation_id": conversation_id,
        "status": UNKNOWN_STATUS,
        "created_at": _derived_created_at(record_dir) or "",
        "created_at_source": "filesystem",
        "backfilled": True,
        "has_repo": has_repo,
        "has_events": (record_dir / EVENTS_FILENAME).is_file(),
        "has_docs": (record_dir / "docs").is_dir(),
        # Derivable: the clone is always at <storage>/<id>/repo, and it is the path the
        # CLI was handed — which is what artifact collection needs to find the
        # ATXDocumentation/ tree written into it.
        "repo_path": str(repo_dir) if has_repo else None,
    }
    # Explicitly unknown, never guessed: the URL, branch and analysis type were only
    # ever in the request body, and the conversation log path only ever on CLI stdout.
    for field in UNRECOVERABLE_FIELDS:
        record[field] = None

    try:
        _write_metadata(record_dir, record)
    except OSError as exc:
        # Read-only storage still gets a usable record; it just gets rebuilt next read.
        logger.warning(f"Could not persist backfilled record {conversation_id}: {exc}")
    else:
        logger.info(f"Backfilled conversation record from storage: {conversation_id}")
    return record


def read_record(conversation_id: str) -> dict | None:
    """Read a conversation record from disk, backfilling if needed.

    This is the only lookup path: it works identically before and after a restart.
    """
    record_dir = get_conversation_dir(conversation_id)
    if record_dir is None:
        return None

    record = _read_metadata(record_dir)
    if record is not None:
        record.setdefault("conversation_id", conversation_id)
        record.setdefault("status", UNKNOWN_STATUS)
        record.setdefault("created_at", "")
        return record

    return _backfill_record(conversation_id, record_dir)


def read_metadata(conversation_id: str) -> dict:
    """Record for a conversation, or ``{}`` if there is none.

    Convenience form for callers that only want a status and have nothing useful to do
    with the difference between "no such conversation" and "no record".
    """
    return read_record(conversation_id) or {}


def list_records() -> list[dict]:
    """Rebuild the conversation listing by scanning storage.

    Newest first, by ``created_at`` — filesystem iteration order is not chronological,
    and while production ids happen to embed a sortable timestamp, a client may supply
    its own ``conversation_id`` via ``POST /analyze``, so ordering on the directory name
    is ordering on something the caller controls.
    """
    storage = get_storage_path()
    if not storage.is_dir():
        return []

    try:
        entries = sorted(storage.iterdir())
    except OSError as exc:
        logger.error(f"Cannot scan conversation storage: {exc}")
        return []

    records: list[dict] = []
    for entry in entries:
        if not entry.is_dir() or not is_valid_conversation_id(entry.name):
            continue
        record = read_record(entry.name)
        if record is not None:
            records.append(record)

    records.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return records


def listing_entry(record: dict) -> dict:
    """Project a record onto the ``GET /conversations`` response shape."""
    return {field: record.get(field) for field in LISTING_FIELDS}


def list_conversations() -> list[dict]:
    """``GET /conversations`` payload: ``[{conversation_id, status, created_at}]``."""
    return [listing_entry(record) for record in list_records()]


def get_conversation(conversation_id: str) -> dict | None:
    """Full record for one conversation, or ``None`` if there is none."""
    return read_record(conversation_id)


def mark_interrupted(conversation_id: str) -> dict:
    """Reconcile a stale ``running`` status left behind by an agent restart (BC-49).

    Liveness is in-memory, so a record that says ``running`` while nothing is tracked in
    this process can only be the remains of a killed run — it will never report a
    terminal status of its own. Marking it lets the stream emit a terminal event instead
    of tailing a status that can no longer change. Read-modify-write against disk, so
    the reconciliation is itself durable. Mirrors the transform agent's
    ``mark_interrupted``.
    """
    updated = update_record(
        conversation_id,
        status="interrupted",
        completed_at=datetime.now(timezone.utc).isoformat(),
        interrupted_reason="Agent restarted while the analysis was running",
    )
    return updated if updated is not None else read_metadata(conversation_id)
