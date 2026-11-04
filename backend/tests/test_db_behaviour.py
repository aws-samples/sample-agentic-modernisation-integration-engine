"""Checker 3 (behaviour tests): unit pieces and its place in the Maker/Checker loop."""

from __future__ import annotations

from services.db_conversion import behaviour_test as bt
from services.db_conversion import mysql_sandbox
from services.db_conversion.conversion_agent import ConversionAgent
from services.db_conversion.conversion_store import ConversionStore
from utils.storage_manager import StorageManager


def test_source_parameters():
    src = """CREATE PROCEDURE Website.SearchForCustomers
@SearchText nvarchar(1000),
@MaximumRowsToReturn int = 10,
@Total int OUTPUT
WITH EXECUTE AS OWNER
AS
BEGIN
    SELECT 1
END"""
    params = bt.source_parameters(src)
    assert [p["name"] for p in params] == ["SearchText", "MaximumRowsToReturn", "Total"]
    assert params[0]["type"].lower() == "nvarchar(1000)"
    assert params[1]["default"] == "10"
    assert params[2]["mode"] == "OUTPUT"


def test_parse_test_cases_filters_unsafe_names_and_values():
    reply = """```json
[{"name": "normal", "params": [{"name": "@SearchText", "value": "Toys"}, {"name": "Max", "value": 5}]},
 {"name": "bad", "params": [{"name": "x; DROP", "value": 1}, {"name": "Obj", "value": {"a": 1}}]},
 "not a case"]
```"""
    cases = bt.parse_test_cases(reply)
    assert cases[0]["params"] == [
        {"name": "SearchText", "value": "Toys"},
        {"name": "Max", "value": 5},
    ]
    assert cases[1]["params"] == []
    assert len(cases) == 2 and all(c["origin"] == "generated" for c in cases)
    assert bt.parse_test_cases("no json") == []


def test_build_mysql_call_maps_names_and_out_params():
    mysql_params = [
        {"name": "p_search_text", "mode": "IN"},
        {"name": "MaximumRowsToReturn", "mode": "IN"},
        {"name": "p_return_code", "mode": "OUT"},
    ]
    sql, values = bt.build_mysql_call(
        "Website",
        "SearchForCustomers",
        mysql_params,
        [
            {"name": "SearchText", "value": "Toys"},
            {"name": "MaximumRowsToReturn", "value": 3},
        ],
    )
    assert sql == "CALL `Website`.`SearchForCustomers`(%s, %s, @amie_out_p_return_code)"
    assert values == ["Toys", 3]


def test_build_mysql_call_requires_values_for_in_params():
    import pytest

    with pytest.raises(bt.BehaviourTestSkipped):
        bt.build_mysql_call("a", "b", [{"name": "x", "mode": "IN"}], [])


def test_compare_sets_and_feedback():
    assert bt._compare_sets([[(1, "a")]], [[(1, "a ")]], ordered=True) == []
    diffs = bt._compare_sets([[(1, "a"), (2, "b")]], [[(1, "a")]], ordered=True)
    assert any("2 rows vs 1 rows" in d for d in diffs)
    lines = bt.feedback_lines(
        {
            "tests": [
                {
                    "name": "empty search",
                    "params": [{"name": "SearchText", "value": "zzz"}],
                    "status": "mismatch",
                    "details": ["set 0: 0 rows vs 1 rows"],
                    "source_error": "",
                    "target_error": "",
                    "result_rows": {"source": [0], "target": [1]},
                    "evidence": {"source": [[]], "target": [["∅"]]},
                },
                {"name": "ok", "params": [], "status": "match", "details": []},
                {
                    "name": "err",
                    "params": [],
                    "status": "mismatch",
                    "details": [],
                    "source_error": "",
                    "target_error": "1305 PROCEDURE does not exist",
                },
            ]
        }
    )
    assert lines[0] == (
        "- Test 'empty search' (SearchText='zzz'):\n"
        "    SQL Server returned result set 1: NO rows\n"
        "    MySQL returned      result set 1: 1 row(s), first: ∅"
    )
    assert lines[1] == (
        "- Test 'err' (no parameters): SQL Server succeeded; "
        "MySQL raised an error: 1305 PROCEDURE does not exist."
    )
    assert len(lines) == 2


def test_measured_summary_for_checker():
    summary = bt.measured_summary(
        {
            "tests": [
                {
                    "name": "none",
                    "params": [{"name": "Max", "value": 0}],
                    "status": "mismatch",
                    "details": [],
                    "result_rows": {"source": [0], "target": [1]},
                    "evidence": {"source": [[]], "target": [["∅"]]},
                },
                {"name": "skip", "params": [], "status": "skipped", "details": []},
            ]
        }
    )
    assert summary == "- 'none' (Max=0): DIFFERENT. SQL Server: result set 1: NO rows"
    assert bt.measured_summary(None) == "- Not measured yet."


# --- loop integration --------------------------------------------------------------------------

MAKER = "```sql\nCREATE PROCEDURE Website.Search(IN t VARCHAR(10)) BEGIN SELECT 1; END\n```\n## Notes\n- x"
JUDGE_PASS = (
    '```json\n{"scores": {"semantic_equivalence": 5, "completeness": 5, "error_handling": 5, '
    '"transaction_semantics": 5, "mysql_idiom": 5}, "verdict": "pass", "issues": []}\n```'
)


class Model:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt, max_tokens):
        self.prompts.append(prompt)
        return self.replies.pop(0)


