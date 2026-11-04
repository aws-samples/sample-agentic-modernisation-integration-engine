"""C# / .NET application scanner: embedded SQL, SQL Server API usage, connection strings, packages.

Application code is where a database migration most often breaks: T-SQL held in string literals,
SQL Server-only ADO.NET APIs, Windows-authenticated connection strings, and providers pinned to
SQL Server. This module finds those deterministically; resolving stored-procedure names against the
database inventory happens in the discovery service, which knows the inventory.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from services.db_discovery.tsql_scanner import IDENT, LineIndex, QNAME, split_qname

APP_EXTENSIONS = {".cs", ".config", ".csproj", ".edmx", ".json", ".vbproj"}


def is_app_file(filename: str) -> bool:
    lower = filename.lower()
    if lower == "packages.config":
        return True
    if lower.endswith(".json"):
        return lower.startswith("appsettings") or lower == "connectionstrings.json"
    return any(lower.endswith(ext) for ext in APP_EXTENSIONS)


# --- C# lexer ----------------------------------------------------------------------------------


@dataclass
class CsLiteral:
    text: str
    start: int
    end: int
    line: int
    interpolated: bool = False
    verbatim: bool = False
    concatenated: bool = False  # merged from "a" + "b" runs
    dynamic: bool = False  # concatenated with a non-literal expression or interpolated


_LITERAL_START = re.compile(r'(\$+@?|@\$+|@)?("""+|")')


def _skip_interpolation_hole(src: str, i: int) -> int:
    """``src[i]`` is ``{``; return the index just past the matching ``}``."""
    depth = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        elif c == '"':
            j = src.find('"', i + 1)
            i = n if j == -1 else j
        i += 1
    return n


def lex_csharp(src: str) -> tuple[list[CsLiteral], str]:
    """Return string literals and a copy of the source with comments blanked out."""
    literals: list[CsLiteral] = []
    code = list(src)
    n = len(src)
    index = LineIndex(src)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if code[k] != "\n":
                code[k] = " "

    i = 0
    while i < n:
        c = src[i]
        if c == "/" and src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
            continue
        if c == "/" and src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            blank(i, j)
            i = j
            continue
        if c == "'":
            # char literal: 'a', '\n', '\'', '\u0041'
            j = i + 1
            if j < n and src[j] == "\\":
                j += 2
                while j < n and src[j] != "'" and j - i < 10:
                    j += 1
            else:
                j += 1
            i = j + 1
            continue

        # Prefixes: $, @, $@, @$, and raw strings (""" with any number of $).
        if c not in '$@"':
            i += 1
            continue
        m = _LITERAL_START.match(src, i)
        if not m or (
            m.start() > 0
            and (src[m.start() - 1].isalnum() or src[m.start() - 1] == "_")
        ):
            i += 1
            continue
        prefix = m.group(1) or ""
        quotes = m.group(2)
        interpolated = "$" in prefix
        verbatim = "@" in prefix
        body_start = m.end()
        parts: list[str] = []

        if len(quotes) >= 3:
            close = src.find(quotes, body_start)
            close = n if close == -1 else close
            body = src[body_start:close]
            if interpolated:
                body = re.sub(r"\{[^{}]*\}", "{?}", body)
            parts.append(body)
            end = min(close + len(quotes), n)
        else:
            j = body_start
            while j < n:
                ch = src[j]
                if verbatim:
                    if ch == '"':
                        if j + 1 < n and src[j + 1] == '"':
                            parts.append('"')
                            j += 2
                            continue
                        break
                else:
                    if ch == "\\" and j + 1 < n:
                        parts.append(
                            {"n": "\n", "t": "\t", "r": "", '"': '"', "\\": "\\"}.get(
                                src[j + 1], src[j + 1]
                            )
                        )
                        j += 2
                        continue
                    if ch == '"' or ch == "\n":
                        break
                if interpolated and ch == "{":
                    if j + 1 < n and src[j + 1] == "{":
                        parts.append("{")
                        j += 2
                        continue
                    j = _skip_interpolation_hole(src, j)
                    parts.append("{?}")
                    continue
                if interpolated and ch == "}" and j + 1 < n and src[j + 1] == "}":
                    parts.append("}")
                    j += 2
                    continue
                parts.append(ch)
                j += 1
            end = min(j + 1, n)

        literals.append(
            CsLiteral(
                text="".join(parts),
                start=i,
                end=end,
                line=index.line_of(i),
                interpolated=interpolated,
                verbatim=verbatim or len(quotes) >= 3,
                dynamic=interpolated and "{?}" in "".join(parts),
            )
        )
        i = end
    return literals, "".join(code)


