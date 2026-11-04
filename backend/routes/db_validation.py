"""Validation harness routes — SQL Server source vs MySQL target.

Response envelopes:
  GET  /api/db-validation/status         → {"source": {...}, "target": {...}, "copy_enabled": bool}
  POST /api/db-validation/runs           → {"run_id": str, "status": "processing"}
  POST /api/db-validation/copy           → {"run_id": str, "status": "processing"}
  GET  /api/db-validation/runs           → {"runs": [...]}  (summaries, newest first)
  GET  /api/db-validation/runs/{run_id}  → full run record

Connections come from the environment only; requests cannot name a host or carry a password.
"""

from __future__ import annotations

import logging
import os
import re
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from config import settings
from state import app_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/db-validation", tags=["db-validation"])

DB_VALIDATION_STORAGE_PATH = os.environ.get(
    "DB_VALIDATION_STORAGE_PATH", "/app/temp/db_validations"
)
MAX_COPY_ROWS = 100_000
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class ProcedureParam(BaseModel):
    name: str
    value: str | int | float | bool | None = None


class ProcedureTest(BaseModel):
    procedure: str
    params: list[ProcedureParam] = []
    ordered: bool = True
    target_call: str = ""


class ValidationRequest(BaseModel):
    discovery_id: str
    tables: list[str] = []  # table object IDs; empty = every table in the discovery
    procedure_tests: list[ProcedureTest] = []


class CopyRequest(BaseModel):
    discovery_id: str
    tables: list[str] = []
    max_rows: int = Field(default=1000, ge=1, le=MAX_COPY_ROWS)


def _storage():
    storage = app_state.db_validation_storage
    if storage is None:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    return storage


def _report(discovery_id: str) -> dict:
    if not _SAFE_ID.match(discovery_id):
        raise HTTPException(status_code=400, detail="Invalid discovery ID")
    report = (
        app_state.db_discovery_storage.load(discovery_id)
        if app_state.db_discovery_storage
        else None
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Discovery not found")
    return report


def _new_run(kind: str, discovery_id: str, request: dict) -> dict:
    storage = _storage()
    base = f"val_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}"
    run_id, n = base, 1
    while storage.load(run_id) or (
        app_state.progress_tracker and app_state.progress_tracker.get(run_id)
    ):
        n += 1
        run_id = f"{base}-{n}"
    record = {
        "analysis_id": run_id,
        "run_id": run_id,
        "kind": kind,
        "discovery_id": discovery_id,
        "request": request,
        "status": "processing",
        "progress": 0,
        "message": "Started",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "completed_at": "",
    }
    storage.save(run_id, record)
    return record


def _finish(record: dict, **fields) -> None:
    record.update(fields)
    record["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _storage().save(record["run_id"], record)


def _progress(record: dict):
    def update(pct: int, message: str) -> None:
        record.update(progress=pct, message=message)
        _storage().save(record["run_id"], record)

    return update


def _run_validation(
    record: dict, report: dict, tables: list[str], tests: list[dict]
) -> None:
    from services.db_validation import connections, harness

    try:
        result = harness.run_validation(
            report, tables or None, tests, progress=_progress(record)
        )
        _finish(
            record,
            status="completed",
            progress=100,
            message="Validation completed",
            result=result,
        )
    except Exception as exc:
        _finish(record, status="failed", message=connections.safe_error(exc))


def _run_copy(record: dict, report: dict, tables: list[str], max_rows: int) -> None:
    from services.db_validation import connections, harness

    try:
        result = harness.copy_tables(
            report, tables or None, max_rows, progress=_progress(record)
        )
        _finish(
            record,
            status="completed",
            progress=100,
            message="Copy completed",
            result=result,
        )
    except Exception as exc:
        _finish(record, status="failed", message=connections.safe_error(exc))


@router.get("/status")
def validation_status() -> dict:
    """Plain def: probes open network connections."""
    from services.db_validation import connections

    return connections.status()


@router.post("/runs")
async def start_validation(
    body: ValidationRequest, background_tasks: BackgroundTasks
) -> dict:
    from services.db_validation import connections, harness

    report = _report(body.discovery_id)
    if connections.source_config() is None:
        raise HTTPException(
            status_code=400,
            detail="SQL Server source is not configured (VALIDATION_SQLSERVER_HOST).",
        )
    if connections.target_config() is None:
        raise HTTPException(status_code=400, detail="MySQL target is not configured.")
    table_ids = {o["id"] for o in report["objects"] if o["type"] == "table"}
    unknown = [t for t in body.tables if t not in table_ids]
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Unknown table IDs: {', '.join(unknown[:5])}"
        )
    tests = [t.model_dump() for t in body.procedure_tests]
    for t in tests:
        error = harness.validate_procedure_test(t)
        if error:
            raise HTTPException(status_code=400, detail=f"{t['procedure']}: {error}")
    record = _new_run(
        "validation",
        body.discovery_id,
        {"tables": body.tables, "procedure_tests": tests},
    )
    background_tasks.add_task(_run_validation, record, report, body.tables, tests)
    return {"run_id": record["run_id"], "status": "processing"}


@router.post("/copy")
async def start_copy(body: CopyRequest, background_tasks: BackgroundTasks) -> dict:
    from services.db_validation import connections

    if not settings.VALIDATION_ALLOW_COPY:
        raise HTTPException(
            status_code=403,
            detail="Row copy is disabled. Set VALIDATION_ALLOW_COPY=true for test environments only.",
        )
    report = _report(body.discovery_id)
    if connections.source_config() is None or connections.target_config() is None:
        raise HTTPException(
            status_code=400,
            detail="Source and target connections must both be configured.",
        )
    record = _new_run(
        "copy", body.discovery_id, {"tables": body.tables, "max_rows": body.max_rows}
    )
    background_tasks.add_task(_run_copy, record, report, body.tables, body.max_rows)
    return {"run_id": record["run_id"], "status": "processing"}


@router.get("/runs")
async def list_runs() -> dict:
    storage = _storage()
    runs = []
    for item in storage.list_analyses():
        data = storage.load(item.analysis_id) or {}
        runs.append(
            {
                k: data.get(k)
                for k in (
                    "run_id",
                    "kind",
                    "discovery_id",
                    "status",
                    "progress",
                    "message",
                    "started_at",
                    "completed_at",
                )
            }
            | {"summary": (data.get("result") or {}).get("summary", {})}
        )
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return {"runs": runs}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    if not _SAFE_ID.match(run_id):
        raise HTTPException(status_code=400, detail="Invalid run ID")
    data = _storage().load(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return data
