"""LLM Judge agent for quality evaluation.

Scores generated text on 5 dimensions using Bedrock Claude:
accuracy, completeness, actionability, specificity, correctness.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

from botocore.exceptions import BotoCoreError, ClientError
from starlette.concurrency import run_in_threadpool

from config import settings
from utils.bedrock import (
    bedrock_failure_for,
    bedrock_runtime_client,
    invoke_with_retry,
)

logger = logging.getLogger(__name__)

# 5 scoring dimensions, each 0-10
SCORING_DIMENSIONS = [
    "accuracy",
    "completeness",
    "actionability",
    "specificity",
    "correctness",
]


class LLMJudge:
    """Quality evaluation agent using 5-dimension scoring.

    Tools:
    - score_dimension: Score text on a single dimension (0-10)
    - check_json_structure: Validate that text contains valid JSON
    - detect_hallucinations: Check for hallucinated content vs context
    """

    def __init__(self) -> None:
        self._client: Any | None = None

    @property
    def _bedrock_client(self) -> Any:
        """Lazy-initialize Bedrock runtime client with explicit timeouts.

        Uses the shared factory rather than a bare `boto3.client`, for the same
        reason documentation generation does: an SDK default timeout is not a
        budget for a long-running call. This path has not failed yet only
        because six scoring calls at 2048 tokens happen to land under
        botocore's 60s default — that is a property of how small today's output
        is, not of the configuration being right. The budget has to be stated
        explicitly so it does not silently become wrong when a prompt grows or
        the model slows.
        """
        if self._client is None:
            self._client = bedrock_runtime_client()
        return self._client

    def _invoke_model(self, prompt: str, max_tokens: int = 2048) -> str:
        """Invoke Bedrock Claude model.

        Synchronous by design: `invoke_with_retry` wraps the request *and* its
        backoff sleeps, so callers on the event loop hand this whole method to a
        worker thread. Inverting that — retrying around a threadpooled call —
        would put the sleeps back on the loop.

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
                "temperature": 0.1,  # Low temperature for consistent scoring
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

        return invoke_with_retry(_call, description="quality evaluation")

    # --- Tool: score_dimension ---

    def score_dimension(self, text: str, dimension: str) -> dict[str, Any]:
        """Score text on a single dimension.

        Args:
            text: The text to evaluate.
            dimension: One of the 5 scoring dimensions.

        Returns:
            Dict with score (0-10), dimension, and justification.
        """
        if dimension not in SCORING_DIMENSIONS:
            return {
                "error": f"Invalid dimension '{dimension}'. "
                f"Must be one of: {SCORING_DIMENSIONS}"
            }

        prompt = (
            f"Score the following text on the dimension '{dimension}' "
            f"on a scale of 0-10.\n\n"
            f"Text to evaluate:\n{text[:4000]}\n\n"
            f"Respond ONLY with a JSON object: "
            f'{{"score": <number>, "justification": "<brief explanation>"}}'
        )

        try:
            response = self._invoke_model(prompt, max_tokens=512)
            # Parse the JSON response
            parsed = self._extract_json(response)
            if parsed and "score" in parsed:
                score = max(0, min(10, int(parsed["score"])))
                return {
                    "dimension": dimension,
                    "score": score,
                    "justification": parsed.get("justification", ""),
                }
            return {"dimension": dimension, "score": 5, "justification": "Parse error"}
        except (BotoCoreError, ClientError, RuntimeError) as e:
            # "Bedrock unavailable" was the same string for a read timeout, a
            # denied model and an absent credential — three different operator
            # actions. Carry the classified cause instead.
            failure = bedrock_failure_for(e)
            logger.warning(
                "Scoring dimension %s failed [%s]: %s",
                dimension,
                failure.kind,
                failure.message,
            )
            return {
                "dimension": dimension,
                "score": 0,
                "error": failure.message,
            }

    # --- Tool: check_json_structure ---

    def check_json_structure(self, text: str) -> dict[str, Any]:
        """Validate that text contains valid JSON structure.

        Args:
            text: Text that should contain JSON.

        Returns:
            Dict with valid (bool), structure info, and any errors.
        """
        try:
            parsed = json.loads(text)
            structure: dict[str, Any] = {
                "valid": True,
                "type": type(parsed).__name__,
            }
            if isinstance(parsed, dict):
                structure["keys"] = list(parsed.keys())[:20]
                structure["key_count"] = len(parsed)
            elif isinstance(parsed, list):
                structure["length"] = len(parsed)
                if parsed:
                    structure["first_item_type"] = type(parsed[0]).__name__
            return structure
        except json.JSONDecodeError as e:
            return {
                "valid": False,
                "error": str(e),
                "error_position": e.pos,
            }

    # --- Tool: detect_hallucinations ---

    def detect_hallucinations(self, text: str, context: str) -> dict[str, Any]:
        """Check for hallucinated content in text vs provided context.

        Args:
            text: Generated text to check.
            context: Source context that the text should be grounded in.

        Returns:
            Dict with hallucination_detected (bool), confidence, and details.
        """
        prompt = (
            "You are a fact-checking expert. Compare the generated text against "
            "the provided context. Identify any claims in the text that are NOT "
            "supported by the context.\n\n"
            f"Context:\n{context[:4000]}\n\n"
            f"Generated text:\n{text[:4000]}\n\n"
            "Respond ONLY with JSON: "
            '{"hallucination_detected": true/false, '
            '"confidence": <0.0-1.0>, '
            '"unsupported_claims": ["claim1", ...]}'
        )

        try:
            response = self._invoke_model(prompt, max_tokens=1024)
            parsed = self._extract_json(response)
            if parsed:
                return {
                    "hallucination_detected": parsed.get(
                        "hallucination_detected", False
                    ),
                    "confidence": min(
                        1.0, max(0.0, float(parsed.get("confidence", 0.5)))
                    ),
                    "unsupported_claims": parsed.get("unsupported_claims", []),
                }
            return {
                "hallucination_detected": False,
                "confidence": 0.0,
                "unsupported_claims": [],
                "note": "Could not parse model response",
            }
        except (BotoCoreError, ClientError, RuntimeError) as e:
            failure = bedrock_failure_for(e)
            logger.warning(
                "Hallucination detection failed [%s]: %s",
                failure.kind,
                failure.message,
            )
            return {
                "hallucination_detected": False,
                "confidence": 0.0,
                "error": failure.message,
            }

    # --- Full Evaluation ---

    def evaluate(self, text: str, context: str = "") -> dict[str, Any]:
        """Run full 5-dimension evaluation on text.

        Args:
            text: Text to evaluate.
            context: Optional source context for grounding checks.

        Returns:
            Dict with overall_score, per-dimension scores, and feedback.
        """
        scores: dict[str, dict[str, Any]] = {}
        total = 0

        for dimension in SCORING_DIMENSIONS:
            result = self.score_dimension(text, dimension)
            scores[dimension] = result
            total += result.get("score", 0)

        overall_score = total / len(SCORING_DIMENSIONS) if SCORING_DIMENSIONS else 0

        # Check for hallucinations if context provided
        hallucination_check: dict[str, Any] | None = None
        if context:
            hallucination_check = self.detect_hallucinations(text, context)

        return {
            "overall_score": round(overall_score, 1),
            "dimensions": scores,
            "hallucination_check": hallucination_check,
            "max_score": 10,
        }

    async def evaluate_streaming(
        self, text: str, context: str = ""
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run 5-dimension evaluation, yielding SSE events per dimension.

        Async generator, so it cannot become a plain `def` and be threadpooled
        by FastAPI — it has to keep yielding a progress event per dimension.
        Each scoring call is a synchronous Bedrock round trip, so it is handed
        to a worker thread; six of them running on the event loop would freeze
        the whole application for the length of the evaluation.

        What goes into the worker thread is `score_dimension`, which contains
        the retry wrapper — so the request and its backoff sleeps are both off
        the loop. The reverse nesting (a retry loop here, awaiting a
        threadpooled call) would sleep on the loop between attempts and
        reintroduce the freeze.

        A Bedrock failure that survives its retries ends the stream with a
        terminal `error` event: continuing would emit an overall score computed
        from a dimension that scored 0 because the call never returned.

        Yields:
            SSE event dicts with scoring progress.
        """
        yield {
            "type": "progress",
            "message": "Starting quality evaluation...",
            "percentage": 5,
        }

        scores: dict[str, dict[str, Any]] = {}
        total = 0

        for i, dimension in enumerate(SCORING_DIMENSIONS):
            pct = 10 + (i * 15)
            yield {
                "type": "progress",
                "message": f"Scoring dimension: {dimension}...",
                "percentage": pct,
            }

            result = await run_in_threadpool(self.score_dimension, text, dimension)
            if result.get("error"):
                yield {
                    "type": "error",
                    "message": (
                        f"Quality evaluation failed while scoring {dimension} — "
                        f"{result['error']}"
                    ),
                }
                return

            scores[dimension] = result
            total += result.get("score", 0)

            yield {
                "type": "tool_result",
                "tool": "score_dimension",
                "output": result,
            }

        overall_score = total / len(SCORING_DIMENSIONS) if SCORING_DIMENSIONS else 0

        yield {"type": "progress", "message": "Evaluation complete", "percentage": 95}

        # Build final result
        evaluation = {
            "overall_score": round(overall_score, 1),
            "dimensions": scores,
            "max_score": 10,
        }

        # Hallucination check
        if context:
            yield {
                "type": "progress",
                "message": "Checking for hallucinations...",
                "percentage": 98,
            }
            hallucination_check = await run_in_threadpool(
                self.detect_hallucinations, text, context
            )
            if hallucination_check.get("error"):
                # A grounding check that could not run is not a check that
                # passed, so the evaluation does not get to report `completed`.
                yield {
                    "type": "error",
                    "message": (
                        "Quality evaluation failed during the hallucination "
                        f"check — {hallucination_check['error']}"
                    ),
                }
                return
            evaluation["hallucination_check"] = hallucination_check

        yield {"type": "content", "text": json.dumps(evaluation, indent=2)}
        yield {"type": "complete", "conversation_id": "", "status": "completed"}

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        """Extract JSON object from model response text.

        Handles cases where JSON is wrapped in markdown code blocks.
        """
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON in code blocks
        import re

        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find bare JSON object
        brace_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return None
