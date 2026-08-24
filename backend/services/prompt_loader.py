"""Prompt loader — loads prompt templates from the prompts/ directory.

Strips front-matter, substitutes {{variables}} with provided values.
Falls back to embedded defaults if the template file is not found — and says so
loudly, because a silent fallback produces plausible-looking but contextless
model output.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from utils.prompt_paths import find_prompt_file, resolve_prompts_dir

logger = logging.getLogger(__name__)

# Backwards-compatible module attribute; resolution itself is done per call so
# that layout/env changes are picked up at runtime.
PROMPTS_DIR = resolve_prompts_dir()

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

# Value used when a template placeholder has no supplied context value. Keeps
# raw template syntax out of the prompt sent to the model.
_MISSING_VALUE = "(not provided)"

# Fallback defaults when prompt files are not found. These MUST carry the same
# {{...}} placeholders as the real templates, so the analysis context is still
# injected on the degraded path instead of being dropped.
_DEFAULTS: dict[str, str] = {
    "analysis-summary": (
        "You are an expert code analyst. Generate a concise executive summary "
        "of the analyzed codebase. Include: overview, key findings, technology "
        "stack, architecture patterns, risks, and recommendations.\n\n"
        "## Analysis Context\n\n"
        "Analysis name: {{name}}\n"
        "Source: {{source_url}}\n"
        "Detected framework: {{framework}}\n"
        "Migration target: {{target_framework}}\n\n"
        "### File statistics\n{{file_stats}}\n\n"
        "### Dependencies\n{{dependencies}}\n\n"
        "### Upgrade recommendations\n{{upgrade_recommendations}}\n\n"
        "### Folder structure\n{{folder_structure}}\n\n"
        "Ground every statement in the data above. Do not speculate about "
        "files or dependencies that are not present in it."
    ),
    "documentation-generation": (
        "You are an expert code documentation generator. Generate comprehensive "
        "documentation for the analyzed codebase. Include: project overview, "
        "architecture, key components, dependencies, build instructions, and "
        "risk assessment.\n\n"
        "## Analysis Context\n\n"
        "Analysis name: {{name}}\n"
        "Source: {{source_url}}\n"
        "Detected framework: {{framework}}\n"
        "Migration target: {{target_framework}}\n\n"
        "### File statistics\n{{file_stats}}\n\n"
        "### Dependencies\n{{dependencies}}\n\n"
        "### Folder structure\n{{folder_structure}}\n\n"
        "### Architecture diagrams\n{{diagrams}}\n\n"
        "Ground every statement in the data above. Do not speculate about "
        "files or dependencies that are not present in it."
    ),
}


@dataclass(frozen=True)
class PromptLoadResult:
    """Outcome of a prompt load, including how the prompt was produced."""

    name: str
    content: str
    source: Path | None
    """Template file the prompt came from, or None for a built-in default."""
    used_fallback: bool
    """True when no template file was found and a built-in default was used."""
    substituted: tuple[str, ...]
    """Variable names that were present in the template and substituted."""
    unresolved: tuple[str, ...]
    """Placeholders present in the template with no supplied value."""
    tried_paths: tuple[Path, ...]
    context_chars: int = 0
    """Total length of the non-empty values injected into the prompt."""

    @property
    def has_context(self) -> bool:
        """True when non-empty context actually made it into the prompt."""
        return self.context_chars > 0


def _strip_front_matter(content: str) -> str:
    """Remove leading YAML front-matter delimited by --- markers."""
    if not content.startswith("---"):
        return content
    try:
        end = content.index("---", 3)
    except ValueError:
        return content  # Malformed front-matter — use as-is.
    return content[end + 3 :].strip()


def render_template(
    name: str,
    template: str,
    variables: dict[str, str] | None = None,
    *,
    source: Path | None = None,
    used_fallback: bool = False,
    tried_paths: tuple[Path, ...] = (),
) -> PromptLoadResult:
    """Substitute variables into a template and report what was filled.

    Every placeholder is either filled from `variables` or replaced with a
    neutral marker, so raw {{token}} syntax never reaches the model.

    Args:
        name: Prompt name, used for logging and in the result.
        template: Raw template text (front-matter already stripped).
        variables: Dict of template variables to substitute.
        source: Template file the text came from, if any.
        used_fallback: True when `template` is a built-in default.
        tried_paths: Paths probed while looking for the template file.

    Returns:
        A PromptLoadResult with the rendered prompt and render metadata.
    """
    content = template
    supplied = variables or {}
    filled: set[str] = set()
    missing: set[str] = set()

    def _substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in supplied:
            filled.add(key)
            return supplied[key]
        missing.add(key)
        return _MISSING_VALUE

    # Single pass: every placeholder is either filled or neutralised, so no raw
    # {{token}} syntax ever reaches the model.
    content = _PLACEHOLDER_RE.sub(_substitute, content)

    substituted = sorted(filled)
    unresolved = sorted(missing)
    if unresolved:
        logger.warning(
            "Prompt '%s' has unfilled placeholders: %s", name, ", ".join(unresolved)
        )

    return PromptLoadResult(
        name=name,
        content=content,
        source=source,
        used_fallback=used_fallback,
        substituted=tuple(substituted),
        unresolved=tuple(unresolved),
        tried_paths=tuple(tried_paths),
        context_chars=sum(len(supplied[key]) for key in filled),
    )


def load_prompt_result(
    name: str,
    variables: dict[str, str] | None = None,
    defaults: dict[str, str] | None = None,
) -> PromptLoadResult:
    """Load a prompt and report how it was produced.

    Args:
        name: Prompt file name without extension (e.g., "analysis-summary").
        variables: Dict of template variables to substitute.
        defaults: Optional override for the built-in fallback templates.

    Returns:
        A PromptLoadResult with the rendered prompt and load metadata.
    """
    path, tried = find_prompt_file(name)
    fallbacks = _DEFAULTS if defaults is None else defaults

    if path is None:
        template = fallbacks.get(name, f"Prompt '{name}' not found.")
        logger.warning(
            "Prompt template '%s' not found — falling back to the built-in "
            "default. Paths tried: %s",
            name,
            ", ".join(str(p) for p in tried),
        )
    else:
        template = _strip_front_matter(path.read_text(encoding="utf-8"))

    return render_template(
        name,
        template,
        variables,
        source=path,
        used_fallback=path is None,
        tried_paths=tuple(tried),
    )


def describe_prompt_degradation(*results: PromptLoadResult) -> str | None:
    """Explain why prompts were degraded, or None when all are healthy.

    A prompt is degraded when its template file could not be found (so a
    built-in default was used) or when no analysis context was substituted into
    it. Either way the model is answering without the codebase in front of it,
    and the output must not be reported as a successful enrichment.
    """
    problems: list[str] = []

    for result in results:
        if result.used_fallback:
            tried = ", ".join(str(p) for p in result.tried_paths)
            problems.append(
                f"prompt template '{result.name}' was not found "
                f"(tried: {tried}) — a built-in default was used"
            )
        if not result.has_context:
            problems.append(
                f"no analysis context was substituted into prompt '{result.name}'"
            )

    if not problems:
        return None

    return (
        "AI output was generated without the analysed codebase as context: "
        + "; ".join(problems)
        + "."
    )


def load_prompt(name: str, variables: dict[str, str] | None = None) -> str:
    """Load a prompt from the prompts directory.

    Strips front-matter, substitutes {{variables}}, and neutralises any
    placeholder left without a value. Falls back to an embedded default (with a
    WARNING log) if the template file is not found.

    Args:
        name: Prompt file name without extension (e.g., "analysis-summary").
        variables: Dict of template variables to substitute.

    Returns:
        The prompt content with variables substituted.
    """
    return load_prompt_result(name, variables).content


def list_prompts() -> list[dict[str, str]]:
    """List all available prompt files with their metadata.

    Returns:
        List of dicts with keys: name, path, has_frontmatter.
    """
    prompts: list[dict[str, str]] = []
    prompts_dir = resolve_prompts_dir()

    if not prompts_dir.is_dir():
        logger.warning("Prompts directory not found: %s", prompts_dir)
        return prompts

    for path in sorted(prompts_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        prompts.append(
            {
                "name": path.stem,
                "path": str(path),
                "has_frontmatter": str(content.startswith("---")),
            }
        )

    return prompts
