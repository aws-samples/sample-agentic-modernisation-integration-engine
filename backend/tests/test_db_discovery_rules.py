"""MySQL compatibility rule catalogue."""

from __future__ import annotations

import pytest

from services.db_discovery.mysql_compat_rules import (
    RULES,
    RULES_BY_ID,
    evaluate,
    hits_to_lines,
)
from services.db_discovery.tsql_scanner import mask_tsql


def _ids(sql: str, object_type: str = "procedure") -> set[str]:
    return {h.rule.id for h in evaluate(mask_tsql(sql), object_type)}


def test_catalogue_is_well_formed():
    assert len(RULES) == len(RULES_BY_ID), "rule IDs must be unique"
    for r in RULES:
        assert r.severity in {"blocker", "high", "medium", "low", "info"}
        assert r.recommendation and r.title and r.category


@pytest.mark.parametrize(
    ("sql", "rule_id"),
    [
        (
            "MERGE Sales.T AS t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = 1;",
            "SYN_MERGE",
        ),
        ("INSERT INTO t (a) OUTPUT inserted.id VALUES (1)", "SYN_OUTPUT_CLAUSE"),
        (
            "WITH c AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM c WHERE n < 5) SELECT * FROM c",
            "SYN_RECURSIVE_CTE",
        ),
        ("SELECT * FROM c OPTION (MAXRECURSION 0)", "SYN_MAXRECURSION"),
        ("CREATE TABLE t (id int IDENTITY(1,1))", "SYN_IDENTITY"),
        ("SET IDENTITY_INSERT dbo.t ON", "SYN_IDENTITY_INSERT"),
        ("SELECT SCOPE_IDENTITY()", "SYN_SCOPE_IDENTITY"),
        ("DECLARE @x int = NEXT VALUE FOR Sequences.S", "SYN_NEXT_VALUE_FOR"),
        ("DECLARE @d datetimeoffset(7)", "DT_DATETIMEOFFSET"),
        ("CREATE TABLE t (g uniqueidentifier NOT NULL)", "DT_UNIQUEIDENTIFIER"),
        ("CREATE TABLE t (h hierarchyid NULL)", "DT_HIERARCHYID"),
        ("CREATE TABLE t (v sql_variant NULL)", "DT_SQL_VARIANT"),
        ("DECLARE @x XML", "DT_XML"),
        ("CREATE TABLE t (rv rowversion NOT NULL)", "DT_ROWVERSION"),
        ("CREATE TABLE t (m money NULL)", "DT_MONEY"),
        ("DECLARE @s nvarchar(max)", "DT_MAX_TYPES"),
        ("CREATE TABLE t (d datetime2(7) NOT NULL)", "DT_DATETIME2_7"),
        ("SELECT a COLLATE Latin1_General_CS_AS FROM t", "SYN_COLLATE"),
        ("SELECT * FROM OtherDb.dbo.Customers", "XDB_THREE_PART_NAME"),
        ("SELECT * FROM LinkedSrv.Db.dbo.Customers", "XDB_LINKED_SERVER"),
        ("SELECT * FROM OPENQUERY(Srv, 'select 1')", "XDB_LINKED_SERVER"),
        ("CREATE ASSEMBLY a FROM 0x00", "FEAT_CLR"),
        ("SEND ON CONVERSATION @h MESSAGE TYPE m", "FEAT_SERVICE_BROKER"),
        ("BEGIN TRY SELECT 1 END TRY BEGIN CATCH THROW; END CATCH", "PROC_TRY_CATCH"),
        ("BEGIN CATCH THROW; END CATCH", "PROC_THROW"),
        ("RAISERROR('x', 16, 1)", "PROC_RAISERROR"),
        ("SELECT ERROR_MESSAGE()", "PROC_ERROR_FUNCTIONS"),
        ("SET XACT_ABORT ON", "TX_XACT_ABORT"),
        ("SAVE TRANSACTION sp1", "TX_SAVEPOINT"),
        ("CREATE TABLE #work (id int)", "PROC_TEMP_TABLE"),
        ("SELECT * INTO ##shared FROM t", "PROC_GLOBAL_TEMP_TABLE"),
        ("DECLARE @t TABLE (id int)", "PROC_TABLE_VARIABLE"),
        ("CREATE PROC p @rows dbo.List READONLY AS SELECT 1", "PROC_TVP"),
        ("EXEC sp_executesql @sql", "PROC_DYNAMIC_SQL"),
        ("EXECUTE (@sql)", "PROC_DYNAMIC_SQL"),
        ("DECLARE c CURSOR FAST_FORWARD FOR SELECT 1", "PROC_CURSOR"),
        ("SELECT TOP 10 * FROM t", "SYN_TOP"),
        ("SELECT TOP (5) PERCENT * FROM t", "SYN_TOP_PERCENT_TIES"),
        (
            "SELECT * FROM t ORDER BY a OFFSET 10 ROWS FETCH NEXT 5 ROWS ONLY",
            "SYN_OFFSET_FETCH",
        ),
        ("SELECT * FROM t WITH (NOLOCK)", "SYN_NOLOCK"),
        ("SELECT * FROM t WITH (UPDLOCK, HOLDLOCK)", "SYN_TABLE_HINT"),
        ("SELECT * FROM t OPTION (RECOMPILE)", "SYN_QUERY_HINT"),
        ("SELECT * FROM a CROSS APPLY dbo.f(a.id)", "SYN_APPLY"),
        ("SELECT * FROM t PIVOT (SUM(x) FOR y IN ([a])) p", "SYN_PIVOT"),
        ("SELECT * FROM OPENJSON(@j)", "FN_JSON_OPENJSON"),
        ("SELECT a FROM t FOR JSON PATH", "FN_JSON_FOR_JSON"),
        ("SELECT JSON_VALUE(c, '$.a') FROM t", "FN_JSON_SCALAR"),
        ("SELECT a FROM t FOR XML PATH('')", "FN_XML_FOR_XML"),
        ("SELECT x.value('(/a)[1]', 'int') FROM t", "FN_XML_METHODS"),
        ("CREATE TABLE t (a int) WITH (SYSTEM_VERSIONING = ON)", "FEAT_TEMPORAL"),
        ("SELECT * FROM t FOR SYSTEM_TIME AS OF @d", "FEAT_TEMPORAL"),
        (
            "CREATE TABLE t (a int) WITH (MEMORY_OPTIMIZED = ON)",
            "FEAT_MEMORY_OPTIMIZED",
        ),
        ("CREATE NONCLUSTERED COLUMNSTORE INDEX i ON t (a)", "FEAT_COLUMNSTORE"),
        (
            "CREATE SECURITY POLICY p ADD FILTER PREDICATE f(a) ON t",
            "SEC_ROW_LEVEL_SECURITY",
        ),
        (
            "CREATE TABLE t (a nvarchar(10) MASKED WITH (FUNCTION = 'default()'))",
            "SEC_DYNAMIC_MASKING",
        ),
        ("CREATE PROC p WITH EXECUTE AS OWNER AS SELECT 1", "SEC_EXECUTE_AS"),
        ("CREATE PROC p WITH ENCRYPTION AS SELECT 1", "SEC_WITH_ENCRYPTION"),
        ("EXEC msdb.dbo.sp_add_job @job_name = N'x'", "SCHED_SQL_AGENT"),
        ("EXEC xp_cmdshell 'dir'", "PLAT_XP_CMDSHELL"),
        ("EXEC sp_OACreate 'x', @o OUT", "PLAT_OLE_AUTOMATION"),
        ("EXEC msdb.dbo.sp_send_dbmail @recipients = 'a'", "PLAT_DATABASE_MAIL"),
        ("SELECT * FROM sys.objects", "PLAT_SYSTEM_CATALOG"),
        ("SELECT * FROM t WHERE CONTAINS(a, 'x')", "FN_FULLTEXT_PREDICATES"),
        ("CREATE SYNONYM s FOR dbo.t", "FEAT_SYNONYM"),
        ("SELECT ISNULL(a, 0), GETDATE() FROM t", "FN_ISNULL"),
        ("SELECT ISNULL(a, 0), GETDATE() FROM t", "FN_GETDATE"),
        ("SELECT CONVERT(varchar(10), d, 112) FROM t", "FN_CONVERT"),
        ("SELECT STRING_AGG(a, ',') FROM t", "FN_STRING_AGG"),
        ("SELECT NEWID()", "FN_NEWID"),
        ("IF @@ROWCOUNT = 0 RETURN", "FN_ROWCOUNT"),
        ("BULK INSERT t FROM 'c:\\x.csv'", "FEAT_BULK_INSERT"),
    ],
)
def test_rule_fires(sql: str, rule_id: str):
    assert rule_id in _ids(sql)


