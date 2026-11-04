"""Conversion routes — target schema DDL, Maker/Checker conversion, human review, DMS artifacts.

Response envelopes:
  GET  /api/db-discovery/{id}/schema.sql                       → text/plain (MySQL DDL)
  GET  /api/db-discovery/{id}/conversions                      → {"conversions": [...], "sandbox": {...}}
  POST /api/db-discovery/{id}/conversions                      → {"queued": [...], "skipped": [...]}
  GET  /api/db-discovery/{id}/conversions/{object_id}          → conversion record
  POST /api/db-discovery/{id}/conversions/{object_id}/review   → conversion record
  GET  /api/db-discovery/{id}/conversions.sql                  → text/plain (schema + approved conversions)
  GET  /api/db-discovery/{id}/dms-artifacts.zip                → application/zip
  GET  /api/db-conversion/sandbox                              → {"configured", "reachable", "version"}
"""

from __future__ import annotations

import logging
import os
import re

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from state import app_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["db-conversion"])

DB_CONVERSION_STORAGE_PATH = os.environ.get(
    "DB_CONVERSION_STORAGE_PATH", "/app/temp/db_conversions"
)
MAX_OBJECTS_PER_REQUEST = 25

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_OBJECT_ID = re.compile(r"^(procedure|function|view|trigger):[\w.#$@ -]+$")


class ConversionRequest(BaseModel):
    object_ids: list[str] = Field(min_length=1)


class ReviewRequest(BaseModel):
    action: str  # approve | reject | edit
    sql: str = ""
    note: str = ""


def _report(discovery_id: str) -> dict:
    if not _SAFE_ID.match(discovery_id):
        raise HTTPException(status_code=400, detail="Invalid discovery ID")
    storage = app_state.db_discovery_storage
    if storage is None:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    report = storage.load(discovery_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Discovery not found")
    return report


def _store():
    from services.db_conversion.conversion_store import ConversionStore

    storage = app_state.db_conversion_storage
    if storage is None:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    return ConversionStore(storage)


def _check_object_id(object_id: str) -> None:
    if not _SAFE_OBJECT_ID.match(object_id):
        raise HTTPException(status_code=400, detail="Invalid object ID")


def _text(
    content: str, filename: str, media: str = "text/plain; charset=utf-8"
) -> Response:
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/db-conversion/sandbox")
async def sandbox_status() -> dict:
    from services.db_conversion import mysql_sandbox

    return mysql_sandbox.ping()


@router.get("/db-discovery/{discovery_id}/schema.sql")
async def schema_sql(discovery_id: str) -> Response:
    from services.db_conversion.schema_ddl import render_schema_sql

    report = _report(discovery_id)
    return _text(render_schema_sql(report), f"{discovery_id}-schema.sql")


@router.get("/db-discovery/{discovery_id}/conversions.sql")
async def conversions_sql(discovery_id: str) -> Response:
    from services.db_conversion.schema_ddl import render_schema_sql

    report = _report(discovery_id)
    records = [r for r in _store().list(discovery_id) if r.get("status") == "approved"]
    parts = [
        render_schema_sql(report),
        "",
        f"-- Approved conversions: {len(records)}",
        "",
    ]
    for r in sorted(
        records, key=lambda r: ("view" != r["object_type"], r["object_id"])
    ):
        parts.append(
            f"-- {r['object_type']} {r['object_name']}"
            + (" (edited by reviewer)" if r.get("edited") else "")
        )
        if r.get("reviewer_note"):
            parts.append(f"-- Reviewer: {r['reviewer_note']}")
        parts.append("DELIMITER $$")
        parts.append(r["final_sql"].rstrip().rstrip(";") + "$$")
        parts.append("DELIMITER ;")
        parts.append("")
    return _text("\n".join(parts), f"{discovery_id}-conversions.sql")


@router.get("/db-discovery/{discovery_id}/dms-artifacts.zip")
async def dms_artifacts(discovery_id: str) -> Response:
    from services.db_dms.artifacts import build_zip

    report = _report(discovery_id)
    return Response(
        content=build_zip(report),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{discovery_id}-dms-artifacts.zip"'
        },
    )


@router.get("/db-discovery/{discovery_id}/conversions")
async def list_conversions(discovery_id: str) -> dict:
    from services.db_conversion import mysql_sandbox

    _report(discovery_id)
    return {"conversions": _store().list(discovery_id), "sandbox": mysql_sandbox.ping()}


def _run_conversions(report: dict, object_ids: list[str]) -> None:
    """Background task (plain def → threadpool): Bedrock and MySQL calls block."""
    from services.db_conversion.conversion_agent import ConversionAgent

    ConversionAgent(_store()).convert_many(report, object_ids)


