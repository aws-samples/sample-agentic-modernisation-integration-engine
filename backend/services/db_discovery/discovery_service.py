"""Database discovery orchestration: walk → scan → cross-reference → score → store."""

from __future__ import annotations

import logging
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

from services.db_discovery import csharp_sql_scanner as cs
from services.db_discovery import tsql_scanner as ts
from services.db_discovery.mysql_compat_rules import (
    RULES,
    RULES_BY_ID,
    SEVERITY_ORDER,
    SEVERITY_WEIGHT,
    evaluate,
    hits_to_lines,
)

logger = logging.getLogger(__name__)

TARGET = "Amazon Aurora MySQL 3 (MySQL 8.0 compatible)"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_SOURCE_CHARS = 200_000
_SKIP_DIRS = {
    ".git",
    "bin",
    "obj",
    "node_modules",
    "packages",
    ".vs",
    "__pycache__",
    "dist",
    "build",
    "target",
}
_REFERABLE = {
    "table",
    "view",
    "procedure",
    "function",
    "sequence",
    "synonym",
    "user_defined_type",
}

EFFORT_FORMULA = (
    "points = sum over distinct rules hit on the object of weight(severity) + lines_of_code / 100; "
    "weights: blocker 13, high 5, medium 2, low 0.5, info 0; "
    "bucket: < 2 low, < 8 medium, < 20 high, otherwise very_high; "
    "an object with a blocker finding is at least high"
)


def all_rules() -> list[dict]:
    return [r.to_dict() for r in RULES] + [r.to_dict() for r in cs.APP_RULES]


def _rule_meta(rule_id: str) -> dict:
    if rule_id in RULES_BY_ID:
        return RULES_BY_ID[rule_id].to_dict()
    return cs.APP_RULES_BY_ID[rule_id].to_dict()


def effort_bucket(points: float, has_blocker: bool) -> str:
    if points < 2:
        bucket = "low"
    elif points < 8:
        bucket = "medium"
    elif points < 20:
        bucket = "high"
    else:
        bucket = "very_high"
    if has_blocker and bucket in ("low", "medium"):
        bucket = "high"
    return bucket


def _type_family(sql_type: str) -> str:
    base = sql_type.split("(", 1)[0]
    if "(max)" in sql_type:
        return f"{base}(max)"
    if base in ("datetime2", "time", "datetimeoffset"):
        return sql_type if "(" in sql_type else f"{base}(7)"
    if "(" not in sql_type:
        return base
    if base in ("decimal", "numeric"):
        return f"{base}(p,s)"
    if base == "float":
        return sql_type
    return f"{base}(n)"


class _SqlObjectState:
    """An inventory object plus the file regions that belong to it."""

    def __init__(self, obj: ts.DbObject) -> None:
        self.obj = obj
        self.regions: list[tuple[int, int]] = [(obj.start, obj.end)]
        self.ast_tables: list[tuple[str, str]] = []
        self.ast_parsed_batches: list[bool] = []


