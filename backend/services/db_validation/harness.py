"""Row counts, checksums and procedure-result comparison between SQL Server and MySQL.

Which columns are compared comes from the discovery report: computed columns, columns whose type has
no mechanical MySQL mapping, spatial columns (the engines store different binary formats) and row
versions are excluded, and the report lists every exclusion so a reviewer knows what was not proven.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from services.db_conversion.schema_ddl import quote as mysql_quote
from services.db_validation import connections
from services.db_validation.normalize import (
    TableChecksum,
    merge_json_chunks,
    normalize_row,
)

logger = logging.getLogger(__name__)

_EXCLUDED_BASE_TYPES = {
    "geography",
    "geometry",
    "hierarchyid",
    "sql_variant",
    "rowversion",
    "timestamp",
    "xml",
}
_UNMAPPABLE = {"—", "?"}
_SAFE_PROC = re.compile(r"^[A-Za-z_][\w]*\.[A-Za-z_][\w]*$")
_SAFE_PARAM = re.compile(r"^@?[A-Za-z_]\w*$")
_SAFE_CALL = re.compile(r"^CALL\s+[`\w.]+\s*\([^;]*\)\s*$", re.I)


def ms_quote(identifier: str) -> str:
    return "[" + identifier.replace("]", "]]") + "]"


@dataclass
class TablePlan:
    object_id: str
    schema: str
    name: str
    columns: list[str]
    excluded: list[dict] = field(default_factory=list)
    # Spatial columns: left out of checksums, but copied (as WKB) so NOT NULL columns load.
    spatial: list[dict] = field(default_factory=list)


def plan_tables(report: dict, object_ids: list[str] | None = None) -> list[TablePlan]:
    plans = []
    wanted = set(object_ids) if object_ids else None
    for o in report["objects"]:
        if o["type"] != "table" or not o["columns"]:
            continue
        if wanted is not None and o["id"] not in wanted:
            continue
        cols, excluded, spatial = [], [], []
        for c in o["columns"]:
            base = c["sql_type"].split("(")[0].lower()
            if base in ("geography", "geometry") and not c.get("computed"):
                spatial.append({"name": c["name"], "base": base})
            if c.get("computed"):
                excluded.append({"column": c["name"], "reason": "computed column"})
            elif c["mysql_type"] in _UNMAPPABLE or base in _EXCLUDED_BASE_TYPES:
                excluded.append(
                    {
                        "column": c["name"],
                        "reason": f"type {c['sql_type']} is stored differently",
                    }
                )
            else:
                cols.append(c["name"])
        plans.append(
            TablePlan(o["id"], o["schema"], o["name"], cols, excluded, spatial)
        )
    return plans


def _source_select(plan: TablePlan, top: int | None = None) -> str:
    cols = ", ".join(ms_quote(c) for c in plan.columns)
    top_sql = f"TOP ({int(top)}) " if top else ""
    return f"SELECT {top_sql}{cols} FROM {ms_quote(plan.schema)}.{ms_quote(plan.name)}"


def _copy_select(plan: TablePlan, top: int | None = None) -> str:
    """Like _source_select, plus spatial columns as WKB (longitude, latitude order)."""
    exprs = [ms_quote(c) for c in plan.columns] + [
        f"{ms_quote(s['name'])}.STAsBinary() AS {ms_quote(s['name'])}"
        for s in plan.spatial
    ]
    top_sql = f"TOP ({int(top)}) " if top else ""
    return f"SELECT {top_sql}{', '.join(exprs)} FROM {ms_quote(plan.schema)}.{ms_quote(plan.name)}"


def _spatial_mark(tcur, plan: TablePlan, s: dict) -> str:
    """Insert placeholder for a spatial column, using the target column's SRID."""
    tcur.execute(
        "SELECT SRS_ID FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (plan.schema, plan.name, s["name"]),
    )
    row = tcur.fetchone()
    srid = int(row[0]) if row and row[0] is not None else 0
    if srid == 0:
        return "ST_GeomFromWKB(%s)"
    # SQL Server WKB is (x=longitude, y=latitude); MySQL's default for SRID 4326 is lat-long.
    return f"ST_GeomFromWKB(%s, {srid}, 'axis-order=long-lat')"


def _target_select(plan: TablePlan) -> str:
    cols = ", ".join(mysql_quote(c) for c in plan.columns)
    return f"SELECT {cols} FROM {mysql_quote(plan.schema)}.{mysql_quote(plan.name)}"


