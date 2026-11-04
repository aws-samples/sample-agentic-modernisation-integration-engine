"""Checker 3: run the original object on SQL Server and the conversion on MySQL with the same inputs
on the same data, and compare what they return and what they change.

Data: before testing, every table the object references is copied in full from SQL Server into the
MySQL sandbox (up to ``MAX_TABLE_ROWS``), including computed, spatial and XML values in forms MySQL can
store, so both engines start from identical data.

Writes: SQL Server runs the call inside a transaction that is always rolled back. MySQL may commit
inside a converted procedure (``START TRANSACTION`` / DDL commit implicitly), so each touched table
is snapshotted inside the sandbox before the call and restored afterwards. After-state is compared
per table by row count and checksum; date/time columns are left out of the write comparison because
values such as ``SYSDATETIME()`` differ between any two runs.

Without a SQL Server source the check is skipped and the conversion is marked as not behaviour-tested.
"""

from __future__ import annotations

import json
import logging
import re
import threading

from services.db_conversion import mysql_sandbox
from services.db_conversion.schema_ddl import quote as mq
from services.db_validation import connections
from services.db_validation.harness import ms_quote
from services.db_validation.normalize import (
    TableChecksum,
    merge_json_chunks,
    normalize_row,
)

logger = logging.getLogger(__name__)

MAX_TABLE_ROWS = 250_000
MAX_TESTS = 6
_COPY_LOCK = threading.Lock()
# (discovery_id, schema generation) -> table ids whose data is loaded in the sandbox
_copied: dict[tuple[str, int], set[str]] = {}

_DATETIME_BASES = {
    "date",
    "datetime",
    "datetime2",
    "smalldatetime",
    "datetimeoffset",
    "time",
}
_WRITE_RE = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE)\b", re.I)
_SAFE_NAME = re.compile(r"^@?[A-Za-z_]\w*$")


class BehaviourTestSkipped(Exception):
    pass


def source_configured() -> bool:
    return connections.source_config() is not None


# --- data copy ---------------------------------------------------------------------------------


def _base(col: dict) -> str:
    return col["sql_type"].split("(")[0]


def _source_expr(col: dict) -> str:
    name, base = ms_quote(col["name"]), _base(col)
    if base in ("geography", "geometry"):
        return f"{name}.STAsText()"
    if base in ("xml", "hierarchyid", "sql_variant"):
        return f"CAST({name} AS nvarchar(max))"
    if base in ("rowversion", "timestamp"):
        return f"CAST({name} AS bigint)"
    return name


def _target_placeholder(col: dict) -> str:
    if _base(col) in ("geography", "geometry"):
        srid = 4326 if _base(col) == "geography" else 0
        return (
            f"ST_GeomFromText(%s, {srid}, 'axis-order=long-lat')"
            if srid
            else "ST_GeomFromText(%s)"
        )
    return "%s"


def _value(v):
    from services.db_validation.harness import _target_value

    return _target_value(v)


def ensure_tables_copied(report: dict, tables: list[dict], src_conn) -> list[dict]:
    """Copy each table from SQL Server into the sandbox once per schema load. Returns problems."""
    did = report["discovery_id"]
    problems: list[dict] = []
    with _COPY_LOCK:
        mysql_sandbox.load_schema(report)
        # A schema load drops every sandbox table, so data copied under an older generation is gone.
        done = _copied.setdefault((did, mysql_sandbox.schema_generation()), set())
        tgt = mysql_sandbox.connect()
        try:
            tcur = tgt.cursor()
            tcur.execute("SET SESSION FOREIGN_KEY_CHECKS = 0")
            tcur.execute("SET SESSION sql_mode = 'NO_ENGINE_SUBSTITUTION'")
            for t in tables:
                if t["id"] in done:
                    continue
                table_ms = f"{ms_quote(t['schema'])}.{ms_quote(t['name'])}"
                scur = src_conn.cursor()
                scur.execute(f"SELECT COUNT_BIG(*) FROM {table_ms}")
                count = int(scur.fetchone()[0])
                if count > MAX_TABLE_ROWS:
                    problems.append(
                        {
                            "table": f"{t['schema']}.{t['name']}",
                            "reason": f"{count} rows exceeds {MAX_TABLE_ROWS}",
                        }
                    )
                    continue
                cols = t["columns"]
                scur.execute(
                    f"SELECT {', '.join(_source_expr(c) for c in cols)} FROM {table_ms}"
                )
                target = f"{mq(t['schema'])}.{mq(t['name'])}"
                tcur.execute(f"DELETE FROM {target}")
                insert = (
                    f"INSERT INTO {target} ({', '.join(mq(c['name']) for c in cols)}) "
                    f"VALUES ({', '.join(_target_placeholder(c) for c in cols)})"
                )
                while True:
                    rows = scur.fetchmany(2000)
                    if not rows:
                        break
                    tcur.executemany(
                        insert, [tuple(_value(v) for v in r) for r in rows]
                    )
                done.add(t["id"])
        finally:
            tgt.close()
    return problems


