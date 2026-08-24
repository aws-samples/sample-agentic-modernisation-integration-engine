"""Conversation records must outlive the process that created them (BC-61).

Same shape as ``atx-transform-agent/tests/test_record_persistence.py`` — the two ATX
agents must not diverge on how durable state works.

Unlike the transform agent, this agent never held its conversation index in a
module-level dict: ``list_conversations`` already scanned storage, so the plain
"listing survives a restart" assertion passed before this change. The divergence was in
the *record and the record store*:

- the listing was ordered by **directory name**, not ``created_at``;
- any directory under the storage root was listed as a conversation, including ones
  holding no conversation payload at all, so every action on that row was empty or 404;
- a directory with trees but no readable ``metadata.json`` reported ``status: "unknown"``
  and ``created_at: ""`` forever — nothing was ever reconstructed or persisted;
- ``metadata.json`` was written with a plain ``write_text``, so a reader landing
  mid-write saw truncated JSON and silently degraded a real status to ``unknown``;
- ``/cancel`` gated on the in-memory process registry, so a conversation left
  ``running`` by a restart 404'd as if it did not exist;
- three separate ad-hoc path checks stood in for one shared id validation module.
"""

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sse_starlette.sse import AppStatus

from config import settings
from main import app
from services import command_service, storage_service

