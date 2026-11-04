"""T-SQL scanner: masking, batches, object inventory, columns, type mapping, references."""

from __future__ import annotations

from pathlib import Path

from services.db_discovery import tsql_scanner as ts


def _objects(sql: str, file: str = "x.sql") -> list[ts.DbObject]:
    masked = ts.mask_tsql(sql)
    found: list[ts.DbObject] = []
    for batch in ts.split_batches(sql, masked):
        found.extend(ts.find_objects(batch, file))
    return found


# --- masking -----------------------------------------------------------------------------------


def test_mask_preserves_length_and_lines():
    sql = "SELECT 1 -- MERGE here\n/* outer /* nested */ still comment */ SELECT 'it''s MERGE'\nGO\n"
    masked = ts.mask_tsql(sql)
    assert len(masked) == len(sql)
    assert masked.count("\n") == sql.count("\n")
    assert "MERGE" not in masked
    assert "still comment" not in masked
    assert "SELECT" in masked and "''" not in masked.replace("' ", "")


def test_mask_keeps_bracketed_identifiers_with_dashes():
    sql = "SELECT [a--b], [x]]y] FROM t"
    masked = ts.mask_tsql(sql)
    assert masked == sql


def test_go_inside_comment_or_string_does_not_split():
    sql = "SELECT 1\n/*\nGO\n*/\nSELECT 'x\nGO\n'\nGO\nSELECT 2\ngo 5\n"
    batches = ts.split_batches(sql, ts.mask_tsql(sql))
    assert len(batches) == 2
    assert "SELECT 2" in batches[1].original


def test_read_sql_file_utf16_and_bom(tmp_path: Path):
    p16 = tmp_path / "a.sql"
    p16.write_bytes("CREATE TABLE dbo.T (id int)\r\nGO\r\n".encode("utf-16"))
    pbom = tmp_path / "b.sql"
    pbom.write_bytes(b"\xef\xbb\xbfCREATE VIEW v AS SELECT 1 AS x")
    assert ts.read_sql_file(p16).startswith("CREATE TABLE")
    assert "\r" not in ts.read_sql_file(p16)
    assert ts.read_sql_file(pbom).startswith("CREATE VIEW")


# --- objects -----------------------------------------------------------------------------------


def test_procedure_runs_to_batch_end_and_ignores_temp_table():
    sql = """CREATE PROCEDURE [Sales].[GetThings] @Id int AS
BEGIN
  CREATE TABLE #t (id int);
  CREATE TABLE dbo.NotAnObject (id int);
  SELECT * FROM Sales.Orders;
END
GO
CREATE TABLE Sales.Orders (OrderID int NOT NULL)
"""
    objs = _objects(sql)
    assert [(o.type, o.qualified) for o in objs] == [
        ("procedure", "Sales.GetThings"),
        ("table", "Sales.Orders"),
    ]
    assert objs[0].subtype == "sql"


def test_unqualified_names_default_to_dbo_and_alter_module_counts():
    objs = _objects(
        "ALTER PROC GetX AS SELECT 1\nGO\nCREATE OR ALTER VIEW v1 AS SELECT 1 AS a\n"
    )
    assert [(o.type, o.qualified) for o in objs] == [
        ("procedure", "dbo.GetX"),
        ("view", "dbo.v1"),
    ]


def test_alter_table_is_not_an_object():
    assert _objects("ALTER TABLE dbo.T ADD c int") == []


def test_function_subtypes():
    sql = (
        "CREATE FUNCTION dbo.f1() RETURNS int AS BEGIN RETURN 1 END\nGO\n"
        "CREATE FUNCTION dbo.f2() RETURNS TABLE AS RETURN SELECT 1 AS a\nGO\n"
        "CREATE FUNCTION dbo.f3() RETURNS @r TABLE (a int) AS BEGIN RETURN END\nGO\n"
        "CREATE FUNCTION dbo.f4(@s nvarchar(10)) RETURNS int AS EXTERNAL NAME A.B.C\n"
    )
    assert [o.subtype for o in _objects(sql)] == [
        "scalar",
        "inline_tvf",
        "multi_statement_tvf",
        "clr",
    ]


def test_trigger_index_and_type_details():
    sql = (
        "CREATE TRIGGER Sales.trg ON Sales.Orders INSTEAD OF INSERT AS SELECT 1\nGO\n"
        "CREATE TRIGGER ddl ON DATABASE FOR CREATE_TABLE AS SELECT 1\nGO\n"
        "CREATE UNIQUE NONCLUSTERED INDEX IX_A ON Sales.Orders (OrderID) INCLUDE (X) WHERE X > 1;\n"
        "CREATE TYPE Website.OrderList AS TABLE (OrderReference int, CustomerID int)\nGO\n"
        "CREATE TYPE dbo.Flag FROM bit NOT NULL\n"
    )
    objs = _objects(sql)
    trg, ddl, idx, tvp, alias = objs
    assert (trg.type, trg.subtype, trg.parent) == (
        "trigger",
        "instead_of",
        "Sales.Orders",
    )
    assert ddl.subtype == "ddl" and ddl.parent == ""
    assert (
        idx.type == "index"
        and idx.parent == "Sales.Orders"
        and idx.name == "Orders.IX_A"
    )
    assert tvp.subtype == "table_type" and [c.name for c in tvp.columns] == [
        "OrderReference",
        "CustomerID",
    ]
    assert alias.subtype == "alias" and alias.alias_base_type == "bit"


