"""Maker → Checker conversion loop for procedures, functions, views and triggers.

Maker: Bedrock (BEDROCK_MODEL_ID) writes one MySQL CREATE statement from the T-SQL source, the
discovery findings for the object, and the generated MySQL DDL of the tables it references.

Checker, deterministic: the statement is compiled in the MySQL 8.0 sandbox with that DDL loaded.
Checker, model: a second Bedrock call scores behavioural equivalence (G-Eval style, 1–5 on five
dimensions) and returns a verdict and issues.

A failed check goes back to the Maker with the MySQL error and the issues, up to
CONVERSION_MAX_ATTEMPTS. The human Reviewer acts on the result; nothing here approves anything.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable

from config import settings
from services.db_conversion import mysql_sandbox, schema_ddl
from services.db_conversion.behaviour_test import measured_summary
from services.db_conversion.conversion_store import ConversionStore
from services.prompt_loader import load_prompt_result
from utils.bedrock import (
    BedrockUnavailableError,
    bedrock_failure_for,
    bedrock_runtime_client,
    invoke_with_retry,
    response_text,
)

logger = logging.getLogger(__name__)

SCORE_KEYS = (
    "semantic_equivalence",
    "completeness",
    "error_handling",
    "transaction_semantics",
    "mysql_idiom",
)
PASS_MIN_SCORE = 4

_MYSQL_KIND = {
    "procedure": "PROCEDURE",
    "function": "FUNCTION",
    "view": "VIEW",
    "trigger": "TRIGGER",
}

ModelCall = Callable[[str, int], str]


def invoke_model(prompt: str, max_tokens: int) -> str:
    """One Bedrock Messages call with the repo's timeout/retry policy."""
    client = bedrock_runtime_client()
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    )

    def _call() -> str:
        response = client.invoke_model(
            modelId=settings.BEDROCK_MODEL_ID,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        return response_text(json.loads(response["body"].read()))

    return invoke_with_retry(_call, description="SQL conversion")


# --- parsing model output ----------------------------------------------------------------------

_FENCE = re.compile(r"```(\w*)\s*\n(.*?)```", re.S)


def extract_sql(text: str) -> tuple[str, str]:
    """Return (sql, notes) from the Maker reply."""
    blocks = _FENCE.findall(text)
    sql = ""
    for tag, body in blocks:
        if tag.lower() in ("sql", "mysql", ""):
            sql = body.strip()
            break
    if not sql and re.match(r"\s*CREATE\b", text, re.I):
        sql = text.split("## Notes")[0].strip()
    notes = ""
    m = re.search(r"##\s*Notes\s*\n(.*)", text, re.S | re.I)
    if m:
        notes = m.group(1).strip()
    return mysql_sandbox.strip_statement(sql), notes


def extract_judgement(text: str) -> dict:
    """Parse the Checker's JSON; a reply that cannot be parsed is a 'revise' verdict."""
    candidates = [b for t, b in _FENCE.findall(text) if t.lower() in ("json", "")] or [
        text
    ]
    for raw in candidates:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            continue
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            continue
        scores = {}
        for key in SCORE_KEYS:
            try:
                scores[key] = max(1, min(5, int(data.get("scores", {}).get(key, 1))))
            except (TypeError, ValueError):
                scores[key] = 1
        verdict = str(data.get("verdict", "revise")).lower()
        issues = [str(i) for i in data.get("issues", []) if str(i).strip()]
        return {
            "scores": scores,
            "verdict": "pass" if verdict == "pass" else "revise",
            "issues": issues,
        }
    return {
        "scores": {k: 1 for k in SCORE_KEYS},
        "verdict": "revise",
        "issues": ["Checker reply could not be parsed as JSON."],
        "unparsed": True,
    }


def judgement_passes(judge: dict) -> bool:
    return (
        judge["verdict"] == "pass" and min(judge["scores"].values()) >= PASS_MIN_SCORE
    )


# --- context -----------------------------------------------------------------------------------


def _by_id(report: dict) -> dict[str, dict]:
    return {o["id"]: o for o in report["objects"]}


def referenced_tables(report: dict, obj: dict, depth: int = 2) -> list[dict]:
    """Tables the object references, directly or through referenced views/functions."""
    by_id = _by_id(report)
    seen: set[str] = set()
    tables: list[dict] = []
    frontier = [obj["id"]]
    if obj.get("parent"):
        frontier += [
            o["id"]
            for o in report["objects"]
            if o["type"] == "table"
            and f"{o['schema']}.{o['name']}".lower() == obj["parent"].lower()
        ]
    for _ in range(depth + 1):
        nxt: list[str] = []
        for oid in frontier:
            for ref in by_id.get(oid, {}).get("references", []) + (
                [oid] if oid != obj["id"] else []
            ):
                if ref in seen or ref not in by_id:
                    continue
                seen.add(ref)
                target = by_id[ref]
                if target["type"] == "table":
                    tables.append(target)
                elif target["type"] in ("view", "function", "procedure"):
                    nxt.append(ref)
        frontier = nxt
    return tables


def build_context(report: dict, obj: dict) -> dict[str, str]:
    findings = [
        f
        for f in report["findings"]
        if f["object_id"] == obj["id"] and f["severity"] != "info"
    ]
    findings_text = (
        "\n".join(
            f"- [{f['severity']}] {f['title']} (lines {', '.join(map(str, f['lines']))}): {f['recommendation']}"
            for f in findings
        )
        or "- No findings: expected to convert mechanically."
    )
    tables = referenced_tables(report, obj)
    ddl = (
        "\n\n".join(schema_ddl.table_ddl(t).sql for t in tables)
        or "-- (no table references found)"
    )
    tvps = []
    by_name = {
        f"{o['schema']}.{o['name']}".lower(): o
        for o in report["objects"]
        if o["type"] == "user_defined_type"
    }
    for m in re.finditer(
        r"@\w+\s+([\w\[\]]+\.[\w\[\]]+)\s+READONLY", obj.get("source", ""), re.I
    ):
        t = by_name.get(m.group(1).replace("[", "").replace("]", "").lower())
        if t:
            cols = ", ".join(f"{c['name']} {c['mysql_type']}" for c in t["columns"])
            tvps.append(f"- `{t['schema']}.{t['name']}` columns: {cols}")
    return {
        "object_type": obj["type"],
        "object_name": f"{obj['schema']}.{obj['name']}"
        if obj["schema"]
        else obj["name"],
        "mysql_kind": _MYSQL_KIND[obj["type"]],
        "source_sql": obj.get("source", ""),
        "findings": findings_text,
        "referenced_ddl": ddl,
        "table_types": "\n".join(tvps) or "- None.",
    }


def _feedback(attempt: dict) -> str:
    parts = [f"Your previous conversion:\n```sql\n{attempt['sql']}\n```"]
    c = attempt.get("compile") or {}
    if c and not c.get("ok"):
        parts.append(
            f"It failed to compile in MySQL 8.0: error {c.get('errno')}: {c.get('message')}"
        )
    j = attempt.get("judge")
    if j and not judgement_passes(j):
        parts.append(
            "The reviewer scored it " + json.dumps(j["scores"]) + " with these issues:"
        )
        parts.extend(f"- {i}" for i in j["issues"])
    b = attempt.get("behaviour")
    if b and b.get("status") == "failed":
        from services.db_conversion.behaviour_test import feedback_lines

        parts.append(
            "MEASURED BEHAVIOUR (this overrides any reviewer suggestion or assumption): the original and "
            "your conversion were run with the same inputs on identical data, on SQL Server and MySQL 8.0. "
            "These results are facts. Make the MySQL object return exactly what SQL Server returned in every "
            "case below, including returning no rows where SQL Server returned no rows."
        )
        parts.extend(feedback_lines(b))
    parts.append("Fix every problem above and return the full corrected statement.")
    return "\n\n".join(parts)


# --- loop --------------------------------------------------------------------------------------


class ConversionAgent:
    def __init__(
        self,
        store: ConversionStore,
        model: ModelCall | None = None,
        compile_fn: Callable[[dict, str, str], mysql_sandbox.CompileResult]
        | None = None,
        max_attempts: int | None = None,
        behaviour_factory=None,
    ) -> None:
        self._store = store
        self._model = model or invoke_model
        self._compile = compile_fn or mysql_sandbox.compile_object
        self._max_attempts = max_attempts or settings.CONVERSION_MAX_ATTEMPTS
        if behaviour_factory is None:
            from services.db_conversion.behaviour_test import BehaviourChecker

            def behaviour_factory():
                return BehaviourChecker(self._model)

        self._behaviour_factory = behaviour_factory

    def convert(self, report: dict, object_id: str) -> dict:
        did = report["discovery_id"]
        obj = _by_id(report).get(object_id)
        if obj is None:
            return self._store.update(
                did, object_id, status="failed", error="Object not found in discovery"
            )
        if not obj.get("source"):
            return self._store.update(
                did,
                object_id,
                status="failed",
                error="The discovery has no source text for this object. Re-run discovery to convert it.",
            )
        record = self._store.update(did, object_id, status="converting", error="")
        context = build_context(report, obj)
        feedback = "- None: this is the first attempt."
        best: dict | None = None
        behaviour = self._behaviour_factory()
        # Behaviour-test preparation, done once and only when needed.
        plan: dict | None = None
        # The previous attempt's measured evidence, shown to the model checker.
        last_behaviour: dict | None = None
        try:
            for n in range(1, self._max_attempts + 1):
                maker = load_prompt_result(
                    "sql-conversion-maker", {**context, "feedback": feedback}
                )
                reply = self._model(maker.content, 16000)
                sql, notes = extract_sql(reply)
                attempt: dict = {
                    "n": n,
                    "sql": sql,
                    "notes": notes,
                    "compile": None,
                    "judge": None,
                    "passed": False,
                }
                if not sql:
                    attempt["compile"] = {
                        "ok": False,
                        "errno": None,
                        "message": "No SQL code block in the reply.",
                    }
                else:
                    attempt["compile"] = self._compile(
                        report, obj["type"], sql
                    ).to_dict()
                    if attempt["compile"]["ok"]:
                        checker = load_prompt_result(
                            "sql-conversion-checker",
                            {
                                **context,
                                "converted_sql": sql,
                                "maker_notes": notes or "- (none)",
                                "measured_behaviour": measured_summary(last_behaviour),
                            },
                        )
                        # Opus spends part of the budget on thinking; 8000 leaves room for the JSON.
                        judge_reply = self._model(checker.content, 8000)
                        attempt["judge"] = extract_judgement(judge_reply)
                        if attempt["judge"].get("unparsed"):
                            attempt["judge"]["raw"] = judge_reply[-2000:]
                        attempt["passed"] = judgement_passes(attempt["judge"])
                if attempt["passed"]:
                    # Checker 3: same inputs on both engines, results and changes compared.
                    if plan is None:
                        plan = self._prepare_behaviour(behaviour, report, obj, record)
                    if plan["status"] == "ready":
                        attempt["behaviour"] = last_behaviour = behaviour.run(
                            obj, plan["cases"], referenced_tables(report, obj)
                        )
                        if attempt["behaviour"]["status"] == "failed":
                            attempt["passed"] = False
                    else:
                        attempt["behaviour"] = {
                            "status": plan["status"],
                            "reason": plan.get("reason", ""),
                        }
                self._store.append_attempt(did, object_id, attempt)
                if attempt["compile"]["ok"] and (
                    best is None or _rank(attempt) > _rank(best)
                ):
                    best = attempt
                if attempt["passed"]:
                    return self._store.update(
                        did,
                        object_id,
                        status="converted",
                        final_sql=sql,
                        notes=notes,
                        error="",
                        behaviour_status=(attempt.get("behaviour") or {}).get(
                            "status", "not_run"
                        ),
                    )
                feedback = _feedback(attempt)
            if best is None:
                last = self._store.get(did, object_id)["attempts"][-1]
                return self._store.update(
                    did,
                    object_id,
                    status="needs_review",
                    final_sql=last["sql"],
                    notes=last["notes"],
                    error=f"No attempt compiled after {self._max_attempts} tries; last MySQL error: "
                    f"{(last.get('compile') or {}).get('message', '')}",
                )
            return self._store.update(
                did,
                object_id,
                status="needs_review",
                final_sql=best["sql"],
                notes=best["notes"],
                error=f"Checker did not pass any of {self._max_attempts} attempts; best attempt kept for review.",
                behaviour_status=(best.get("behaviour") or {}).get("status", "not_run"),
            )
        except BedrockUnavailableError as exc:
            return self._store.update(did, object_id, status="failed", error=str(exc))
        except mysql_sandbox.SandboxUnavailableError as exc:
            return self._store.update(did, object_id, status="failed", error=str(exc))
        except Exception as exc:  # classified Bedrock failures and anything unexpected
            failure = bedrock_failure_for(exc)
            logger.warning(
                "Conversion of %s failed [%s]: %s",
                object_id,
                failure.kind,
                failure.message,
            )
            return self._store.update(
                did, object_id, status="failed", error=failure.message
            )
        finally:
            behaviour.close()

    def _prepare_behaviour(
        self, behaviour, report: dict, obj: dict, record: dict
    ) -> dict:
        """Copy data and design test cases; a failure here skips Checker 3 rather than the conversion."""
        try:
            plan = behaviour.prepare(
                report,
                obj,
                referenced_tables(report, obj),
                record.get("custom_tests") or [],
                record.get("test_cases") or [],
            )
        except (
            Exception
        ) as exc:  # source unreachable, copy failure: report, don't fail the conversion
            from services.db_validation.connections import safe_error

            logger.warning(
                "Behaviour test preparation for %s failed: %s",
                obj["id"],
                safe_error(exc),
            )
            plan = {
                "status": "error",
                "reason": f"behaviour test setup failed: {safe_error(exc)}",
            }
        if plan.get("status") == "ready":
            self._store.update(
                report["discovery_id"],
                obj["id"],
                test_cases=[c for c in plan["cases"] if c.get("origin") == "generated"],
                copy_problems=plan.get("copy_problems", []),
            )
        return plan

    def convert_many(self, report: dict, object_ids: list[str], progress=None) -> None:
        for k, object_id in enumerate(object_ids, start=1):
            if progress:
                progress(k, len(object_ids), object_id)
            self.convert(report, object_id)


def _rank(attempt: dict) -> tuple:
    j = attempt.get("judge") or {
        "scores": {k: 0 for k in SCORE_KEYS},
        "verdict": "revise",
    }
    b = (attempt.get("behaviour") or {}).get("status")
    return (
        attempt["passed"],
        b == "passed",
        min(j["scores"].values()),
        sum(j["scores"].values()),
    )
