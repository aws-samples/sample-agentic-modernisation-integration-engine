"""End-to-end discovery over the WWI legacy .NET fixture plus a small T-SQL project."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from services.db_discovery.discovery_service import (
    DatabaseDiscoveryService,
    effort_bucket,
    render_markdown,
)
from utils.progress_tracker import ProgressTracker
from utils.storage_manager import StorageManager

FIXTURE = Path(__file__).resolve().parents[2] / "samples" / "wwi-legacy-dotnet"

WWI_SUBSET = {
    "Website/Stored Procedures/SearchForCustomers.sql": """CREATE PROCEDURE Website.SearchForCustomers
@SearchText nvarchar(1000),
@MaximumRowsToReturn int
WITH EXECUTE AS OWNER
AS
BEGIN
    SELECT TOP(@MaximumRowsToReturn) c.CustomerID, c.CustomerName
    FROM Sales.Customers AS c
    WHERE c.CustomerName LIKE N'%' + @SearchText + N'%'
    FOR JSON AUTO, ROOT(N'Customers');
END;
""",
    "Website/Stored Procedures/ActivateWebsiteLogon.sql": """CREATE PROCEDURE Website.ActivateWebsiteLogon
@PersonID int, @LogonName nvarchar(50), @InitialPassword nvarchar(40)
WITH EXECUTE AS OWNER
AS
BEGIN
    UPDATE [Application].People SET IsPermittedToLogon = 1, LogonName = @LogonName
    WHERE PersonID = @PersonID;
    IF @@ROWCOUNT = 0
    BEGIN
        PRINT N'The PersonID must be valid';
        THROW 51000, N'Invalid PersonID', 1;
        RETURN -1;
    END;
END;
""",
    "Website/Stored Procedures/InsertCustomerOrders.sql": """CREATE PROCEDURE Website.InsertCustomerOrders
@Orders Website.OrderList READONLY,
@OrderLines Website.OrderLineList READONLY
AS
BEGIN
    SET XACT_ABORT ON;
    BEGIN TRY
        BEGIN TRAN;
        INSERT Sales.Orders (OrderID) SELECT NEXT VALUE FOR Sequences.OrderID FROM @Orders;
        COMMIT;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK;
        THROW;
    END CATCH;
END;
""",
    "Website/User Defined Types/OrderList.sql": "CREATE TYPE Website.OrderList AS TABLE (OrderReference int NULL, CustomerID int NULL)\n",
    "Sales/Tables/Customers.sql": """CREATE TABLE [Sales].[Customers] (
    [CustomerID] INT CONSTRAINT [DF_C] DEFAULT (NEXT VALUE FOR [Sequences].[CustomerID]) NOT NULL,
    [CustomerName] NVARCHAR (100) NOT NULL,
    [DeliveryLocation] [sys].[geography] NULL,
    [ValidFrom] DATETIME2 (7) GENERATED ALWAYS AS ROW START NOT NULL,
    [ValidTo] DATETIME2 (7) GENERATED ALWAYS AS ROW END NOT NULL,
    CONSTRAINT [PK_Sales_Customers] PRIMARY KEY CLUSTERED ([CustomerID] ASC),
    PERIOD FOR SYSTEM_TIME ([ValidFrom], [ValidTo])
)
WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE=[Sales].[Customers_Archive]));
GO
CREATE NONCLUSTERED INDEX [IX_Sales_Customers_Name] ON [Sales].[Customers]([CustomerName] ASC);
GO
EXECUTE sp_addextendedproperty @name = N'Description', @value = 'Main entity', @level0type = N'SCHEMA', @level0name = N'Sales', @level1type = N'TABLE', @level1name = N'Customers';
""",
    "Sequences/CustomerID.sql": "CREATE SEQUENCE [Sequences].[CustomerID] AS INT START WITH 1 INCREMENT BY 1;\n",
    "Integration/Stored Procedures/GetCityUpdates.sql": """CREATE PROCEDURE Integration.GetCityUpdates
@LastCutoff datetime2(7), @NewCutoff datetime2(7)
AS
BEGIN
    CREATE TABLE #CityChanges (CityID int);
    SELECT * FROM #CityChanges;
END;
""",
    "Website/Stored Procedures/RecordVehicleTemperature.sql": """CREATE PROCEDURE Website.RecordVehicleTemperature
@FullSensorDataArray nvarchar(1000)
AS
    SELECT * FROM OPENJSON(@FullSensorDataArray);