_PLUS_ONLY = re.compile(r"^\s*\+\s*$")
_PLUS_EXPR_AFTER = re.compile(r"^\s*\+\s*(?!\")[\w(@]")
_PLUS_EXPR_BEFORE = re.compile(r"[\w)\]]\s*\+\s*$")


def merge_concatenations(src: str, literals: list[CsLiteral]) -> list[CsLiteral]:
    """Merge ``"a" + "b"`` runs into one literal; flag runs joined to non-literal expressions."""
    merged: list[CsLiteral] = []
    for lit in literals:
        if merged and _PLUS_ONLY.match(src[merged[-1].end : lit.start]):
            prev = merged[-1]
            prev.text += lit.text
            prev.end = lit.end
            prev.concatenated = True
            prev.interpolated = prev.interpolated or lit.interpolated
            prev.dynamic = prev.dynamic or lit.dynamic
            continue
        merged.append(lit)
    for lit in merged:
        after = src[lit.end : lit.end + 80]
        before = src[max(0, lit.start - 80) : lit.start]
        # A trailing `+ ";"` style join to another literal was merged above; anything left that is
        # joined with + is an expression.
        if _PLUS_EXPR_AFTER.match(after) or _PLUS_EXPR_BEFORE.search(
            before.split("\n")[-1]
        ):
            lit.dynamic = True
            lit.concatenated = True
    return merged


# --- SQL classification ------------------------------------------------------------------------

_SQL_SHAPES = [
    re.compile(r"^\s*;?\s*SELECT\b[\s\S]*?\bFROM\b", re.I),
    re.compile(
        r"^\s*SELECT\s+(?:@@|SCOPE_IDENTITY|CAST\s*\(|COUNT\s*\(|NEWID|GETDATE|TOP\b)",
        re.I,
    ),
    re.compile(
        r"^\s*INSERT\s+(?:INTO\s+)?[\[\w@#].*?\b(?:VALUES|SELECT|OUTPUT|DEFAULT)\b",
        re.I | re.S,
    ),
    re.compile(r"^\s*INSERT\s+INTO\b", re.I),
    re.compile(
        r"^\s*UPDATE\s+(?:TOP\s*\([^)]*\)\s*)?[\[\w@#][\w\[\]\.]*\s+SET\b", re.I
    ),
    re.compile(r"^\s*DELETE\s+(?:FROM\s+)?[\[\w@#]", re.I),
    re.compile(r"^\s*MERGE\b[\s\S]*?\bUSING\b", re.I),
    re.compile(rf"^\s*EXEC(?:UTE)?\s+(?:@\w+\s*=\s*)?{IDENT}", re.I),
    re.compile(r"^\s*;?\s*WITH\s+[\[\w]+[\s\S]*?\bAS\s*\([\s\S]*?\bSELECT\b", re.I),
    re.compile(
        r"^\s*(?:CREATE|ALTER|DROP)\s+(?:TABLE|PROC(?:EDURE)?|VIEW|INDEX|FUNCTION|TRIGGER)\b",
        re.I,
    ),
    re.compile(r"^\s*TRUNCATE\s+TABLE\b", re.I),
    re.compile(
        r"^\s*(?:DECLARE\s+@|SET\s+NOCOUNT|IF\s+(?:NOT\s+)?EXISTS\s*\(\s*SELECT|BEGIN\s+TRAN)",
        re.I,
    ),
]