# --- test case design --------------------------------------------------------------------------

_PARAM_RE = re.compile(
    r"(@\w+)\s+([\w\[\].]+(?:\s*\([^)]*\))?)(?:\s*=\s*([^,\n]+?))?\s*(OUT(?:PUT)?|READONLY)?\s*(?:,|\n|\bAS\b|\bWITH\b)",
    re.I,
)


def source_parameters(source: str) -> list[dict]:
    header = re.split(r"\bAS\b\s*(?:BEGIN\b|\n)", source, maxsplit=1, flags=re.I)[0]
    params = []
    for m in _PARAM_RE.finditer(header + "\n"):
        params.append(
            {
                "name": m.group(1).lstrip("@"),
                "type": m.group(2),
                "default": (m.group(3) or "").strip(),
                "mode": (m.group(4) or "IN").upper(),
            }
        )
    return params


def _sample_rows(src_conn, tables: list[dict]) -> str:
    out = []
    for t in tables[:6]:
        cols = [
            c
            for c in t["columns"]
            if _base(c) not in ("geography", "geometry", "varbinary", "image", "xml")
        ][:12]
        if not cols:
            continue
        cur = src_conn.cursor()
        cur.execute(
            f"SELECT TOP (5) {', '.join(ms_quote(c['name']) for c in cols)} "
            f"FROM {ms_quote(t['schema'])}.{ms_quote(t['name'])}"
        )
        rows = cur.fetchall()
        lines = [" | ".join(c["name"] for c in cols)] + [
            " | ".join(str(v)[:40] for v in r) for r in rows
        ]
        out.append(f"### {t['schema']}.{t['name']}\n" + "\n".join(lines))
    return "\n\n".join(out) or "(no sample rows)"


def parse_test_cases(text: str) -> list[dict]:
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    raw = m.group(1) if m else text
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    cases = []
    for item in data[:MAX_TESTS]:
        if not isinstance(item, dict):
            continue
        params = []
        for p in item.get("params", []) or []:
            name = str(p.get("name", "")).lstrip("@")
            value = p.get("value")
            if _SAFE_NAME.match(name) and (
                value is None or isinstance(value, (str, int, float, bool))
            ):
                params.append({"name": name, "value": value})
        cases.append(
            {
                "name": str(item.get("name", "case"))[:200],
                "params": params,
                "origin": "generated",
            }
        )
    return cases


# --- execution ---------------------------------------------------------------------------------


def _result_sets(cursor) -> list[list[tuple]]:
    sets = []
    while True:
        if cursor.description:
            sets.append([tuple(r) for r in cursor.fetchall()])
        if not cursor.nextset():
            break
    return sets


def _mysql_params(tcur, schema: str, name: str) -> list[dict]:
    tcur.execute(
        "SELECT PARAMETER_NAME, PARAMETER_MODE FROM information_schema.PARAMETERS "
        "WHERE SPECIFIC_SCHEMA = %s AND SPECIFIC_NAME = %s AND PARAMETER_NAME IS NOT NULL "
        "ORDER BY ORDINAL_POSITION",
        (schema, name),
    )
    return [{"name": r[0], "mode": (r[1] or "IN").upper()} for r in tcur.fetchall()]


