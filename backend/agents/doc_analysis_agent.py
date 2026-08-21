"""Documentation analysis agent.

Uses Bedrock Claude to generate comprehensive documentation and summaries
from code analysis results. Yields SSE events during generation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

from botocore.exceptions import BotoCoreError, ClientError
from starlette.concurrency import run_in_threadpool

from agents.prompt_loader import load_prompt, load_prompt_result
from config import settings
from services.prompt_loader import describe_prompt_degradation
from utils.bedrock import (
    bedrock_failure_for,
    bedrock_runtime_client,
    invoke_with_retry,
)
from utils.storage_manager import StorageManager

logger = logging.getLogger(__name__)


class DocAnalysisAgent:
    """Agent for generating documentation from code analysis results.

    Tools:
    - analyze_codebase_context: Load and summarize analysis data
    - generate_kiro_spec: Generate a Kiro spec for a specific file
    - validate_analysis_output: Validate the completeness of an analysis
    """

    def __init__(self, storage: StorageManager) -> None:
        self.storage = storage
        self._client: Any | None = None

    @property
    def _bedrock_client(self) -> Any:
        """Lazy-initialize Bedrock runtime client with explicit timeouts.

        Uses the shared factory so this path gets the same explicit read timeout
        as pipeline enrichment: botocore's 60s default is shorter than a
        long-form documentation generation actually takes.
        """
        if self._client is None:
            self._client = bedrock_runtime_client()
        return self._client

    @staticmethod
    def _prompt_variables(analysis_id: str, data: dict[str, Any]) -> dict[str, str]:
        """Build the template variables the prompt templates expect.

        Every placeholder used by the documentation/summary templates is
        supplied here, so the model always receives the analysis context.
        """
        return {
            "name": data.get("analysis_id", analysis_id),
            "file_stats": json.dumps(data.get("file_stats", []))[:4000],
            "dependencies": json.dumps(data.get("dependencies", []))[:4000],
            "upgrade_recommendations": json.dumps(
                data.get("upgrade_recommendations", [])
            )[:4000],
            "folder_structure": json.dumps(data.get("folder_structure", {}))[:4000],
            "diagrams": json.dumps(data.get("diagrams", {}))[:2000],
            "source_url": data.get("source_url", "N/A"),
            "framework": data.get("framework", "detected"),
            "target_framework": data.get("target_framework", ""),
        }

    def _invoke_model(self, prompt: str, max_tokens: int = 4096) -> str:
        """Invoke Bedrock Claude model.

        Args:
            prompt: The full prompt to send.
            max_tokens: Maximum tokens in response.

        Returns:
            Generated text from the model.

        Raises:
            RuntimeError: If Bedrock is unavailable.
        """
        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            }
        )

        def _call() -> str:
            response = self._bedrock_client.invoke_model(
                modelId=settings.BEDROCK_MODEL_ID,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
            result = json.loads(response["body"].read())
            return result["content"][0]["text"]

        return invoke_with_retry(_call, description="text generation")

    # --- Tool: analyze_codebase_context ---

    def analyze_codebase_context(self, analysis_id: str) -> dict[str, Any]:
        """Load and summarize analysis data for documentation generation.

        Args:
            analysis_id: The analysis to load context for.

        Returns:
            Dict with context summary (file stats, deps, folder structure, diagrams).
        """
        data = self.storage.load(analysis_id)
        if data is None:
            return {"error": f"Analysis {analysis_id} not found"}

        file_stats = data.get("file_stats", [])
        dependencies = data.get("dependencies", [])
        parsed_files = data.get("parsed_files", [])

        # Summarize parsed files
        total_classes = 0
        total_methods = 0
        languages: set[str] = set()
        for pf in parsed_files:
            if isinstance(pf, dict):
                total_classes += len(pf.get("classes", []))
                total_methods += len(pf.get("methods", []))
                lang = pf.get("language", "")
                if lang:
                    languages.add(lang)

        return {
            "analysis_id": analysis_id,
            "total_files": sum(s.get("count", 0) for s in file_stats),
            "total_lines": sum(s.get("total_lines", 0) for s in file_stats),
            "total_dependencies": len(dependencies),
            "total_classes": total_classes,
            "total_methods": total_methods,
            "languages": sorted(languages),
            "has_diagrams": bool(data.get("diagrams")),
            "source_type": data.get("source_type", "unknown"),
            "source_url": data.get("source_url"),
        }

    # --- Tool: generate_kiro_spec ---

    def generate_kiro_spec(self, analysis_id: str, file_path: str) -> dict[str, Any]:
        """Generate a Kiro-style spec for a specific file.

        Args:
            analysis_id: The analysis containing the file.
            file_path: Path to the file within the analysis.

        Returns:
            Dict with the generated spec or error.
        """
        data = self.storage.load(analysis_id)
        if data is None:
            return {"error": f"Analysis {analysis_id} not found"}

        # Find the file in parsed_files
        parsed_files = data.get("parsed_files", [])
        target_file: dict[str, Any] | None = None
        for pf in parsed_files:
            if isinstance(pf, dict) and pf.get("filename") == file_path:
                target_file = pf
                break

        if target_file is None:
            return {"error": f"File {file_path} not found in analysis"}

        try:
            prompt = load_prompt(
                "kiro-spec-generation",
                variables={
                    "file_path": file_path,
                    "file_content": json.dumps(target_file, indent=2)[:8000],
                },
            )
            result = self._invoke_model(prompt)
            return {"spec": result, "file_path": file_path}
        except (BotoCoreError, ClientError, RuntimeError) as e:
            logger.warning("Bedrock unavailable for Kiro spec generation: %s", e)
            return {"error": "Bedrock unavailable", "file_path": file_path}

    # --- Tool: validate_analysis_output ---

    def validate_analysis_output(self, analysis_id: str) -> dict[str, Any]:
        """Validate the completeness of analysis output.

        Args:
            analysis_id: The analysis to validate.

        Returns:
            Dict with validation results (valid, missing fields, warnings).
        """
        data = self.storage.load(analysis_id)
        if data is None:
            return {"valid": False, "error": f"Analysis {analysis_id} not found"}

        required_fields = [
            "analysis_id",
            "source_type",
            "file_stats",
            "folder_structure",
            "dependencies",
        ]
        optional_fields = [
            "dependency_graph",
            "upgrade_recommendations",
            "diagrams",
            "ai_summary",
            "ai_documentation",
        ]

        missing: list[str] = []
        warnings: list[str] = []

        for field in required_fields:
            if field not in data or data[field] is None:
                missing.append(field)

        for field in optional_fields:
            if field not in data or data[field] is None:
                warnings.append(f"Optional field '{field}' not populated")

        # Check data quality
        file_stats = data.get("file_stats", [])
        if isinstance(file_stats, list) and len(file_stats) == 0:
            warnings.append("file_stats is empty — no files were parsed")

        return {
            "valid": len(missing) == 0,
            "missing_fields": missing,
            "warnings": warnings,
            "analysis_id": analysis_id,
        }

    # --- SSE Streaming Methods ---

    async def generate_documentation(
        self, analysis_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Generate documentation via Bedrock, yielding SSE events.

        This has to stay an async generator — it must keep yielding SSE events
        to the client — so it cannot be declared `def` to get FastAPI's
        threadpool. The blocking steps (the Bedrock call and the storage reads
        and writes) are handed to a worker thread explicitly instead; a
        synchronous Bedrock call here would block the loop between yields just
        as badly as a blocking background task.

        Yields:
            SSE event dicts with type, content/message fields.
        """
        yield {
            "type": "progress",
            "message": "Loading analysis context...",
            "percentage": 10,
        }

        context = await run_in_threadpool(self.analyze_codebase_context, analysis_id)
        if "error" in context:
            yield {"type": "error", "message": context["error"]}
            return

        yield {
            "type": "progress",
            "message": "Preparing documentation prompt...",
            "percentage": 30,
        }

        # Build the context for the prompt
        data = await run_in_threadpool(self.storage.load, analysis_id)
        if data is None:
            yield {"type": "error", "message": f"Analysis {analysis_id} not found"}
            return

        variables = self._prompt_variables(analysis_id, data)

        doc_load = load_prompt_result("documentation-generation", variables=variables)
        degradation = describe_prompt_degradation(doc_load)

        yield {
            "type": "progress",
            "message": "Generating documentation with AI...",
            "percentage": 50,
        }

        try:
            documentation = await run_in_threadpool(
                self._invoke_model, doc_load.content, 8192
            )
            yield {"type": "content", "text": documentation}
            yield {
                "type": "progress",
                "message": "Documentation generated",
                "percentage": 90,
            }

            # Store the result
            data["ai_documentation"] = documentation
            if degradation:
                logger.warning(
                    "Documentation generated without context for %s: %s",
                    analysis_id,
                    degradation,
                )
                data["ai_enrichment_status"] = "degraded"
                data["ai_enrichment_error"] = degradation
            else:
                data["ai_enrichment_status"] = "completed"
                data.pop("ai_enrichment_error", None)
            await run_in_threadpool(self.storage.save, analysis_id, data)

            yield {
                "type": "complete",
                "conversation_id": analysis_id,
                "status": "degraded" if degradation else "completed",
            }
        except (BotoCoreError, ClientError, RuntimeError) as e:
            # An exception during generation is `failed`; only a Bedrock that
            # could not be called at all is `skipped`. Collapsing the two made a
            # real timeout read as "the AI step did not run".
            failure = bedrock_failure_for(e)
            status = "skipped" if failure.unavailable else "failed"
            message = (
                failure.message
                if failure.unavailable
                else f"Documentation generation failed — {failure.message}"
            )
            logger.warning(
                "Documentation generation %s for %s [%s]: %s",
                status,
                analysis_id,
                failure.kind,
                message,
            )
            if data:
                data["ai_enrichment_status"] = status
                data["ai_enrichment_error"] = message
                await run_in_threadpool(self.storage.save, analysis_id, data)
            yield {"type": "error", "message": message}

    async def generate_summary(
        self, analysis_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Generate an executive summary via Bedrock, yielding SSE events.

        Async generator, so the blocking Bedrock call and storage I/O go to a
        worker thread rather than running on the event loop between yields.

        Yields:
            SSE event dicts with type, content/message fields.
        """
        yield {
            "type": "progress",
            "message": "Loading analysis data...",
            "percentage": 10,
        }

        data = await run_in_threadpool(self.storage.load, analysis_id)
        if data is None:
            yield {"type": "error", "message": f"Analysis {analysis_id} not found"}
            return

        yield {
            "type": "progress",
            "message": "Generating AI summary...",
            "percentage": 40,
        }

        variables = self._prompt_variables(analysis_id, data)

        summary_load = load_prompt_result("analysis-summary", variables=variables)
        degradation = describe_prompt_degradation(summary_load)

        try:
            summary = await run_in_threadpool(
                self._invoke_model, summary_load.content, 4096
            )
            yield {"type": "content", "text": summary}
            yield {"type": "progress", "message": "Summary generated", "percentage": 90}

            # Store
            data["ai_summary"] = summary
            if degradation:
                logger.warning(
                    "Summary generated without context for %s: %s",
                    analysis_id,
                    degradation,
                )
                data["ai_enrichment_status"] = "degraded"
                data["ai_enrichment_error"] = degradation
            elif data.get("ai_enrichment_status") != "completed":
                data["ai_enrichment_status"] = "completed"
                data.pop("ai_enrichment_error", None)
            await run_in_threadpool(self.storage.save, analysis_id, data)

            yield {
                "type": "complete",
                "conversation_id": analysis_id,
                "status": "degraded" if degradation else "completed",
            }
        except (BotoCoreError, ClientError, RuntimeError) as e:
            failure = bedrock_failure_for(e)
            message = (
                failure.message
                if failure.unavailable
                else f"Summary generation failed — {failure.message}"
            )
            logger.warning(
                "Summary generation failed for %s [%s]: %s",
                analysis_id,
                failure.kind,
                message,
            )
            yield {"type": "error", "message": message}

    async def enrich_analysis(self, analysis_id: str) -> dict[str, str]:
        """Run AI enrichment (both summary and documentation).

        Called as part of the analysis pipeline Phase 2.
        Returns status dict; does not stream (background task).

        Kept a coroutine rather than flipped to a plain `def`: it is an
        awaitable on a public agent class, so changing that would break any
        `await agent.enrich_analysis(...)` caller. Instead the two Bedrock
        calls and the storage writes are pushed to a worker thread, which makes
        it loop-safe whether it is awaited directly or handed to
        `BackgroundTasks`.
        """
        data = await run_in_threadpool(self.storage.load, analysis_id)
        if data is None:
            return {"status": "error", "message": f"Analysis {analysis_id} not found"}

        variables = self._prompt_variables(analysis_id, data)

        try:
            # Generate summary
            summary_load = load_prompt_result("analysis-summary", variables=variables)
            ai_summary = await run_in_threadpool(
                self._invoke_model, summary_load.content, 4096
            )
            data["ai_summary"] = ai_summary

            # Generate documentation
            doc_load = load_prompt_result(
                "documentation-generation", variables=variables
            )
            ai_documentation = await run_in_threadpool(
                self._invoke_model, doc_load.content, 8192
            )
            data["ai_documentation"] = ai_documentation

            degradation = describe_prompt_degradation(summary_load, doc_load)
            if degradation:
                logger.warning(
                    "AI enrichment degraded for %s: %s", analysis_id, degradation
                )
                data["ai_enrichment_status"] = "degraded"
                data["ai_enrichment_error"] = degradation
                await run_in_threadpool(self.storage.save, analysis_id, data)
                return {"status": "degraded", "message": degradation}

            data["ai_enrichment_status"] = "completed"
            data.pop("ai_enrichment_error", None)
            await run_in_threadpool(self.storage.save, analysis_id, data)
            return {"status": "completed"}

        except (BotoCoreError, ClientError, RuntimeError) as e:
            failure = bedrock_failure_for(e)
            status = "skipped" if failure.unavailable else "failed"
            logger.warning(
                "AI enrichment %s for %s [%s]: %s",
                status,
                analysis_id,
                failure.kind,
                failure.message,
            )
            data["ai_enrichment_status"] = status
            data["ai_enrichment_error"] = failure.message
            await run_in_threadpool(self.storage.save, analysis_id, data)
            return {"status": status, "message": failure.message}