def test_database_level_objects_and_agent_jobs():
    sql = (
        "CREATE SCHEMA [Website] AUTHORIZATION dbo\nGO\n"
        "CREATE ROLE [External Sales]\nGO\n"
        "CREATE SEQUENCE [Sequences].[CityID] AS INT START WITH 1\nGO\n"
        "ALTER DATABASE [$(DatabaseName)] ADD FILEGROUP [WWI_InMemory] CONTAINS MEMORY_OPTIMIZED_DATA;\nGO\n"
        "EXEC msdb.dbo.sp_add_job @job_name = N'Nightly ''load''', @enabled = 1\nGO\n"
    )
    objs = {(o.type, o.qualified, o.subtype) for o in _objects(sql)}
    assert ("schema", "Website", "") in objs
    assert ("security_principal", "External Sales", "role") in objs
    assert ("sequence", "Sequences.CityID", "") in objs
    assert ("filegroup", "WWI_InMemory", "memory_optimized") in objs
    assert ("sql_agent_job", "Nightly 'load'", "msdb") in objs


def test_table_subtypes():
    sql = (
        "CREATE TABLE a.T1 (id int, ValidFrom datetime2 GENERATED ALWAYS AS ROW START, "
        "ValidTo datetime2 GENERATED ALWAYS AS ROW END, PERIOD FOR SYSTEM_TIME (ValidFrom, ValidTo)) "
        "WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = a.T1_Archive), MEMORY_OPTIMIZED = ON)\n"
    )
    (t,) = _objects(sql)
    assert t.subtype == "temporal,memory_optimized"
    assert [c.name for c in t.columns] == ["id", "ValidFrom", "ValidTo"]
    assert t.columns[1].generated_always


# --- columns and type mapping ------------------------------------------------------------------


def test_table_columns():
    sql = """CREATE TABLE [Application].[People] (
    [PersonID] INT CONSTRAINT [DF_P] DEFAULT (NEXT VALUE FOR [Sequences].[PersonID]) NOT NULL,
    [FullName] NVARCHAR (50) NOT NULL,
    [SearchName] AS (concat([PreferredName], N' ', [FullName])) PERSISTED NOT NULL,
    [Photo] VARBINARY (MAX) NULL,
    [Amount] DECIMAL(18, 2) NULL,
    [Id2] BIGINT IDENTITY(1,1) NOT NULL,
    [Loc] [sys].[geography] NULL,
    CONSTRAINT [PK_People] PRIMARY KEY CLUSTERED ([PersonID] ASC),
    CONSTRAINT [FK_x] FOREIGN KEY ([PersonID]) REFERENCES [Application].[Other] ([Id])
)"""
    (t,) = _objects(sql)
    cols = {c.name: c for c in t.columns}
    assert list(cols) == [
        "PersonID",
        "FullName",
        "SearchName",
        "Photo",
        "Amount",
        "Id2",
        "Loc",
    ]
    assert cols["PersonID"].default_sequence and not cols["PersonID"].nullable
    assert cols["FullName"].sql_type == "nvarchar(50)"
    assert cols["SearchName"].computed and cols["SearchName"].persisted
    assert cols["Photo"].sql_type == "varbinary(max)" and cols["Photo"].nullable
    assert cols["Amount"].sql_type == "decimal(18,2)"
    assert cols["Id2"].identity
    assert cols["Loc"].base_type == "geography"
    assert ["Application", "Other"] in t.raw_references


def test_type_mapping():
    m = ts.map_type_to_mysql
    assert m("nvarchar(50)")[0] == "VARCHAR(50)"
    assert m("nvarchar(max)")[0] == "LONGTEXT"
    assert m("datetime2(7)") == ("DATETIME(6)", "precision 7 → 6")
    assert m("datetime2(3)") == ("DATETIME(3)", "")
    assert (
        m("datetimeoffset(7)")[0] == "DATETIME(6)"
        and "offset" in m("datetimeoffset(7)")[1]
    )
    assert m("tinyint")[0] == "TINYINT UNSIGNED"
    assert m("bit")[0] == "TINYINT(1)"
    assert m("money")[0] == "DECIMAL(19,4)"
    assert m("decimal(18,2)")[0] == "DECIMAL(18,2)"
    assert m("float")[0] == "DOUBLE" and m("float(24)")[0] == "FLOAT"
    assert m("uniqueidentifier")[0] == "CHAR(36)"
    assert m("sql_variant")[0] == "—"
    assert m("varbinary(max)")[0] == "LONGBLOB"
    assert m("my_custom_type")[0] == "?"


def test_references_skip_variables_and_temp_tables():
    masked = ts.mask_tsql(
        "SELECT * FROM Sales.Orders o JOIN [Application].[People] p ON 1=1; "
        "INSERT INTO #t SELECT 1; UPDATE @tv SET a = 1; EXEC Website.DoIt; "
        "SELECT * FROM OtherDb.dbo.Remote"
    )
    refs = ts.extract_references(masked)
    assert ["Sales", "Orders"] in refs
    assert ["Application", "People"] in refs
    assert ["Website", "DoIt"] in refs
    assert ["OtherDb", "dbo", "Remote"] in refs
    assert not any(r[0].startswith(("#", "@")) for r in refs)


def test_sqlglot_parse_flag():
    ok = ts.Batch(0, "SELECT a FROM Sales.Orders", "SELECT a FROM Sales.Orders")
    ts.sqlglot_parse(ok)
    assert ok.ast_parsed and ("Sales", "Orders") in ok.ast_tables
    bad = ts.Batch(0, "EXEC sp_addextendedproperty @name = N'x'", "")
    ts.sqlglot_parse(bad)
    assert not bad.ast_parsed