# Prose such as "Select an option from the list" has the shape of SELECT … FROM but none of the
# punctuation real SQL carries: a column list, parameter, qualified name, operator or parenthesis.
_SQL_PUNCTUATION = re.compile(r"[*,=@()\[\];<>]|\w\.\w")
_PROSE_WORDS = re.compile(
    r"\b(?:the|an|your|you|please|our|my|this|these|those|will|would|should|can|cannot|must)\b",
    re.I,
)


def looks_like_sql(text: str) -> bool:
    t = text.strip()
    if len(t) < 8 or "\n\n\n\n" in t[:200]:
        return False
    if not _SQL_PUNCTUATION.search(t[:4000]) and _PROSE_WORDS.search(t[:4000]):
        return False
    return any(p.search(t[:4000]) for p in _SQL_SHAPES)


# --- Application rules -------------------------------------------------------------------------


@dataclass(frozen=True)
class AppRule:
    id: str
    title: str
    severity: str
    pattern: re.Pattern[str] | None
    recommendation: str
    category: str = "application"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "severity": self.severity,
            "recommendation": self.recommendation,
            "applies_to": ["application"],
        }


def _a(
    rule_id, title, severity, pattern, recommendation, category="application"
) -> AppRule:
    return AppRule(
        rule_id,
        title,
        severity,
        re.compile(pattern, re.MULTILINE) if pattern else None,
        recommendation,
        category,
    )