def _key(name: str) -> str:
    k = name.lower().lstrip("@")
    for prefix in ("p_", "in_", "i_", "v_"):
        if k.startswith(prefix):
            k = k[len(prefix) :]
    return k.replace("_", "")


def build_mysql_call(
    schema: str, name: str, mysql_params: list[dict], case_params: list[dict]
) -> tuple[str, list]:
    by_key = {_key(p["name"]): p["value"] for p in case_params}
    args, values = [], []
    for p in mysql_params:
        if p["mode"] in ("OUT", "INOUT") and _key(p["name"]) not in by_key:
            var = re.sub(r"\W", "", p["name"])
            args.append(f"@amie_out_{var}")
            continue
        if _key(p["name"]) not in by_key:
            raise BehaviourTestSkipped(f"no test value for MySQL parameter {p['name']}")
        args.append("%s")
        values.append(by_key[_key(p["name"])])
    return f"CALL {mq(schema)}.{mq(name)}({', '.join(args)})", values


def _write_tables(obj: dict, tables: list[dict]) -> list[dict]:
    return tables if _WRITE_RE.search(obj.get("source", "")) else []


def _state(cursor, t: dict, engine: str) -> dict:
    # Left out: dates/times (SYSDATETIME differs between runs), identity and sequence-default keys
    # (a SQL Server rollback does not reset a sequence, so generated keys diverge), and types stored
    # differently on the two engines.
    skip = _DATETIME_BASES | {"geography", "geometry", "rowversion", "timestamp"}
    cols = [
        c
        for c in t["columns"]
        if _base(c) not in skip
        and not c.get("identity")
        and not c.get("default_sequence")
    ]
    if not cols:
        return {"rows": None, "checksum": ""}
    if engine == "mssql":
        exprs = ", ".join(_source_expr(c) for c in cols)
        cursor.execute(
            f"SELECT {exprs} FROM {ms_quote(t['schema'])}.{ms_quote(t['name'])}"
        )
    else:
        cursor.execute(
            f"SELECT {', '.join(mq(c['name']) for c in cols)} FROM {mq(t['schema'])}.{mq(t['name'])}"
        )
    cs = TableChecksum()
    for row in cursor.fetchall():
        cs.add(row)
    return {"rows": cs.rows, "checksum": cs.hexdigest()}


def _snapshot(tcur, t: dict) -> str:
    snap = mq("amie_snap") + "." + mq(f"{t['schema']}__{t['name']}")
    tcur.execute("CREATE DATABASE IF NOT EXISTS `amie_snap`")
    tcur.execute(f"DROP TABLE IF EXISTS {snap}")
    tcur.execute(
        f"CREATE TABLE {snap} AS SELECT * FROM {mq(t['schema'])}.{mq(t['name'])}"
    )
    return snap


def _restore(tcur, t: dict, snap: str) -> None:
    target = f"{mq(t['schema'])}.{mq(t['name'])}"
    tcur.execute(f"DELETE FROM {target}")
    tcur.execute(f"INSERT INTO {target} SELECT * FROM {snap}")
    tcur.execute(f"DROP TABLE IF EXISTS {snap}")
    # Reset AUTO_INCREMENT to max+1 so the next test starts from the same key position.
    tcur.execute(f"ALTER TABLE {target} AUTO_INCREMENT = 1")


def _compare_sets(src_sets, tgt_sets, ordered: bool) -> list[str]:
    diffs = []
    if len(src_sets) != len(tgt_sets):
        diffs.append(f"result set count {len(src_sets)} vs {len(tgt_sets)}")
    for k, (a, b) in enumerate(zip(src_sets, tgt_sets)):
        na, nb = [normalize_row(r) for r in a], [normalize_row(r) for r in b]
        if not ordered:
            na, nb = sorted(na), sorted(nb)
        if len(na) != len(nb):
            diffs.append(f"set {k}: {len(na)} rows vs {len(nb)} rows")
        for i, (ra, rb) in enumerate(zip(na, nb)):
            if ra != rb:
                diffs.append(
                    f"set {k} row {i}: SQL Server {ra[:240]!r} vs MySQL {rb[:240]!r}"
                )
                break
    return diffs


