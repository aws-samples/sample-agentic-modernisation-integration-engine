"""Deterministic MySQL 8.0 DDL from a discovery report.

A SQL Server schema becomes a MySQL database with the same name, so ``Sales.Orders`` becomes
`` `Sales`.`Orders` `` and qualified references in converted code keep their shape. Only what is
needed to create the tables is emitted: columns, nullability, AUTO_INCREMENT and the primary key.
Indexes, foreign keys, defaults and history-table triggers belong to the reviewed conversion, not to
this mechanical step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Types the mapping cannot express directly. They become nullable LONGTEXT with a TODO comment so
# the table still creates and a reviewer sees what needs a decision.
_UNMAPPABLE = {"—", "?", "GENERATED ALWAYS AS (…)"}


def quote(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def qualified(schema: str, name: str) -> str:
    return f"{quote(schema)}.{quote(name)}" if schema else quote(name)


@dataclass
class TableDdl:
    object_id: str
    schema: str
    name: str
    sql: str


def _column_sql(col: dict, in_primary_key: bool) -> tuple[str, str | None]:
    mysql_type = col["mysql_type"]
    todo: str | None = None
    base = mysql_type.replace(" AUTO_INCREMENT", "")
    if base in _UNMAPPABLE or col.get("computed"):
        kind = "computed column" if col.get("computed") else f"type {col['sql_type']}"
        todo = f"{col['name']}: {kind} has no mechanical MySQL mapping"
        return f"  {quote(col['name'])} LONGTEXT NULL COMMENT 'TODO: {kind}'", todo
    nullable = col["nullable"] and not in_primary_key
    parts = [f"  {quote(col['name'])} {base}", "NULL" if nullable else "NOT NULL"]
    if col.get("identity"):
        parts.append("AUTO_INCREMENT")
    return " ".join(parts), todo


def table_ddl(obj: dict) -> TableDdl:
    pk = obj.get("primary_key") or []
    pk_lower = {c.lower() for c in pk}
    lines: list[str] = []
    todos: list[str] = []
    for col in obj["columns"]:
        line, todo = _column_sql(col, col["name"].lower() in pk_lower)
        lines.append(line)
        if todo:
            todos.append(todo)
    identity_cols = [c["name"] for c in obj["columns"] if c.get("identity")]
    key_cols = [
        c for c in pk if any(col["name"].lower() == c.lower() for col in obj["columns"])
    ]
    if key_cols:
        lines.append(f"  PRIMARY KEY ({', '.join(quote(c) for c in key_cols)})")
    # MySQL requires an AUTO_INCREMENT column to be the first column of some key.
    for ident in identity_cols:
        if not key_cols or key_cols[0].lower() != ident.lower():
            lines.append(f"  KEY {quote('ix_ai_' + ident)} ({quote(ident)})")
    header = [
        f"-- {obj['type']} {obj['schema']}.{obj['name']} (from {obj['file']}:{obj['line']})"
    ]
    if obj.get("subtype") and obj["subtype"] != "standard":
        header.append(f"-- SQL Server table features: {obj['subtype']}")
    header.extend(f"-- TODO {t}" for t in todos)
    if not key_cols:
        header.append(
            "-- WARNING: no primary key; AWS DMS CDC cannot capture changes for this table"
        )
    body = ",\n".join(lines)
    sql = (
        "\n".join(header)
        + f"\nCREATE TABLE IF NOT EXISTS {qualified(obj['schema'], obj['name'])} (\n{body}\n)"
        + " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;"
    )
    return TableDdl(obj["id"], obj["schema"], obj["name"], sql)


def widen_for_row_size(sql: str) -> str:
    """Retry form for MySQL error 1118 (row size too large): long VARCHARs become TEXT."""

    def repl(m: re.Match[str]) -> str:
        return "TEXT" if int(m.group(1)) >= 1000 else m.group(0)

    return re.sub(r"\bVARCHAR\((\d+)\)", repl, sql)


_SCHEMA_OBJECT_TYPES = {
    "table",
    "view",
    "procedure",
    "function",
    "trigger",
    "sequence",
    "user_defined_type",
}


def schema_databases(report: dict) -> list[str]:
    """Every SQL Server schema that owns an object, plus declared schemas, as MySQL databases.

    A schema with only procedures still needs a database, or ``CREATE PROCEDURE Website.X`` fails
    with "unknown database".
    """
    names = {
        o["schema"]
        for o in report["objects"]
        if o["schema"] and o["type"] in _SCHEMA_OBJECT_TYPES
    }
    names |= {o["name"] for o in report["objects"] if o["type"] == "schema"}
    return sorted(names)


def tables(report: dict) -> list[TableDdl]:
    return [
        table_ddl(o) for o in report["objects"] if o["type"] == "table" and o["columns"]
    ]


def render_schema_sql(report: dict) -> str:
    out = [
        f"-- MySQL 8.0 schema generated from discovery {report['discovery_id']}",
        "-- Deterministic mapping (no model). Review every TODO before use.",
        "-- A SQL Server schema maps to a MySQL database of the same name.",
        "",
    ]
    out.extend(
        f"CREATE DATABASE IF NOT EXISTS {quote(db)};" for db in schema_databases(report)
    )
    out.append("")
    for t in tables(report):
        out.append(t.sql)
        out.append("")
    return "\n".join(out)