APP_RULES: list[AppRule] = [
    _a(
        "APP_SQLCLIENT",
        "System.Data.SqlClient / Microsoft.Data.SqlClient provider",
        "medium",
        r"\busing\s+(?:System|Microsoft)\.Data\.SqlClient\s*;|\b(?:System|Microsoft)\.Data\.SqlClient\.\w",
        "Replace with MySqlConnector (MySqlConnection, MySqlCommand, MySqlParameter). Parameter "
        "names keep the @ prefix.",
    ),
    _a(
        "APP_SQLCLIENT_TYPES",
        "SqlConnection / SqlCommand / SqlDataAdapter usage",
        "low",
        r"\bnew\s+Sql(?:Connection|Command|DataAdapter|CommandBuilder|Parameter)\b",
        "Rename to the MySqlConnector types; behaviour of CommandTimeout and parameter type "
        "inference differs.",
    ),
    _a(
        "APP_SQLBULKCOPY",
        "SqlBulkCopy",
        "medium",
        r"\bSqlBulkCopy\b",
        "Use MySqlConnector's MySqlBulkCopy (requires AllowLoadLocalInfile=true), or LOAD DATA "
        "FROM S3 for very large loads.",
    ),
    _a(
        "APP_SQLDEPENDENCY",
        "SqlDependency / query notifications",
        "blocker",
        r"\bSqlDependency\b|\bSqlNotificationRequest\b",
        "No query notifications in MySQL. Publish change events from the application (Amazon SNS/"
        "EventBridge), or poll.",
    ),
    _a(
        "APP_TVP",
        "Table-valued parameter (SqlDbType.Structured)",
        "high",
        r"\bSqlDbType\.Structured\b",
        "MySQL has no table-valued parameters. Serialise the rows to JSON and expand them with "
        "JSON_TABLE in the procedure.",
    ),
    _a(
        "APP_SQLDBTYPE",
        "SQL Server-specific SqlDbType",
        "medium",
        r"\bSqlDbType\.(?:UniqueIdentifier|DateTimeOffset|Xml|Udt|Variant|Timestamp|Money|SmallMoney|Image|NText|DateTime2)\b",
        "Map to MySqlDbType equivalents (Guid, DateTime, JSON/LongText, Decimal, Blob).",
    ),
    _a(
        "APP_SQLEXCEPTION",
        "SqlException error-number handling",
        "medium",
        r"\bSqlException\b",
        "Catch MySqlException and map error numbers: 2627/2601 duplicate key → 1062, "
        "1205 deadlock → 1213, 547 FK violation → 1451/1452, -2 timeout → command timeout.",
    ),
    _a(
        "APP_SQLSERVER_TYPES",
        "Microsoft.SqlServer.Types (SqlGeography, SqlHierarchyId)",
        "high",
        r"\bSql(?:Geography|Geometry|HierarchyId)\b|\bMicrosoft\.SqlServer\.Types\b",
        "Use NetTopologySuite for spatial data and a path/closure-table model for hierarchies.",
    ),
    _a(
        "APP_TRANSACTIONSCOPE",
        "TransactionScope",
        "medium",
        r"\bTransactionScope\b",
        "Keep each scope on one connection: MySqlConnector enlists a single connection, but "
        "multiple connections escalate to XA transactions.",
    ),
    _a(
        "APP_XMLREADER",
        "ExecuteXmlReader (FOR XML results)",
        "high",
        r"\.ExecuteXmlReader\s*\(",
        "MySQL cannot return XML streams. Return rows or JSON and build XML in the application.",
    ),
    _a(
        "APP_EF6",
        "Entity Framework 6",
        "medium",
        r"\busing\s+System\.Data\.Entity\s*;",
        "Use MySql.Data.EntityFramework for EF6, or move to EF Core with "
        "Pomelo.EntityFrameworkCore.MySql as part of the .NET 10 port.",
    ),
    _a(
        "APP_EF6_SQLSERVER_FUNCTIONS",
        "EF SqlFunctions / SqlServer-only DbFunctions",
        "high",
        r"\bSqlFunctions\.\w+|\bSystem\.Data\.Entity\.SqlServer\b|\bSqlServerDbFunctionsExtensions\b",
        "These translate only to T-SQL. Replace with provider-neutral LINQ or MySQL functions.",
    ),
    _a(
        "APP_EF_RAW_SQL",
        "EF raw SQL (SqlQuery / ExecuteSqlCommand / FromSqlRaw)",
        "medium",
        r"\.(?:SqlQuery|ExecuteSqlCommand(?:Async)?|FromSqlRaw|FromSqlInterpolated|ExecuteSqlRaw(?:Async)?|ExecuteSqlInterpolated(?:Async)?|SqlQueryRaw)\s*[<(]",
        "Raw SQL is passed through unchanged: convert its T-SQL (see the embedded SQL findings).",
    ),
    _a(
        "APP_EF_SCHEMA",
        "EF default schema / ToTable with schema",
        "medium",
        r"\bHasDefaultSchema\s*\(|\.ToTable\s*\(\s*\"[^\"]+\"\s*,\s*\"",
        "A SQL Server schema becomes a MySQL database. Remove the schema, or map each schema to a "
        "database explicitly.",
    ),
    _a(
        "APP_EF_COLUMN_TYPE",
        "EF column type names specific to SQL Server",
        "medium",
        r"HasColumnType\s*\(\s*\"(?:datetime2|uniqueidentifier|money|smallmoney|nvarchar\(max\)|xml|datetimeoffset|geography|hierarchyid|image|ntext)|TypeName\s*=\s*\"(?:datetime2|uniqueidentifier|money|xml|datetimeoffset)",
        "Change the column types to MySQL type names.",
    ),
    _a(
        "APP_EF_SPATIAL",
        "DbGeography / DbGeometry",
        "high",
        r"\bDb(?:Geography|Geometry)\b",
        "Use NetTopologySuite with Pomelo's spatial support in EF Core.",
    ),
    _a(
        "APP_EFCORE_SQLSERVER",
        "EF Core UseSqlServer",
        "medium",
        r"\.UseSqlServer\s*\(",
        "Use UseMySql(connectionString, ServerVersion.AutoDetect(…)) from Pomelo.",
    ),
    _a(
        "APP_EF_ROWVERSION",
        "EF rowversion concurrency token",
        "medium",
        r"\.IsRowVersion\s*\(|\[Timestamp\]",
        "MySQL has no rowversion. Use a version column updated by the application "
        "(IsConcurrencyToken).",
    ),
    _a(
        "APP_EF_DEFAULT_SQL",
        "EF HasDefaultValueSql / HasComputedColumnSql",
        "medium",
        r"\.HasDefaultValueSql\s*\(|\.HasComputedColumnSql\s*\(",
        "These hold T-SQL expressions (GETDATE(), NEWID()); convert them to MySQL expressions.",
    ),
    _a(
        "APP_EDMX_SQLSERVER",
        "EDMX model bound to SQL Server",
        "high",
        r"Provider=\"System\.Data\.SqlClient\"",
        "The SSDL is SQL Server-specific. Regenerate the model against MySQL, or move to EF Core "
        "code-first during the .NET 10 port.",
    ),
    _a(
        "APP_ENTLIB",
        "Enterprise Library Data / SqlHelper",
        "medium",
        r"\bMicrosoft\.Practices\.EnterpriseLibrary\.Data\b|\bSqlHelper\.\w+",
        "These helpers are SQL Server-bound. Replace with MySqlConnector, optionally with Dapper.",
    ),
    _a(
        "APP_SMO",
        "SQL Server Management Objects (SMO)",
        "high",
        r"\bMicrosoft\.SqlServer\.Management\.\w",
        "SMO cannot manage MySQL. Replace administrative code with MySQL statements or AWS APIs.",
    ),
    _a(
        "APP_SQL_INJECTION",
        "SQL built by concatenation or interpolation",
        "medium",
        None,
        "Use parameters. Converting this statement is the moment to remove the injection risk.",
        "security",
    ),
    _a(
        "APP_CONN_PROVIDER",
        "SQL Server connection string",
        "low",
        None,
        "Rewrite for MySqlConnector (Server, Port, Database, User ID, SslMode=Required) and change "
        "providerName to MySqlConnector.",
    ),
    _a(
        "APP_CONN_WINDOWS_AUTH",
        "Windows authentication in connection string",
        "high",
        None,
        "Aurora MySQL has no Windows authentication. Use IAM database authentication or "
        "credentials from AWS Secrets Manager; this is also required for the Linux move.",
    ),
    _a(
        "APP_CONN_MARS",
        "MultipleActiveResultSets=true",
        "medium",
        None,
        "MySQL has no MARS. Close each data reader before running the next command on the "
        "connection, or open a second connection.",
    ),
    _a(
        "APP_CONN_PASSWORD",
        "Password stored in a connection string",
        "high",
        None,
        "Move credentials to AWS Secrets Manager and load them at start-up.",
        "security",
    ),
]
APP_RULES_BY_ID = {r.id: r for r in APP_RULES}