class FakeBehaviour:
    def __init__(self, outcomes, prepare_status="ready"):
        self.outcomes = list(outcomes)
        self.prepare_status = prepare_status
        self.prepared = 0
        self.closed = False

    def prepare(self, report, obj, tables, custom, existing):
        self.prepared += 1
        if self.prepare_status != "ready":
            return {
                "status": self.prepare_status,
                "reason": "No SQL Server source is configured",
            }
        return {
            "status": "ready",
            "cases": [{"name": "c1", "params": [], "origin": "generated"}] + custom,
        }

    def run(self, obj, cases, tables):
        return self.outcomes.pop(0)

    def close(self):
        self.closed = True


REPORT = {
    "discovery_id": "dbupload_x",
    "objects": [
        {
            "id": "procedure:website.search",
            "schema": "Website",
            "name": "Search",
            "type": "procedure",
            "subtype": "sql",
            "parent": "",
            "references": [],
            "columns": [],
            "source": "CREATE PROCEDURE Website.Search @t nvarchar(10) AS SELECT 1",
        }
    ],
    "findings": [],
}

FAILED = {
    "status": "failed",
    "counts": {"mismatch": 1},
    "tests": [
        {
            "name": "c1",
            "params": [],
            "status": "mismatch",
            "details": ["set 0: 3 rows vs 2 rows"],
            "source_error": "",
            "target_error": "",
        }
    ],
}
PASSED = {
    "status": "passed",
    "counts": {"match": 1},
    "tests": [
        {
            "name": "c1",
            "params": [],
            "status": "match",
            "details": [],
            "source_error": "",
            "target_error": "",
        }
    ],
}


def _agent(tmp_path, model, behaviour):
    store = ConversionStore(StorageManager(base_path=str(tmp_path / "c")))
    store.queue("dbupload_x", REPORT["objects"])
    agent = ConversionAgent(
        store,
        model=model,
        compile_fn=lambda r, t, s: mysql_sandbox.CompileResult(True),
        max_attempts=3,
        behaviour_factory=lambda: behaviour,
    )
    return store, agent


def test_behaviour_mismatch_goes_back_to_the_maker_before_review(tmp_path):
    model = Model([MAKER, JUDGE_PASS, MAKER, JUDGE_PASS])
    behaviour = FakeBehaviour([FAILED, PASSED])
    store, agent = _agent(tmp_path, model, behaviour)
    rec = agent.convert(REPORT, "procedure:website.search")
    assert rec["status"] == "converted" and rec["behaviour_status"] == "passed"
    assert [a["passed"] for a in rec["attempts"]] == [False, True]
    assert rec["attempts"][0]["behaviour"]["status"] == "failed"
    assert "MEASURED BEHAVIOUR" in model.prompts[2]
    assert "Test 'c1'" in model.prompts[2]
    # The checker of attempt 2 sees attempt 1's measured results.
    assert "'c1' (no parameters): DIFFERENT" in model.prompts[3]
    assert behaviour.prepared == 1 and behaviour.closed
    assert rec["test_cases"] == [{"name": "c1", "params": [], "origin": "generated"}]


def test_without_source_conversion_passes_but_is_flagged(tmp_path):
    behaviour = FakeBehaviour([], prepare_status="not_configured")
    store, agent = _agent(tmp_path, Model([MAKER, JUDGE_PASS]), behaviour)
    rec = agent.convert(REPORT, "procedure:website.search")
    assert rec["status"] == "converted"
    assert rec["behaviour_status"] == "not_configured"
    assert rec["attempts"][0]["behaviour"]["reason"].startswith("No SQL Server source")


def test_setup_failure_skips_checker_3_without_failing(tmp_path):
    class Broken(FakeBehaviour):
        def prepare(self, *a):
            raise RuntimeError("login timeout")

    store, agent = _agent(tmp_path, Model([MAKER, JUDGE_PASS]), Broken([]))
    rec = agent.convert(REPORT, "procedure:website.search")
    assert rec["status"] == "converted" and rec["behaviour_status"] == "error"
    assert "login timeout" in rec["attempts"][0]["behaviour"]["reason"]


def test_exhausted_attempts_keep_best_with_behaviour_status(tmp_path):
    model = Model([MAKER, JUDGE_PASS] * 3)
    store, agent = _agent(tmp_path, model, FakeBehaviour([FAILED, FAILED, FAILED]))
    rec = agent.convert(REPORT, "procedure:website.search")
    assert rec["status"] == "needs_review" and rec["behaviour_status"] == "failed"


def test_custom_tests_are_kept_across_requeue(tmp_path):
    store = ConversionStore(StorageManager(base_path=str(tmp_path / "c")))
    store.queue("dbupload_x", REPORT["objects"])
    store.update(
        "dbupload_x",
        "procedure:website.search",
        status="converted",
        custom_tests=[{"name": "mine", "params": []}],
        test_cases=[{"name": "gen", "params": []}],
    )
    (rec,) = store.queue("dbupload_x", REPORT["objects"])
    assert rec["custom_tests"] == [{"name": "mine", "params": []}]
    assert rec["test_cases"] == [{"name": "gen", "params": []}]


def test_recover_interrupted_marks_active_records_failed(tmp_path):
    store = ConversionStore(StorageManager(base_path=str(tmp_path / "c")))
    store.queue("dbupload_x", REPORT["objects"])
    assert store.recover_interrupted() == 1
    (rec,) = store.list("dbupload_x")
    assert rec["status"] == "failed" and "Interrupted" in rec["error"]
    assert store.queue("dbupload_x", REPORT["objects"])  # can be queued again