def _checksum(cursor, sql: str) -> TableChecksum:
    cursor.execute(sql)
    checksum = TableChecksum()
    while True:
        rows = cursor.fetchmany(5000)
        if not rows:
            break
        for row in rows:
            checksum.add(row)
    return checksum


def compare_table(src_conn, tgt_conn, plan: TablePlan) -> dict:
    result = {
        "object_id": plan.object_id,
        "table": f"{plan.schema}.{plan.name}",
        "columns_compared": len(plan.columns),
        "excluded_columns": plan.excluded,
        "source_rows": None,
        "target_rows": None,
        "source_checksum": "",
        "target_checksum": "",
        "status": "",
        "error": "",
    }
    if not plan.columns:
        result.update(status="error", error="No comparable columns")
        return result
    started = time.monotonic()
    try:
        src = _checksum(src_conn.cursor(), _source_select(plan))
        result.update(source_rows=src.rows, source_checksum=src.hexdigest())
    except Exception as exc:
        errno = exc.args[0] if getattr(exc, "args", None) else None
        if (
            errno == 208
        ):  # Invalid object name: the scripts define a table the database does not have
            result.update(
                status="missing_in_source",
                error="Table is defined in the scripts but does not exist in the SQL Server database",
            )
        else:
            result.update(
                status="error", error=f"source: {connections.safe_error(exc)}"
            )
        return result
    try:
        tgt = _checksum(tgt_conn.cursor(), _target_select(plan))
        result.update(target_rows=tgt.rows, target_checksum=tgt.hexdigest())
    except Exception as exc:
        errno = exc.args[0] if getattr(exc, "args", None) else None
        status = "missing_in_target" if errno in (1146, 1049) else "error"
        result.update(status=status, error=f"target: {connections.safe_error(exc)}")
        return result
    if src.rows == tgt.rows and src.value == tgt.value:
        result["status"] = "match"
    elif tgt.rows == 0:
        # An empty target is a load that has not happened yet (AWS DMS or the test copy), not a
        # difference in the migrated data.
        result["status"] = "not_loaded"
        result["error"] = (
            "Target table is empty: load the data (AWS DMS, or the test copy) before validating"
        )
    else:
        result["status"] = "mismatch"
    result["seconds"] = round(time.monotonic() - started, 2)
    return result


# --- procedure tests ---------------------------------------------------------------------------


def _all_result_sets(cursor) -> list[list[tuple]]:
    sets = []
    while True:
        if cursor.description:
            sets.append([tuple(r) for r in cursor.fetchall()])
        if not cursor.nextset():
            break
    return sets


def validate_procedure_test(test: dict) -> str | None:
    if not _SAFE_PROC.match(str(test.get("procedure", ""))):
        return "procedure must be schema.name"
    for p in test.get("params", []):
        if not _SAFE_PARAM.match(str(p.get("name", ""))):
            return f"invalid parameter name: {p.get('name')!r}"
    call = test.get("target_call")
    if call and not _SAFE_CALL.match(call):
        return "target_call must be a single CALL statement"
    return None


def compare_procedure(src_conn, tgt_conn, test: dict) -> dict:
    schema, name = test["procedure"].split(".", 1)
    params = test.get("params", [])
    values = [p.get("value") for p in params]
    ordered = test.get("ordered", True)
    result = {
        "procedure": test["procedure"],
        "params": params,
        "status": "",
        "error": "",
        "details": [],
    }
    src_sql = f"EXEC {ms_quote(schema)}.{ms_quote(name)} " + ", ".join(
        f"@{p['name'].lstrip('@')} = %s" for p in params
    )
    tgt_sql = test.get("target_call") or (
        f"CALL {mysql_quote(schema)}.{mysql_quote(name)}("
        + ", ".join(["%s"] * len(params))
        + ")"
    )
    try:
        cur = src_conn.cursor()
        cur.execute(src_sql, tuple(values) if values else None)
        src_sets = [merge_json_chunks(s) for s in _all_result_sets(cur)]
    except Exception as exc:
        result.update(status="error", error=f"source: {connections.safe_error(exc)}")
        return result
    try:
        cur = tgt_conn.cursor()
        cur.execute(tgt_sql, tuple(values) if values else None)
        tgt_sets = _all_result_sets(cur)
    except Exception as exc:
        result.update(status="error", error=f"target: {connections.safe_error(exc)}")
        return result
    result["source_result_sets"] = [len(s) for s in src_sets]
    result["target_result_sets"] = [len(s) for s in tgt_sets]
    if len(src_sets) != len(tgt_sets):
        result["details"].append(
            f"result set count differs: {len(src_sets)} vs {len(tgt_sets)}"
        )
    for k, (a, b) in enumerate(zip(src_sets, tgt_sets)):
        na, nb = [normalize_row(r) for r in a], [normalize_row(r) for r in b]
        if not ordered:
            na, nb = sorted(na), sorted(nb)
        if len(na) != len(nb):
            result["details"].append(f"set {k}: row count {len(na)} vs {len(nb)}")
        for i, (ra, rb) in enumerate(zip(na, nb)):
            if ra != rb:
                result["details"].append(
                    f"set {k} row {i}: source {ra[:300]!r} vs target {rb[:300]!r}"
                )
                break
    result["status"] = "match" if not result["details"] else "mismatch"
    return result