TECHNOLOGIES: dict[str, re.Pattern[str]] = {
    "ADO.NET (System.Data.SqlClient)": re.compile(r"\bSystem\.Data\.SqlClient\b"),
    "ADO.NET (Microsoft.Data.SqlClient)": re.compile(r"\bMicrosoft\.Data\.SqlClient\b"),
    "Dapper": re.compile(r"\busing\s+Dapper\s*;"),
    "Entity Framework 6": re.compile(r"\busing\s+System\.Data\.Entity\s*;"),
    "EF Core (SQL Server)": re.compile(
        r"\.UseSqlServer\s*\(|Microsoft\.EntityFrameworkCore\.SqlServer"
    ),
    "Enterprise Library Data": re.compile(
        r"\bMicrosoft\.Practices\.EnterpriseLibrary\.Data\b"
    ),
    "SQL Server Management Objects": re.compile(
        r"\bMicrosoft\.SqlServer\.Management\.\w"
    ),
}

PACKAGE_REPLACEMENTS: dict[str, str] = {
    "system.data.sqlclient": "MySqlConnector",
    "microsoft.data.sqlclient": "MySqlConnector",
    "entityframework": "MySql.Data.EntityFramework (EF6) or Pomelo.EntityFrameworkCore.MySql (EF Core on .NET 10)",
    "microsoft.entityframeworkcore.sqlserver": "Pomelo.EntityFrameworkCore.MySql",
    "dapper": "Dapper (unchanged; convert its SQL text)",
    "microsoft.sqlserver.types": "NetTopologySuite (spatial); redesign hierarchyid",
    "microsoft.sqlserver.sqlmanagementobjects": "None: replace with MySQL statements or AWS APIs",
    "enterpriselibrary.data": "MySqlConnector (+ Dapper)",
    "microsoft.practices.enterpriselibrary.data": "MySqlConnector (+ Dapper)",
    "nhibernate": "NHibernate with MySQL8Dialect and MySqlConnector driver",
    "hangfire.sqlserver": "Hangfire.MySqlStorage, or move scheduling to EventBridge Scheduler + AWS Batch",
    "microsoft.extensions.caching.sqlserver": "Amazon ElastiCache (Redis/Valkey) distributed cache",
    "linq2db.sqlserver": "linq2db.MySqlConnector",
}

