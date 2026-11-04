"""MySQL 8.0 sandbox used by the conversion Checker.

The sandbox holds only generated schema: for each discovery, every SQL Server schema becomes a
database and every table is created from the deterministic DDL. A converted object is then compiled
there. MySQL checks syntax (not table existence) when it creates a procedure or function, and it
resolves tables when it creates a view or trigger, so with the schema loaded the compile check is
meaningful for all four object types.

One sandbox is shared by every discovery. A process-wide lock serialises schema loads and compiles,
and the schema is reloaded whenever a different discovery's objects are checked.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass

import pymysql

from config import settings
from services.db_conversion import schema_ddl

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_loaded_discovery: str | None = None
_generation = 0  # incremented on every schema load; a load drops all sandbox data

_DROP_KEYWORD = {
    "procedure": "PROCEDURE",
    "function": "FUNCTION",
    "view": "VIEW",
    "trigger": "TRIGGER",
}


class SandboxUnavailableError(RuntimeError):
    """The sandbox is not configured or cannot be reached."""


@dataclass
class CompileResult:
    ok: bool
    errno: int | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errno": self.errno, "message": self.message}


@dataclass
class SchemaLoadResult:
    databases: int
    tables: int
    failed: list[dict]


def configured() -> bool:
    return bool(settings.MYSQL_SANDBOX_HOST)


def connect(database: str | None = None) -> pymysql.connections.Connection:
    if not configured():
        raise SandboxUnavailableError(
            "MySQL sandbox is not configured (MYSQL_SANDBOX_HOST is empty). "
            "Start the `mysql` compose service."
        )
    try:
        return pymysql.connect(
            host=settings.MYSQL_SANDBOX_HOST,
            port=settings.MYSQL_SANDBOX_PORT,
            user=settings.MYSQL_SANDBOX_USER,
            password=settings.MYSQL_SANDBOX_PASSWORD,
            database=database,
            connect_timeout=10,
            read_timeout=120,
            write_timeout=120,
            charset="utf8mb4",
            autocommit=True,
        )
    except pymysql.err.OperationalError as exc:
        raise SandboxUnavailableError(
            f"MySQL sandbox unreachable: {exc.args[-1]}"
        ) from exc


def ping() -> dict:
    """Reachability and server version, for status endpoints."""
    if not configured():
        return {"configured": False, "reachable": False, "version": ""}
    try:
        conn = connect()
    except SandboxUnavailableError as exc:
        return {
            "configured": True,
            "reachable": False,
            "version": "",
            "error": str(exc),
        }
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
        return {"configured": True, "reachable": True, "version": version}
    finally:
        conn.close()


def _exec(cur, sql: str) -> CompileResult:
    try:
        cur.execute(sql)
        return CompileResult(True)
    except pymysql.MySQLError as exc:
        errno = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        message = str(exc.args[1]) if len(exc.args) > 1 else str(exc)
        return CompileResult(False, errno, message)


def _load_schema_locked(report: dict) -> SchemaLoadResult:
    global _loaded_discovery, _generation
    conn = connect()
    failed: list[dict] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION'"
            )
            dbs = schema_ddl.schema_databases(report)
            for db in dbs:
                cur.execute(f"DROP DATABASE IF EXISTS {schema_ddl.quote(db)}")
                cur.execute(
                    f"CREATE DATABASE {schema_ddl.quote(db)} CHARACTER SET utf8mb4"
                )
            tables = schema_ddl.tables(report)
            for t in tables:
                result = _exec(cur, t.sql)
                if not result.ok and result.errno == 1118:
                    result = _exec(cur, schema_ddl.widen_for_row_size(t.sql))
                if not result.ok:
                    failed.append({"object_id": t.object_id, **result.to_dict()})
        _loaded_discovery = report["discovery_id"]
        _generation += 1
        if failed:
            logger.warning("Sandbox schema load: %d tables failed", len(failed))
        return SchemaLoadResult(len(dbs), len(tables) - len(failed), failed)
    finally:
        conn.close()


def loaded_discovery() -> str | None:
    return _loaded_discovery


def schema_generation() -> int:
    return _generation


def load_schema(report: dict, force: bool = False) -> SchemaLoadResult | None:
    """Load the discovery's schema unless it is already the loaded one."""
    with _LOCK:
        if not force and _loaded_discovery == report["discovery_id"]:
            return None
        return _load_schema_locked(report)


_NAME_AFTER_CREATE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:ALGORITHM\s*=\s*\w+\s+)?(?:DEFINER\s*=\s*\S+\s+)?"
    r"(?:SQL\s+SECURITY\s+\w+\s+)?(PROCEDURE|FUNCTION|VIEW|TRIGGER)\s+((?:`[^`]+`|\w+)(?:\s*\.\s*(?:`[^`]+`|\w+))?)",
    re.I,
)


def strip_statement(sql: str) -> str:
    """Remove DELIMITER lines and a trailing custom delimiter the model may still emit."""
    lines = [
        ln
        for ln in sql.strip().splitlines()
        if not re.match(r"^\s*DELIMITER\b", ln, re.I)
    ]
    text = "\n".join(lines).strip()
    text = re.sub(r"(\$\$|//)\s*$", "", text).strip()
    return text


def compile_object(report: dict, object_type: str, sql: str) -> CompileResult:
    """Compile one converted CREATE statement in the sandbox (drop first, then create)."""
    statement = strip_statement(sql)
    m = _NAME_AFTER_CREATE.match(statement)
    if not m:
        return CompileResult(
            False,
            None,
            "Output is not a single CREATE PROCEDURE/FUNCTION/VIEW/TRIGGER statement.",
        )
    kind, name = m.group(1).upper(), m.group(2)
    if kind != _DROP_KEYWORD.get(object_type, kind):
        return CompileResult(
            False,
            None,
            f"Expected CREATE {_DROP_KEYWORD[object_type]}, got CREATE {kind}.",
        )
    with _LOCK:
        global _loaded_discovery
        if _loaded_discovery != report["discovery_id"]:
            _load_schema_locked(report)
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION'"
                )
                # Unqualified names resolve against the first schema database.
                dbs = schema_ddl.schema_databases(report)
                if dbs:
                    cur.execute(f"USE {schema_ddl.quote(dbs[0])}")
                drop = _exec(cur, f"DROP {kind} IF EXISTS {name}")
                if not drop.ok and drop.errno not in (1049,):
                    return drop
                return _exec(cur, statement)
        finally:
            conn.close()