def run_case(obj: dict, case: dict, tables: list[dict], src_conn) -> dict:
    """Run one case on both engines; never leaves changes behind."""
    schema, name = obj["schema"], obj["name"]
    result = {
        "name": case["name"],
        "params": case["params"],
        "origin": case.get("origin", "generated"),
        "status": "",
        "details": [],
        "source_error": "",
        "target_error": "",
    }
    writes = _write_tables(obj, tables)
    ordered = (
        bool(re.search(r"\bORDER\s+BY\b", obj.get("source", ""), re.I))
        and "FOR JSON" not in obj.get("source", "").upper()
    )

    if obj["type"] == "view":
        src_sql, src_vals = f"SELECT * FROM {ms_quote(schema)}.{ms_quote(name)}", None
        tgt_builder = lambda tcur: (f"SELECT * FROM {mq(schema)}.{mq(name)}", None)  # noqa: E731
    elif obj["type"] == "function":
        vals = [p["value"] for p in case["params"]]
        marks = ", ".join(["%s"] * len(vals))
        if obj.get("subtype") == "scalar":
            src_sql = f"SELECT {ms_quote(schema)}.{ms_quote(name)}({marks})"

            def tgt_builder(tcur):
                return f"SELECT {mq(schema)}.{mq(name)}({marks})", vals
        else:
            raise BehaviourTestSkipped(
                "table-valued functions have no MySQL equivalent to call directly"
            )
        src_vals = vals
    elif obj["type"] == "procedure":
        if re.search(r"\bREADONLY\b", obj.get("source", ""), re.I):
            raise BehaviourTestSkipped(
                "takes table-valued parameters, which the harness cannot pass to SQL Server"
            )
        src_sql = f"EXEC {ms_quote(schema)}.{ms_quote(name)} " + ", ".join(
            f"@{p['name']} = %s" for p in case["params"]
        )
        src_vals = [p["value"] for p in case["params"]]

        def tgt_builder(tcur):
            return build_mysql_call(
                schema, name, _mysql_params(tcur, schema, name), case["params"]
            )
    else:
        raise BehaviourTestSkipped(
            f"{obj['type']} objects are compiled and scored, not behaviour-tested"
        )

    # SQL Server: the driver runs with autocommit off, so the call happens inside a transaction that
    # is always rolled back. A COMMIT inside the procedure only decrements @@TRANCOUNT.
    src_sets, src_after = [], {}
    try:
        scur = src_conn.cursor()
        scur.execute(src_sql, tuple(src_vals) if src_vals else None)
        src_sets = [merge_json_chunks(s) for s in _result_sets(scur)]
        src_after = {t["id"]: _state(src_conn.cursor(), t, "mssql") for t in writes}
    except Exception as exc:
        result["source_error"] = connections.safe_error(exc)[:500]
    finally:
        try:
            src_conn.rollback()
        except Exception:
            logger.warning(
                "SQL Server rollback after behaviour test failed", exc_info=True
            )

    # MySQL: snapshot touched tables, run, capture, restore.
    tgt_sets, tgt_after = [], {}
    tgt = mysql_sandbox.connect()
    snaps: list[tuple[dict, str]] = []
    try:
        tcur = tgt.cursor()
        for t in writes:
            snaps.append((t, _snapshot(tcur, t)))
        sql, vals = tgt_builder(tcur)
        try:
            tcur.execute(sql, tuple(vals) if vals else None)
            tgt_sets = _result_sets(tcur)
            tgt_after = {t["id"]: _state(tgt.cursor(), t, "mysql") for t in writes}
        except Exception as exc:
            result["target_error"] = connections.safe_error(exc)[:500]
            try:
                tcur.execute("ROLLBACK")
            except Exception:
                pass
    finally:
        try:
            rc = tgt.cursor()
            rc.execute("SET SESSION FOREIGN_KEY_CHECKS = 0")
            for t, snap in snaps:
                _restore(rc, t, snap)
        finally:
            tgt.close()

    if result["source_error"] or result["target_error"]:
        if result["source_error"] and result["target_error"]:
            result["status"] = "match"
            result["details"].append("both engines raised an error")
        else:
            result["status"] = "mismatch"
            result["details"].append(
                "only SQL Server raised an error"
                if result["source_error"]
                else "only MySQL raised an error"
            )
        return result
    diffs = _compare_sets(src_sets, tgt_sets, ordered)
    for t in writes:
        a, b = src_after.get(t["id"]), tgt_after.get(t["id"])
        if a and b and a != b:
            diffs.append(
                f"table {t['schema']}.{t['name']} after the call: {a['rows']} vs {b['rows']} rows, checksums differ"
            )
    result["status"] = "match" if not diffs else "mismatch"
    result["details"] = diffs
    # What each engine actually returned (first rows, normalised), so the Maker and the reviewer
    # see the values, not only a row count.
    result["evidence"] = {
        "source": [[normalize_row(r)[:300] for r in s[:3]] for s in src_sets[:3]],
        "target": [[normalize_row(r)[:300] for r in s[:3]] for s in tgt_sets[:3]],
    }
    result["result_rows"] = {
        "source": [len(s) for s in src_sets],
        "target": [len(s) for s in tgt_sets],
    }
    return result