# --- Findings and scan result ------------------------------------------------------------------


@dataclass
class AppFinding:
    rule_id: str
    file: str
    lines: list[int]
    snippet: str


@dataclass
class EmbeddedSql:
    file: str
    line: int
    text: str
    interpolated: bool
    concatenated: bool
    dynamic: bool
    verbatim: bool


@dataclass
class ProcCandidate:
    file: str
    line: int
    parts: list[str]
    via: str  # "exec" | "command_type" | "literal"


@dataclass
class ConnectionString:
    file: str
    line: int
    name: str
    redacted: str
    server: str
    database: str
    provider: str
    windows_auth: bool
    mars: bool
    password_present: bool


@dataclass
class Package:
    name: str
    version: str
    file: str
    replacement: str
    source: str = "package"  # "package" (PackageReference / packages.config) or "assembly_reference"


@dataclass
class AppScan:
    file: str
    technologies: list[str] = field(default_factory=list)
    findings: list[AppFinding] = field(default_factory=list)
    embedded_sql: list[EmbeddedSql] = field(default_factory=list)
    proc_candidates: list[ProcCandidate] = field(default_factory=list)
    connection_strings: list[ConnectionString] = field(default_factory=list)
    packages: list[Package] = field(default_factory=list)
    target_frameworks: list[str] = field(default_factory=list)


def _snippet(src: str, line: int) -> str:
    lines = src.split("\n")
    return lines[line - 1].strip()[:200] if 0 < line <= len(lines) else ""


def _rule_lines(pattern: re.Pattern[str], text: str, index: LineIndex) -> list[int]:
    lines: list[int] = []
    for m in pattern.finditer(text):
        ln = index.line_of(m.start())
        if ln not in lines:
            lines.append(ln)
    return lines


# --- Connection strings ------------------------------------------------------------------------

_CONN_KEYS = re.compile(
    r"(?:^|;)\s*(?:Data\s+Source|Server|Address|Addr|Network\s+Address)\s*=", re.I
)
_SQLSERVER_MARKERS = re.compile(
    r"Initial\s+Catalog\s*=|Integrated\s+Security\s*=|Trusted_Connection\s*=|MultipleActiveResultSets\s*=|"
    r"TrustServerCertificate\s*=|\(localdb\)|\\SQLEXPRESS|tcp:|provider=System\.Data\.SqlClient|"
    r"AttachDbFilename\s*=|Persist\s+Security\s+Info\s*=",
    re.I,
)
# XML: every element's attributes; JSON: every string token from the start (so keys and values
# alternate correctly); C#: string literals from the lexer.
_XML_ELEMENT = re.compile(r"<[A-Za-z][\w:.-]*\b([^<>]*)>", re.S)
_XML_ATTR = re.compile(r"([\w:.-]+)\s*=\s*\"([^\"]*)\"")
_JSON_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')


def _kv(conn: str, *keys: str) -> str:
    for key in keys:
        m = re.search(rf"(?:^|;)\s*{key}\s*=\s*([^;]*)", conn, re.I)
        if m:
            return m.group(1).strip().strip("'\"")
    return ""


def redact_connection_string(conn: str) -> str:
    return re.sub(r"((?:Password|Pwd)\s*=\s*)([^;\"']*)", r"\1***", conn, flags=re.I)