@router.post("/db-discovery/{discovery_id}/conversions")
async def start_conversions(
    discovery_id: str, body: ConversionRequest, background_tasks: BackgroundTasks
) -> dict:
    report = _report(discovery_id)
    if len(body.object_ids) > MAX_OBJECTS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_OBJECTS_PER_REQUEST} objects per request",
        )
    by_id = {o["id"]: o for o in report["objects"]}
    objects, skipped = [], []
    for oid in dict.fromkeys(body.object_ids):
        obj = by_id.get(oid)
        if obj is None or obj["type"] not in (
            "procedure",
            "function",
            "view",
            "trigger",
        ):
            skipped.append(
                {
                    "object_id": oid,
                    "reason": "not a convertible object in this discovery",
                }
            )
        else:
            objects.append(obj)
    queued = _store().queue(discovery_id, objects)
    queued_ids = {r["object_id"] for r in queued}
    skipped += [
        {"object_id": o["id"], "reason": "already queued or running"}
        for o in objects
        if o["id"] not in queued_ids
    ]
    if queued:
        background_tasks.add_task(
            _run_conversions, report, [r["object_id"] for r in queued]
        )
    return {"queued": queued, "skipped": skipped}


@router.get("/db-discovery/{discovery_id}/conversions/{object_id}")
async def get_conversion(discovery_id: str, object_id: str) -> dict:
    _report(discovery_id)
    _check_object_id(object_id)
    record = _store().get(discovery_id, object_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Conversion not found")
    return record


class TestParam(BaseModel):
    name: str
    value: str | int | float | bool | None = None


class TestCase(BaseModel):
    name: str = "custom"
    params: list[TestParam] = []


class TestCasesRequest(BaseModel):
    tests: list[TestCase] = Field(default_factory=list, max_length=10)


_SAFE_PARAM = re.compile(r"^@?[A-Za-z_]\w*$")


@router.put("/db-discovery/{discovery_id}/conversions/{object_id}/tests")
async def set_custom_tests(
    discovery_id: str, object_id: str, body: TestCasesRequest
) -> dict:
    """Reviewer-supplied behaviour test cases, run alongside generated ones on the next conversion."""
    _report(discovery_id)
    _check_object_id(object_id)
    store = _store()
    if store.get(discovery_id, object_id) is None:
        raise HTTPException(status_code=404, detail="Conversion not found")
    tests = []
    for t in body.tests:
        for p in t.params:
            if not _SAFE_PARAM.match(p.name):
                raise HTTPException(
                    status_code=400, detail=f"Invalid parameter name: {p.name!r}"
                )
        tests.append(
            {
                "name": t.name[:200],
                "params": [
                    {"name": p.name.lstrip("@"), "value": p.value} for p in t.params
                ],
            }
        )
    return store.update(discovery_id, object_id, custom_tests=tests)


@router.post("/db-discovery/{discovery_id}/conversions/{object_id}/review")
def review_conversion(discovery_id: str, object_id: str, body: ReviewRequest) -> dict:
    """Plain def: an edit compiles in the sandbox, which blocks."""
    from services.db_conversion import mysql_sandbox

    report = _report(discovery_id)
    _check_object_id(object_id)
    store = _store()
    record = store.get(discovery_id, object_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Conversion not found")
    if record.get("status") in ("queued", "converting"):
        raise HTTPException(status_code=409, detail="Conversion is still running")
    note = body.note.strip()[:2000]
    if body.action == "approve":
        if not record.get("final_sql"):
            raise HTTPException(
                status_code=400,
                detail="Nothing to approve: the conversion produced no SQL",
            )
        return store.update(
            discovery_id, object_id, status="approved", reviewer_note=note
        )
    if body.action == "reject":
        return store.update(
            discovery_id, object_id, status="rejected", reviewer_note=note
        )
    if body.action == "edit":
        sql = body.sql.strip()
        if not sql:
            raise HTTPException(status_code=400, detail="sql is required for an edit")
        try:
            result = mysql_sandbox.compile_object(report, record["object_type"], sql)
        except mysql_sandbox.SandboxUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not result.ok:
            raise HTTPException(
                status_code=400,
                detail=f"Edited SQL does not compile in MySQL 8.0: error {result.errno}: {result.message}",
            )
        return store.update(
            discovery_id,
            object_id,
            status="approved",
            final_sql=mysql_sandbox.strip_statement(sql),
            edited=True,
            reviewer_note=note,
        )
    raise HTTPException(
        status_code=400, detail="action must be approve, reject or edit"
    )