class DatabaseDiscoveryService:
    """Runs one discovery over a directory tree and returns (and optionally stores) the report."""

    def __init__(self, storage=None, tracker=None) -> None:
        self._storage = storage
        self._tracker = tracker

    # --- public -------------------------------------------------------------------------------

    def run(
        self,
        root: str,
        discovery_id: str,
        source_type: str,
        source_url: str = "",
        branch: str = "",
        subpath: str = "",
    ) -> dict:
        self._progress(discovery_id, 5, "scanning", "Listing files...")
        sql_files, app_files, skipped = self._walk(root)

        self._progress(
            discovery_id, 15, "parsing", f"Parsing {len(sql_files)} SQL files..."
        )
        states, ast_stats, total_sql_lines = self._scan_sql(root, sql_files)

        self._progress(
            discovery_id, 45, "rules", "Evaluating MySQL compatibility rules..."
        )
        objects, findings = self._evaluate_objects(root, states)

        self._progress(
            discovery_id,
            65,
            "application",
            f"Scanning {len(app_files)} application files...",
        )
        application, app_findings = self._scan_application(root, app_files, objects)
        findings.extend(app_findings)

        self._progress(
            discovery_id, 85, "summary", "Scoring effort and building the summary..."
        )
        self._score(objects, findings)
        report = {
            "discovery_id": discovery_id,
            "source_type": source_type,
            "source_url": source_url,
            "branch": branch,
            "subpath": subpath,
            "target": TARGET,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "objects": objects,
            "findings": findings,
            "type_mappings": self._type_mappings(objects),
            "application": application,
        }
        report["summary"] = self._summary(
            report, sql_files, app_files, skipped, ast_stats, total_sql_lines
        )
        if self._storage is not None:
            self._storage.save(discovery_id, report)
        return report

    # --- walking ------------------------------------------------------------------------------

    def _walk(self, root: str) -> tuple[list[str], list[str], list[str]]:
        sql_files: list[str] = []
        app_files: list[str] = []
        skipped: list[str] = []
        root_path = Path(root)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
            )
            for filename in sorted(filenames):
                full = Path(dirpath) / filename
                if full.is_symlink():
                    continue
                rel = full.relative_to(root_path).as_posix()
                is_sql = filename.lower().endswith(".sql")
                is_app = cs.is_app_file(filename)
                if not (is_sql or is_app):
                    continue
                try:
                    if full.stat().st_size > MAX_FILE_BYTES:
                        skipped.append(rel)
                        continue
                except OSError:
                    continue
                (sql_files if is_sql else app_files).append(rel)
        return sql_files, app_files, skipped

    # --- SQL ----------------------------------------------------------------------------------

    def _scan_sql(self, root: str, sql_files: list[str]):
        states: list[_SqlObjectState] = []
        self._file_text: dict[str, tuple[str, str]] = {}
        batches_total = parsed_total = 0
        total_lines = 0
        for rel in sql_files:
            try:
                original = ts.read_sql_file(Path(root) / rel)
            except OSError as exc:
                logger.debug("Skipping %s: %s", rel, exc)
                continue
            masked = ts.mask_tsql(original)
            self._file_text[rel] = (original, masked)
            total_lines += ts.non_blank_lines(masked)
            first: _SqlObjectState | None = None
            script: _SqlObjectState | None = None
            for batch in ts.split_batches(original, masked):
                batches_total += 1
                ts.sqlglot_parse(batch)
                parsed_total += int(batch.ast_parsed)
                found = ts.find_objects(batch, rel)
                if found:
                    for obj in found:
                        state = _SqlObjectState(obj)
                        state.ast_tables = list(batch.ast_tables)
                        state.ast_parsed_batches.append(batch.ast_parsed)
                        states.append(state)
                        first = first or state
                    continue
                # A batch that defines nothing (ALTER TABLE, extended properties, data loads)
                # belongs to the file's first object (the table in an SSDT table file), or to
                # the file's script object when the file defines no object.
                region = (batch.start, batch.start + len(batch.masked))
                target = first
                if target is None:
                    if script is None:
                        script = _SqlObjectState(
                            ts.DbObject(
                                schema="",
                                name=rel,
                                type="script",
                                subtype="",
                                file=rel,
                                start=region[0],
                                end=region[1],
                            )
                        )
                        script.regions = []
                        states.append(script)
                    target = script
                target.regions.append(region)
                target.ast_parsed_batches.append(batch.ast_parsed)
        return states, {"batches": batches_total, "parsed": parsed_total}, total_lines

    def _evaluate_objects(self, root: str, states: list[_SqlObjectState]):
        objects: list[dict] = []
        findings: list[dict] = []
        used_ids: Counter[str] = Counter()
        alias_types: dict[str, str] = {}
        for st in states:
            if st.obj.type == "user_defined_type" and st.obj.alias_base_type:
                alias_types[st.obj.name.lower()] = st.obj.alias_base_type.lower()
                alias_types[f"{st.obj.schema}.{st.obj.name}".lower()] = (
                    st.obj.alias_base_type.lower()
                )

        for st in states:
            obj = st.obj
            original, masked = self._file_text[obj.file]
            index = ts.LineIndex(original)
            obj.line = index.line_of(st.regions[0][0]) if st.regions else 1
            obj.end_line = (
                index.line_of(max(e for _, e in st.regions) - 1)
                if st.regions
                else obj.line
            )

            base_id = f"{obj.type}:{obj.qualified}".lower()
            used_ids[base_id] += 1
            obj_id = (
                base_id if used_ids[base_id] == 1 else f"{base_id}#{used_ids[base_id]}"
            )

            loc = 0
            per_rule: dict[str, list[int]] = defaultdict(list)
            for start, end in st.regions:
                region_masked = masked[start:end]
                loc += ts.non_blank_lines(region_masked)
                base_line = index.line_of(start)
                for rule, lines in hits_to_lines(
                    evaluate(region_masked, obj.type), region_masked, base_line
                ):
                    for ln in lines:
                        if ln not in per_rule[rule.id]:
                            per_rule[rule.id].append(ln)

            columns = []
            for col in obj.columns:
                sql_type = col.sql_type
                note_prefix = ""
                if col.base_type in alias_types:
                    note_prefix = (
                        f"alias type {col.base_type} → {alias_types[col.base_type]}; "
                    )
                    sql_type = alias_types[col.base_type]
                mysql_type, note = ts.map_type_to_mysql(sql_type)
                if col.identity:
                    mysql_type += " AUTO_INCREMENT"
                columns.append(
                    {
                        "name": col.name,
                        "sql_type": col.sql_type,
                        "nullable": col.nullable,
                        "identity": col.identity,
                        "computed": col.computed,
                        "persisted": col.persisted,
                        "has_default": col.has_default,
                        "default_sequence": col.default_sequence,
                        "generated_always": col.generated_always,
                        "mysql_type": mysql_type,
                        "mapping_note": (note_prefix + note).strip("; "),
                    }
                )
            if any(c["computed"] for c in columns):
                per_rule["SYN_COMPUTED_COLUMN"] = [obj.line]
            if obj.type != "script" and (
                len(obj.name) > 64 or any(len(c["name"]) > 64 for c in columns)
            ):
                per_rule["SYN_IDENTIFIER_LENGTH"] = [obj.line]

            for rule_id, lines in per_rule.items():
                rule = RULES_BY_ID[rule_id]
                findings.append(
                    {
                        "id": f"F{len(findings) + 1:05d}",
                        "rule_id": rule.id,
                        "title": rule.title,
                        "category": rule.category,
                        "severity": rule.severity,
                        "source": "database",
                        "object_id": obj_id,
                        "file": obj.file,
                        "line": lines[0],
                        "occurrences": len(lines),
                        "lines": lines[:5],
                        "snippet": self._line(original, lines[0]),
                        "recommendation": rule.recommendation,
                    }
                )

            # Module source is kept for the conversion agent: the scanned files are gone
            # once discovery finishes (the upload or clone is deleted).
            source = ""
            if obj.type in ts.MODULE_TYPES:
                source = "\n".join(original[s:e] for s, e in st.regions).strip()[
                    :MAX_SOURCE_CHARS
                ]

            objects.append(
                {
                    "id": obj_id,
                    "source": source,
                    "schema": obj.schema,
                    "name": obj.name,
                    "type": obj.type,
                    "subtype": obj.subtype,
                    "file": obj.file,
                    "line": obj.line,
                    "end_line": obj.end_line,
                    "lines_of_code": loc,
                    "parent": obj.parent,
                    "alias_base_type": obj.alias_base_type,
                    "primary_key": obj.primary_key,
                    "columns": columns,
                    "_raw_refs": obj.raw_references
                    + [[db, name] if db else [name] for db, name in st.ast_tables],
                    "references": [],
                    "referenced_by": [],
                    "called_from_code": [],
                    "ast_parsed": bool(st.ast_parsed_batches)
                    and all(st.ast_parsed_batches),
                }
            )
        self._resolve_references(objects)
        return objects, findings

    @staticmethod
    def _line(text: str, line: int) -> str:
        lines = text.split("\n")
        return lines[line - 1].strip()[:200] if 0 < line <= len(lines) else ""

    @staticmethod
    def _object_index(
        objects: list[dict],
    ) -> tuple[dict[str, str], dict[str, list[str]]]:
        by_qualified: dict[str, str] = {}
        by_name: dict[str, list[str]] = defaultdict(list)
        for o in objects:
            if o["type"] not in _REFERABLE:
                continue
            by_qualified.setdefault(f"{o['schema']}.{o['name']}".lower(), o["id"])
            by_name[o["name"].lower()].append(o["id"])
        return by_qualified, by_name

    def _resolve(self, parts: list[str], by_qualified, by_name) -> str | None:
        parts = [p for p in parts if p]
        if not parts:
            return None
        if len(parts) >= 2:
            return by_qualified.get(f"{parts[-2]}.{parts[-1]}".lower())
        hit = by_qualified.get(f"dbo.{parts[0]}".lower())
        if hit:
            return hit
        candidates = by_name.get(parts[0].lower(), [])
        return candidates[0] if len(candidates) == 1 else None

    def _resolve_references(self, objects: list[dict]) -> None:
        by_qualified, by_name = self._object_index(objects)
        by_id = {o["id"]: o for o in objects}
        for o in objects:
            refs: list[str] = []
            for parts in o.pop("_raw_refs"):
                target = self._resolve(parts, by_qualified, by_name)
                if target and target != o["id"] and target not in refs:
                    refs.append(target)
            if o["parent"]:
                target = by_qualified.get(o["parent"].lower())
                if target and target not in refs:
                    refs.append(target)
            o["references"] = refs
        for o in objects:
            for target in o["references"]:
                by_id[target]["referenced_by"].append(o["id"])

    # --- application --------------------------------------------------------------------------

    def _scan_application(self, root: str, app_files: list[str], objects: list[dict]):
        by_qualified, by_name = self._object_index(objects)
        procedures = {o["id"] for o in objects if o["type"] == "procedure"}
        by_id = {o["id"]: o for o in objects}
        findings: list[dict] = []
        files: list[dict] = []
        embedded: list[dict] = []
        calls: list[dict] = []
        conns: list[dict] = []
        packages: list[dict] = []
        frameworks: list[dict] = []
        technologies: Counter[str] = Counter()

        def add_finding(
            rule_id: str,
            file: str,
            lines: list[int],
            snippet: str,
            extra: dict | None = None,
        ):
            meta = _rule_meta(rule_id)
            findings.append(
                {
                    "id": "",
                    "rule_id": rule_id,
                    "title": meta["title"],
                    "category": meta["category"],
                    "severity": meta["severity"],
                    "source": "application",
                    "object_id": (extra or {}).get("object_id", f"file:{file}"),
                    "file": file,
                    "line": lines[0],
                    "occurrences": len(lines),
                    "lines": lines[:5],
                    "snippet": snippet,
                    "recommendation": meta["recommendation"],
                }
            )

        for rel in app_files:
            try:
                src = (Path(root) / rel).read_text(
                    encoding="utf-8-sig", errors="replace"
                )
            except OSError:
                continue
            src = src.replace("\r\n", "\n")
            scan = (
                cs.scan_csharp(src, rel)
                if rel.lower().endswith(".cs")
                else cs.scan_config(src, rel)
            )
            before = len(findings)

            for f in scan.findings:
                add_finding(f.rule_id, f.file, f.lines, f.snippet)
            for c in scan.connection_strings:
                conns.append(
                    {
                        "file": c.file,
                        "line": c.line,
                        "name": c.name,
                        "connection_string": c.redacted,
                        "server": c.server,
                        "database": c.database,
                        "provider": c.provider,
                        "windows_auth": c.windows_auth,
                        "mars": c.mars,
                        "password_present": c.password_present,
                    }
                )
                for f in cs.connection_findings(c):
                    add_finding(f.rule_id, f.file, f.lines, f.snippet)
            for p in scan.packages:
                packages.append(
                    {
                        "name": p.name,
                        "version": p.version,
                        "file": p.file,
                        "replacement": p.replacement,
                        "source": p.source,
                    }
                )
            for tf in scan.target_frameworks:
                frameworks.append({"file": rel, "framework": tf})
            for t in scan.technologies:
                technologies[t] += 1

            for k, e in enumerate(scan.embedded_sql):
                emb_id = f"sql:{rel}:{e.line}:{k}"
                emb_masked = ts.mask_tsql(e.text)
                rule_ids: list[str] = []
                for rule, rel_lines in hits_to_lines(
                    evaluate(emb_masked, "embedded_sql"), emb_masked, 1
                ):
                    line = e.line + (
                        rel_lines[0] - 1 if e.verbatim and not e.concatenated else 0
                    )
                    rule_ids.append(rule.id)
                    add_finding(
                        rule.id,
                        rel,
                        [line],
                        self._line(src, line),
                        {"object_id": emb_id},
                    )
                if e.dynamic:
                    rule_ids.append("APP_SQL_INJECTION")
                    add_finding(
                        "APP_SQL_INJECTION",
                        rel,
                        [e.line],
                        self._line(src, e.line),
                        {"object_id": emb_id},
                    )
                embedded.append(
                    {
                        "id": emb_id,
                        "file": rel,
                        "line": e.line,
                        "sql": e.text[:4000],
                        "interpolated": e.interpolated,
                        "concatenated": e.concatenated,
                        "dynamic": e.dynamic,
                        "rule_ids": rule_ids,
                    }
                )

            for cand in scan.proc_candidates:
                target = self._resolve(cand.parts, by_qualified, by_name)
                if target and target not in procedures:
                    continue  # names a table, type or view (e.g. a TVP TypeName), not a procedure
                resolved = target is not None
                if not resolved and cand.via == "literal":
                    continue  # a bare string that is not a known procedure is not a call
                call = {
                    "file": rel,
                    "line": cand.line,
                    "procedure": ".".join(cand.parts),
                    "object_id": target if resolved else None,
                    "via": cand.via,
                    "resolved": resolved,
                }
                calls.append(call)
                if resolved:
                    by_id[target]["called_from_code"].append(f"{rel}:{cand.line}")

            files.append(
                {
                    "file": rel,
                    "technologies": scan.technologies,
                    "embedded_sql_count": len(scan.embedded_sql),
                    "finding_count": len(findings) - before,
                }
            )

        files = [
            f
            for f in files
            if f["finding_count"] or f["embedded_sql_count"] or f["technologies"]
        ]
        # The same package is often listed by packages.config and as a .csproj assembly reference;
        # keep one entry per name, preferring the NuGet package version over the assembly version.
        unique: dict[str, dict] = {}
        for p in packages:
            key = p["name"].lower()
            if key not in unique or (
                unique[key]["source"] == "assembly_reference"
                and p["source"] == "package"
            ):
                unique[key] = p
        packages = list(unique.values())
        application = {
            "files": files,
            "embedded_sql": embedded,
            "stored_procedure_calls": calls,
            "connection_strings": conns,
            "packages": packages,
            "target_frameworks": frameworks,
            "technologies": [
                {"name": k, "files": v} for k, v in technologies.most_common()
            ],
        }
        return application, findings

    # --- scoring and summary ------------------------------------------------------------------

    def _score(self, objects: list[dict], findings: list[dict]) -> None:
        for n, f in enumerate(findings, start=1):
            f["id"] = f"F{n:05d}"
        by_object: dict[str, list[dict]] = defaultdict(list)
        for f in findings:
            by_object[f["object_id"]].append(f)
        for o in objects:
            own = by_object.get(o["id"], [])
            counts = Counter(f["severity"] for f in own)
            points = (
                sum(SEVERITY_WEIGHT[f["severity"]] for f in own)
                + o["lines_of_code"] / 100
            )
            o["finding_counts"] = {s: counts.get(s, 0) for s in SEVERITY_ORDER}
            o["effort_points"] = round(points, 1)
            o["effort"] = effort_bucket(points, counts.get("blocker", 0) > 0)

    @staticmethod
    def _type_mappings(objects: list[dict]) -> list[dict]:
        families: dict[str, dict] = {}
        for o in objects:
            if o["type"] not in ("table", "user_defined_type"):
                continue
            for c in o["columns"]:
                fam = _type_family(c["sql_type"])
                entry = families.get(fam)
                if entry is None:
                    mysql_type, note = ts.map_type_to_mysql(fam)
                    entry = families[fam] = {
                        "sql_server_type": fam,
                        "mysql_type": mysql_type,
                        "note": note,
                        "column_count": 0,
                    }
                entry["column_count"] += 1
        return sorted(families.values(), key=lambda e: -e["column_count"])

    @staticmethod
    def _summary(
        report, sql_files, app_files, skipped, ast_stats, total_sql_lines
    ) -> dict:
        objects = report["objects"]
        findings = report["findings"]
        app = report["application"]
        sev = Counter(f["severity"] for f in findings)
        cat = Counter(f["category"] for f in findings)
        rule_occ: dict[str, dict] = {}
        for f in findings:
            e = rule_occ.setdefault(
                f["rule_id"],
                {
                    "rule_id": f["rule_id"],
                    "title": f["title"],
                    "severity": f["severity"],
                    "category": f["category"],
                    "occurrences": 0,
                    "objects": 0,
                },
            )
            e["occurrences"] += f["occurrences"]
            e["objects"] += 1
        top = sorted(
            rule_occ.values(),
            key=lambda e: (
                -SEVERITY_WEIGHT[e["severity"]],
                -e["objects"],
                -e["occurrences"],
            ),
        )
        modules = [
            o for o in objects if o["type"] in ("procedure", "function", "trigger")
        ]
        schemas = sorted(
            {o["schema"] for o in objects if o["schema"]}
            | {o["name"] for o in objects if o["type"] == "schema"}
        )
        calls = app["stored_procedure_calls"]
        called_procs = {c["object_id"] for c in calls if c["resolved"]}
        all_procs = [o for o in objects if o["type"] == "procedure"]
        manual = [o for o in objects if o["effort"] in ("high", "very_high")]
        return {
            "files_scanned": {
                "sql": len(sql_files),
                "application": len(app_files),
                "skipped_too_large": skipped,
            },
            "object_counts": dict(Counter(o["type"] for o in objects).most_common()),
            "finding_counts": {s: sev.get(s, 0) for s in SEVERITY_ORDER},
            "finding_counts_by_source": dict(Counter(f["source"] for f in findings)),
            "category_counts": dict(cat.most_common()),
            "effort_counts": {
                b: sum(1 for o in objects if o["effort"] == b)
                for b in ("low", "medium", "high", "very_high")
            },
            "effort_formula": EFFORT_FORMULA,
            "objects_needing_manual_work": len(manual),
            "top_rules": top[:15],
            "schemas": schemas,
            "total_sql_lines": total_sql_lines,
            "ast_parse": ast_stats,
            "logic_distribution": {
                "database_modules": len(modules),
                "database_module_lines": sum(o["lines_of_code"] for o in modules),
                "embedded_sql_statements": len(app["embedded_sql"]),
                "dynamic_embedded_sql_statements": sum(
                    1 for e in app["embedded_sql"] if e["dynamic"]
                ),
            },
            "application": {
                "files_with_findings": len(app["files"]),
                "embedded_sql_statements": len(app["embedded_sql"]),
                "stored_procedure_calls": len(calls),
                "unresolved_procedure_calls": sum(
                    1 for c in calls if not c["resolved"]
                ),
                "procedures_called_from_code": len(called_procs),
                "procedures_not_called_from_code": len(all_procs) - len(called_procs),
                "connection_strings": len(app["connection_strings"]),
                "technologies": [t["name"] for t in app["technologies"]],
            },
        }

    def _progress(self, discovery_id: str, pct: int, step: str, message: str) -> None:
        if self._tracker is not None:
            self._tracker.update(discovery_id, pct, step, message)