UNRECOVERABLE_FIELDS = ("repository_url", "branch", "analysis_type", "conversation_log")


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Point every storage consumer at a temporary root."""
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setattr(settings, "storage_path", str(root))
    return root


def _simulate_restart() -> None:
    """Drop every scrap of in-process state, exactly as a container restart does.

    Anything the agent still answers afterwards is answered from disk.
    """
    command_service.running_processes.clear()
    command_service.worker_tasks.clear()
    AppStatus.should_exit_event = None


@pytest.fixture(autouse=True)
def clean_process_state():
    _simulate_restart()
    yield
    _simulate_restart()


def _persist(
    storage: Path,
    conversation_id: str,
    *,
    status: str = "completed",
    created_at: str = "2025-01-01T00:00:00+00:00",
    docs: dict[str, str] | None = None,
    events: list[dict] | None = None,
    **extra,
) -> Path:
    """Lay out a conversation the way a finished analysis leaves it on disk."""
    conv_dir = storage / conversation_id
    conv_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "repo").mkdir(exist_ok=True)

    for rel_path, content in (docs or {}).items():
        target = conv_dir / "docs" / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    lines = (
        events if events is not None else [{"type": "init", "conversation_id": conversation_id}, {"type": "complete"}]
    )
    (conv_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in lines))

    record = {
        "conversation_id": conversation_id,
        "analysis_type": "code-assessment",
        "repository_url": "https://github.com/org/repo",
        "branch": "main",
        "repo_path": str(conv_dir / "repo"),
        "status": status,
        "created_at": created_at,
        **extra,
    }
    (conv_dir / "metadata.json").write_text(json.dumps(record, indent=2))
    return conv_dir


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- 1. Survival across a restart ---


@pytest.mark.asyncio
async def test_a_completed_conversation_and_its_endpoints_survive_a_restart(storage):
    """The listing and every gated endpoint answer from disk with no process state.

    This is the headline defect: ``GET /conversations/{id}/docs`` returning 404 for a
    completed conversation whose whole storage tree is still on disk.
    """
    conversation_id = "atx_20250101_000000_aaaa0001"
    _persist(storage, conversation_id, docs={"README.md": "# Assessment\n"})

    _simulate_restart()

    async with _client() as client:
        listing = await client.get("/conversations")
        docs = await client.get(f"/conversations/{conversation_id}/docs")
        logs = await client.get(f"/conversations/{conversation_id}/logs")

    assert listing.status_code == 200
    assert [c["conversation_id"] for c in listing.json()["conversations"]] == [conversation_id]

    assert docs.status_code == 200, docs.text
    assert [d["name"] for d in docs.json()["docs"]] == ["README.md"]
    assert docs.json()["status"] == "completed"

    assert logs.status_code == 200


@pytest.mark.asyncio
async def test_listing_keeps_its_documented_envelope(storage):
    """Response shape is unchanged: conversation_id, not id (BC-33 equivalent)."""
    _persist(storage, "atx_20250101_000000_aaaa0002")

    _simulate_restart()

    async with _client() as client:
        payload = (await client.get("/conversations")).json()

    assert set(payload) == {"conversations"}
    for entry in payload["conversations"]:
        assert set(entry) == {"conversation_id", "status", "created_at"}


# --- 2. Ordering ---


@pytest.mark.asyncio
async def test_listing_is_newest_first_by_created_at_not_by_directory_name(storage):
    """Filesystem iteration order is not chronological, and the id need not sort.

    Regression pin for ``sorted(storage.iterdir(), reverse=True)``: these ids sort
    the wrong way round relative to their ``created_at``.
    """
    _persist(storage, "aaa_first", created_at="2025-06-01T00:00:00+00:00")
    _persist(storage, "zzz_second", created_at="2025-01-01T00:00:00+00:00")

    _simulate_restart()

    async with _client() as client:
        entries = (await client.get("/conversations")).json()["conversations"]

    assert [e["conversation_id"] for e in entries] == ["aaa_first", "zzz_second"]


# --- 3. Directories with no conversation payload ---


@pytest.mark.asyncio
async def test_a_directory_with_no_conversation_payload_is_not_listed(storage):
    """A row whose every action is empty or 404 is worse than no row.

    Regression pin for the live symptom: a ``repos/`` scratch directory under the
    storage root was listed as a conversation with status ``unknown``.
    """
    scratch = storage / "repos" / "org_project"
    scratch.mkdir(parents=True)
    (scratch / "pom.xml").write_text("<project/>\n")
    _persist(storage, "atx_20250101_000000_aaaa0003")

    _simulate_restart()

    async with _client() as client:
        entries = (await client.get("/conversations")).json()["conversations"]

    assert [e["conversation_id"] for e in entries] == ["atx_20250101_000000_aaaa0003"]


@pytest.mark.asyncio
async def test_reading_docs_does_not_turn_a_bare_directory_into_a_conversation(storage):
    """Repair on read must not create a payload marker where there was none.

    ``ensure_artifacts_collected`` unconditionally created ``docs/``, so probing an
    arbitrary directory gave it a payload and made it look like a conversation.
    """
    (storage / "repos").mkdir()

    async with _client() as client:
        await client.get("/conversations/repos/docs")
        entries = (await client.get("/conversations")).json()["conversations"]

    assert entries == []
    assert not (storage / "repos" / "docs").exists()


def test_a_recorded_conversation_with_no_payload_is_still_listed(storage):
    """A run that failed before producing anything is real history."""
    conv_dir = storage / "atx_20250101_000000_aaaa0004"
    conv_dir.mkdir()
    (conv_dir / "metadata.json").write_text(
        json.dumps(
            {
                "conversation_id": conv_dir.name,
                "status": "failed",
                "created_at": "2025-01-01T00:00:00+00:00",
                "error": "Repository preparation failed",
            }
        )
    )

    entries = storage_service.list_conversations()

    assert [e["conversation_id"] for e in entries] == [conv_dir.name]
    assert entries[0]["status"] == "failed"


# --- 4. Atomic writes ---


def test_metadata_writes_leave_no_partial_or_temp_files(storage):
    """A poll landing mid-write reads the old record or the new one, never a stub."""
    conversation_id = "atx_20250101_000000_aaaa0005"
    _persist(storage, conversation_id, status="running")

    storage_service.update_record(conversation_id, status="completed", return_code=0)

    conv_dir = storage / conversation_id
    assert list(conv_dir.glob("*.tmp")) == []
    record = json.loads((conv_dir / "metadata.json").read_text())
    assert record["status"] == "completed"
    # Fields written at creation are not lost by the update.
    assert record["repository_url"] == "https://github.com/org/repo"


def test_every_intermediate_state_of_the_record_file_is_parseable(storage):
    """Atomicity, observed: no write ever leaves a truncated file on disk.

    Every rename is intercepted and the destination inspected as it would be by a
    concurrent reader, so a truncate-then-write implementation fails here.
    """
    conversation_id = "atx_20250101_000000_aaaa0006"
    _persist(storage, conversation_id, status="running")
    metadata_path = storage / conversation_id / "metadata.json"

    observed: list[str] = []
    real_replace = storage_service.os.replace

    def observing_replace(src, dst):
        # Before the rename the destination must still hold the *previous* record.
        observed.append(Path(dst).read_text())
        real_replace(src, dst)
        observed.append(Path(dst).read_text())

    storage_service.os.replace = observing_replace
    try:
        storage_service.update_record(conversation_id, status="completed")
    finally:
        storage_service.os.replace = real_replace

    assert observed, "record was not written through an atomic rename"
    statuses = [json.loads(text)["status"] for text in observed]
    assert statuses == ["running", "completed"]
    assert json.loads(metadata_path.read_text())["status"] == "completed"


# --- 5. Backfill ---


def test_directory_with_trees_but_no_metadata_is_reconstructed(storage):
    """Repair on read: derivable fields recovered, the rest explicitly unknown."""
    conversation_id = "atx_20250101_000000_aaaa0007"
    conv_dir = storage / conversation_id
    (conv_dir / "repo").mkdir(parents=True)
    (conv_dir / "repo" / "pom.xml").write_text("<project/>\n")
    assert not (conv_dir / "metadata.json").exists()

    record = storage_service.read_record(conversation_id)

    assert record is not None
    assert record["conversation_id"] == conversation_id
    assert record["backfilled"] is True
    assert record["status"] == "unknown"
    # Derivable from the filesystem.
    assert record["created_at"], "created_at should come from filesystem timestamps"
    assert record["created_at_source"] == "filesystem"
    assert record["has_repo"] is True
    assert record["repo_path"] == str(conv_dir / "repo")
    # Not derivable — present and null, never invented.
    for field in UNRECOVERABLE_FIELDS:
        assert field in record, f"{field} should be present-but-null, not absent"
        assert record[field] is None, f"{field} was fabricated: {record[field]!r}"


def test_backfill_is_persisted_once_and_is_stable(storage):
    """Idempotent, following the ``ensure_artifacts_collected`` precedent."""
    conversation_id = "atx_20250101_000000_aaaa0008"
    (storage / conversation_id / "repo").mkdir(parents=True)

    first = storage_service.read_record(conversation_id)
    assert (storage / conversation_id / "metadata.json").is_file(), "backfill was not persisted"

    second = storage_service.read_record(conversation_id)
    third = storage_service.read_record(conversation_id)
    assert second == first
    assert third == first
    assert second.get("backfilled") is True


def test_backfill_replaces_unreadable_metadata_rather_than_reporting_unknown(storage):
    """Truncated metadata is repaired, not silently reported as status unknown."""
    conversation_id = "atx_20250101_000000_aaaa0009"
    conv_dir = storage / conversation_id
    (conv_dir / "repo").mkdir(parents=True)
    (conv_dir / "metadata.json").write_text('{"conversation_id": "atx_2025')

    record = storage_service.read_record(conversation_id)

    assert record is not None
    assert record["backfilled"] is True
    assert record["created_at"], "a repaired record carries a filesystem-derived timestamp"


@pytest.mark.asyncio
async def test_a_restart_stranded_conversation_repairs_its_docs_on_first_read(storage):
    """End to end: backfill makes the record findable, and collection then runs.

    The CLI wrote ``ATXDocumentation/`` into the cloned repo and the agent restarted
    before the worker collected it. Nothing on disk records the conversation.
    """
    conversation_id = "atx_20250101_000000_aaaa0010"
    docs_source = storage / conversation_id / "repo" / "ATXDocumentation"
    docs_source.mkdir(parents=True)
    (docs_source / "README.md").write_text("# Recovered Assessment\n")

    _simulate_restart()

    async with _client() as client:
        listing = await client.get("/conversations")
        response = await client.get(f"/conversations/{conversation_id}/docs")

    assert [c["conversation_id"] for c in listing.json()["conversations"]] == [conversation_id]
    assert response.status_code == 200, response.text
    body = response.json()
    assert [d["name"] for d in body["docs"]] == ["README.md"]
    assert body["status"] == "unknown"


# --- 6. Stale `running` reconciliation reads from disk (BC-49) ---


def test_stale_running_is_reconciled_from_the_persisted_status(storage):
    """Reconciliation reads ``metadata.json``, not an in-memory index."""
    conversation_id = "atx_20250101_000000_aaaa0011"
    _persist(storage, conversation_id, status="running")

    _simulate_restart()

    assert storage_service.read_record(conversation_id)["status"] == "running"
    assert command_service.is_tracked(conversation_id) is False

    storage_service.mark_interrupted(conversation_id)

    reread = json.loads((storage / conversation_id / "metadata.json").read_text())
    assert reread["status"] == "interrupted"
    assert reread["completed_at"]
    # Creation-time fields survive the reconciliation.
    assert reread["repository_url"] == "https://github.com/org/repo"


@pytest.mark.asyncio
async def test_stream_reconciles_a_stale_running_conversation_after_a_restart(storage):
    """The stream still emits a terminal event rather than tailing forever."""
    conversation_id = "atx_20250101_000000_aaaa0012"
    _persist(
        storage,
        conversation_id,
        status="running",
        events=[{"type": "init", "conversation_id": conversation_id}],
    )

    _simulate_restart()

    async with _client() as client:
        response = await client.get(f"/conversations/{conversation_id}/stream")

    assert response.status_code == 200
    events = [json.loads(line[len("data: ") :]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["type"] == "error"
    assert "interrupted" in events[-1]["message"].lower()
    assert json.loads((storage / conversation_id / "metadata.json").read_text())["status"] == "interrupted"


@pytest.mark.asyncio
async def test_cancelling_a_restart_stranded_conversation_reconciles_it(storage):
    """``/cancel`` gated on the in-memory registry, so this 404'd as "not found".

    The conversation exists on disk and its status is reconcilable, so the endpoint
    answers about it instead of denying it exists. An id with nothing on disk still
    404s.
    """
    conversation_id = "atx_20250101_000000_aaaa0013"
    _persist(storage, conversation_id, status="running")

    _simulate_restart()

    async with _client() as client:
        cancelled = await client.post(f"/cancel/{conversation_id}")
        unknown = await client.post("/cancel/atx_20250101_000000_nosuchid")

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["conversation_id"] == conversation_id
    assert cancelled.json()["status"] == "interrupted"
    assert json.loads((storage / conversation_id / "metadata.json").read_text())["status"] == "interrupted"

    assert unknown.status_code == 404


# --- 7. Shared id validation ---


@pytest.mark.parametrize("conversation_id", ["..", "../etc", "a/b", ".hidden", "with space", ""])
def test_traversal_shaped_ids_never_resolve_to_a_record(storage, conversation_id):
    assert storage_service.read_record(conversation_id) is None
    assert storage_service.get_conversation_dir(conversation_id) is None


def test_a_sibling_directory_sharing_the_storage_prefix_is_not_reachable(storage):
    """The old check was ``str(resolved).startswith(str(storage))`` — a prefix test.

    ``/tmp/.../storage-evil`` starts with ``/tmp/.../storage``, so a string prefix
    check accepts it. Containment is a path relationship, not a string one.
    """
    sibling = storage.parent / f"{storage.name}-evil"
    sibling.mkdir()
    (sibling / "metadata.json").write_text("{}")

    assert storage_service.get_conversation_dir(f"../{sibling.name}") is None
    assert storage_service.read_record(f"../{sibling.name}") is None


def test_reads_do_not_create_the_storage_root(tmp_path, monkeypatch):
    """Listing an absent storage root reports nothing; it does not create it."""
    absent = tmp_path / "not-created-yet"
    monkeypatch.setattr(settings, "storage_path", str(absent))

    assert storage_service.list_conversations() == []
    assert storage_service.read_record("atx_20250101_000000_aaaa0014") is None
    assert not absent.exists()
