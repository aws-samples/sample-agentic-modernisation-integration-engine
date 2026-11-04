"""Schema DDL, Maker/Checker loop and conversion routes (Bedrock and sandbox mocked)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from services.db_conversion import mysql_sandbox, schema_ddl
from services.db_conversion.conversion_agent import (
    ConversionAgent,
    extract_judgement,
    extract_sql,
    judgement_passes,
)
from services.db_conversion.conversion_store import ConversionStore
from state import app_state
from utils.progress_tracker import ProgressTracker
from utils.storage_manager import StorageManager

COL = {
    "nullable": True,
    "identity": False,
    "computed": False,
    "persisted": False,
    "has_default": False,
    "default_sequence": False,
    "generated_always": False,
    "mapping_note": "",
}


def _col(name, sql_type, mysql_type, **kw):
    return {**COL, "name": name, "sql_type": sql_type, "mysql_type": mysql_type, **kw}


def _report() -> dict:
    table = {
        "id": "table:sales.customers",
        "schema": "Sales",
        "name": "Customers",
        "type": "table",
        "subtype": "temporal",
        "file": "Sales/Customers.sql",
        "line": 1,
        "primary_key": ["CustomerID"],
        "columns": [
            _col("CustomerID", "int", "INT", nullable=False),
            _col("CustomerName", "nvarchar(100)", "VARCHAR(100)", nullable=False),
            _col("SearchName", "computed", "GENERATED ALWAYS AS (…)", computed=True),
            _col(
                "Seq", "bigint", "BIGINT AUTO_INCREMENT", identity=True, nullable=False
            ),
            _col("Odd", "sql_variant", "—"),
        ],
        "references": [],
    }
    keyless = {
        **table,
        "id": "table:dbo.log",
        "schema": "dbo",
        "name": "Log",
        "primary_key": [],
        "subtype": "standard",
        "columns": [_col("Msg", "nvarchar(max)", "LONGTEXT")],
    }
    proc = {
        "id": "procedure:website.search",
        "schema": "Website",
        "name": "Search",
        "type": "procedure",
        "subtype": "sql",
        "file": "Website/Search.sql",
        "line": 1,
        "parent": "",
        "columns": [],
        "primary_key": [],
        "references": ["table:sales.customers"],
        "source": "CREATE PROCEDURE Website.Search @t nvarchar(10) AS SELECT TOP 5 * FROM Sales.Customers",
    }
    return {
        "discovery_id": "dbupload_20261103_000000",
        "objects": [table, keyless, proc],
        "findings": [
            {
                "object_id": proc["id"],
                "severity": "low",
                "title": "TOP n",
                "lines": [1],
                "recommendation": "Use LIMIT n.",
            },
        ],
    }


# --- schema DDL --------------------------------------------------------------------------------


def test_table_ddl_maps_columns_keys_and_todos():
    sql = schema_ddl.table_ddl(_report()["objects"][0]).sql
    assert "CREATE TABLE IF NOT EXISTS `Sales`.`Customers`" in sql
    assert "`CustomerID` INT NOT NULL" in sql
    assert "`SearchName` LONGTEXT NULL COMMENT 'TODO: computed column'" in sql
    assert "`Odd` LONGTEXT NULL COMMENT 'TODO: type sql_variant'" in sql
    assert "`Seq` BIGINT NOT NULL AUTO_INCREMENT" in sql
    assert "PRIMARY KEY (`CustomerID`)" in sql
    # AUTO_INCREMENT must lead a key in MySQL.
    assert "KEY `ix_ai_Seq` (`Seq`)" in sql
    assert "-- SQL Server table features: temporal" in sql


def test_schema_sql_databases_include_schemas_without_tables_and_warn_on_missing_pk():
    out = schema_ddl.render_schema_sql(_report())
    assert "CREATE DATABASE IF NOT EXISTS `Website`;" in out
    assert "CREATE DATABASE IF NOT EXISTS `Sales`;" in out
    assert "WARNING: no primary key" in out


def test_widen_for_row_size():
    assert (
        schema_ddl.widen_for_row_size("a VARCHAR(4000), b VARCHAR(50)")
        == "a TEXT, b VARCHAR(50)"
    )


# --- parsing -----------------------------------------------------------------------------------


def test_extract_sql_and_notes_strip_delimiters():
    reply = "Here:\n```sql\nDELIMITER $$\nCREATE PROCEDURE a.b() BEGIN SELECT 1; END$$\nDELIMITER ;\n```\n## Notes\n- x"
    sql, notes = extract_sql(reply)
    assert sql == "CREATE PROCEDURE a.b() BEGIN SELECT 1; END"
    assert notes == "- x"


def test_extract_judgement_and_pass_rule():
    j = extract_judgement(
        '```json\n{"scores": {"semantic_equivalence": 5, "completeness": 4, '
        '"error_handling": 4, "transaction_semantics": 5, "mysql_idiom": 9}, '
        '"verdict": "PASS", "issues": []}\n```'
    )
    assert (
        j["scores"]["mysql_idiom"] == 5
        and j["verdict"] == "pass"
        and judgement_passes(j)
    )
    low = {**j, "scores": {**j["scores"], "completeness": 3}}
    assert not judgement_passes(low)
    bad = extract_judgement("no json here")
    assert bad["verdict"] == "revise" and bad["unparsed"]


# --- agent loop --------------------------------------------------------------------------------

MAKER_OK = "```sql\nCREATE PROCEDURE Website.Search(IN t VARCHAR(10)) BEGIN SELECT * FROM Sales.Customers LIMIT 5; END\n```\n## Notes\n- TOP → LIMIT"
JUDGE_PASS = '```json\n{"scores": {"semantic_equivalence": 5, "completeness": 5, "error_handling": 4, "transaction_semantics": 5, "mysql_idiom": 5}, "verdict": "pass", "issues": []}\n```'
JUDGE_REVISE = '```json\n{"scores": {"semantic_equivalence": 2, "completeness": 5, "error_handling": 4, "transaction_semantics": 5, "mysql_idiom": 5}, "verdict": "revise", "issues": ["ORDER BY missing"]}\n```'


class FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, prompt, max_tokens):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _agent(tmp_path, model, compile_results, max_attempts=3):
    store = ConversionStore(StorageManager(base_path=str(tmp_path / "conv")))
    results = list(compile_results)

    def fake_compile(report, object_type, sql):
        return results.pop(0)

    return store, ConversionAgent(
        store, model=model, compile_fn=fake_compile, max_attempts=max_attempts
    )


def test_agent_passes_first_time_and_context_reaches_the_maker(tmp_path):
    report = _report()
    model = FakeModel([MAKER_OK, JUDGE_PASS])
    store, agent = _agent(tmp_path, model, [mysql_sandbox.CompileResult(True)])
    store.queue(report["discovery_id"], [report["objects"][2]])
    rec = agent.convert(report, "procedure:website.search")
    assert rec["status"] == "converted"
    assert rec["final_sql"].startswith("CREATE PROCEDURE Website.Search")
    assert len(rec["attempts"]) == 1 and rec["attempts"][0]["passed"]
    maker_prompt = model.prompts[0]
    assert "SELECT TOP 5 * FROM Sales.Customers" in maker_prompt
    assert "CREATE TABLE IF NOT EXISTS `Sales`.`Customers`" in maker_prompt
    assert "Use LIMIT n." in maker_prompt
    assert "{{" not in maker_prompt


def test_agent_feeds_compile_errors_and_checker_issues_back(tmp_path):
    report = _report()
    model = FakeModel([MAKER_OK, MAKER_OK, JUDGE_REVISE, MAKER_OK, JUDGE_PASS])
    compiles = [
        mysql_sandbox.CompileResult(False, 1064, "syntax near TOP"),
        mysql_sandbox.CompileResult(True),
        mysql_sandbox.CompileResult(True),
    ]
    store, agent = _agent(tmp_path, model, compiles)
    store.queue(report["discovery_id"], [report["objects"][2]])
    rec = agent.convert(report, "procedure:website.search")
    assert rec["status"] == "converted" and len(rec["attempts"]) == 3
    assert "error 1064: syntax near TOP" in model.prompts[1]
    assert "ORDER BY missing" in model.prompts[3]


def test_agent_exhausts_attempts_and_keeps_best_for_review(tmp_path):
    report = _report()
    model = FakeModel([MAKER_OK, JUDGE_REVISE, MAKER_OK, JUDGE_REVISE])
    store, agent = _agent(
        tmp_path, model, [mysql_sandbox.CompileResult(True)] * 2, max_attempts=2
    )
    store.queue(report["discovery_id"], [report["objects"][2]])
    rec = agent.convert(report, "procedure:website.search")
    assert rec["status"] == "needs_review" and rec["final_sql"]
    assert "best attempt kept" in rec["error"]


def test_agent_reports_outages_as_failed(tmp_path):
    report = _report()

    def boom(prompt, max_tokens):
        raise mysql_sandbox.SandboxUnavailableError("MySQL sandbox unreachable")

    store, agent = _agent(tmp_path, boom, [])
    store.queue(report["discovery_id"], [report["objects"][2]])
    rec = agent.convert(report, "procedure:website.search")
    assert rec["status"] == "failed" and "unreachable" in rec["error"]


def test_agent_requires_source(tmp_path):
    report = _report()
    report["objects"][2]["source"] = ""
    store, agent = _agent(tmp_path, FakeModel([]), [])
    store.queue(report["discovery_id"], [report["objects"][2]])
    assert (
        "Re-run discovery" in agent.convert(report, "procedure:website.search")["error"]
    )


# --- routes ------------------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    app_state.progress_tracker = ProgressTracker()
    app_state.storage_manager = StorageManager(base_path=str(tmp_path / "analyses"))
    app_state.db_discovery_storage = StorageManager(base_path=str(tmp_path / "db"))
    app_state.db_conversion_storage = StorageManager(base_path=str(tmp_path / "conv"))
    app_state.db_discovery_storage.save("dbupload_20261103_000000", _report())
    import services.db_conversion.conversion_agent as ca

    monkeypatch.setattr(ca, "invoke_model", FakeModel([MAKER_OK, JUDGE_PASS]))
    monkeypatch.setattr(
        mysql_sandbox,
        "compile_object",
        lambda r, t, s: mysql_sandbox.CompileResult(
            "TOP" not in s,
            None if "TOP" not in s else 1064,
            "" if "TOP" not in s else "syntax near TOP",
        ),
    )
    monkeypatch.setattr(
        mysql_sandbox,
        "ping",
        lambda: {"configured": True, "reachable": True, "version": "8.0"},
    )
    return TestClient(app)


BASE = "/api/db-discovery/dbupload_20261103_000000"


def test_conversion_routes_lifecycle(client: TestClient):
    res = client.post(
        f"{BASE}/conversions",
        json={"object_ids": ["procedure:website.search", "table:sales.customers"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [q["object_id"] for q in body["queued"]] == ["procedure:website.search"]
    assert body["skipped"][0]["object_id"] == "table:sales.customers"

    listing = client.get(f"{BASE}/conversions").json()
    assert listing["sandbox"]["reachable"]
    (rec,) = listing["conversions"]
    assert rec["status"] == "converted"

    # Export contains only approved conversions.
    assert (
        "CREATE PROCEDURE Website.Search"
        not in client.get(f"{BASE}/conversions.sql").text
    )

    bad = client.post(
        f"{BASE}/conversions/procedure:website.search/review",
        json={
            "action": "edit",
            "sql": "CREATE PROCEDURE Website.Search() BEGIN SELECT TOP 1; END",
        },
    )
    assert bad.status_code == 400 and "does not compile" in bad.json()["detail"]

    ok = client.post(
        f"{BASE}/conversions/procedure:website.search/review",
        json={
            "action": "edit",
            "sql": "CREATE PROCEDURE Website.Search() BEGIN SELECT 1; END",
            "note": "simplified",
        },
    )
    assert (
        ok.status_code == 200
        and ok.json()["status"] == "approved"
        and ok.json()["edited"]
    )

    export = client.get(f"{BASE}/conversions.sql")
    assert export.status_code == 200
    assert "CREATE DATABASE IF NOT EXISTS `Sales`;" in export.text
    assert "CREATE PROCEDURE Website.Search() BEGIN SELECT 1; END$$" in export.text
    assert "-- Reviewer: simplified" in export.text

    rej = client.post(
        f"{BASE}/conversions/procedure:website.search/review", json={"action": "reject"}
    )
    assert rej.json()["status"] == "rejected"
    assert "Approved conversions: 0" in client.get(f"{BASE}/conversions.sql").text


def test_conversion_route_validation(client: TestClient):
    assert (
        client.post(f"{BASE}/conversions", json={"object_ids": []}).status_code == 422
    )
    too_many = client.post(
        f"{BASE}/conversions",
        json={"object_ids": [f"procedure:x.{i}" for i in range(26)]},
    )
    assert too_many.status_code == 400
    assert client.get(f"{BASE}/conversions/table:../x").status_code in (400, 404)
    assert client.get(f"{BASE}/conversions/procedure:website.none").status_code == 404
    assert (
        client.get("/api/db-discovery/dbupload_19990101_000000/conversions").status_code
        == 404
    )
    assert (
        client.post(
            f"{BASE}/conversions/procedure:website.search/review",
            json={"action": "approve"},
        ).status_code
        == 404
    )


def test_schema_sql_route(client: TestClient):
    res = client.get(f"{BASE}/schema.sql")
    assert res.status_code == 200 and "attachment" in res.headers["content-disposition"]
    assert "CREATE TABLE IF NOT EXISTS `dbo`.`Log`" in res.text


def test_custom_tests_route(client: TestClient):
    client.post(
        f"{BASE}/conversions", json={"object_ids": ["procedure:website.search"]}
    )
    url = f"{BASE}/conversions/procedure:website.search/tests"
    ok = client.put(
        url,
        json={"tests": [{"name": "toys", "params": [{"name": "@t", "value": "Toys"}]}]},
    )
    assert ok.status_code == 200 and ok.json()["custom_tests"] == [
        {"name": "toys", "params": [{"name": "t", "value": "Toys"}]}
    ]
    bad = client.put(
        url, json={"tests": [{"params": [{"name": "x; drop", "value": 1}]}]}
    )
    assert bad.status_code == 400
    assert (
        client.put(
            f"{BASE}/conversions/procedure:website.none/tests", json={"tests": []}
        ).status_code
        == 404
    )
