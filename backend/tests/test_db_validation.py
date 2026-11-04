"""Validation harness: normalisation, table/procedure comparison, routes, and a sandbox round trip."""

from __future__ import annotations

import datetime as dt
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config import settings
from main import app
from services.db_validation import harness
from services.db_validation.normalize import (
    TableChecksum,
    merge_json_chunks,
    normalize_value,
)
from state import app_state
from utils.progress_tracker import ProgressTracker
from utils.storage_manager import StorageManager

UTC = dt.timezone.utc


# --- normalisation -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (True, 1),
        (Decimal("12.5000"), Decimal("12.5")),
        (Decimal("0.00"), 0),
        (
            uuid.UUID("6F9619FF-8B86-D011-B42D-00C04FC964FF"),
            "6f9619ff-8b86-d011-b42d-00c04fc964ff",
        ),
        (
            dt.datetime(2026, 1, 2, 3, 4, 5, 123456),
            dt.datetime(2026, 1, 2, 3, 4, 5, 123456),
        ),
        (
            dt.datetime(2026, 1, 2, 13, 0, tzinfo=dt.timezone(dt.timedelta(hours=10))),
            dt.datetime(2026, 1, 2, 3, 0),
        ),
        (dt.time(1, 2, 3), dt.timedelta(hours=1, minutes=2, seconds=3)),
        ("abc  ", "abc"),
        ('{"b": 1, "a": [1, 2]}', '{"a":[1,2],"b":1}'),
        (b"\x01\xff", bytearray(b"\x01\xff")),
    ],
)
def test_equal_values_normalise_equally(a, b):
    assert normalize_value(a) == normalize_value(b)


def test_distinct_values_stay_distinct():
    assert normalize_value(None) != normalize_value("")
    assert normalize_value(Decimal("1.5")) != normalize_value(Decimal("1.50001"))


def test_checksum_is_order_independent_but_counts_duplicates():
    a, b = TableChecksum(), TableChecksum()
    for row in [(1, "x"), (2, "y")]:
        a.add(row)
    for row in [(2, "y"), (1, "x")]:
        b.add(row)
    assert a.hexdigest() == b.hexdigest()
    c = TableChecksum()
    for row in [(1, "x"), (1, "x"), (2, "y"), (2, "y")]:
        c.add(row)
    assert c.hexdigest() != TableChecksum().hexdigest()
    d = TableChecksum()
    for row in [(1, "x"), (2, "y"), (1, "x"), (2, "y")]:
        d.add(row)
    assert c.hexdigest() == d.hexdigest()


# --- planning and comparison with fake connections ---------------------------------------------


def _col(name, sql_type, mysql_type, computed=False):
    return {
        "name": name,
        "sql_type": sql_type,
        "mysql_type": mysql_type,
        "computed": computed,
        "nullable": True,
        "identity": False,
    }


REPORT = {
    "discovery_id": "dbupload_20261103_000000",
    "objects": [
        {
            "id": "table:sales.t",
            "schema": "Sales",
            "name": "T",
            "type": "table",
            "primary_key": ["Id"],
            "columns": [
                _col("Id", "int", "INT"),
                _col("Name", "nvarchar(50)", "VARCHAR(50)"),
                _col("Loc", "geography", "GEOMETRY SRID 4326"),
                _col("Calc", "computed", "GENERATED ALWAYS AS (…)", computed=True),
            ],
        },
        {
            "id": "procedure:sales.p",
            "schema": "Sales",
            "name": "p",
            "type": "procedure",
            "columns": [],
        },
    ],
}


def test_plan_excludes_spatial_and_computed_columns():
    (plan,) = harness.plan_tables(REPORT)
    assert plan.columns == ["Id", "Name"]
    assert {e["column"] for e in plan.excluded} == {"Loc", "Calc"}
    assert (
        harness._source_select(plan, top=10)
        == "SELECT TOP (10) [Id], [Name] FROM [Sales].[T]"
    )
    assert harness._target_select(plan) == "SELECT `Id`, `Name` FROM `Sales`.`T`"


class FakeCursor:
    def __init__(self, data):
        self.data = data  # sql -> list of result sets (each a list of rows)
        self.sets: list = []
        self.description = None
        self.executed: list = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        for key, value in self.data.items():
            if sql.startswith(key):
                if isinstance(value, Exception):
                    raise value
                self.sets = [list(s) for s in value]
                break
        else:
            self.sets = []
        self.description = [("c",)] if self.sets else None
        self._pos = 0

    def fetchmany(self, n):
        rows = self.sets[0][self._pos : self._pos + n] if self.sets else []
        self._pos += n
        return rows

    def fetchall(self):
        return self.sets[0] if self.sets else []

    def nextset(self):
        if len(self.sets) > 1:
            self.sets.pop(0)
            self.description = [("c",)]
            return True
        return False


class FakeConn:
    def __init__(self, data):
        self.data = data
        self.cursors: list[FakeCursor] = []

    def cursor(self):
        c = FakeCursor(self.data)
        self.cursors.append(c)
        return c

    def close(self):
        pass