# --- runs --------------------------------------------------------------------------------------

Progress = Callable[[int, str], None]


def run_validation(
    report: dict,
    table_ids: list[str] | None,
    procedure_tests: list[dict],
    progress: Progress | None = None,
    connect_source=None,
    connect_target=None,
) -> dict:
    plans = plan_tables(report, table_ids)
    src = (connect_source or connections.connect_source)()
    tgt = (connect_target or connections.connect_target)()
    tables, procs = [], []
    try:
        total = max(1, len(plans) + len(procedure_tests))
        for k, plan in enumerate(plans):
            if progress:
                progress(int(100 * k / total), f"Comparing {plan.schema}.{plan.name}")
            tables.append(compare_table(src, tgt, plan))
        for k, test in enumerate(procedure_tests):
            if progress:
                progress(
                    int(100 * (len(plans) + k) / total), f"Calling {test['procedure']}"
                )
            procs.append(compare_procedure(src, tgt, test))
    finally:
        src.close()
        tgt.close()
    counts: dict[str, int] = {}
    for r in tables + procs:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"tables": tables, "procedures": procs, "summary": counts}


def _target_value(value):
    import datetime as dt
    import uuid

    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value


def copy_tables(
    report: dict,
    table_ids: list[str] | None,
    max_rows: int,
    progress: Progress | None = None,
    connect_source=None,
    connect_target=None,
) -> dict:
    """Test-only: replace target rows with up to max_rows source rows per table."""
    plans = plan_tables(report, table_ids)
    src = (connect_source or connections.connect_source)()
    tgt = (connect_target or connections.connect_target)()
    results = []
    try:
        tcur = tgt.cursor()
        tcur.execute("SET SESSION FOREIGN_KEY_CHECKS = 0")
        for k, plan in enumerate(plans):
            if progress:
                progress(
                    int(100 * k / max(1, len(plans))),
                    f"Copying {plan.schema}.{plan.name}",
                )
            item = {
                "object_id": plan.object_id,
                "table": f"{plan.schema}.{plan.name}",
                "rows": 0,
                "status": "",
                "error": "",
            }
            try:
                scur = src.cursor()
                scur.execute(_copy_select(plan, top=max_rows))
                rows = [tuple(_target_value(v) for v in r) for r in scur.fetchall()]
                target = f"{mysql_quote(plan.schema)}.{mysql_quote(plan.name)}"
                tcur.execute(f"DELETE FROM {target}")
                if rows:
                    names = plan.columns + [s["name"] for s in plan.spatial]
                    cols = ", ".join(mysql_quote(c) for c in names)
                    marks = ", ".join(
                        ["%s"] * len(plan.columns)
                        + [_spatial_mark(tcur, plan, s) for s in plan.spatial]
                    )
                    tcur.executemany(
                        f"INSERT INTO {target} ({cols}) VALUES ({marks})", rows
                    )
                item.update(rows=len(rows), status="copied")
            except Exception as exc:
                errno = exc.args[0] if getattr(exc, "args", None) else None
                if (
                    errno == 208
                ):  # the scripts define a table the database does not have
                    item.update(
                        status="missing_in_source",
                        error="Table is defined in the scripts but does not exist in the SQL Server database",
                    )
                else:
                    item.update(status="error", error=connections.safe_error(exc))
            results.append(item)
    finally:
        src.close()
        tgt.close()
    return {"tables": results}