def _parse_connection(
    raw: str, file: str, line: int, name: str, context: str
) -> ConnectionString | None:
    conn = html.unescape(raw)
    inner = re.search(r'provider\s+connection\s+string\s*=\s*"([^"]*)"', conn, re.I)
    provider_hint = conn
    if inner:
        conn = inner.group(1)
    if not _CONN_KEYS.search(conn):
        return None
    has_db = re.search(r"(?:Initial\s+Catalog|Database)\s*=", conn, re.I)
    is_sqlserver = (
        bool(_SQLSERVER_MARKERS.search(provider_hint))
        or "System.Data.SqlClient" in context
    )
    if not (is_sqlserver or has_db):
        return None
    windows = bool(
        re.search(
            r"Integrated\s+Security\s*=\s*(?:true|sspi|yes)|Trusted_Connection\s*=\s*(?:true|yes)",
            conn,
            re.I,
        )
    )
    password = bool(re.search(r"(?:Password|Pwd)\s*=\s*[^;\s]", conn, re.I))
    return ConnectionString(
        file=file,
        line=line,
        name=name,
        redacted=redact_connection_string(conn)[:1000],
        server=_kv(
            conn, r"Data\s+Source", "Server", "Address", "Addr", r"Network\s+Address"
        ),
        database=_kv(conn, r"Initial\s+Catalog", "Database"),
        provider="sqlserver" if is_sqlserver else "unknown",
        windows_auth=windows,
        mars=bool(re.search(r"MultipleActiveResultSets\s*=\s*true", conn, re.I)),
        password_present=password,
    )


def _connection_candidates(src: str, file: str) -> list[tuple[int, str, str]]:
    """``(offset, raw value, name)`` for every string value that could be a connection string."""
    lower = file.lower()
    out: list[tuple[int, str, str]] = []
    if lower.endswith(".cs"):
        literals, _ = lex_csharp(src)
        return [(lit.start, lit.text, "") for lit in literals if len(lit.text) >= 12]
    if lower.endswith(".json"):
        tokens = list(_JSON_STRING.finditer(src))
        for k, m in enumerate(tokens):
            after = src[m.end() : m.end() + 3].lstrip()
            if after.startswith(":"):
                continue  # a key
            name = tokens[k - 1].group(1) if k > 0 else ""
            out.append(
                (m.start(1), m.group(1).replace('\\"', '"').replace("\\\\", "\\"), name)
            )
        return out
    for el in _XML_ELEMENT.finditer(src):
        attrs = list(_XML_ATTR.finditer(el.group(1)))
        name = next(
            (a.group(2) for a in attrs if a.group(1).lower() in ("name", "key")), ""
        )
        for a in attrs:
            if len(a.group(2)) >= 12:
                out.append((el.start(1) + a.start(2), a.group(2), name))
    return out


def find_connection_strings(src: str, file: str) -> list[ConnectionString]:
    index = LineIndex(src)
    found: list[ConnectionString] = []
    seen: set[str] = set()
    for offset, raw, name in _connection_candidates(src, file):
        window = src[max(0, offset - 600) : offset + len(raw) + 300]
        cs = _parse_connection(raw, file, index.line_of(offset), name, window)
        if cs and cs.redacted not in seen:
            seen.add(cs.redacted)
            found.append(cs)
    return found


# --- Packages ----------------------------------------------------------------------------------

_PKG_REF = re.compile(
    r'<PackageReference\s+Include\s*=\s*"([^"]+)"(?:\s+Version\s*=\s*"([^"]*)")?', re.I
)
_PKG_CONFIG = re.compile(
    r'<package\s+id\s*=\s*"([^"]+)"\s+version\s*=\s*"([^"]*)"', re.I
)
_ASM_REF = re.compile(
    r'<Reference\s+Include\s*=\s*"([^",]+)(?:,\s*Version=([\d.]+))?', re.I
)
_TFV = re.compile(
    r"<TargetFrameworkVersion>\s*([^<\s]+)\s*</TargetFrameworkVersion>", re.I
)
_TF = re.compile(r"<TargetFrameworks?>\s*([^<]+?)\s*</TargetFrameworks?>", re.I)


def find_packages(src: str, file: str) -> tuple[list[Package], list[str]]:
    packages: list[Package] = []
    for regex, source in (
        (_PKG_REF, "package"),
        (_PKG_CONFIG, "package"),
        (_ASM_REF, "assembly_reference"),
    ):
        for m in regex.finditer(src):
            name, version = m.group(1).strip(), (m.group(2) or "").strip()
            replacement = PACKAGE_REPLACEMENTS.get(name.lower())
            if replacement and not any(
                p.name.lower() == name.lower() for p in packages
            ):
                packages.append(Package(name, version, file, replacement, source))
    frameworks = [m.group(1) for m in _TFV.finditer(src)]
    for m in _TF.finditer(src):
        frameworks.extend(f.strip() for f in m.group(1).split(";") if f.strip())
    return packages, frameworks


