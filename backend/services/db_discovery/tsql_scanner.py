"""T-SQL script scanner: masking, batch splitting, object inventory, columns, references.

Everything here works on a *masked* copy of the source in which comment text and string-literal
contents are blanked out, character for character, so offsets and line numbers stay identical to
the original. Pattern matching on the masked text cannot be fooled by commented-out code or by SQL
held in string data (dynamic SQL is flagged by a rule instead of being parsed as if it ran).

sqlglot is used as a second opinion: each batch is offered to its T-SQL parser and whether it
produced a real AST is recorded. The inventory never depends on that parse succeeding, because
sqlglot falls back to an opaque ``Command`` for much procedural T-SQL.
"""

from __future__ import annotations

import bisect
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

logging.getLogger("sqlglot").setLevel(logging.ERROR)

# Largest batch offered to sqlglot. Larger batches are generated data-load scripts, where a parse
# costs seconds and tells us nothing about conversion effort.
_SQLGLOT_MAX_BATCH_CHARS = 200_000

# --- Identifiers -------------------------------------------------------------------------------

IDENT = r'(?:\[(?:[^\]]|\]\])+\]|"[^"\n]+"|[A-Za-z_@#][\w@#$]*)'
QNAME = rf"{IDENT}(?:\s*\.\s*{IDENT}|\.\.{IDENT}){{0,3}}"

_IDENT_RE = re.compile(IDENT)
_QNAME_RE = re.compile(QNAME)

# Object types that live in a schema; everything else is database- or server-level.
SCHEMA_SCOPED = {
    "table",
    "view",
    "procedure",
    "function",
    "trigger",
    "sequence",
    "user_defined_type",
    "index",
    "synonym",
    "security_policy",
    "service_broker",
    "statistics",
    "xml_schema_collection",
}

MODULE_TYPES = {"procedure", "function", "view", "trigger"}


def unquote(ident: str) -> str:
    """Strip ``[...]`` or ``"..."`` quoting from one identifier part."""
    ident = ident.strip()
    if ident.startswith("[") and ident.endswith("]"):
        return ident[1:-1].replace("]]", "]")
    if ident.startswith('"') and ident.endswith('"'):
        return ident[1:-1]
    return ident


def split_qname(qname: str) -> list[str]:
    """Split a possibly-quoted multi-part name into unquoted parts.

    ``db..obj`` is returned as ``[db, "dbo", obj]`` (the empty part is the default schema).
    """
    if ".." in qname and "[" not in qname:
        head, _, tail = qname.partition("..")
        return [unquote(head), "dbo", *[unquote(p) for p in _IDENT_RE.findall(tail)]]
    return [unquote(p) for p in _IDENT_RE.findall(qname)]


# --- Reading and masking -----------------------------------------------------------------------


def read_sql_file(path: str | Path) -> str:
    """Decode a SQL script. SSMS and SSDT write UTF-16 or UTF-8 with a BOM as often as UTF-8."""
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    elif data.startswith(b"\xef\xbb\xbf"):
        text = data[3:].decode("utf-8", errors="replace")
    elif len(data) > 4 and data[:400].count(b"\x00") > len(data[:400]) // 4:
        # UTF-16 without a BOM: every other byte of ASCII text is NUL.
        text = data.decode("utf-16-le", errors="replace")
    else:
        text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def mask_tsql(text: str) -> str:
    """Blank comment text and string-literal contents, preserving length and newlines.

    String quotes are kept (so ``EXEC('...')`` still reads as ``EXEC('   ')``); bracketed and
    double-quoted identifiers are kept verbatim. T-SQL block comments nest.
    """
    out = list(text)
    n = len(text)

    def blank(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        c = text[i]
        if c == "-" and text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
        elif c == "/" and text.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth += 1
                    j += 2
                elif text.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            blank(i, j)
            i = j
        elif c == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            blank(i + 1, j)
            i = j + 1
        elif c == "[":
            j = i + 1
            while j < n:
                if text[j] == "]":
                    if j + 1 < n and text[j + 1] == "]":
                        j += 2
                        continue
                    break
                if text[j] == "\n":
                    # Not an identifier (e.g. a LIKE character class outside a string); stop.
                    break
                j += 1
            i = j + 1
        elif c == '"':
            j = text.find('"', i + 1)
            i = n if j == -1 else j + 1
        else:
            i += 1
    return "".join(out)


class LineIndex:
    """Maps character offsets to 1-based line numbers."""

    def __init__(self, text: str) -> None:
        self._starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self._starts, offset)


