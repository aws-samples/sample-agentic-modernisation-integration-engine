"""Conversion records: one JSON document per discovery, updated under a lock."""

from __future__ import annotations

import threading
import time

from utils.storage_manager import StorageManager

_LOCK = threading.Lock()

CONVERTIBLE_TYPES = {"procedure", "function", "view", "trigger"}
ACTIVE_STATUSES = {"queued", "converting"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ConversionStore:
    def __init__(self, storage: StorageManager) -> None:
        self._storage = storage

    def load(self, discovery_id: str) -> dict:
        data = self._storage.load(discovery_id)
        return data or {
            "analysis_id": discovery_id,
            "discovery_id": discovery_id,
            "conversions": {},
        }

    def list(self, discovery_id: str) -> list[dict]:
        return list(self.load(discovery_id)["conversions"].values())

    def get(self, discovery_id: str, object_id: str) -> dict | None:
        return self.load(discovery_id)["conversions"].get(object_id)

    def update(self, discovery_id: str, object_id: str, **fields) -> dict:
        with _LOCK:
            doc = self.load(discovery_id)
            record = doc["conversions"].get(
                object_id, {"object_id": object_id, "attempts": []}
            )
            record.update(fields)
            record["updated_at"] = _now()
            doc["conversions"][object_id] = record
            doc["completed_at"] = _now()
            self._storage.save(discovery_id, doc)
            return record

    def append_attempt(self, discovery_id: str, object_id: str, attempt: dict) -> dict:
        with _LOCK:
            doc = self.load(discovery_id)
            record = doc["conversions"][object_id]
            record.setdefault("attempts", []).append(attempt)
            record["updated_at"] = _now()
            self._storage.save(discovery_id, doc)
            return record

    def recover_interrupted(self) -> int:
        """Mark conversions left queued/converting by a previous process as failed.

        Conversions run in the backend process; after a restart nothing is working on them, and an
        active status would also block re-queueing them.
        """
        recovered = 0
        with _LOCK:
            for item in self._storage.list_analyses():
                doc = self._storage.load(item.analysis_id)
                if not doc or "conversions" not in doc:
                    continue
                changed = False
                for record in doc["conversions"].values():
                    if record.get("status") in ACTIVE_STATUSES:
                        record["status"] = "failed"
                        record["error"] = (
                            "Interrupted: the backend restarted while this conversion was running. Convert it again."
                        )
                        record["updated_at"] = _now()
                        changed = True
                        recovered += 1
                if changed:
                    self._storage.save(item.analysis_id, doc)
        return recovered

    def queue(self, discovery_id: str, objects: list[dict]) -> list[dict]:
        """Queue objects, refusing ones already queued or running."""
        queued = []
        with _LOCK:
            doc = self.load(discovery_id)
            for obj in objects:
                existing = doc["conversions"].get(obj["id"])
                if existing and existing.get("status") in ACTIVE_STATUSES:
                    continue
                # Test cases (generated and reviewer-supplied) survive a re-conversion.
                kept = {
                    k: (existing or {}).get(k, [])
                    for k in ("test_cases", "custom_tests")
                }
                record = {
                    "object_id": obj["id"],
                    "object_type": obj["type"],
                    "object_name": f"{obj['schema']}.{obj['name']}"
                    if obj["schema"]
                    else obj["name"],
                    "status": "queued",
                    "attempts": [],
                    "final_sql": "",
                    "notes": "",
                    "reviewer_note": "",
                    "edited": False,
                    "error": "",
                    "behaviour_status": "not_run",
                    **kept,
                    "updated_at": _now(),
                }
                doc["conversions"][obj["id"]] = record
                queued.append(record)
            doc["completed_at"] = _now()
            self._storage.save(discovery_id, doc)
        return queued
