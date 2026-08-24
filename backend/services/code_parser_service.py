"""Code parser service — orchestrates the full analysis pipeline.

Phase 1: Deterministic (parse → file stats → dependencies → diagrams)
Phase 2: AI enrichment via Bedrock (documentation, summary)

Stores parsed_files as list[dict] via dataclasses.asdict().
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import asdict
from pathlib import Path

from config import settings
from parsers.base_parser import ParseResult
from parsers.parser_manager import ParserManager
from services.dependency_analyzer import DependencyAnalyzer
from services.diagram_generator import DiagramGenerator
from services.enhanced_dependency_analyzer import (
    DependencyVulnerabilityResult,
    EnhancedDependencyAnalyzer,
)
from services.file_analyzer import FileAnalyzer
from services.prompt_loader import describe_prompt_degradation, load_prompt_result
from services.version_analyzer import VersionAnalyzer
from state import app_state
from utils.bedrock import (
    BedrockUnavailableError,
    bedrock_failure_for,
    bedrock_runtime_client,
    invoke_with_retry,
)

logger = logging.getLogger(__name__)


class CodeParserService:
    """Orchestrates the full analysis pipeline: parse → analyze → diagrams → store."""

    def __init__(self) -> None:
        self._parser_manager = ParserManager()
        self._file_analyzer = FileAnalyzer()
        self._dependency_analyzer = DependencyAnalyzer()
        self._vulnerability_analyzer = EnhancedDependencyAnalyzer()
        self._version_analyzer = VersionAnalyzer()
        self._diagram_generator = DiagramGenerator()

    def analyze_zip(self, zip_path: str, analysis_id: str) -> None:
        """Run the full pipeline on an uploaded ZIP file.

        Args:
            zip_path: Path to the uploaded ZIP file.
            analysis_id: Unique ID for this analysis.
        """
        extract_dir = tempfile.mkdtemp(prefix="analysis_")
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            self._run_pipeline(extract_dir, analysis_id, "upload")
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
            # Clean up the uploaded ZIP.
            if os.path.exists(zip_path):
                os.unlink(zip_path)

    def analyze_directory(
        self,
        dir_path: str,
        analysis_id: str,
        source_type: str = "github",
        source_url: str = "",
    ) -> None:
        """Run the full pipeline on a directory (e.g., cloned repo).

        Args:
            dir_path: Path to the directory to analyze.
            analysis_id: Unique ID for this analysis.
            source_type: Source type (github, upload).
            source_url: Source URL (for GitHub repos).
        """
        self._run_pipeline(dir_path, analysis_id, source_type, source_url=source_url)

    def _run_pipeline(
        self,
        root_path: str,
        analysis_id: str,
        source_type: str,
        source_url: str = "",
    ) -> None:
        """Execute the full analysis pipeline.

        Phase 1: Deterministic analysis.
        Phase 2: AI enrichment (optional, graceful failure).
        """
        tracker = app_state.progress_tracker
        storage = app_state.storage_manager

        if not tracker or not storage:
            logger.error("AppState not initialized (tracker or storage missing)")
            return

        try:
            # Phase 1: Deterministic Analysis
            tracker.update(analysis_id, 10, "parsing", "Parsing source files...")

            # Parse files
            parsed_files = self._parse_files(root_path)

            tracker.update(
                analysis_id, 30, "file_analysis", "Analyzing file structure..."
            )

            # File stats and folder structure
            file_stats, folder_structure = self._file_analyzer.analyze(root_path)

            tracker.update(
                analysis_id, 50, "dependencies", "Extracting dependencies..."
            )

            # Dependency analysis
            deps = self._dependency_analyzer.analyze(root_path)
            deps_as_dicts = [
                {
                    "name": d.name,
                    "version": d.version,
                    "ecosystem": d.ecosystem,
                    "source_file": d.source_file,
                }
                for d in deps
            ]

            # Build dependency graph
            dependency_graph = self._build_dependency_graph(deps_as_dicts)

            tracker.update(
                analysis_id,
                60,
                "vulnerabilities",
                "Scanning OSV for known vulnerabilities...",
            )

            # Vulnerability scan — grounds upgrade recommendations in advisory data.
            vuln_results = self._scan_vulnerabilities(deps_as_dicts)

            tracker.update(analysis_id, 65, "versions", "Analyzing version upgrades...")

            # Version analysis
            upgrade_recs = self._version_analyzer.analyze(deps_as_dicts, vuln_results)

            tracker.update(analysis_id, 75, "diagrams", "Generating diagrams...")

            # Diagram generation — convert dicts back to ParseResult for MermaidParser
            parse_results = self._dicts_to_parse_results(parsed_files)
            diagram_set = self._diagram_generator.generate(parse_results)

            tracker.update(analysis_id, 85, "storing", "Storing results...")

            # Assemble result
            result = {
                "analysis_id": analysis_id,
                "source_type": source_type,
                "source_url": source_url,
                "file_stats": [
                    {
                        "extension": s.extension,
                        "count": s.count,
                        "total_lines": s.total_lines,
                        "total_size": s.total_size,
                    }
                    for s in file_stats
                ],
                "folder_structure": self._folder_node_to_dict(folder_structure),
                "dependencies": deps_as_dicts,
                "dependency_graph": dependency_graph,
                "upgrade_recommendations": [
                    {
                        "name": r.name,
                        "current_version": r.current_version,
                        "current_version_note": r.current_version_note,
                        "recommended_version": r.recommended_version,
                        "ecosystem": r.ecosystem,
                        "reason": r.reason,
                    }
                    for r in upgrade_recs
                ],
                "diagrams": {
                    "class_diagram": diagram_set.class_diagram,
                    "sequence_diagram": diagram_set.sequence_diagram,
                    "integration_diagram": diagram_set.integration_diagram,
                },
                "parsed_files": parsed_files,
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

            # Save Phase 1 results
            storage.save(analysis_id, result)

            # Phase 2: AI Enrichment (optional)
            tracker.update(analysis_id, 90, "ai_enrichment", "Running AI enrichment...")
            self._run_ai_enrichment(analysis_id, result)

            tracker.complete(analysis_id)

        except Exception as exc:
            logger.exception("Pipeline failed for %s: %s", analysis_id, exc)
            tracker.fail(analysis_id, str(exc))

    def _parse_files(self, root_path: str) -> list[dict]:
        """Parse all supported source files in the directory tree.

        Returns:
            List of ParseResult serialized as dicts via dataclasses.asdict().
        """
        parsed: list[dict] = []

        for dirpath, _dirnames, filenames in os.walk(root_path):
            # Skip hidden/build directories.
            if any(
                part.startswith(".")
                or part in ("node_modules", "__pycache__", "target", "build", "dist")
                for part in Path(dirpath).parts
            ):
                continue

            for filename in filenames:
                if not self._parser_manager.supports_file(filename):
                    continue

                file_path = os.path.join(dirpath, filename)
                try:
                    with open(file_path, encoding="utf-8", errors="ignore") as f:
                        source = f.read()

                    result = self._parser_manager.parse_file(source, filename)
                    if result:
                        parsed.append(asdict(result))
                except (OSError, UnicodeDecodeError) as exc:
                    logger.debug("Skipping %s: %s", file_path, exc)

        return parsed

    def _scan_vulnerabilities(
        self, deps: list[dict]
    ) -> list[DependencyVulnerabilityResult]:
        """Query OSV for advisories, degrading to an empty result on failure.

        A CVE with a published fixed version is the strongest upgrade recommendation
        available, but the scan reaches an external API — if it is unreachable the
        analysis still completes using the curated rules alone.
        """
        if not settings.VULN_SCAN_ENABLED:
            return []
        try:
            return self._vulnerability_analyzer.scan(deps)
        except Exception as exc:
            logger.warning("Vulnerability scan unavailable: %s", exc)
            return []

    def _build_dependency_graph(self, deps: list[dict]) -> dict:
        """Build a dependency graph with nodes and links.

        Uses "links" (backend convention; frontend maps to "edges").
        """
        nodes: list[dict] = []
        links: list[dict] = []
        node_ids: set[str] = set()

        for dep in deps:
            node_id = dep["name"]
            if node_id not in node_ids:
                node_ids.add(node_id)
                nodes.append(
                    {
                        "id": node_id,
                        "label": node_id.split(":")[-1] if ":" in node_id else node_id,
                        "type": dep["ecosystem"],
                        "metadata": {"version": dep["version"]},
                    }
                )

            # Create links from source file to dependency.
            source_node = dep.get("source_file", "root")
            if source_node not in node_ids:
                node_ids.add(source_node)
                nodes.append(
                    {
                        "id": source_node,
                        "label": source_node,
                        "type": "source",
                        "metadata": {},
                    }
                )

            links.append(
                {
                    "source": source_node,
                    "target": node_id,
                    "type": "depends_on",
                }
            )

        return {"nodes": nodes, "links": links}

    def _folder_node_to_dict(self, node: object) -> dict:
        """Convert FolderNode dataclass to dict."""
        from services.file_analyzer import FolderNode

        if not isinstance(node, FolderNode):
            return {"name": "unknown", "type": "directory", "children": []}

        result: dict = {"name": node.name, "type": node.type}
        if node.type == "directory":
            result["children"] = [
                self._folder_node_to_dict(child) for child in node.children
            ]
        if node.size is not None:
            result["size"] = node.size
        return result

    def _dicts_to_parse_results(self, parsed_dicts: list[dict]) -> list[ParseResult]:
        """Convert serialized parse results back to ParseResult dataclass instances."""
        from parsers.base_parser import ClassInfo, MethodInfo

        results: list[ParseResult] = []
        for d in parsed_dicts:
            classes = [
                ClassInfo(
                    name=c.get("name", ""),
                    line_number=c.get("line_number", 0),
                    methods=c.get("methods", []),
                    parent_classes=c.get("parent_classes", []),
                )
                for c in d.get("classes", [])
            ]
            methods = [
                MethodInfo(
                    name=m.get("name", ""),
                    line_number=m.get("line_number", 0),
                    parameters=m.get("parameters", []),
                    return_type=m.get("return_type"),
                    class_name=m.get("class_name"),
                )
                for m in d.get("methods", [])
            ]
            results.append(
                ParseResult(
                    classes=classes,
                    methods=methods,
                    imports=d.get("imports", []),
                    complexity=d.get("complexity", 0),
                    language=d.get("language", ""),
                    line_count=d.get("line_count", 0),
                )
            )
        return results

    def _run_ai_enrichment(self, analysis_id: str, result: dict) -> None:
        """Phase 2: AI enrichment via Bedrock (optional, never blocks analysis).

        Status follows the design's enrichment semantics, which distinguish
        outcomes that need different operator responses:

        - `completed` — prompts carried real templates and real context
        - `degraded`  — the model answered, but without the analysis context
        - `skipped`   — enrichment was not attempted: deliberately disabled, or
                        Bedrock unavailable (no credentials, no region, endpoint
                        unreachable)
        - `failed`    — enrichment was attempted and an exception was raised

        `ai_enrichment_error` names the cause and the action it implies, because
        a read timeout, a denied model and an absent credential are three
        different fixes. Whatever happens here, the deterministic phase-1
        results are already stored and are never removed.
        """
        storage = app_state.storage_manager
        if not storage:
            return

        if settings.SKIP_AI_ENRICHMENT:
            logger.info(
                "AI enrichment skipped for %s: disabled via SKIP_AI_ENRICHMENT",
                analysis_id,
            )
            result["ai_enrichment_status"] = "skipped"
            result["ai_enrichment_error"] = (
                "AI enrichment was deliberately not attempted "
                "(SKIP_AI_ENRICHMENT is enabled)."
            )
            storage.save(analysis_id, result)
            return

        # Names the step in the failure message, so an operator knows which of
        # the two model calls failed.
        stage = "AI enrichment"

        try:
            # Prepare context for AI prompts.
            context_vars = {
                "name": analysis_id,
                "file_stats": json.dumps(result.get("file_stats", [])[:20], indent=2),
                "dependencies": json.dumps(
                    result.get("dependencies", [])[:30], indent=2
                ),
                "upgrade_recommendations": json.dumps(
                    result.get("upgrade_recommendations", [])[:30], indent=2
                ),
                "folder_structure": json.dumps(
                    result.get("folder_structure", {}), indent=2
                )[:5000],
                "diagrams": result.get("diagrams", {}).get("class_diagram", "")[:3000],
                "source_url": result.get("source_url", "uploaded archive"),
                "framework": result.get("framework", "detected"),
                "target_framework": result.get("target_framework", ""),
            }

            # Load prompts.
            summary_load = load_prompt_result("analysis-summary", context_vars)
            doc_load = load_prompt_result("documentation-generation", context_vars)

            # Each result is recorded as soon as it arrives: if the second call
            # fails, the first one's output has already been generated and paid
            # for, and discarding it would lose real content for no reason.
            stage = "AI summary generation"
            result["ai_summary"] = self._invoke_bedrock(
                summary_load.content, context_vars
            )
            stage = "AI documentation generation"
            result["ai_documentation"] = self._invoke_bedrock(
                doc_load.content, context_vars
            )

            degradation = describe_prompt_degradation(summary_load, doc_load)
            if degradation:
                # The model answered, but it never saw the analysis context —
                # do not present that output as real documentation.
                logger.warning(
                    "AI enrichment degraded for %s: %s", analysis_id, degradation
                )
                result["ai_enrichment_status"] = "degraded"
                result["ai_enrichment_error"] = degradation
            else:
                result["ai_enrichment_status"] = "completed"
                result.pop("ai_enrichment_error", None)
            storage.save(analysis_id, result)

        except BedrockUnavailableError as exc:
            # Bedrock could not be called at all — nothing was attempted, so
            # this is "skipped", per the enrichment status table.
            logger.warning(
                "AI enrichment skipped for %s (Bedrock unavailable): %s",
                analysis_id,
                exc,
            )
            result["ai_enrichment_status"] = "skipped"
            result["ai_enrichment_error"] = str(exc)
            storage.save(analysis_id, result)

        except Exception as exc:
            # An exception was raised during enrichment: that is "failed", not
            # "skipped". Reporting it as skipped made a real Bedrock timeout
            # look to the user like the AI step had simply not been run.
            failure = bedrock_failure_for(exc)
            if failure.unavailable:
                message = failure.message
                status = "skipped"
            else:
                message = f"{stage} failed — {failure.message}"
                status = "failed"

            logger.warning(
                "AI enrichment %s for %s [%s]: %s",
                status,
                analysis_id,
                failure.kind,
                message,
            )
            result["ai_enrichment_status"] = status
            result["ai_enrichment_error"] = message
            # Never blocks analysis completion; deterministic results persist.
            storage.save(analysis_id, result)

    def _invoke_bedrock(self, prompt: str, context: dict) -> str:
        """Invoke AWS Bedrock Claude for text generation.

        The client carries an explicit read timeout (botocore's 60s default is
        shorter than a real 4096-token documentation generation) and retries are
        applied only where a retry can help.

        Returns:
            The generated text.

        Raises:
            BedrockUnavailableError: Bedrock could not be called at all.
            BedrockCallError: The call was attempted and failed.
        """
        client = bedrock_runtime_client()

        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "messages": [
                    {"role": "user", "content": prompt},
                ],
            }
        )

        def _call() -> str:
            response = client.invoke_model(
                modelId=settings.BEDROCK_MODEL_ID,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            response_body = json.loads(response["body"].read())
            return response_body.get("content", [{}])[0].get("text", "")

        return invoke_with_retry(_call, description="text generation")