@pytest.mark.parametrize(
    ("sql", "rule_id"),
    [
        ("-- MERGE Sales.T USING s ON 1 = 1\nSELECT 1", "SYN_MERGE"),
        ("SELECT 'MERGE INTO t USING s' AS txt", "SYN_MERGE"),
        ("SELECT * FROM t OPTION (MERGE JOIN)", "SYN_MERGE"),
        ("SELECT Money, Text FROM t", "DT_MONEY"),
        ("SELECT * FROM Sales.Orders", "XDB_THREE_PART_NAME"),
        ("SELECT o.OrderID FROM Sales.Orders o", "XDB_THREE_PART_NAME"),
        ("SELECT @@IDENTITY", "SYN_IDENTITY"),
        ("SELECT CURRENT_TIMESTAMP", "DT_ROWVERSION"),
        ("SELECT s.StockItemID FROM Warehouse.StockItems s", "FN_SPATIAL_METHODS"),
        ("WITH c AS (SELECT 1 AS n) SELECT * FROM c", "SYN_RECURSIVE_CTE"),
        ("SELECT a FROM t FOR XML PATH('')", "DT_XML"),
        ("SELECT * FROM msdb.dbo.sysjobs", "XDB_THREE_PART_NAME"),
        ("SELECT TRY_CONVERT(int, a) FROM t", "FN_CONVERT"),
    ],
)
def test_rule_does_not_fire(sql: str, rule_id: str):
    assert rule_id not in _ids(sql)


def test_applies_to_limits_rules():
    trigger_sql = (
        "CREATE TRIGGER t ON dbo.x AFTER INSERT, UPDATE AS SELECT * FROM inserted"
    )
    assert {"FEAT_TRIGGER_MULTI_EVENT", "FEAT_TRIGGER_PSEUDO_TABLES"} <= _ids(
        trigger_sql, "trigger"
    )
    assert "FEAT_TRIGGER_PSEUDO_TABLES" not in _ids(
        "SELECT * FROM inserted", "procedure"
    )
    assert "PROC_RETURN_VALUE" in _ids("CREATE PROC p AS RETURN 1", "procedure")
    assert "PROC_RETURN_VALUE" not in _ids(
        "CREATE FUNCTION f() RETURNS int AS BEGIN RETURN 1 END", "function"
    )


def test_hits_to_lines_offsets_by_base_line():
    sql = "SELECT 1\nSELECT TOP 1 a FROM t\nSELECT TOP 2 b FROM t"
    masked = mask_tsql(sql)
    lines = dict(
        (r.id, ln)
        for r, ln in hits_to_lines(evaluate(masked, "procedure"), masked, 100)
    )
    assert lines["SYN_TOP"] == [101, 102]