# --- Batches -----------------------------------------------------------------------------------

_GO_RE = re.compile(
    r"^[ \t]*GO(?:[ \t]+\d+)?[ \t]*;?[ \t]*$", re.IGNORECASE | re.MULTILINE
)


@dataclass
class Batch:
    start: int  # offset of the batch in the file
    original: str
    masked: str
    ast_parsed: bool = False
    ast_tables: list[tuple[str, str]] = field(default_factory=list)


def split_batches(original: str, masked: str) -> list[Batch]:
    """Split on ``GO`` lines found in the masked text (so a ``GO`` in a comment is ignored)."""
    batches: list[Batch] = []
    pos = 0
    for m in _GO_RE.finditer(masked):
        if masked[pos : m.start()].strip():
            batches.append(
                Batch(pos, original[pos : m.start()], masked[pos : m.start()])
            )
        pos = m.end()
    if masked[pos:].strip():
        batches.append(Batch(pos, original[pos:], masked[pos:]))
    return batches


def sqlglot_parse(batch: Batch) -> None:
    """Record whether sqlglot's T-SQL parser produced a real AST, and the tables it saw."""
    if len(batch.original) > _SQLGLOT_MAX_BATCH_CHARS:
        return
    try:
        expressions = sqlglot.parse(
            batch.original, read="tsql", error_level=sqlglot.ErrorLevel.RAISE
        )
    except Exception:  # sqlglot raises several error types; any of them means "no AST"
        return
    parsed = [e for e in expressions if e is not None]
    if not parsed or any(isinstance(e, exp.Command) for e in parsed):
        return
    batch.ast_parsed = True
    for expression in parsed:
        for table in expression.find_all(exp.Table):
            if table.name:
                batch.ast_tables.append((table.db or "", table.name))


# --- Objects -----------------------------------------------------------------------------------