""",
    "PostDeploymentScripts/Seed.sql": "INSERT INTO Sales.Customers (CustomerName) VALUES (N'x');\nGO\n",
}


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for rel, text in WWI_SUBSET.items():
        p = root / "database" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    shutil.copytree(FIXTURE / "src", root / "src")
    shutil.copytree(FIXTURE / "database", root / "database", dirs_exist_ok=True)
    (root / "bin").mkdir()
    (root / "bin" / "ignored.sql").write_text(
        "CREATE TABLE dbo.ShouldNotAppear (a int)"
    )
    return root


@pytest.fixture()
def report(project: Path, tmp_path: Path) -> dict:
    storage = StorageManager(base_path=str(tmp_path / "store"))
    tracker = ProgressTracker()
    tracker.start("dbupload_test")
    result = DatabaseDiscoveryService(storage=storage, tracker=tracker).run(
        str(project), "dbupload_test", "upload"
    )
    assert storage.load("dbupload_test") == result
    return result


def _obj(report: dict, obj_id: str) -> dict:
    return next(o for o in report["objects"] if o["id"] == obj_id)


def test_inventory(report: dict):
    ids = {o["id"] for o in report["objects"]}
    assert "procedure:website.searchforcustomers" in ids
    assert "table:sales.customers" in ids
    assert "index:sales.customers.ix_sales_customers_name" in ids
    assert "sequence:sequences.customerid" in ids
    assert "user_defined_type:website.orderlist" in ids
    assert any(
        o["type"] == "sql_agent_job" and o["name"] == "WWI - Nightly Daily Process"
        for o in report["objects"]
    )
    assert any(o["type"] == "script" for o in report["objects"])
    assert not any("ShouldNotAppear" in o["name"] for o in report["objects"])


def test_table_columns_mapping_and_extended_property_attached(report: dict):
    t = _obj(report, "table:sales.customers")
    cols = {c["name"]: c for c in t["columns"]}
    assert cols["CustomerName"]["mysql_type"] == "VARCHAR(100)"
    assert cols["ValidFrom"]["mysql_type"] == "DATETIME(6)"
    rules = {f["rule_id"] for f in report["findings"] if f["object_id"] == t["id"]}
    assert {"FEAT_TEMPORAL", "SYN_NEXT_VALUE_FOR", "DT_SPATIAL"} <= rules
    assert t["effort"] in ("high", "very_high")
    # A batch that defines nothing joins the first object in its file, not a script object.
    assert "PLAT_EXTENDED_PROPERTY" in rules


def test_references_and_code_callers(report: dict):
    proc = _obj(report, "procedure:website.searchforcustomers")
    assert "table:sales.customers" in proc["references"]
    assert proc["called_from_code"] and proc["called_from_code"][0].endswith(
        "CustomerRepository.cs:29"
    )
    assert (
        "procedure:website.searchforcustomers"
        in _obj(report, "table:sales.customers")["referenced_by"]
    )

    calls = report["application"]["stored_procedure_calls"]
    resolved = {c["procedure"] for c in calls if c["resolved"]}
    assert {
        "Website.SearchForCustomers",
        "Website.ActivateWebsiteLogon",
        "Website.InsertCustomerOrders",
        "Integration.GetCityUpdates",
        "Website.RecordVehicleTemperature",
    } <= resolved
    unresolved = {c["procedure"] for c in calls if not c["resolved"]}
    assert unresolved == {"Sales.RecalculateCustomerBalances"}
    # A TVP TypeName literal names a type, not a procedure.
    assert not any(c["procedure"] == "Website.OrderList" for c in calls)


def test_application_findings(report: dict):
    app = report["application"]
    by_file_rule = {
        (f["file"].split("/")[-1], f["rule_id"])
        for f in report["findings"]
        if f["source"] == "application"
    }
    assert ("CustomerRepository.cs", "APP_SQL_INJECTION") in by_file_rule
    assert ("OrderRepository.cs", "SYN_MERGE") in by_file_rule
    assert ("OrderRepository.cs", "APP_TVP") in by_file_rule
    assert ("WwiContext.cs", "APP_EF6") in by_file_rule
    assert ("NightlyStockSnapshotJob.cs", "APP_SQLBULKCOPY") in by_file_rule
    assert ("Web.config", "APP_CONN_WINDOWS_AUTH") in by_file_rule
    assert ("Web.config", "APP_CONN_PASSWORD") in by_file_rule
    assert all(
        "NotARealPassword" not in c["connection_string"]
        for c in app["connection_strings"]
    )
    names = [p["name"] for p in app["packages"]]
    assert sorted(names) == ["Dapper", "EntityFramework", "Microsoft.SqlServer.Types"]
    assert (
        next(p for p in app["packages"] if p["name"] == "EntityFramework")["version"]
        == "6.4.4"
    )
    assert {"ADO.NET (System.Data.SqlClient)", "Dapper", "Entity Framework 6"} <= {
        t["name"] for t in app["technologies"]
    }


def test_summary_and_markdown(report: dict):
    s = report["summary"]
    assert s["files_scanned"]["sql"] >= len(WWI_SUBSET)
    assert s["finding_counts"]["high"] > 0
    assert sum(s["effort_counts"].values()) == len(report["objects"])
    assert s["application"]["unresolved_procedure_calls"] == 1
    assert s["logic_distribution"]["embedded_sql_statements"] > 0
    md = render_markdown(report)
    assert md.startswith("# Database Discovery")
    assert "Website.SearchForCustomers" in md or "SearchForCustomers" in md
    assert "NotARealPassword" not in md


def test_finding_ids_are_unique(report: dict):
    ids = [f["id"] for f in report["findings"]]
    assert len(ids) == len(set(ids)) and all(ids)


def test_effort_bucket():
    assert effort_bucket(0, False) == "low"
    assert effort_bucket(3, False) == "medium"
    assert effort_bucket(1, True) == "high"
    assert effort_bucket(25, False) == "very_high"