# --- File scan ---------------------------------------------------------------------------------

_SP_COMMAND = re.compile(r"\bCommandType\s*\.\s*StoredProcedure\b")
_EXEC_NAME = re.compile(rf"^\s*EXEC(?:UTE)?\s+(?:@\w+\s*=\s*)?(?P<q>{QNAME})", re.I)
_BARE_NAME = re.compile(rf"^\s*(?P<q>{QNAME})\s*$")


def scan_csharp(src: str, file: str) -> AppScan:
    result = AppScan(file=file)
    literals, code = lex_csharp(src)
    literals = merge_concatenations(src, literals)
    index = LineIndex(src)

    for tech, pattern in TECHNOLOGIES.items():
        if pattern.search(code):
            result.technologies.append(tech)

    for rule in APP_RULES:
        if rule.pattern is None:
            continue
        lines = _rule_lines(rule.pattern, code, index)
        if lines:
            result.findings.append(
                AppFinding(rule.id, file, lines, _snippet(src, lines[0]))
            )

    sp_lines = {index.line_of(m.start()) for m in _SP_COMMAND.finditer(code)}

    for lit in literals:
        text = lit.text
        exec_m = _EXEC_NAME.match(text)
        if exec_m and not exec_m.group("q").startswith("@"):
            result.proc_candidates.append(
                ProcCandidate(file, lit.line, split_qname(exec_m.group("q")), "exec")
            )
        else:
            bare = _BARE_NAME.match(text)
            if bare and not text.strip().startswith(("@", "#")) and len(text) <= 300:
                near = any(abs(lit.line - ln) <= 5 for ln in sp_lines)
                result.proc_candidates.append(
                    ProcCandidate(
                        file,
                        lit.line,
                        split_qname(bare.group("q")),
                        "command_type" if near else "literal",
                    )
                )
        if looks_like_sql(text):
            result.embedded_sql.append(
                EmbeddedSql(
                    file=file,
                    line=lit.line,
                    text=text,
                    interpolated=lit.interpolated,
                    concatenated=lit.concatenated,
                    dynamic=lit.dynamic,
                    verbatim=lit.verbatim,
                )
            )

    result.connection_strings = [cs for cs in find_connection_strings(src, file)]
    return result


def scan_config(src: str, file: str) -> AppScan:
    """Scan .config / .csproj / packages.config / .edmx / appsettings.json."""
    result = AppScan(file=file)
    index = LineIndex(src)
    result.connection_strings = find_connection_strings(src, file)
    result.packages, result.target_frameworks = find_packages(src, file)
    for rule_id in ("APP_EDMX_SQLSERVER", "APP_ENTLIB"):
        rule = APP_RULES_BY_ID[rule_id]
        lines = _rule_lines(rule.pattern, src, index) if rule.pattern else []
        if lines:
            result.findings.append(
                AppFinding(rule.id, file, lines, _snippet(src, lines[0]))
            )
    if "System.Data.SqlClient" in src and file.lower().endswith(".config"):
        result.technologies.append("ADO.NET (System.Data.SqlClient)")
    return result


def connection_findings(cs: ConnectionString) -> list[AppFinding]:
    findings = [AppFinding("APP_CONN_PROVIDER", cs.file, [cs.line], cs.redacted[:200])]
    if cs.windows_auth:
        findings.append(
            AppFinding("APP_CONN_WINDOWS_AUTH", cs.file, [cs.line], cs.redacted[:200])
        )
    if cs.mars:
        findings.append(
            AppFinding("APP_CONN_MARS", cs.file, [cs.line], cs.redacted[:200])
        )
    if cs.password_present:
        findings.append(
            AppFinding("APP_CONN_PASSWORD", cs.file, [cs.line], cs.redacted[:200])
        )
    return findings