_HEADER_RE = re.compile(
    r"""\b(?P<verb>CREATE(?:\s+OR\s+ALTER)?|ALTER)\s+(?P<kind>
        PROC(?:EDURE)?|FUNCTION|VIEW|TRIGGER|TABLE|SEQUENCE|TYPE|SCHEMA|SYNONYM
      | APPLICATION\s+ROLE|ROLE|USER|LOGIN|ASSEMBLY|QUEUE|SERVICE|CONTRACT|MESSAGE\s+TYPE
      | SECURITY\s+POLICY|PARTITION\s+FUNCTION|PARTITION\s+SCHEME|FULLTEXT\s+CATALOG
      | FULLTEXT\s+INDEX|XML\s+SCHEMA\s+COLLECTION|STATISTICS
      | (?:PRIMARY\s+)?XML\s+INDEX|SPATIAL\s+INDEX
      | (?:UNIQUE\s+)?(?:CLUSTERED\s+|NONCLUSTERED\s+)?(?:COLUMNSTORE\s+)?INDEX
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

_FILEGROUP_RE = re.compile(
    rf"\bALTER\s+DATABASE\s+{IDENT}\s+ADD\s+FILEGROUP\s+(?P<name>{IDENT})", re.I
)
# @job_name is a string literal, so it is read from the original text at the masked match offset.
_AGENT_JOB_RE = re.compile(r"\bsp_add_job\b", re.I)
_JOB_NAME_RE = re.compile(r"@job_name\s*=\s*N?'((?:[^']|'')*)'", re.I)


def _kind_to_type(kind: str) -> str:
    k = re.sub(r"\s+", " ", kind.upper())
    if k.startswith("PROC"):
        return "procedure"
    if k.endswith("INDEX") and k != "FULLTEXT INDEX":
        return "index"
    return {
        "FUNCTION": "function",
        "VIEW": "view",
        "TRIGGER": "trigger",
        "TABLE": "table",
        "SEQUENCE": "sequence",
        "TYPE": "user_defined_type",
        "SCHEMA": "schema",
        "SYNONYM": "synonym",
        "ROLE": "security_principal",
        "APPLICATION ROLE": "security_principal",
        "USER": "security_principal",
        "LOGIN": "security_principal",
        "ASSEMBLY": "assembly",
        "QUEUE": "service_broker",
        "SERVICE": "service_broker",
        "CONTRACT": "service_broker",
        "MESSAGE TYPE": "service_broker",
        "SECURITY POLICY": "security_policy",
        "PARTITION FUNCTION": "partition_function",
        "PARTITION SCHEME": "partition_scheme",
        "FULLTEXT CATALOG": "fulltext",
        "FULLTEXT INDEX": "fulltext",
        "XML SCHEMA COLLECTION": "xml_schema_collection",
        "STATISTICS": "statistics",
    }.get(k, "other")


@dataclass
class Column:
    name: str
    sql_type: str
    base_type: str
    nullable: bool
    identity: bool = False
    computed: bool = False
    persisted: bool = False
    has_default: bool = False
    default_sequence: bool = False
    generated_always: bool = False
    mysql_type: str = ""
    mapping_note: str = ""


@dataclass
class DbObject:
    schema: str
    name: str
    type: str
    subtype: str
    file: str
    start: int  # offset in file
    end: int
    line: int = 0
    end_line: int = 0
    parent: str = (
        ""  # "schema.name" of the owning table for index/trigger/fulltext/statistics
    )
    columns: list[Column] = field(default_factory=list)
    alias_base_type: str = ""
    primary_key: list[str] = field(default_factory=list)
    raw_references: list[list[str]] = field(default_factory=list)
    ast_parsed: bool | None = None

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


def _parse_name_after(masked: str, pos: int) -> tuple[list[str], int]:
    m = _QNAME_RE.match(masked, pos)
    if not m:
        return [], pos
    return split_qname(m.group(0)), m.end()


def _schema_and_name(parts: list[str], obj_type: str) -> tuple[str, str]:
    if not parts:
        return ("dbo" if obj_type in SCHEMA_SCOPED else ""), "(unnamed)"
    if obj_type not in SCHEMA_SCOPED:
        return "", parts[-1]
    if len(parts) == 1:
        return "dbo", parts[0]
    return parts[-2] or "dbo", parts[-1]


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _on_target(masked: str, pos: int) -> str:
    """Read ``ON <qname>`` starting at ``pos`` (used for index/trigger/statistics parents)."""
    m = re.compile(rf"\s*ON\s+({QNAME})", re.I).match(masked, pos)
    if not m:
        return ""
    parts = split_qname(m.group(1))
    if not parts:
        return ""
    if parts[-1].upper() in ("DATABASE", "SERVER") or (
        len(parts) == 1 and parts[0].upper() == "ALL"
    ):
        return ""
    return f"{parts[-2] if len(parts) > 1 else 'dbo'}.{parts[-1]}"


def _subtype(obj_type: str, kind: str, masked_obj: str) -> str:
    text = masked_obj
    if obj_type == "procedure":
        if re.search(r"\bEXTERNAL\s+NAME\b", text, re.I):
            return "clr"
        if re.search(r"\bNATIVE_COMPILATION\b", text, re.I):
            return "natively_compiled"
        return "sql"
    if obj_type == "function":
        if re.search(r"\bEXTERNAL\s+NAME\b", text, re.I):
            return "clr"
        if re.search(r"\bRETURNS\s+@\w+\s+TABLE\b", text, re.I):
            return "multi_statement_tvf"
        if re.search(r"\bRETURNS\s+TABLE\b", text, re.I):
            return "inline_tvf"
        return "scalar"
    if obj_type == "trigger":
        if re.search(r"\bON\s+(?:DATABASE|ALL\s+SERVER)\b", text, re.I):
            return "ddl"
        if re.search(r"\bINSTEAD\s+OF\b", text, re.I):
            return "instead_of"
        return "after"
    if obj_type == "user_defined_type":
        if re.search(r"\bAS\s+TABLE\b", text, re.I):
            return "table_type"
        if re.search(r"\bEXTERNAL\s+NAME\b", text, re.I):
            return "clr"
        return "alias"
    if obj_type == "index":
        return re.sub(r"\s+", "_", kind.strip().lower())
    if obj_type == "table":
        tags = []
        if re.search(r"\bSYSTEM_VERSIONING\s*=\s*ON\b", text, re.I):
            tags.append("temporal")
        if re.search(r"\bMEMORY_OPTIMIZED\s*=\s*ON\b", text, re.I):
            tags.append("memory_optimized")
        if re.search(r"\bAS\s+(?:NODE|EDGE)\b", text, re.I):
            tags.append("graph")
        return ",".join(tags) or "standard"
    if obj_type == "view":
        return (
            "schemabound" if re.search(r"\bSCHEMABINDING\b", text, re.I) else "standard"
        )
    if obj_type in ("service_broker", "security_principal", "fulltext"):
        return re.sub(r"\s+", "_", kind.strip().lower())
    return ""


def find_objects(batch: Batch, file: str) -> list[DbObject]:
    """Find the objects a batch defines.

    A CREATE PROCEDURE/FUNCTION/VIEW/TRIGGER must be the first statement in its batch and runs to
    the batch end, so scanning stops at the first module header (a ``CREATE TABLE #t`` inside a
    procedure is part of the procedure). Other objects run to the next header.
    """
    masked = batch.masked
    headers: list[tuple[re.Match[str], str]] = []
    for m in _HEADER_RE.finditer(masked):
        verb = m.group("verb").upper()
        obj_type = _kind_to_type(m.group("kind"))
        if verb == "ALTER" and obj_type not in MODULE_TYPES:
            continue  # ALTER TABLE/INDEX/... modifies, it does not define
        headers.append((m, obj_type))
        if obj_type in MODULE_TYPES:
            break

    objects: list[DbObject] = []
    for idx, (m, obj_type) in enumerate(headers):
        kind = m.group("kind")
        start = m.start()
        end = headers[idx + 1][0].start() if idx + 1 < len(headers) else len(masked)
        if obj_type in MODULE_TYPES:
            end = len(masked)
        name_pos = _skip_ws(masked, m.end())
        parent = ""

        if (
            obj_type == "fulltext"
            and kind.upper().startswith("FULLTEXT")
            and ("INDEX" in kind.upper())
        ):
            parent = _on_target(masked, m.end())
            parts = ["fulltext_index_on_" + (parent or "unknown")]
            after = m.end()
        else:
            parts, after = _parse_name_after(masked, name_pos)
            if obj_type in ("index", "statistics", "trigger"):
                parent = _on_target(masked, after)

        if obj_type == "table" and parts and parts[-1].startswith("#"):
            continue  # temporary table, not a schema object

        schema, name = _schema_and_name(parts, obj_type)
        obj_masked = masked[start:end]
        obj = DbObject(
            schema=schema,
            name=name,
            type=obj_type,
            subtype=_subtype(obj_type, kind, obj_masked),
            file=file,
            start=batch.start + start,
            end=batch.start + end,
            parent=parent,
            ast_parsed=batch.ast_parsed,
        )
        if obj_type == "index" and parent:
            # Index names are only unique per table.
            obj.schema, obj.name = (
                parent.split(".", 1)[0],
                f"{parent.split('.', 1)[1]}.{name}",
            )
        if obj_type == "table":
            obj.columns = parse_table_columns(obj_masked, after - start)
            obj.primary_key = parse_primary_key(obj_masked, obj.columns)
        if obj_type == "user_defined_type" and obj.subtype == "alias":
            fm = re.compile(rf"\s*FROM\s+({IDENT}(?:\s*\(\s*[\w\s,]+\))?)", re.I).match(
                masked, after
            )
            if fm:
                obj.alias_base_type = re.sub(r"\s+", "", fm.group(1))
        if obj_type == "user_defined_type" and obj.subtype == "table_type":
            obj.columns = parse_table_columns(obj_masked, after - start)
        obj.raw_references = extract_references(obj_masked)
        objects.append(obj)

    for m in _FILEGROUP_RE.finditer(masked):
        objects.append(
            DbObject(
                schema="",
                name=unquote(m.group("name")),
                type="filegroup",
                subtype="memory_optimized"
                if re.search(
                    r"CONTAINS\s+MEMORY_OPTIMIZED_DATA",
                    masked[m.end() : m.end() + 80],
                    re.I,
                )
                else "",
                file=file,
                start=batch.start + m.start(),
                end=batch.start + m.end(),
            )
        )

    for m in _AGENT_JOB_RE.finditer(masked):
        window = batch.original[m.start() : m.start() + 2000]
        jm = _JOB_NAME_RE.search(window)
        name = jm.group(1).replace("''", "'") if jm else "(unnamed job)"
        objects.append(
            DbObject(
                schema="",
                name=name,
                type="sql_agent_job",
                subtype="msdb",
                file=file,
                start=batch.start + m.start(),
                end=batch.start + len(masked),
            )
        )
    return objects


# --- Table columns -----------------------------------------------------------------------------

_CONSTRAINT_START = re.compile(
    r"(?:CONSTRAINT|PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|INDEX|PERIOD\s+FOR)\b",
    re.I,
)
_TYPE_RE = re.compile(
    rf"(?P<type>{IDENT}(?:\s*\.\s*{IDENT})?)\s*(?P<args>\(\s*(?:max|\d+)\s*(?:,\s*\d+\s*)?\))?",
    re.I,
)


def _matching_paren(text: str, open_pos: int) -> int:
    depth = 0
    for k in range(open_pos, len(text)):
        if text[k] == "(":
            depth += 1
        elif text[k] == ")":
            depth -= 1
            if depth == 0:
                return k
    return len(text)


def _split_top_level(body: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def parse_table_columns(masked_obj: str, after_name: int) -> list[Column]:
    """Parse column definitions from a masked ``CREATE TABLE`` (or ``AS TABLE`` type)."""
    open_pos = masked_obj.find("(", max(after_name, 0))
    if open_pos == -1:
        return []
    close_pos = _matching_paren(masked_obj, open_pos)
    columns: list[Column] = []
    for element in _split_top_level(masked_obj[open_pos + 1 : close_pos]):
        el = element.strip()
        if not el or _CONSTRAINT_START.match(el):
            continue
        nm = _IDENT_RE.match(el)
        if not nm:
            continue
        name = unquote(nm.group(0))
        rest = el[nm.end() :].strip()
        if re.match(r"AS\b", rest, re.I):
            columns.append(
                Column(
                    name=name,
                    sql_type="computed",
                    base_type="computed",
                    nullable=not re.search(r"\bNOT\s+NULL\b", rest, re.I),
                    computed=True,
                    persisted=bool(re.search(r"\bPERSISTED\b", rest, re.I)),
                )
            )
            continue
        tm = _TYPE_RE.match(rest)
        if not tm:
            continue
        type_parts = split_qname(tm.group("type"))
        base = type_parts[-1].lower() if type_parts else ""
        args = re.sub(r"\s+", "", tm.group("args") or "").lower()
        columns.append(
            Column(
                name=name,
                sql_type=f"{base}{args}",
                base_type=base,
                nullable=not re.search(r"\bNOT\s+NULL\b", rest, re.I),
                identity=bool(re.search(r"\bIDENTITY\b", rest, re.I)),
                has_default=bool(re.search(r"\bDEFAULT\b", rest, re.I)),
                default_sequence=bool(re.search(r"\bNEXT\s+VALUE\s+FOR\b", rest, re.I)),
                generated_always=bool(
                    re.search(r"\bGENERATED\s+ALWAYS\s+AS\s+ROW\b", rest, re.I)
                ),
            )
        )
    return columns


_PK_CONSTRAINT = re.compile(
    r"\bPRIMARY\s+KEY\s*(?:CLUSTERED|NONCLUSTERED)?\s*(?:HASH\s*)?\(([^)]*)\)", re.I
)
_PK_INLINE = re.compile(
    rf"(?:^|[(,])\s*({IDENT})\s+[^,()]*?\bPRIMARY\s+KEY\b(?!\s*(?:CLUSTERED\s*|NONCLUSTERED\s*)?\()",
    re.I,
)


def parse_primary_key(masked_obj: str, columns: list[Column]) -> list[str]:
    """Primary-key columns of a masked CREATE TABLE, from a table constraint or an inline key."""
    m = _PK_CONSTRAINT.search(masked_obj)
    if m:
        cols = []
        for part in m.group(1).split(","):
            ident = _IDENT_RE.match(part.strip())
            if ident:
                cols.append(unquote(ident.group(0)))
        return cols
    names = {c.name.lower() for c in columns}
    for m in _PK_INLINE.finditer(masked_obj):
        name = unquote(m.group(1))
        if name.lower() in names:
            return [name]
    return []


# --- Type mapping ------------------------------------------------------------------------------


def _args(sql_type: str) -> list[str]:
    m = re.search(r"\(([^)]*)\)", sql_type)
    return [a.strip() for a in m.group(1).split(",")] if m else []


def map_type_to_mysql(sql_type: str) -> tuple[str, str]:
    """Suggest a MySQL 8.0 type for a SQL Server type. Returns ``(mysql_type, note)``."""
    base = re.sub(r"\(.*", "", sql_type).strip().lower()
    a = _args(sql_type)
    first = a[0] if a else ""
    is_max = first == "max"

    def prec(default: int) -> int:
        try:
            return min(int(first), 6) if first else min(default, 6)
        except ValueError:
            return min(default, 6)

    simple = {
        "bigint": ("BIGINT", ""),
        "int": ("INT", ""),
        "smallint": ("SMALLINT", ""),
        "tinyint": ("TINYINT UNSIGNED", "SQL Server tinyint is 0–255"),
        "bit": ("TINYINT(1)", ""),
        "money": ("DECIMAL(19,4)", ""),
        "smallmoney": ("DECIMAL(10,4)", ""),
        "real": ("FLOAT", ""),
        "date": ("DATE", ""),
        "datetime": ("DATETIME(3)", "SQL Server datetime rounds to 1/300 s"),
        "smalldatetime": ("DATETIME", ""),
        "text": ("LONGTEXT", "deprecated type; cannot be fully indexed"),
        "ntext": ("LONGTEXT", "deprecated type; utf8mb4; cannot be fully indexed"),
        "image": ("LONGBLOB", "deprecated type"),
        "uniqueidentifier": (
            "CHAR(36)",
            "or BINARY(16) with UUID_TO_BIN for compact keys",
        ),
        "xml": ("LONGTEXT", "no XML type or XQuery methods in MySQL"),
        "sysname": ("VARCHAR(128)", ""),
        "geography": ("GEOMETRY SRID 4326", "spatial functions differ (ST_* names)"),
        "geometry": ("GEOMETRY", "spatial functions differ (ST_* names)"),
        "hierarchyid": (
            "VARCHAR(4000)",
            "no equivalent: model as materialised path or closure table",
        ),
        "sql_variant": ("—", "no equivalent: redesign the column"),
        "rowversion": (
            "BIGINT UNSIGNED",
            "no automatic row version: maintain with a trigger or use TIMESTAMP(6) ON UPDATE",
        ),
        "timestamp": (
            "BIGINT UNSIGNED",
            "SQL Server timestamp is rowversion, not a date: maintain with a trigger",
        ),
    }
    if base in simple:
        return simple[base]
    if base in ("decimal", "numeric"):
        return (f"DECIMAL({','.join(a)})" if a else "DECIMAL(18,0)"), ""
    if base == "float":
        try:
            return ("FLOAT", "") if first and int(first) <= 24 else ("DOUBLE", "")
        except ValueError:
            return "DOUBLE", ""
    if base == "time":
        return f"TIME({prec(7)})", (
            "precision 7 → 6" if not first or first == "7" else ""
        )
    if base == "datetime2":
        return f"DATETIME({prec(7)})", (
            "precision 7 → 6" if not first or first == "7" else ""
        )
    if base == "datetimeoffset":
        return (
            f"DATETIME({prec(7)})",
            "UTC offset is lost: store UTC plus a separate offset column",
        )
    if base in ("char", "nchar"):
        return f"CHAR({first or 1})", ("utf8mb4" if base == "nchar" else "")
    if base in ("varchar", "nvarchar"):
        if is_max:
            return "LONGTEXT", "cannot be fully indexed; use a prefix index"
        return f"VARCHAR({first or 1})", (
            "utf8mb4; check the 65,535-byte row limit" if base == "nvarchar" else ""
        )
    if base == "binary":
        return f"BINARY({first or 1})", ""
    if base == "varbinary":
        return ("LONGBLOB", "") if is_max else (f"VARBINARY({first or 1})", "")
    if base == "computed":
        return "GENERATED ALWAYS AS (…)", "rewrite the expression with MySQL functions"
    return "?", "user-defined or unknown type: resolve the base type"


# --- References --------------------------------------------------------------------------------

_REF_RE = re.compile(
    rf"\b(?:FROM|JOIN|INTO|UPDATE|MERGE(?:\s+INTO)?|EXEC(?:UTE)?|REFERENCES|APPLY|TRUNCATE\s+TABLE)\s+(?P<q>{QNAME})",
    re.I,
)
_NOT_OBJECTS = {
    "select",
    "set",
    "values",
    "as",
    "with",
    "where",
    "statistics",
    "default",
}


def extract_references(masked: str) -> list[list[str]]:
    """Identifiers that follow FROM/JOIN/INTO/UPDATE/MERGE/EXEC/REFERENCES/APPLY."""
    refs: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for m in _REF_RE.finditer(masked):
        q = m.group("q")
        if q.startswith(("@", "#")):
            continue
        parts = split_qname(q)
        if not parts or parts[-1].lower() in _NOT_OBJECTS:
            continue
        key = tuple(p.lower() for p in parts)
        if key not in seen:
            seen.add(key)
            refs.append(parts)
    return refs


def non_blank_lines(masked: str) -> int:
    """Lines of code: lines with something other than whitespace once comments are masked."""
    return sum(1 for line in masked.split("\n") if line.strip())