# --- Markdown report ---------------------------------------------------------------------------


def _md_escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    s = report["summary"]
    lines: list[str] = []
    add = lines.append
    add(f"# Database Discovery: SQL Server → {report['target']}")
    add("")
    add(f"- Discovery ID: `{report['discovery_id']}`")
    source = report.get("source_url") or "uploaded archive"
    if report.get("subpath"):
        source += f" (path `{report['subpath']}`)"
    if report.get("branch"):
        source += f", branch `{report['branch']}`"
    add(f"- Source: {source}")
    add(f"- Completed: {report['completed_at']}")
    add(
        f"- Files scanned: {s['files_scanned']['sql']} SQL, {s['files_scanned']['application']} application; "
        f"{s['total_sql_lines']:,} lines of T-SQL"
    )
    add(
        f"- sqlglot T-SQL parse coverage: {s['ast_parse']['parsed']} of {s['ast_parse']['batches']} batches"
    )
    add("")
    add("## Summary")
    add("")
    add("| Severity | Findings |")
    add("|---|---|")
    for k, v in s["finding_counts"].items():
        add(f"| {k} | {v} |")
    add("")
    add("| Effort | Objects |")
    add("|---|---|")
    for k, v in s["effort_counts"].items():
        add(f"| {k} | {v} |")
    add("")
    add(f"Effort formula: {s['effort_formula']}.")
    add("")
    add("### Objects by type")
    add("")
    add("| Type | Count |")
    add("|---|---|")
    for k, v in s["object_counts"].items():
        add(f"| {k} | {v} |")
    add("")
    ld = s["logic_distribution"]
    add("### Where the logic lives")
    add("")
    add(
        f"- {ld['database_modules']} procedures, functions and triggers with "
        f"{ld['database_module_lines']:,} lines of T-SQL"
    )
    add(
        f"- {ld['embedded_sql_statements']} SQL statements embedded in application code "
        f"({ld['dynamic_embedded_sql_statements']} built dynamically)"
    )
    a = s["application"]
    add(
        f"- {a['procedures_called_from_code']} procedures are called from the scanned application code; "
        f"{a['procedures_not_called_from_code']} are not (called by other procedures, jobs, or code not scanned)"
    )
    add("")
    add("## Top rules")
    add("")
    add("| Rule | Severity | Category | Objects | Occurrences |")
    add("|---|---|---|---|---|")
    for r in s["top_rules"]:
        add(
            f"| {_md_escape(r['title'])} (`{r['rule_id']}`) | {r['severity']} | {r['category']} | {r['objects']} | {r['occurrences']} |"
        )
    add("")
    add("## Objects needing manual work (high / very high effort)")
    add("")
    add("| Object | Type | LOC | Blocker | High | Medium | Effort |")
    add("|---|---|---|---|---|---|---|")
    manual = sorted(
        (o for o in report["objects"] if o["effort"] in ("high", "very_high")),
        key=lambda o: -o["effort_points"],
    )
    for o in manual[:200]:
        fc = o["finding_counts"]
        add(
            f"| `{_md_escape(o['schema'] + '.' if o['schema'] else '')}{_md_escape(o['name'])}` | {o['type']} | "
            f"{o['lines_of_code']} | {fc['blocker']} | {fc['high']} | {fc['medium']} | {o['effort']} |"
        )
    if len(manual) > 200:
        add(f"| … {len(manual) - 200} more | | | | | | |")
    add("")
    add("## Type mapping")
    add("")
    add("| SQL Server | MySQL 8.0 | Columns | Note |")
    add("|---|---|---|---|")
    for t in report["type_mappings"]:
        add(
            f"| {t['sql_server_type']} | {t['mysql_type']} | {t['column_count']} | {_md_escape(t['note'])} |"
        )
    add("")
    app = report["application"]
    add("## Application")
    add("")
    if app["technologies"]:
        add(
            "Data-access technologies: "
            + ", ".join(
                f"{t['name']} ({t['files']} files)" for t in app["technologies"]
            )
        )
        add("")
    if app["packages"]:
        add("| Package | Version | Suggested replacement |")
        add("|---|---|---|")
        for p in app["packages"]:
            add(f"| {p['name']} | {p['version']} | {_md_escape(p['replacement'])} |")
        add("")
    if app["connection_strings"]:
        add("| File | Name | Database | Windows auth | MARS | Password in config |")
        add("|---|---|---|---|---|---|")
        for c in app["connection_strings"]:
            add(
                f"| {c['file']}:{c['line']} | {c['name']} | {c['database']} | {c['windows_auth']} | "
                f"{c['mars']} | {c['password_present']} |"
            )
        add("")
    if app["stored_procedure_calls"]:
        add("| Procedure call | Location | Resolved |")
        add("|---|---|---|")
        for c in app["stored_procedure_calls"]:
            add(f"| `{c['procedure']}` | {c['file']}:{c['line']} | {c['resolved']} |")
        add("")
    add("## Findings")
    add("")
    add("| Severity | Rule | Object / file | Line | Occurrences | Recommendation |")
    add("|---|---|---|---|---|---|")
    order = {k: i for i, k in enumerate(SEVERITY_ORDER)}
    for f in sorted(
        report["findings"],
        key=lambda f: (order[f["severity"]], f["rule_id"], f["file"]),
    ):
        if f["severity"] == "info":
            continue
        add(
            f"| {f['severity']} | {_md_escape(f['title'])} | {_md_escape(f['object_id'])} | "
            f"{f['file']}:{f['line']} | {f['occurrences']} | {_md_escape(f['recommendation'])} |"
        )
    add("")
    return "\n".join(lines)