def test_compare_table_match_mismatch_missing():
    (plan,) = harness.plan_tables(REPORT)
    src = FakeConn({"SELECT [Id]": [[(1, "a"), (2, "b ")]]})
    same = FakeConn({"SELECT `Id`": [[(2, "b"), (1, "a")]]})
    assert harness.compare_table(src, same, plan)["status"] == "match"
    diff = FakeConn({"SELECT `Id`": [[(1, "a")]]})
    r = harness.compare_table(src, diff, plan)
    assert r["status"] == "mismatch" and r["source_rows"] == 2 and r["target_rows"] == 1
    missing = FakeConn(
        {"SELECT `Id`": Exception(1146, "Table 'Sales.T' doesn't exist")}
    )
    assert harness.compare_table(src, missing, plan)["status"] == "missing_in_target"


def test_compare_procedure_sql_and_diff():
    test = {
        "procedure": "Sales.p",
        "params": [{"name": "Id", "value": 3}],
        "ordered": True,
    }
    src = FakeConn({"EXEC": [[(1, Decimal("2.50"))], [("done",)]]})
    tgt = FakeConn({"CALL": [[(1, Decimal("2.5"))], [("done",)]]})
    r = harness.compare_procedure(src, tgt, test)
    assert r["status"] == "match", r
    assert src.cursors[0].executed[0] == ("EXEC [Sales].[p] @Id = %s", (3,))
    assert tgt.cursors[0].executed[0] == ("CALL `Sales`.`p`(%s)", (3,))
    bad = harness.compare_procedure(
        src, FakeConn({"CALL": [[(1, Decimal("9"))]]}), test
    )
    assert bad["status"] == "mismatch"
    assert any("result set count" in d for d in bad["details"]) and any(
        "row 0" in d for d in bad["details"]
    )


@pytest.mark.parametrize(
    "test",
    [
        {"procedure": "p", "params": []},
        {"procedure": "Sales.p; DROP", "params": []},
        {"procedure": "Sales.p", "params": [{"name": "x; --"}]},
        {
            "procedure": "Sales.p",
            "params": [],
            "target_call": "CALL a.b(); DROP TABLE x",
        },
    ],
)
def test_procedure_test_validation_rejects_injection(test):
    assert harness.validate_procedure_test(test)


# --- routes ------------------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    app_state.progress_tracker = ProgressTracker()
    app_state.db_discovery_storage = StorageManager(base_path=str(tmp_path / "db"))
    app_state.db_validation_storage = StorageManager(base_path=str(tmp_path / "val"))
    app_state.db_discovery_storage.save(REPORT["discovery_id"], REPORT)
    monkeypatch.setattr(settings, "VALIDATION_SQLSERVER_HOST", "")
    monkeypatch.setattr(settings, "VALIDATION_ALLOW_COPY", False)
    return TestClient(app)


def test_runs_require_configured_source_and_copy_is_gated(
    client: TestClient, monkeypatch
):
    res = client.post(
        "/api/db-validation/runs", json={"discovery_id": REPORT["discovery_id"]}
    )
    assert res.status_code == 400 and "not configured" in res.json()["detail"]
    copy = client.post(
        "/api/db-validation/copy", json={"discovery_id": REPORT["discovery_id"]}
    )
    assert copy.status_code == 403
    status = client.get("/api/db-validation/status").json()
    assert status["source"]["configured"] is False and status["copy_enabled"] is False
    assert "password" not in status["target"] and "password" not in status["source"]
    if settings.MYSQL_SANDBOX_PASSWORD:
        assert settings.MYSQL_SANDBOX_PASSWORD not in str(status)


