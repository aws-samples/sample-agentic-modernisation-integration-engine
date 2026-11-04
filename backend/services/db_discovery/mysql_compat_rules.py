"""SQL Server → MySQL 8.0 (Aurora MySQL v3) incompatibility rule catalogue.

Each rule is a case-insensitive regex over *masked* T-SQL (see ``tsql_scanner.mask_tsql``), so
comments and string data never match. ``applies_to`` limits a rule to object types; ``None`` means
any T-SQL context, including SQL embedded in application code (object type ``embedded_sql``).

Severity is the expected conversion effort, not the risk of the construct:

- ``blocker`` — no MySQL equivalent; the capability must move out of the database or be redesigned
- ``high``    — manual rewrite of the logic
- ``medium``  — rewrite with a well-known pattern
- ``low``     — mechanical change that schema-conversion tooling normally handles
- ``info``    — no change needed, or dropped on conversion
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from services.db_discovery.tsql_scanner import IDENT, LineIndex

SEVERITY_WEIGHT: dict[str, float] = {
    "blocker": 13.0,
    "high": 5.0,
    "medium": 2.0,
    "low": 0.5,
    "info": 0.0,
}
SEVERITY_ORDER = ["blocker", "high", "medium", "low", "info"]


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    category: str
    severity: str
    recommendation: str
    pattern: re.Pattern[str] | None = None
    applies_to: frozenset[str] | None = None
    matcher: Callable[[str], list[int]] | None = field(default=None, compare=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "severity": self.severity,
            "recommendation": self.recommendation,
            "applies_to": sorted(self.applies_to) if self.applies_to else None,
        }


@dataclass
class RuleHit:
    rule: Rule
    offsets: list[int]  # offsets into the evaluated text, one per occurrence


_FLAGS = re.IGNORECASE | re.MULTILINE

# A bare type keyword followed by something that only follows a type in a declaration. This keeps
# a column *named* Money or Text from being reported as the type.
_TYPE_TAIL = (
    r"(?=\s*(?:\(|NOT\b|NULL\b|,|\)|=|;|$|COLLATE\b|IDENTITY\b|DEFAULT\b|CONSTRAINT\b|"
    r"PRIMARY\b|SPARSE\b|OUTPUT\b|OUT\b|READONLY\b|ROWGUIDCOL\b|MASKED\b|GENERATED\b|AS\b|"
    r"DECLARE\b|SET\b|SELECT\b|BEGIN\b|RETURNS\b|WITH\b|FILESTREAM\b|UNIQUE\b|CHECK\b))"
)


# ...and preceded by something that only precedes a type: a column or parameter name at the start
# of a definition, ``@var [AS]``, ``AS`` (CAST), ``RETURNS``, or ``CONVERT(``. A SQL keyword in the
# name position (``SELECT Money, …``) is not a definition.
_NOT_A_NAME = (
    r"(?!(?:SELECT|WHERE|AND|OR|ON|BY|THEN|ELSE|WHEN|FROM|JOIN|SET|RETURN|NOT|IS|IN|LIKE|DISTINCT|"
    r"TOP|CASE|END|GROUP|ORDER|HAVING|UNION|ALL|INTO|VALUES|EXEC|EXECUTE|PRINT|IF|BEGIN|DECLARE|"
    r"WITH|AS|NULL|DEFAULT|OUTPUT|UPDATE|DELETE|INSERT)\b)"
)
_TYPE_PREFIX = (
    rf"(?:@\w+\s+(?:AS\s+)?|\bAS\s+|\bRETURNS\s+|\bCONVERT\s*\(\s*|"
    rf"(?:^|[(,])\s*{_NOT_A_NAME}(?:\[[^\]\n]+\]|\w+)\s+)"
)


def _type(name: str) -> str:
    return rf"{_TYPE_PREFIX}(?:\[?sys\]?\.)?\[?{name}\]?{_TYPE_TAIL}"


def _three_part(
    context: str = r"FROM|JOIN|INTO|UPDATE|EXEC(?:UTE)?|REFERENCES|APPLY|MERGE",
) -> str:
    return (
        rf"\b(?:{context})\s+(?!\[?(?:msdb|master|tempdb|model)\]?\s*\.)"
        rf"(?![@#]){IDENT}\s*\.\s*(?:{IDENT})?\s*\.\s*{IDENT}(?!\s*\.\s*[\w\[])"
    )


def _four_part() -> str:
    return (
        r"\b(?:FROM|JOIN|INTO|UPDATE|EXEC(?:UTE)?|APPLY|MERGE)\s+(?![@#])"
        rf"{IDENT}\s*\.\s*{IDENT}?\s*\.\s*{IDENT}?\s*\.\s*{IDENT}"
    )


_CTE_DEF = re.compile(
    rf"(?:\bWITH|,)\s*(?P<name>{IDENT})\s*(?:\([^()]*\))?\s*AS\s*\(", _FLAGS
)


def _recursive_ctes(masked: str) -> list[int]:
    """A CTE whose own body references its name is recursive (T-SQL has no RECURSIVE keyword)."""
    hits: list[int] = []
    for m in _CTE_DEF.finditer(masked):
        name = m.group("name").strip("[]")
        if not name or name.upper() in ("XMLNAMESPACES",) or name.startswith("@"):
            continue
        open_pos = m.end() - 1
        depth, k = 0, open_pos
        while k < len(masked):
            if masked[k] == "(":
                depth += 1
            elif masked[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        body = masked[open_pos:k]
        if re.search(
            rf"(?<![\w.]){re.escape(name)}(?!\w)|\[{re.escape(name)}\]", body, re.I
        ):
            hits.append(m.start())
    return hits


def _r(
    rule_id: str,
    title: str,
    category: str,
    severity: str,
    pattern: str | None,
    recommendation: str,
    applies_to: set[str] | None = None,
    matcher: Callable[[str], list[int]] | None = None,
) -> Rule:
    return Rule(
        id=rule_id,
        title=title,
        category=category,
        severity=severity,
        recommendation=recommendation,
        pattern=re.compile(pattern, _FLAGS) if pattern else None,
        applies_to=frozenset(applies_to) if applies_to else None,
        matcher=matcher,
    )


PROC = {"procedure"}
MODULES = {"procedure", "function", "trigger"}
TRIGGER = {"trigger"}
INDEX = {"index"}

RULES: list[Rule] = [
    # --- Data types ---------------------------------------------------------------------------
    _r(
        "DT_DATETIMEOFFSET",
        "DATETIMEOFFSET data type",
        "data_type",
        "high",
        _type("DATETIMEOFFSET"),
        "MySQL DATETIME has no time-zone offset. Store UTC in DATETIME(6) and the offset in a "
        "separate column, or normalise to UTC in the application.",
    ),
    _r(
        "DT_UNIQUEIDENTIFIER",
        "UNIQUEIDENTIFIER data type",
        "data_type",
        "medium",
        _type("UNIQUEIDENTIFIER"),
        "Use CHAR(36), or BINARY(16) with UUID_TO_BIN(UUID(), 1) for compact, index-friendly keys.",
    ),
    _r(
        "DT_HIERARCHYID",
        "HIERARCHYID data type",
        "data_type",
        "high",
        _type("HIERARCHYID"),
        "No equivalent. Model the hierarchy as a materialised path, a closure table, or use "
        "recursive CTEs over a parent key.",
    ),
    _r(
        "DT_SQL_VARIANT",
        "SQL_VARIANT data type",
        "data_type",
        "blocker",
        _type("SQL_VARIANT"),
        "No equivalent. Split into typed columns or store JSON with an explicit type tag.",
    ),
    _r(
        "DT_XML",
        "XML data type",
        "data_type",
        "high",
        _TYPE_PREFIX
        + r"\[?XML\]?(?!\s*(?:INDEX|SCHEMA|PATH|RAW|AUTO|EXPLICIT|NAMESPACES)\b)"
        + _TYPE_TAIL,
        "No XML type or XQuery methods. Store as LONGTEXT and parse in the application, or "
        "convert the documents to JSON and use MySQL JSON functions.",
    ),
    _r(
        "DT_SPATIAL",
        "GEOGRAPHY / GEOMETRY data type",
        "data_type",
        "medium",
        _type("(?:GEOGRAPHY|GEOMETRY)"),
        "Use MySQL GEOMETRY/POINT with SRID 4326 for geography. Method calls (.STDistance, "
        ".Lat) become ST_* functions with different argument order.",
    ),
    _r(
        "DT_ROWVERSION",
        "ROWVERSION / TIMESTAMP data type",
        "data_type",
        "high",
        _type("(?:ROWVERSION|TIMESTAMP)"),
        "SQL Server timestamp is a row version, not a date. Replace optimistic-concurrency "
        "checks with a version column maintained by the application or a trigger.",
    ),
    _r(
        "DT_MONEY",
        "MONEY / SMALLMONEY data type",
        "data_type",
        "low",
        _type("(?:SMALL)?MONEY"),
        "Map to DECIMAL(19,4) / DECIMAL(10,4).",
    ),
    _r(
        "DT_MAX_TYPES",
        "VARCHAR(MAX) / NVARCHAR(MAX) / VARBINARY(MAX)",
        "data_type",
        "low",
        r"\b(?:N?VARCHAR|VARBINARY)\s*\(\s*MAX\s*\)",
        "Map to LONGTEXT / LONGBLOB. These cannot be fully indexed or have a non-expression "
        "default; use a prefix index where the column is searched.",
    ),
    _r(
        "DT_DATETIME2_7",
        "DATETIME2 / TIME with 7-digit precision",
        "data_type",
        "low",
        r"\b(?:DATETIME2|TIME)\s*\(\s*7\s*\)|" + _type("DATETIME2") + r"(?!\s*\()",
        "MySQL fractional seconds stop at 6 digits: DATETIME(6). Comparisons on the 7th digit "
        "change behaviour.",
    ),
    _r(
        "DT_LEGACY_LOB",
        "TEXT / NTEXT / IMAGE data type",
        "data_type",
        "low",
        _type("(?:N?TEXT|IMAGE)"),
        "Map to LONGTEXT / LONGBLOB (these types are deprecated in SQL Server as well).",
    ),
    _r(
        "DT_TABLE_TYPE",
        "User-defined table type",
        "data_type",
        "high",
        r"\bAS\s+TABLE\b",
        "No table types in MySQL. Callers that pass table-valued parameters need a JSON "
        "parameter read with JSON_TABLE, or a staging temporary table.",
        {"user_defined_type"},
    ),
    _r(
        "DT_ALIAS_TYPE",
        "User-defined alias type",
        "data_type",
        "low",
        r"\bCREATE\s+TYPE\s+\S+\s+FROM\b",
        "No alias types. Inline the base type into every column and parameter that uses it.",
        {"user_defined_type"},
    ),
    # --- Syntax -------------------------------------------------------------------------------
    _r(
        "SYN_MERGE",
        "MERGE statement",
        "syntax",
        "high",
        r"\bMERGE\s+(?!JOIN\b)(?:TOP\s*\([^)]*\)\s*)?(?:INTO\s+)?(?![,)])[\[\w@#]",
        "MySQL has no MERGE. Rewrite as INSERT … ON DUPLICATE KEY UPDATE plus a separate "
        "DELETE for the not-matched-by-source branch, inside one transaction.",
    ),
    _r(
        "SYN_OUTPUT_CLAUSE",
        "OUTPUT clause",
        "syntax",
        "high",
        r"\bOUTPUT\s+(?:inserted|deleted)\s*\.",
        "MySQL has no OUTPUT clause. Capture keys with LAST_INSERT_ID() or a follow-up SELECT "
        "in the same transaction.",
    ),
    _r(
        "SYN_RECURSIVE_CTE",
        "Recursive common table expression",
        "syntax",
        "medium",
        None,
        "Add the RECURSIVE keyword (WITH RECURSIVE). Recursion depth is limited by "
        "cte_max_recursion_depth (default 1000).",
        matcher=_recursive_ctes,
    ),
    _r(
        "SYN_MAXRECURSION",
        "OPTION (MAXRECURSION)",
        "syntax",
        "medium",
        r"\bMAXRECURSION\b",
        "Replace with SET SESSION cte_max_recursion_depth = n before the query.",
    ),
    _r(
        "SYN_TOP",
        "TOP n",
        "syntax",
        "low",
        r"\bTOP\s*(?:\(\s*[^)]+\)|\d+)",
        "Use LIMIT n at the end of the query.",
    ),
    _r(
        "SYN_TOP_PERCENT_TIES",
        "TOP … PERCENT / WITH TIES",
        "syntax",
        "medium",
        r"\bTOP\s*(?:\([^)]*\)|\d+)\s*(?:PERCENT|WITH\s+TIES)\b",
        "LIMIT has no PERCENT or WITH TIES. Compute the row count first, or use RANK() in a "
        "derived table.",
    ),
    _r(
        "SYN_OFFSET_FETCH",
        "OFFSET … FETCH paging",
        "syntax",
        "low",
        r"\bOFFSET\s+[\w@()+\-\s]{1,40}?\s+ROWS?\b",
        "Use LIMIT n OFFSET m.",
    ),
    _r(
        "SYN_NOLOCK",
        "NOLOCK / READUNCOMMITTED hint",
        "syntax",
        "low",
        r"\b(?:NOLOCK|READUNCOMMITTED)\b",
        "Remove. InnoDB reads do not block writers (MVCC). If dirty reads are really required, "
        "set the session isolation level instead.",
    ),
    _r(
        "SYN_TABLE_HINT",
        "Table hint (UPDLOCK, HOLDLOCK, ROWLOCK, INDEX …)",
        "syntax",
        "low",
        r"\bWITH\s*\(\s*(?:UPDLOCK|HOLDLOCK|ROWLOCK|PAGLOCK|TABLOCKX?|READPAST|XLOCK|"
        r"SERIALIZABLE|READCOMMITTED|REPEATABLEREAD|NOWAIT|FORCESEEK|FORCESCAN|INDEX\s*\()",
        "Replace locking hints with SELECT … FOR UPDATE / FOR SHARE (with SKIP LOCKED or NOWAIT); "
        "index hints become USE INDEX / FORCE INDEX.",
    ),
    _r(
        "SYN_QUERY_HINT",
        "Query hint OPTION (…)",
        "syntax",
        "low",
        r"\bOPTION\s*\(\s*(?:RECOMPILE|OPTIMIZE\s+FOR|MAXDOP|FORCE\s+ORDER|HASH|LOOP|MERGE\s+JOIN|"
        r"FAST|KEEP|KEEPFIXED|USE\s+HINT|QUERYTRACEON|EXPAND)",
        "Remove, or translate to MySQL optimizer hints (/*+ … */) after performance testing.",
    ),
    _r(
        "SYN_APPLY",
        "CROSS APPLY / OUTER APPLY",
        "syntax",
        "medium",
        r"\b(?:CROSS|OUTER)\s+APPLY\b",
        "Use a LATERAL derived table (MySQL 8.0.14+) or rewrite as a JOIN.",
    ),
    _r(
        "SYN_PIVOT",
        "PIVOT / UNPIVOT",
        "syntax",
        "medium",
        r"\b(?:UN)?PIVOT\s*\(",
        "Rewrite PIVOT as conditional aggregation (SUM(CASE WHEN …)); UNPIVOT as UNION ALL.",
    ),
    _r(
        "SYN_DELETE_ALIAS",
        "DELETE alias FROM … JOIN",
        "syntax",
        "medium",
        r"\bDELETE\s+(?!FROM\b|TOP\b)[\w\[\]\.]+\s+FROM\b",
        "Use MySQL multi-table DELETE syntax: DELETE t FROM t JOIN ….",
    ),
    _r(
        "SYN_COLLATE",
        "Explicit COLLATE clause",
        "syntax",
        "medium",
        r"\bCOLLATE\s+\w+",
        "Map to a utf8mb4 collation. SQL Server _CI_AS semantics are closest to "
        "utf8mb4_0900_as_ci; MySQL's default utf8mb4_0900_ai_ci is also accent-insensitive.",
    ),
    _r(
        "SYN_SELECT_ASSIGN",
        "SELECT @variable = …",
        "syntax",
        "low",
        r"\bSELECT\s+@\w+\s*=",
        "Use SELECT … INTO var, or SET var = (SELECT …).",
    ),
    _r(
        "SYN_IDENTITY",
        "IDENTITY column",
        "syntax",
        "low",
        r"(?<!@)\bIDENTITY\b(?:\s*\(\s*-?\d+\s*,\s*-?\d+\s*\))?",
        "Use AUTO_INCREMENT (one per table, must be indexed). Seed with AUTO_INCREMENT = n; the "
        "increment is server-wide (auto_increment_increment).",
    ),
    _r(
        "SYN_IDENTITY_INSERT",
        "SET IDENTITY_INSERT",
        "syntax",
        "medium",
        r"\bIDENTITY_INSERT\b",
        "Not needed: MySQL accepts explicit values for AUTO_INCREMENT columns. Remove the SET "
        "statements and check that sequences resume above the inserted maximum.",
    ),
    _r(
        "SYN_SCOPE_IDENTITY",
        "SCOPE_IDENTITY / @@IDENTITY / IDENT_CURRENT",
        "syntax",
        "low",
        r"\bSCOPE_IDENTITY\s*\(|@@IDENTITY\b|\bIDENT_CURRENT\s*\(",
        "Use LAST_INSERT_ID(). IDENT_CURRENT has no equivalent; read information_schema.TABLES.",
    ),
    _r(
        "SYN_SEQUENCE",
        "SEQUENCE object",
        "feature",
        "high",
        r"\bCREATE\s+SEQUENCE\b",
        "MySQL has no sequences. Use AUTO_INCREMENT, or a one-row-per-sequence table advanced by "
        "a stored function in its own transaction.",
        {"sequence"},
    ),
    _r(
        "SYN_NEXT_VALUE_FOR",
        "NEXT VALUE FOR",
        "feature",
        "high",
        r"\bNEXT\s+VALUE\s+FOR\b",
        "Replace with AUTO_INCREMENT on the column, or a call to a sequence-emulation function.",
    ),
    _r(
        "SYN_COMPUTED_COLUMN",
        "Computed column",
        "syntax",
        "low",
        None,
        "Use GENERATED ALWAYS AS (expr) STORED (PERSISTED) or VIRTUAL, rewriting the expression "
        "with deterministic MySQL functions.",
    ),
    _r(
        "SYN_IDENTIFIER_LENGTH",
        "Identifier longer than 64 characters",
        "syntax",
        "low",
        None,
        "MySQL identifiers are limited to 64 characters. Rename the object.",
    ),
    # --- Procedural ---------------------------------------------------------------------------
    _r(
        "PROC_TRY_CATCH",
        "TRY … CATCH",
        "procedural",
        "medium",
        r"\bBEGIN\s+TRY\b",
        "Use DECLARE EXIT HANDLER FOR SQLEXCEPTION with GET DIAGNOSTICS, then RESIGNAL.",
    ),
    _r(
        "PROC_THROW",
        "THROW",
        "procedural",
        "medium",
        r"(?<![\w@])THROW\b",
        "Use SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = …, or RESIGNAL inside a handler.",
    ),
    _r(
        "PROC_RAISERROR",
        "RAISERROR",
        "procedural",
        "medium",
        r"\bRAISERROR\b",
        "Use SIGNAL. Severity levels have no equivalent; informational messages (severity < 11) "
        "are usually dropped.",
    ),
    _r(
        "PROC_ERROR_FUNCTIONS",
        "ERROR_MESSAGE() / ERROR_NUMBER() / @@ERROR",
        "procedural",
        "medium",
        r"\bERROR_(?:MESSAGE|NUMBER|SEVERITY|STATE|LINE|PROCEDURE)\s*\(|@@ERROR\b",
        "Use GET DIAGNOSTICS CONDITION 1 … MESSAGE_TEXT, MYSQL_ERRNO inside a handler.",
    ),
    _r(
        "PROC_PRINT",
        "PRINT",
        "procedural",
        "low",
        r"^[ \t]*PRINT\b",
        "No PRINT. Remove, or return diagnostics through a result set or a log table.",
    ),
    _r(
        "PROC_RETURN_VALUE",
        "Procedure returns a status value",
        "procedural",
        "medium",
        r"\bRETURN\s*(?:\(|-?\d|@)",
        "MySQL procedures cannot return a value. Use an OUT parameter, and update callers that "
        "read the return code.",
        PROC,
    ),
    _r(
        "PROC_OUTPUT_PARAM",
        "OUTPUT parameter",
        "procedural",
        "low",
        r"@\w+\s+[\w\[\]\.]+(?:\s*\([^)\n]*\))?(?:\s*=\s*[^,\n]+?)?\s+OUT(?:PUT)?\b(?!\s+(?:inserted|deleted)\b)",
        "Declare as OUT or INOUT before the parameter name.",
        MODULES,
    ),
    _r(
        "PROC_DECLARE_VARIABLE",
        "Local variable declarations",
        "procedural",
        "low",
        r"\bDECLARE\s+@\w+",
        "Rename variables without @ (or use @session variables deliberately) and move every "
        "DECLARE to the start of its BEGIN … END block.",
        MODULES,
    ),
    _r(
        "PROC_TABLE_VARIABLE",
        "Table variable",
        "procedural",
        "medium",
        r"\bDECLARE\s+@\w+\s+(?:AS\s+)?TABLE\b",
        "Use CREATE TEMPORARY TABLE, and drop it at the end of the procedure.",
    ),
    _r(
        "PROC_TEMP_TABLE",
        "#temporary table",
        "procedural",
        "medium",
        r"(?<![\w#\]@$])#(?!#)[A-Za-z_]\w*",
        "Use CREATE TEMPORARY TABLE. A MySQL temporary table cannot be referenced twice in one "
        "query (self-join or UNION), which often forces a second copy.",
    ),
    _r(
        "PROC_GLOBAL_TEMP_TABLE",
        "##global temporary table",
        "procedural",
        "high",
        r"(?<![\w#])##[A-Za-z_]\w*",
        "No global temporary tables. Use a regular table keyed by session or job ID.",
    ),
    _r(
        "PROC_TVP",
        "Table-valued parameter (READONLY)",
        "procedural",
        "high",
        r"\bREADONLY\b",
        "Pass rows as a JSON parameter and expand with JSON_TABLE, or have the caller fill a "
        "staging table first.",
        MODULES,
    ),
    _r(
        "PROC_DYNAMIC_SQL",
        "Dynamic SQL (sp_executesql / EXEC(@sql))",
        "procedural",
        "high",
        r"\bsp_executesql\b|\bEXEC(?:UTE)?\s*\(\s*(?:@|N?')",
        "Use PREPARE / EXECUTE / DEALLOCATE PREPARE, and convert the generated T-SQL text as "
        "well. Review for SQL injection.",
    ),
    _r(
        "PROC_CURSOR",
        "Cursor",
        "procedural",
        "medium",
        r"\bDECLARE\s+@?\w+\s+(?:INSENSITIVE\s+|SCROLL\s+)?CURSOR\b|@@FETCH_STATUS\b",
        "MySQL cursors are forward-only, must be declared after variables, and end with a "
        "NOT FOUND handler instead of @@FETCH_STATUS. Prefer a set-based rewrite.",
    ),
    _r(
        "PROC_GOTO",
        "GOTO",
        "procedural",
        "medium",
        r"\bGOTO\s+\w+",
        "No GOTO. Restructure with labelled blocks and LEAVE / ITERATE.",
    ),
    _r(
        "PROC_WAITFOR",
        "WAITFOR DELAY / TIME",
        "procedural",
        "medium",
        r"\bWAITFOR\s+(?:DELAY|TIME)\b",
        "Use DO SLEEP(seconds).",
    ),
    _r(
        "PROC_NATIVE_COMPILATION",
        "Natively compiled module",
        "feature",
        "high",
        r"\bNATIVE_COMPILATION\b|\bBEGIN\s+ATOMIC\b",
        "Convert to an ordinary stored procedure; performance characteristics change and need "
        "load testing.",
    ),
    _r(
        "PROC_SET_OPTIONS",
        "SET ANSI_NULLS / QUOTED_IDENTIFIER / NOCOUNT …",
        "procedural",
        "info",
        r"\bSET\s+(?:ANSI_NULLS|QUOTED_IDENTIFIER|ANSI_PADDING|ANSI_WARNINGS|ARITHABORT|"
        r"CONCAT_NULL_YIELDS_NULL|NUMERIC_ROUNDABORT|NOCOUNT)\b",
        "Remove. Set sql_mode once per session or in the parameter group instead.",
    ),
    # --- Functions ----------------------------------------------------------------------------
    _r(
        "FN_ISNULL",
        "ISNULL()",
        "syntax",
        "low",
        r"\bISNULL\s*\(",
        "Use IFNULL() or COALESCE().",
    ),
    _r(
        "FN_GETDATE",
        "GETDATE() / SYSDATETIME() / GETUTCDATE()",
        "syntax",
        "low",
        r"\b(?:GETDATE|GETUTCDATE|SYSDATETIME|SYSUTCDATETIME)\s*\(",
        "Use NOW(6) / UTC_TIMESTAMP(6). Check the session time_zone on Aurora (UTC by default).",
    ),
    _r(
        "FN_TIMEZONE",
        "SYSDATETIMEOFFSET / SWITCHOFFSET / AT TIME ZONE",
        "syntax",
        "medium",
        r"\b(?:SYSDATETIMEOFFSET|TODATETIMEOFFSET|SWITCHOFFSET)\s*\(|\bAT\s+TIME\s+ZONE\b",
        "Use CONVERT_TZ() with named zones (load the time-zone tables in Aurora).",
    ),
    _r(
        "FN_DATE_FUNCTIONS",
        "DATEADD / DATEDIFF / DATEPART / DATENAME / EOMONTH",
        "syntax",
        "low",
        r"\b(?:DATEADD|DATEDIFF|DATEDIFF_BIG|DATEPART|DATENAME|EOMONTH|DATEFROMPARTS|"
        r"DATETIMEFROMPARTS|DATETIME2FROMPARTS)\s*\(",
        "Use DATE_ADD / TIMESTAMPDIFF / EXTRACT / LAST_DAY. DATEDIFF counts boundaries crossed, "
        "TIMESTAMPDIFF counts whole units: results differ.",
    ),
    _r(
        "FN_CONVERT",
        "CONVERT(type, expr [, style])",
        "syntax",
        "medium",
        r"\bCONVERT\s*\(",
        "MySQL CONVERT is CONVERT(expr, type) and has no style codes. Use CAST, or DATE_FORMAT / "
        "STR_TO_DATE for styled dates.",
    ),
    _r(
        "FN_TRY_CONVERT",
        "TRY_CONVERT / TRY_CAST / TRY_PARSE / PARSE",
        "syntax",
        "medium",
        r"\b(?:TRY_CONVERT|TRY_CAST|TRY_PARSE|PARSE)\s*\(",
        "No TRY_ variants. Validate with REGEXP_LIKE before CAST, or handle in the application.",
    ),
    _r(
        "FN_FORMAT",
        "FORMAT()",
        "syntax",
        "medium",
        r"(?<![\w.])FORMAT\s*\(",
        "MySQL FORMAT() only formats numbers. Use DATE_FORMAT for dates; .NET format strings "
        "must be translated.",
    ),
    _r(
        "FN_STRING_FUNCTIONS",
        "CHARINDEX / PATINDEX / LEN / DATALENGTH / REPLICATE / SPACE",
        "syntax",
        "low",
        r"\b(?:CHARINDEX|PATINDEX|LEN|DATALENGTH|REPLICATE|SPACE)\s*\(",
        "Use LOCATE / REGEXP_INSTR / CHAR_LENGTH / LENGTH / REPEAT. LEN ignores trailing spaces; "
        "CHAR_LENGTH does not.",
    ),
    _r(
        "FN_IIF_CHOOSE",
        "IIF() / CHOOSE()",
        "syntax",
        "low",
        r"\b(?:IIF|CHOOSE)\s*\(",
        "Use IF() / ELT() or CASE.",
    ),
    _r(
        "FN_STRING_AGG",
        "STRING_AGG()",
        "syntax",
        "low",
        r"\bSTRING_AGG\s*\(",
        "Use GROUP_CONCAT(… SEPARATOR …); raise group_concat_max_len for long results.",
    ),
    _r(
        "FN_STRING_SPLIT",
        "STRING_SPLIT()",
        "syntax",
        "medium",
        r"\bSTRING_SPLIT\s*\(",
        "No split function. Use JSON_TABLE over a JSON array, or a recursive CTE.",
    ),
    _r(
        "FN_STUFF",
        "STUFF()",
        "syntax",
        "medium",
        r"\bSTUFF\s*\(",
        "Use INSERT(str, pos, len, newstr). STUFF(… FOR XML PATH) concatenation becomes "
        "GROUP_CONCAT.",
    ),
    _r(
        "FN_NEWID",
        "NEWID() / NEWSEQUENTIALID()",
        "syntax",
        "medium",
        r"\b(?:NEWID|NEWSEQUENTIALID)\s*\(",
        "Use UUID(), or UUID_TO_BIN(UUID(), 1) for time-ordered binary keys.",
    ),
    _r(
        "FN_ROWCOUNT",
        "@@ROWCOUNT",
        "syntax",
        "low",
        r"@@ROWCOUNT\b",
        "Use ROW_COUNT() immediately after the statement.",
    ),
    _r(
        "FN_HASH",
        "HASHBYTES / CHECKSUM / BINARY_CHECKSUM",
        "syntax",
        "medium",
        r"\b(?:HASHBYTES|CHECKSUM|BINARY_CHECKSUM|CHECKSUM_AGG)\s*\(",
        "Use SHA2 / MD5 / CRC32. Stored hashes computed by SQL Server will not match.",
    ),
    _r(
        "FN_SESSION_CONTEXT",
        "SESSION_CONTEXT / CONTEXT_INFO",
        "syntax",
        "medium",
        r"\b(?:SESSION_CONTEXT|CONTEXT_INFO|sp_set_session_context)\b",
        "Use user-defined session variables (@var) set by the application on connect.",
    ),
    _r(
        "FN_IDENTITY_FUNCTIONS",
        "SUSER_SNAME / ORIGINAL_LOGIN / APP_NAME / @@SPID",
        "syntax",
        "low",
        r"\b(?:SUSER_SNAME|SUSER_NAME|ORIGINAL_LOGIN|USER_NAME|APP_NAME|HOST_NAME)\s*\(|@@SPID\b",
        "Use CURRENT_USER() / USER() / CONNECTION_ID(). Application name is not available.",
    ),
    _r(
        "FN_ISNUMERIC",
        "ISNUMERIC()",
        "syntax",
        "medium",
        r"\bISNUMERIC\s*\(",
        "Use REGEXP_LIKE(expr, '^-?[0-9]+(\\.[0-9]+)?$'); ISNUMERIC accepts currency symbols.",
    ),
    _r(
        "FN_JSON_OPENJSON",
        "OPENJSON()",
        "feature",
        "high",
        r"\bOPENJSON\s*\(",
        "Use JSON_TABLE() with an explicit column list; the WITH clause maps closely.",
    ),
    _r(
        "FN_JSON_FOR_JSON",
        "FOR JSON",
        "feature",
        "high",
        r"\bFOR\s+JSON\b",
        "Build JSON with JSON_OBJECT / JSON_ARRAYAGG. Nesting and NULL handling differ.",
    ),
    _r(
        "FN_JSON_SCALAR",
        "JSON_VALUE / JSON_QUERY / JSON_MODIFY / ISJSON",
        "syntax",
        "medium",
        r"\b(?:JSON_VALUE|JSON_QUERY|JSON_MODIFY|ISJSON)\s*\(",
        "Use JSON_UNQUOTE(JSON_EXTRACT()) / JSON_EXTRACT / JSON_SET / JSON_VALID.",
    ),
    _r(
        "FN_XML_FOR_XML",
        "FOR XML",
        "feature",
        "high",
        r"\bFOR\s+XML\b",
        "No XML generation. FOR XML PATH('') string concatenation becomes GROUP_CONCAT; real XML "
        "output moves to the application.",
    ),
    _r(
        "FN_XML_METHODS",
        "XML methods (.nodes / .value / .query / .exist / .modify) and OPENXML",
        "feature",
        "high",
        r"\.(?:nodes|value|query|exist|modify)\s*\(|\bOPENXML\s*\(|\bsp_xml_preparedocument\b",
        "No XQuery. Use ExtractValue() for simple XPath, or move XML parsing to the application.",
    ),
    _r(
        "FN_FULLTEXT_PREDICATES",
        "CONTAINS / FREETEXT full-text predicates",
        "feature",
        "medium",
        r"\b(?:CONTAINS|FREETEXT|CONTAINSTABLE|FREETEXTTABLE)\s*\(",
        "Use MATCH(…) AGAINST(… IN BOOLEAN MODE) over an InnoDB FULLTEXT index; ranking differs.",
    ),
    _r(
        "FN_SPATIAL_METHODS",
        "Spatial methods (.STDistance, .STIntersects, ::Point …)",
        "feature",
        "medium",
        r"\.(?:STDistance|STIntersects|STIntersection|STContains|STWithin|STAsText|STBuffer|STArea|"
        r"STLength|STX|STY|STSrid|STIsValid|STEquals|STNumPoints|STUnion|STGeomFromText|"
        r"STPointFromText|Lat|Long|Reduce|BufferWithTolerance)\b|\b(?:geography|geometry)\s*::",
        "Use ST_Distance_Sphere / ST_Intersects / ST_GeomFromText / POINT(). Argument order "
        "(longitude, latitude) differs.",
    ),
    # --- Transactions -------------------------------------------------------------------------
    _r(
        "TX_BEGIN_TRAN",
        "BEGIN TRAN / COMMIT TRAN",
        "transaction",
        "low",
        r"\bBEGIN\s+TRAN(?:SACTION)?\b",
        "Use START TRANSACTION / COMMIT / ROLLBACK.",
    ),
    _r(
        "TX_SAVEPOINT",
        "SAVE TRANSACTION",
        "transaction",
        "medium",
        r"\bSAVE\s+TRAN(?:SACTION)?\b",
        "Use SAVEPOINT name / ROLLBACK TO SAVEPOINT name.",
    ),
    _r(
        "TX_XACT_ABORT",
        "SET XACT_ABORT",
        "transaction",
        "medium",
        r"\bXACT_ABORT\b",
        "No equivalent. Add an EXIT HANDLER that rolls back and resignals.",
    ),
    _r(
        "TX_TRANCOUNT",
        "@@TRANCOUNT / XACT_STATE()",
        "transaction",
        "medium",
        r"@@TRANCOUNT\b|\bXACT_STATE\s*\(",
        "No nested transaction count. Track transaction ownership explicitly; MySQL commits "
        "implicitly on DDL.",
    ),
    _r(
        "TX_SNAPSHOT",
        "SNAPSHOT isolation",
        "transaction",
        "medium",
        r"\bISOLATION\s+LEVEL\s*=?\s*SNAPSHOT\b|\bALLOW_SNAPSHOT_ISOLATION\b",
        "No SNAPSHOT level. InnoDB REPEATABLE READ gives consistent reads; write-conflict "
        "detection differs.",
    ),
    _r(
        "TX_DISTRIBUTED",
        "Distributed transaction",
        "transaction",
        "high",
        r"\bBEGIN\s+DISTRIBUTED\s+TRAN",
        "No MSDTC. Redesign as a single-database transaction or a saga with compensating steps.",
    ),
    # --- Features -----------------------------------------------------------------------------
    _r(
        "FEAT_TEMPORAL",
        "System-versioned temporal table",
        "feature",
        "high",
        r"\bSYSTEM_VERSIONING\b|\bFOR\s+SYSTEM_TIME\b",
        "No temporal tables. Keep the history table and maintain it with AFTER UPDATE/DELETE "
        "triggers; rewrite FOR SYSTEM_TIME queries against it.",
    ),
    _r(
        "FEAT_MEMORY_OPTIMIZED",
        "Memory-optimised table",
        "feature",
        "high",
        r"\bMEMORY_OPTIMIZED\s*=\s*ON\b",
        "Convert to an InnoDB table. If it is a staging table, consider a temporary table.",
    ),
    _r(
        "FEAT_COLUMNSTORE",
        "Columnstore index",
        "feature",
        "medium",
        r"\bCOLUMNSTORE\b",
        "No columnstore in InnoDB. Drop the index; for analytics, consider Aurora zero-ETL "
        "integration with Amazon Redshift.",
    ),
    _r(
        "FEAT_FILTERED_INDEX",
        "Filtered index",
        "feature",
        "medium",
        r"\)\s*(?:INCLUDE\s*\([^)]*\)\s*)?WHERE\b",
        "No filtered indexes. Index the full column, or index a generated column that is NULL "
        "outside the filter.",
        INDEX,
    ),
    _r(
        "FEAT_INCLUDE_COLUMNS",
        "Index INCLUDE columns",
        "feature",
        "low",
        r"\bINCLUDE\s*\(",
        "No included columns. Add them to the key (watch the 3072-byte key limit) or drop them.",
        INDEX,
    ),
    _r(
        "FEAT_CLUSTERED_INDEX",
        "Clustered index not on the primary key",
        "feature",
        "medium",
        r"\bCREATE\s+(?:UNIQUE\s+)?CLUSTERED\s+INDEX\b",
        "InnoDB always clusters on the primary key. Re-check access paths that relied on this "
        "clustering.",
        INDEX,
    ),
    _r(
        "FEAT_PARTITIONING",
        "Partition function / scheme",
        "feature",
        "medium",
        r"\bCREATE\s+PARTITION\s+(?:FUNCTION|SCHEME)\b|\bON\s+\[?PS_\w+\]?\s*\(",
        "Use MySQL RANGE partitioning in the table definition. The partition key must be part of "
        "every unique key, including the primary key.",
    ),
    _r(
        "FEAT_FILEGROUP",
        "Filegroup / storage placement",
        "platform",
        "info",
        r"\bADD\s+FILEGROUP\b|\bTEXTIMAGE_ON\b|\bFILESTREAM_ON\b|\bON\s+\[?(?:USERDATA|PRIMARY)\]?\s*;?\s*$",
        "Remove. Aurora manages storage.",
    ),
    _r(
        "FEAT_FILESTREAM",
        "FILESTREAM / FileTable",
        "feature",
        "blocker",
        r"\bFILESTREAM\b|\bAS\s+FILETABLE\b",
        "Move file content to Amazon S3 and keep the object key in the table.",
    ),
    _r(
        "FEAT_DATA_COMPRESSION",
        "DATA_COMPRESSION",
        "platform",
        "low",
        r"\bDATA_COMPRESSION\s*=",
        "Remove, or use ROW_FORMAT=COMPRESSED after measuring.",
    ),
    _r(
        "FEAT_SYNONYM",
        "Synonym",
        "feature",
        "medium",
        r"\bCREATE\s+SYNONYM\b",
        "No synonyms. Replace with a view, or reference the target directly.",
    ),
    _r(
        "FEAT_FULLTEXT_INDEX",
        "Full-text catalog / index",
        "feature",
        "medium",
        r"\bCREATE\s+FULLTEXT\s+(?:CATALOG|INDEX)\b",
        "Create an InnoDB FULLTEXT index; there are no catalogs, and stop-lists and stemming "
        "differ.",
    ),
    _r(
        "FEAT_SERVICE_BROKER",
        "Service Broker",
        "feature",
        "blocker",
        r"\bCREATE\s+(?:QUEUE|SERVICE|CONTRACT|MESSAGE\s+TYPE)\b|\bSEND\s+ON\s+CONVERSATION\b|"
        r"\bBEGIN\s+DIALOG\b|\bEND\s+CONVERSATION\b|\bWAITFOR\s*\(\s*RECEIVE\b",
        "Move messaging to Amazon SQS or Amazon EventBridge, driven by the application.",
    ),
    _r(
        "FEAT_CLR",
        "CLR assembly / external module",
        "feature",
        "blocker",
        r"\bCREATE\s+ASSEMBLY\b|\bEXTERNAL\s+NAME\b",
        "Re-implement the CLR logic in the application (.NET 10 service), or call AWS Lambda from "
        "Aurora MySQL with lambda_sync().",
    ),
    _r(
        "FEAT_DDL_TRIGGER",
        "DDL / server trigger",
        "feature",
        "high",
        r"\bON\s+(?:DATABASE|ALL\s+SERVER)\b",
        "No DDL triggers. Use Aurora database activity streams or audit logs for DDL auditing.",
        TRIGGER,
    ),
    _r(
        "FEAT_INSTEAD_OF_TRIGGER",
        "INSTEAD OF trigger",
        "feature",
        "high",
        r"\bINSTEAD\s+OF\b",
        "No INSTEAD OF triggers. Move the logic into a stored procedure or the application, and "
        "make the view read-only.",
        TRIGGER,
    ),
    _r(
        "FEAT_TRIGGER_MULTI_EVENT",
        "Trigger on several events",
        "feature",
        "medium",
        r"\b(?:FOR|AFTER)\s+(?:INSERT|UPDATE|DELETE)\s*,",
        "A MySQL trigger handles one event. Split into one trigger per event.",
        TRIGGER,
    ),
    _r(
        "FEAT_TRIGGER_PSEUDO_TABLES",
        "inserted / deleted pseudo-tables",
        "feature",
        "medium",
        r"(?<![\w.@])(?:inserted|deleted)\b",
        "MySQL triggers are row-level: set-based logic over inserted/deleted becomes per-row logic "
        "over NEW / OLD.",
        TRIGGER,
    ),
    _r(
        "FEAT_TRIGGER_UPDATE_FN",
        "UPDATE(column) / COLUMNS_UPDATED()",
        "feature",
        "medium",
        r"\bUPDATE\s*\(\s*[\[\w]+\s*\]?\s*\)|\bCOLUMNS_UPDATED\s*\(",
        "Compare NEW.col <> OLD.col (NULL-safe: NOT (NEW.col <=> OLD.col)).",
        TRIGGER,
    ),
    _r(
        "FEAT_STATISTICS",
        "CREATE / UPDATE STATISTICS",
        "platform",
        "low",
        r"\bCREATE\s+STATISTICS\b|\bUPDATE\s+STATISTICS\b",
        "Use ANALYZE TABLE (and histograms with ANALYZE TABLE … UPDATE HISTOGRAM).",
    ),
    _r(
        "FEAT_DBCC",
        "DBCC command",
        "platform",
        "medium",
        r"\bDBCC\s+\w+",
        "No DBCC. CHECKIDENT becomes ALTER TABLE … AUTO_INCREMENT; checks move to Aurora "
        "operations.",
    ),
    _r(
        "FEAT_BULK_INSERT",
        "BULK INSERT",
        "feature",
        "medium",
        r"\bBULK\s+INSERT\b",
        "Use LOAD DATA FROM S3 (Aurora MySQL) or LOAD DATA LOCAL INFILE.",
    ),
    # --- Security -----------------------------------------------------------------------------
    _r(
        "SEC_ROW_LEVEL_SECURITY",
        "Row-level security policy",
        "security",
        "high",
        r"\bCREATE\s+SECURITY\s+POLICY\b|\bADD\s+(?:FILTER|BLOCK)\s+PREDICATE\b",
        "No row-level security. Enforce through SQL SECURITY DEFINER views that filter on a "
        "session variable, or in the application.",
    ),
    _r(
        "SEC_DYNAMIC_MASKING",
        "Dynamic data masking",
        "security",
        "medium",
        r"\bMASKED\s+WITH\b",
        "No dynamic masking. Expose masked columns through views, or mask in the application.",
    ),
    _r(
        "SEC_ALWAYS_ENCRYPTED",
        "Always Encrypted column",
        "security",
        "high",
        r"\bENCRYPTED\s+WITH\b",
        "No client-side column encryption in MySQL. Encrypt in the application with AWS KMS, or "
        "rely on Aurora encryption at rest.",
    ),
    _r(
        "SEC_EXECUTE_AS",
        "EXECUTE AS",
        "security",
        "medium",
        r"\bEXEC(?:UTE)?\s+AS\s+(?:OWNER|CALLER|SELF|USER|LOGIN)\b",
        "Use SQL SECURITY DEFINER / INVOKER on the routine, with a DEFINER account.",
    ),
    _r(
        "SEC_WITH_ENCRYPTION",
        "WITH ENCRYPTION module",
        "security",
        "high",
        r"\bWITH\s+(?:\w+\s*,\s*)*ENCRYPTION\b(?!\s*=)",
        "The module text is obfuscated in the database. Recover the source before conversion.",
    ),
    _r(
        "SEC_DENY",
        "DENY permission",
        "security",
        "medium",
        r"\bDENY\s+\w",
        "MySQL has no DENY. Restructure grants so the permission is never granted.",
    ),
    _r(
        "SEC_PRINCIPALS",
        "Login / user / role",
        "security",
        "low",
        r"\bCREATE\s+(?:LOGIN|USER|ROLE|APPLICATION\s+ROLE)\b",
        "Recreate as MySQL users and roles; consider IAM database authentication for Aurora.",
        {"security_principal"},
    ),
    _r(
        "SEC_WINDOWS_LOGIN",
        "Windows / Entra ID login",
        "security",
        "high",
        r"\bFROM\s+(?:WINDOWS|EXTERNAL\s+PROVIDER)\b",
        "No Windows authentication on Aurora MySQL. Use IAM database authentication or "
        "Secrets Manager-managed credentials.",
    ),
    # --- Cross-database and platform ----------------------------------------------------------
    _r(
        "XDB_THREE_PART_NAME",
        "Cross-database reference (three-part name)",
        "cross_database",
        "high",
        _three_part(),
        "A MySQL schema is a database: same-cluster cross-database queries work with db.table, "
        "but a database in another instance needs an API boundary or consolidation.",
    ),
    _r(
        "XDB_LINKED_SERVER",
        "Linked server (four-part name, OPENQUERY, OPENROWSET)",
        "cross_database",
        "blocker",
        _four_part()
        + r"|\b(?:OPENQUERY|OPENROWSET|OPENDATASOURCE)\s*\(|\bsp_addlinkedserver\b",
        "No linked servers. Replace with application-level integration, AWS Glue, or AWS DMS "
        "replication into the same cluster.",
    ),
    _r(
        "PLAT_SYSTEM_CATALOG",
        "System catalog access (sys.*, OBJECT_ID)",
        "platform",
        "medium",
        r"\bsys\s*\.\s*\w+|\bOBJECT_ID\s*\(|\bOBJECTPROPERTY\s*\(|\bCOL_LENGTH\s*\(",
        "Rewrite against information_schema / performance_schema.",
    ),
    _r(
        "PLAT_SYSTEM_DATABASE",
        "master / msdb / tempdb reference",
        "platform",
        "medium",
        r"\b(?:master|msdb|tempdb|model)\s*\.\s*\w",
        "No system databases with these names; find the equivalent capability in Aurora.",
    ),
    _r(
        "PLAT_EXTENDED_PROPERTY",
        "Extended property",
        "platform",
        "info",
        r"\bsp_addextendedproperty\b",
        "Move descriptions into COMMENT clauses on the table and columns.",
    ),
    _r(
        "PLAT_XP_CMDSHELL",
        "xp_cmdshell",
        "platform",
        "blocker",
        r"\bxp_cmdshell\b",
        "Operating-system access from the database is not possible on Aurora. Move to AWS Batch "
        "or Lambda.",
    ),
    _r(
        "PLAT_OLE_AUTOMATION",
        "OLE automation (sp_OA*)",
        "platform",
        "blocker",
        r"\bsp_OA(?:Create|Method|GetProperty|SetProperty|Destroy)\b",
        "Move to the application, or call AWS Lambda with lambda_sync().",
    ),
    _r(
        "PLAT_EXTENDED_PROC",
        "Extended stored procedure (xp_*)",
        "platform",
        "high",
        r"\bxp_(?!cmdshell\b)\w+",
        "Replace with an application or AWS service equivalent.",
    ),
    _r(
        "PLAT_DATABASE_MAIL",
        "Database Mail (sp_send_dbmail)",
        "platform",
        "high",
        r"\bsp_send_dbmail\b",
        "Send from the application through Amazon SES, or invoke Lambda with lambda_async().",
    ),
    _r(
        "SCHED_SQL_AGENT",
        "SQL Server Agent job",
        "scheduling",
        "high",
        r"\bsp_(?:add_job|add_jobstep|add_jobschedule|add_schedule|attach_schedule|add_jobserver|"
        r"start_job|update_job|delete_job)\b",
        "No SQL Agent. Schedule with Amazon EventBridge Scheduler and run the step as an AWS Batch "
        "job, ECS task, or Lambda; MySQL events suit only pure-SQL jobs.",
    ),
]

RULES_BY_ID: dict[str, Rule] = {r.id: r for r in RULES}


def evaluate(masked: str, object_type: str) -> list[RuleHit]:
    """Run every applicable rule over one object's masked text."""
    hits: list[RuleHit] = []
    for rule in RULES:
        if rule.applies_to is not None and object_type not in rule.applies_to:
            continue
        if rule.matcher is not None:
            offsets = rule.matcher(masked)
        elif rule.pattern is not None:
            offsets = [m.start() for m in rule.pattern.finditer(masked)]
        else:
            continue  # derived in the discovery service (columns, identifier length)
        if offsets:
            hits.append(RuleHit(rule, offsets))
    return hits


def hits_to_lines(
    hits: list[RuleHit], text: str, base_line: int
) -> list[tuple[Rule, list[int]]]:
    """Convert hit offsets to absolute 1-based line numbers (de-duplicated, in order)."""
    index = LineIndex(text)
    result: list[tuple[Rule, list[int]]] = []
    for hit in hits:
        lines: list[int] = []
        for off in hit.offsets:
            line = base_line + index.line_of(off) - 1
            if line not in lines:
                lines.append(line)
        result.append((hit.rule, lines))
    return result
