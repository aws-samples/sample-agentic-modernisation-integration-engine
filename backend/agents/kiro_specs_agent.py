"""Kiro Specifications agent.

Generates Kiro-style specifications (requirements, design, tasks)
from code analysis results. Uses in-process MCP access to analysis data.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

from botocore.exceptions import BotoCoreError, ClientError
from starlette.concurrency import run_in_threadpool

from agents.prompt_loader import load_prompt
from config import settings
from utils.bedrock import (
    bedrock_failure_for,
    bedrock_runtime_client,
    invoke_with_retry,
)
from utils.storage_manager import StorageManager

logger = logging.getLogger(__name__)


class KiroSpecsAgent:
    """Agent for generating Kiro-style specifications.

    Tools:
    - get_analysis_context: Retrieve analysis data for spec generation
    - generate_component_spec: Generate spec for a specific component
    - validate_specs: Validate generated specifications
    """

    def __init__(self, storage: StorageManager) -> None:
        self.storage = storage
        self._client: Any | None = None

    @property
    def _bedrock_client(self) -> Any:
        """Lazy-initialize Bedrock runtime client with explicit timeouts.

        Uses the shared factory. This is the more exposed of the two agents that
        were still on a bare `boto3.client`: `generate_specs_streaming` asks for
        8192 output tokens, twice the 4096 that made `documentation-generation`
        take 75.1s against a real analysis context. A full spec generation
        plausibly exceeds botocore's 60s default read timeout outright, so this
        path was not "probably fine" — it was one large repository away from the
        same silent-retry failure, with botocore's default retries re-running an
        under-timed call that could never finish.
        """
        if self._client is None:
            self._client = bedrock_runtime_client()
        return self._client

    def _invoke_model(self, prompt: str, max_tokens: int = 4096) -> str:
        """Invoke Bedrock Claude model.

        Synchronous by design: `invoke_with_retry` wraps the request *and* its
        backoff sleeps, so async callers hand this whole method to a worker
        thread. A retry loop on the event loop around a threadpooled call would
        move the sleeps back onto the loop.

        Args:
            prompt: The full prompt to send.
            max_tokens: Maximum tokens in response.

        Returns:
            Generated text.

        Raises:
            BedrockUnavailableError: Bedrock could not be called at all.
            BedrockCallError: The call was attempted and failed after retries;
                the attached failure names the cause and the operator action.
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

        return invoke_with_retry(_call, description="specification generation")

    # --- Tool: get_analysis_context ---

    def get_analysis_context(self, analysis_id: str) -> dict[str, Any]:
        """Retrieve analysis data for spec generation via in-process MCP.

        Args:
            analysis_id: The analysis to retrieve context for.

        Returns:
            Dict with analysis context (file stats, deps, structure, etc.).
        """
        data = self.storage.load(analysis_id)
        if data is None:
            return {"error": f"Analysis {analysis_id} not found"}

        # Build context suitable for spec generation
        file_stats = data.get("file_stats", [])
        dependencies = data.get("dependencies", [])
        folder_structure = data.get("folder_structure", {})
        parsed_files = data.get("parsed_files", [])

        # Extract key components from parsed files
        components: list[dict[str, Any]] = []
        for pf in parsed_files:
            if isinstance(pf, dict):
                classes = pf.get("classes", [])
                for cls in classes:
                    if isinstance(cls, dict):
                        components.append(
                            {
                                "name": cls.get("name", ""),
                                "type": "class",
                                "file": pf.get("filename", ""),
                                "methods": cls.get("methods", []),
                                "parents": cls.get("parent_classes", []),
                            }
                        )

        return {
            "analysis_id": analysis_id,
            "source_type": data.get("source_type", "unknown"),
            "source_url": data.get("source_url"),
            "file_stats": file_stats,
            "dependency_count": len(dependencies),
            "component_count": len(components),
            "components": components[:50],  # Limit for context window
            "languages": list(
                {s.get("extension", "") for s in file_stats if isinstance(s, dict)}
            ),
            "folder_structure": folder_structure,
        }

    # --- Tool: generate_component_spec ---

    def generate_component_spec(
        self, analysis_id: str, file_path: str
    ) -> dict[str, Any]:
        """Generate a Kiro spec for a specific component/file.

        Args:
            analysis_id: The analysis containing the component.
            file_path: Path to the target file.

        Returns:
            Dict with requirements, design, and tasks sections.
        """
        data = self.storage.load(analysis_id)
        if data is None:
            return {"error": f"Analysis {analysis_id} not found"}

        # Find the parsed file
        parsed_files = data.get("parsed_files", [])
        target: dict[str, Any] | None = None
        for pf in parsed_files:
            if isinstance(pf, dict) and pf.get("filename") == file_path:
                target = pf
                break

        if target is None:
            return {"error": f"File {file_path} not found in analysis"}

        try:
            prompt = load_prompt(
                "kiro-spec-generation",
                variables={
                    "file_path": file_path,
                    "file_content": json.dumps(target, indent=2)[:6000],
                    "dependencies": json.dumps(data.get("dependencies", [])[:20]),
                },
            )
            result = self._invoke_model(prompt, max_tokens=4096)

            # Parse sections from the generated spec
            spec = self._parse_spec_sections(result)
            spec["file_path"] = file_path
            return spec

        except (BotoCoreError, ClientError, RuntimeError) as e:
            failure = bedrock_failure_for(e)
            logger.warning(
                "Component spec generation failed for %s [%s]: %s",
                file_path,
                failure.kind,
                failure.message,
            )
            return {"error": failure.message, "file_path": file_path}

    # --- Tool: validate_specs ---

    def validate_specs(self, specs: dict[str, Any]) -> dict[str, Any]:
        """Validate generated specifications for completeness.

        Args:
            specs: Generated spec dict with requirements/design/tasks.

        Returns:
            Dict with valid (bool), issues found, and suggestions.
        """
        issues: list[str] = []
        suggestions: list[str] = []

        # Check required sections
        if "requirements" not in specs or not specs["requirements"]:
            issues.append("Missing 'requirements' section")
        if "design" not in specs or not specs["design"]:
            issues.append("Missing 'design' section")
        if "tasks" not in specs or not specs["tasks"]:
            issues.append("Missing 'tasks' section")

        # Validate requirements format
        requirements = specs.get("requirements", "")
        if isinstance(requirements, str):
            if "##" not in requirements:
                suggestions.append(
                    "Requirements should use ## headers for acceptance criteria"
                )
            if len(requirements) < 100:
                suggestions.append("Requirements section seems too short")

        # Validate design format
        design = specs.get("design", "")
        if isinstance(design, str):
            if len(design) < 100:
                suggestions.append("Design section seems too short")

        # Validate tasks format
        tasks = specs.get("tasks", "")
        if isinstance(tasks, str):
            if "- [" not in tasks and "1." not in tasks:
                suggestions.append(
                    "Tasks should use checkbox format (- [ ]) or numbered list"
                )

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "suggestions": suggestions,
        }

    # --- SSE Streaming ---

    async def generate_specs_streaming(
        self, analysis_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Generate full Kiro specs for an analysis, yielding SSE events.

        Async generator, so the blocking Bedrock call and the storage read are
        handed to a worker thread instead of running on the event loop between
        yields. What is threadpooled is `_invoke_model`, which already contains
        the retry wrapper, so the request and its backoff sleeps are both off
        the loop — not a retry loop here awaiting a threadpooled call, which
        would sleep on the loop between attempts.

        Yields:
            SSE event dicts with generation progress and results.
        """
        yield {
            "type": "progress",
            "message": "Loading analysis context...",
            "percentage": 10,
        }

        context = await run_in_threadpool(self.get_analysis_context, analysis_id)
        if "error" in context:
            yield {"type": "error", "message": context["error"]}
            return

        yield {
            "type": "progress",
            "message": "Generating specifications...",
            "percentage": 30,
        }

        # Build spec generation prompt
        variables = {
            "file_stats": json.dumps(context.get("file_stats", []))[:3000],
            "dependencies": json.dumps([{"count": context.get("dependency_count", 0)}]),
            "folder_structure": json.dumps(context.get("folder_structure", {}))[:3000],
            "source_url": context.get("source_url", "N/A"),
        }

        prompt = load_prompt("kiro-spec-generation", variables=variables)

        try:
            result = await run_in_threadpool(self._invoke_model, prompt, 8192)
            yield {"type": "content", "text": result}
            yield {
                "type": "progress",
                "message": "Specifications generated",
                "percentage": 90,
            }

            # Validate
            spec = self._parse_spec_sections(result)
            validation = self.validate_specs(spec)

            yield {
                "type": "tool_result",
                "tool": "validate_specs",
                "output": validation,
            }

            yield {
                "type": "complete",
                "conversation_id": analysis_id,
                "status": "completed",
            }
        except (BotoCoreError, ClientError, RuntimeError) as e:
            # A read timeout, a denied model and a missing credential all used
            # to arrive as "Bedrock unavailable", which named neither the cause
            # nor an action. The terminal `error` event now carries both.
            failure = bedrock_failure_for(e)
            message = (
                failure.message
                if failure.unavailable
                else f"Spec generation failed — {failure.message}"
            )
            logger.warning(
                "Spec generation failed for %s [%s]: %s",
                analysis_id,
                failure.kind,
                message,
            )
            yield {"type": "error", "message": message}

    @staticmethod
    def _parse_spec_sections(text: str) -> dict[str, str]:
        """Parse requirements/design/tasks sections from generated text."""
        sections: dict[str, str] = {
            "requirements": "",
            "design": "",
            "tasks": "",
        }

        current_section: str | None = None
        current_lines: list[str] = []

        for line in text.split("\n"):
            lower = line.lower().strip()
            if "requirement" in lower and line.startswith("#"):
                if current_section and current_lines:
                    sections[current_section] = "\n".join(current_lines).strip()
                current_section = "requirements"
                current_lines = [line]
            elif "design" in lower and line.startswith("#"):
                if current_section and current_lines:
                    sections[current_section] = "\n".join(current_lines).strip()
                current_section = "design"
                current_lines = [line]
            elif "task" in lower and line.startswith("#"):
                if current_section and current_lines:
                    sections[current_section] = "\n".join(current_lines).strip()
                current_section = "tasks"
                current_lines = [line]
            elif current_section:
                current_lines.append(line)

        # Capture the last section
        if current_section and current_lines:
            sections[current_section] = "\n".join(current_lines).strip()

        # If no sections found, put everything in requirements
        if not any(sections.values()):
            sections["requirements"] = text

        return sections