def test_validation_run_lifecycle_with_fake_connections(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(settings, "VALIDATION_SQLSERVER_HOST", "sql.example")
    monkeypatch.setattr(settings, "MYSQL_SANDBOX_HOST", "mysql")
    from services.db_validation import connections

    monkeypatch.setattr(
        connections,
        "connect_source",
        lambda: FakeConn({"SELECT [Id]": [[(1, "a")]], "EXEC": [[(1,)]]}),
    )
    monkeypatch.setattr(
        connections,
        "connect_target",
        lambda: FakeConn({"SELECT `Id`": [[(1, "a")]], "CALL": [[(1,)]]}),
    )

    bad = client.post(
        "/api/db-validation/runs",
        json={"discovery_id": REPORT["discovery_id"], "tables": ["table:nope"]},
    )
    assert bad.status_code == 400
    res = client.post(
        "/api/db-validation/runs",
        json={
            "discovery_id": REPORT["discovery_id"],
            "procedure_tests": [{"procedure": "Sales.p", "params": []}],
        },
    )
    assert res.status_code == 200, res.text
    run = client.get(f"/api/db-validation/runs/{res.json()['run_id']}").json()
    assert run["status"] == "completed", run
    assert run["result"]["tables"][0]["status"] == "match"
    assert run["result"]["procedures"][0]["status"] == "match"
    assert run["result"]["summary"] == {"match": 2}
    listing = client.get("/api/db-validation/runs").json()["runs"]
    assert listing[0]["run_id"] == run["run_id"] and listing[0]["summary"] == {
        "match": 2
    }
    assert client.get("/api/db-validation/runs/val_19990101_000000").status_code == 404


# --- real sandbox round trip (skipped when the sandbox is not reachable) -----------------------


def _sandbox_reachable() -> bool:
    if not os.environ.get("MYSQL_SANDBOX_HOST"):
        return False
    from services.db_conversion import mysql_sandbox

    return mysql_sandbox.ping().get("reachable", False)


@pytest.mark.skipif(not _sandbox_reachable(), reason="MySQL sandbox not reachable")
def test_copy_then_checksum_round_trip_through_mysql():
    from services.db_conversion import mysql_sandbox

    report = {
        "discovery_id": "dbupload_roundtrip",
        "objects": [
            {
                "id": "table:rt.items",
                "schema": "rt",
                "name": "Items",
                "type": "table",
                "subtype": "standard",
                "file": "x.sql",
                "line": 1,
                "primary_key": ["Id"],
                "columns": [
                    {**_col("Id", "int", "INT"), "nullable": False},
                    _col("Guid", "uniqueidentifier", "CHAR(36)"),
                    _col("Price", "decimal(18,2)", "DECIMAL(18,2)"),
                    _col("Active", "bit", "TINYINT(1)"),
                    _col("At", "datetime2(7)", "DATETIME(6)"),
                    _col("Offset", "datetimeoffset(7)", "DATETIME(6)"),
                    _col("Name", "nvarchar(20)", "VARCHAR(20)"),
                    _col("Blob", "varbinary(max)", "LONGBLOB"),
                ],
            }
        ],
    }
    mysql_sandbox.load_schema(report, force=True)
    rows = [
        (
            1,
            uuid.uuid4(),
            Decimal("19.90"),
            True,
            dt.datetime(2026, 1, 1, 8, 30, 0, 123456),
            dt.datetime(2026, 1, 1, 18, 0, tzinfo=dt.timezone(dt.timedelta(hours=10))),
            "Café ",
            b"\x00\x01",
        ),
        (2, None, None, False, None, None, None, None),
    ]
    src = lambda: FakeConn({"SELECT TOP": [rows], "SELECT [Id]": [rows]})  # noqa: E731
    copied = harness.copy_tables(report, None, 100, connect_source=src)
    assert (
        copied["tables"][0]["status"] == "copied" and copied["tables"][0]["rows"] == 2
    ), copied
    result = harness.run_validation(report, None, [], connect_source=src)
    assert result["tables"][0]["status"] == "match", result


def test_empty_target_is_not_loaded_and_missing_source_table_is_reported():
    (plan,) = harness.plan_tables(REPORT)
    src = FakeConn({"SELECT [Id]": [[(1, "a")]]})
    empty = FakeConn({"SELECT `Id`": [[]]})
    r = harness.compare_table(src, empty, plan)
    assert (
        r["status"] == "not_loaded" and r["source_rows"] == 1 and r["target_rows"] == 0
    )
    both_empty = harness.compare_table(FakeConn({"SELECT [Id]": [[]]}), empty, plan)
    assert both_empty["status"] == "match"
    gone = FakeConn({"SELECT [Id]": Exception(208, b"Invalid object name 'Sales.T'.")})
    r = harness.compare_table(gone, empty, plan)
    assert r["status"] == "missing_in_source" and "does not exist" in r["error"]


def test_merge_json_chunks_rejoins_for_json_rows():
    doc = '[{"a":1,"b":"' + "x" * 3000 + '"}]'
    rows = [(doc[:2033],), (doc[2033:],)]
    assert merge_json_chunks(rows) == [(doc,)]


def test_merge_json_chunks_leaves_other_sets_alone():
    plain = [("a",), ("b",)]
    assert merge_json_chunks(plain) == plain
    two_cols = [("{", 1), ("}", 2)]
    assert merge_json_chunks(two_cols) == two_cols
    single = [('{"a":1}',)]
    assert merge_json_chunks(single) == single
    broken = [('[{"a":',), ("1",)]
    assert merge_json_chunks(broken) == broken


def test_compare_procedure_matches_chunked_for_json():
    doc = '[{"Name":"Tailspin","Phone":"1","Fax":"2","Pad":"' + "y" * 2500 + '"}]'
    reordered = '[{"Fax":"2","Name":"Tailspin","Pad":"' + "y" * 2500 + '","Phone":"1"}]'

    class Cur:
        def __init__(self, sets):
            self.sets, self.i = sets, 0

        @property
        def description(self):
            return [("c",)]

        def execute(self, *_a):
            pass

        def fetchall(self):
            return self.sets[self.i]

        def nextset(self):
            self.i += 1
            return self.i < len(self.sets)

    class Conn:
        def __init__(self, sets):
            self.sets = sets

        def cursor(self):
            return Cur(self.sets)

    src = Conn([[(doc[:2033],), (doc[2033:],)]])
    tgt = Conn([[(reordered,)]])
    result = harness.compare_procedure(
        src, tgt, {"procedure": "Website.SearchForCustomers", "params": []}
    )
    assert result["status"] == "match", result["details"]
