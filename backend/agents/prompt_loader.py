"""Prompt loading utilities for AI agents.

Loads prompts from the backend `prompts/` directory (resolved via
utils.prompt_paths so it works in both the local and container layouts), strips
front-matter, and substitutes template variables.

Falls back to embedded defaults when a template file is missing — with a WARNING
log, because a silent fallback yields contextless model output.
"""

from __future__ import annotations

import logging
from pathlib import Path

from services.prompt_loader import PromptLoadResult, render_template
from services.prompt_loader import load_prompt_result as _load_prompt_result
from utils.prompt_paths import resolve_prompts_dir

logger = logging.getLogger(__name__)

# Backwards-compatible module attribute; resolution happens per call.
PROMPTS_DIR: Path = resolve_prompts_dir()

# Defaults carry the same {{...}} placeholders as the real templates so the
# analysis context is still injected on the fallback path.
_CONTEXT_BLOCK = (
    "\n\n## Analysis Context\n\n"
    "Analysis name: {{name}}\n"
    "Source: {{source_url}}\n"
    "Detected framework: {{framework}}\n"
    "Migration target: {{target_framework}}\n\n"
    "### File statistics\n{{file_stats}}\n\n"
    "### Dependencies\n{{dependencies}}\n\n"
    "### Upgrade recommendations\n{{upgrade_recommendations}}\n\n"
    "### Folder structure\n{{folder_structure}}\n\n"
    "### Architecture diagrams\n{{diagrams}}\n\n"
    "Ground every statement in the data above. Do not speculate about files or "
    "dependencies that are not present in it."
)

_DEFAULT_PROMPTS: dict[str, str] = {
    "documentation-generation": (
        "You are an expert code documentation generator. "
        "Analyze the provided codebase context and generate comprehensive "
        "documentation in markdown format covering: project overview, "
        "architecture, key components, dependencies, build instructions, "
        "and risk assessment." + _CONTEXT_BLOCK
    ),
    "analysis-summary": (
        "You are a senior software architect. Generate a concise executive "
        "summary of the analyzed codebase including: key statistics, "
        "architecture patterns, technology stack, dependencies, "
        "and modernization recommendations." + _CONTEXT_BLOCK
    ),
    "quality-evaluation": (
        "You are a quality evaluation expert. Score the provided text on "
        "5 dimensions (accuracy, completeness, actionability, specificity, "
        "correctness) with a score from 0 to 10 for each dimension. "
        "Provide justification for each score.\n\n"
        "## Text to evaluate\n\n{{content}}"
    ),
    "kiro-spec-generation": (
        "You are a Kiro specification generator. Given analysis context, "
        "generate a Kiro-style specification with three sections: "
        "requirements (acceptance criteria), design (architecture decisions), "
        "and tasks (implementation plan).\n\n"
        "## Analysis Context\n\n"
        "Analysis summary: {{analysis_summary}}\n"
        "Detected framework: {{framework}}\n"
        "Migration target: {{target_framework}}\n\n"
        "### Dependencies\n{{dependencies}}\n\n"
        "### Upgrade recommendations\n{{upgrade_recommendations}}"
    ),
}


def load_prompt_result(
    name: str, variables: dict[str, str] | None = None
) -> PromptLoadResult:
    """Load a prompt and report how it was produced.

    Args:
        name: Prompt file name (without .md extension).
        variables: Optional dict of variable substitutions.

    Returns:
        A PromptLoadResult with the rendered prompt and load metadata.
    """
    result = _load_prompt_result(name, variables, defaults=_DEFAULT_PROMPTS)
    if result.used_fallback and name not in _DEFAULT_PROMPTS:
        # Unknown prompt name — use the generic agent default.
        return render_template(
            name,
            _get_default_prompt(name),
            variables,
            used_fallback=True,
            tried_paths=result.tried_paths,
        )
    return result


def load_prompt(name: str, variables: dict[str, str] | None = None) -> str:
    """Load a prompt from the prompts directory.

    Strips front-matter, substitutes {{variables}} with provided values, and
    neutralises any placeholder left without a value. Falls back to an embedded
    default (logged at WARNING) if the template file is not found.

    Args:
        name: Prompt file name (without .md extension).
        variables: Optional dict of variable substitutions.

    Returns:
        The prompt text with variables substituted.
    """
    return load_prompt_result(name, variables).content


def _get_default_prompt(name: str) -> str:
    """Return the embedded default prompt for a given name."""
    return _DEFAULT_PROMPTS.get(name, f"You are a helpful AI assistant for: {name}")