def summarise(results: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    passed = bool(results) and all(r["status"] == "match" for r in results)
    return {
        "status": "passed" if passed else "failed",
        "counts": counts,
        "tests": results,
    }


def _describe_output(sets: list[list[str]] | None, counts: list[int] | None) -> str:
    if counts is None:
        return "(not captured)"
    if not counts:
        return "no result set at all"
    parts = []
    for k, n in enumerate(counts):
        rows = (sets or [[]] * len(counts))[k] if sets and k < len(sets) else []
        if n == 0:
            parts.append(f"result set {k + 1}: NO rows")
        else:
            shown = "; ".join(r.replace("\x1f", " | ") for r in rows)
            parts.append(f"result set {k + 1}: {n} row(s), first: {shown}")
    return "; ".join(parts)


def feedback_lines(behaviour: dict) -> list[str]:
    """Each failing test as measured fact: inputs, what SQL Server returned, what MySQL returned."""
    lines = []
    for t in behaviour.get("tests", []):
        if t["status"] != "mismatch":
            continue
        params = (
            ", ".join(f"{p['name']}={p['value']!r}" for p in t["params"])
            or "no parameters"
        )
        if t.get("source_error") or t.get("target_error"):
            src = (
                f"raised an error: {t['source_error']}"
                if t.get("source_error")
                else "succeeded"
            )
            tgt = (
                f"raised an error: {t['target_error']}"
                if t.get("target_error")
                else "succeeded"
            )
            lines.append(
                f"- Test '{t['name']}' ({params}): SQL Server {src}; MySQL {tgt}."
            )
            continue
        rows = t.get("result_rows") or {}
        ev = t.get("evidence") or {}
        lines.append(
            f"- Test '{t['name']}' ({params}):\n"
            f"    SQL Server returned {_describe_output(ev.get('source'), rows.get('source'))}\n"
            f"    MySQL returned      {_describe_output(ev.get('target'), rows.get('target'))}"
        )
        table_diffs = [d for d in t["details"] if d.startswith("table ")]
        lines.extend(f"    {d}" for d in table_diffs)
    return lines


def measured_summary(behaviour: dict | None) -> str:
    """Behaviour evidence for the model checker, so it does not speculate about measured cases."""
    if not behaviour or not behaviour.get("tests"):
        return "- Not measured yet."
    out = []
    for t in behaviour["tests"]:
        if t["status"] == "skipped":
            continue
        params = (
            ", ".join(f"{p['name']}={p['value']!r}" for p in t["params"])
            or "no parameters"
        )
        rows = t.get("result_rows") or {}
        ev = t.get("evidence") or {}
        verdict = "SAME on both engines" if t["status"] == "match" else "DIFFERENT"
        out.append(
            f"- '{t['name']}' ({params}): {verdict}. SQL Server: "
            f"{_describe_output(ev.get('source'), rows.get('source'))}"
        )
    return "\n".join(out) or "- Not measured yet."


# --- checker used by the conversion agent ------------------------------------------------------


class BehaviourChecker:
    """Prepares data and test cases once per object, then runs them for each passing attempt."""

    def __init__(self, model, connect_source=None) -> None:
        self._model = model
        self._connect_source = connect_source or connections.connect_source
        self._conn = None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def _source(self):
        if self._conn is None:
            self._conn = self._connect_source()
        return self._conn

    def prepare(
        self,
        report: dict,
        obj: dict,
        tables: list[dict],
        custom: list[dict],
        existing: list[dict],
    ) -> dict:
        """Returns {"status": "ready", "cases": [...], "copy_problems": [...]} or a skip record."""
        if (
            not source_configured()
            and self._connect_source is connections.connect_source
        ):
            return {
                "status": "not_configured",
                "reason": "No SQL Server source is configured (VALIDATION_SQLSERVER_*).",
            }
        if obj["type"] not in ("procedure", "function", "view"):
            return {
                "status": "skipped",
                "reason": f"{obj['type']} objects are compiled and scored, not behaviour-tested",
            }
        if obj["type"] == "function" and obj.get("subtype") != "scalar":
            return {
                "status": "skipped",
                "reason": "only scalar functions can be called directly on both engines",
            }
        if obj["type"] == "procedure" and re.search(
            r"\bREADONLY\b", obj.get("source", ""), re.I
        ):
            return {
                "status": "skipped",
                "reason": "takes table-valued parameters, which the harness cannot pass to SQL Server",
            }
        src = self._source()
        # The scripts and the live database can differ (a release backup predating newer scripts,
        # objects dropped in production). A missing source object is not a behaviour difference.
        cur = src.cursor()
        cur.execute(
            "SELECT OBJECT_ID(%s)",
            (f"{ms_quote(obj['schema'])}.{ms_quote(obj['name'])}",),
        )
        if cur.fetchone()[0] is None:
            return {
                "status": "skipped",
                "reason": f"{obj['schema']}.{obj['name']} does not exist in the SQL Server source database",
            }
        problems = ensure_tables_copied(report, tables, src)
        if existing:
            generated = [c for c in existing if c.get("origin") == "generated"]
        elif obj["type"] == "view":
            generated = [
                {"name": "select all rows", "params": [], "origin": "generated"}
            ]
        else:
            from services.prompt_loader import load_prompt_result

            prompt = load_prompt_result(
                "sql-conversion-tests",
                {
                    "object_type": obj["type"],
                    "object_name": f"{obj['schema']}.{obj['name']}",
                    "source_sql": obj.get("source", ""),
                    "parameters": json.dumps(
                        source_parameters(obj.get("source", "")), indent=1
                    )
                    or "[]",
                    "sample_rows": _sample_rows(src, tables),
                },
            )
            generated = parse_test_cases(self._model(prompt.content, 6000))
        cases = generated + [{**c, "origin": "custom"} for c in custom]
        if not cases:
            return {"status": "skipped", "reason": "no test cases could be generated"}
        return {"status": "ready", "cases": cases, "copy_problems": problems}

    def run(self, obj: dict, cases: list[dict], tables: list[dict]) -> dict:
        results = []
        for case in cases:
            try:
                results.append(run_case(obj, case, tables, self._source()))
            except BehaviourTestSkipped as exc:
                results.append(
                    {
                        "name": case["name"],
                        "params": case["params"],
                        "origin": case.get("origin"),
                        "status": "skipped",
                        "details": [str(exc)],
                        "source_error": "",
                        "target_error": "",
                    }
                )
        ran = [r for r in results if r["status"] != "skipped"]
        if not ran:
            return {
                "status": "skipped",
                "reason": "; ".join(results[0]["details"]) if results else "no cases",
                "tests": results,
            }
        summary = summarise(ran)
        summary["tests"] = results
        return summary
