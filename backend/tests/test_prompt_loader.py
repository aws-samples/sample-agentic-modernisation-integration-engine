"""Regression tests for prompt template loading.

Guards the failure mode where the prompt directory did not resolve inside the
container, `load_prompt` silently fell back to placeholder-free defaults, and the
model produced documentation with no knowledge of the analysed codebase.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from agents import prompt_loader as agent_loader
from services import prompt_loader as service_loader
from utils.prompt_paths import BACKEND_ROOT, find_prompt_file, resolve_prompts_dir

PLACEHOLDER_RE = re.compile(r"\{\{\s*[a-zA-Z0-9_]+\s*\}\}")

ENRICHMENT_PROMPTS = ["analysis-summary", "documentation-generation"]

FULL_CONTEXT = {
    "name": "github_20260804_200238",
    "source_url": "https://github.com/example/legacy-billing",
    "framework": "spring-boot",
    "target_framework": "quarkus",
    "file_stats": '[{"extension": ".java", "count": 42}]',
    "dependencies": '[{"name": "log4j", "version": "1.2.17"}]',
    "upgrade_recommendations": '[{"name": "log4j", "latest": "2.24.1"}]',
    "folder_structure": '{"name": "legacy-billing", "type": "directory"}',
    "diagrams": "classDiagram\n  class InvoiceService",
    "analysis_summary": "A Spring Boot billing monolith.",
    "content": "text under evaluation",
}


# --- Template shipping / path resolution ---


@pytest.mark.parametrize("name", ENRICHMENT_PROMPTS)
def test_template_ships_inside_backend_package(name: str) -> None:
    """Templates live under backend/prompts so they are present in the image."""
    path, tried = find_prompt_file(name)
    assert path is not None, f"{name}.md not found. Tried: {tried}"
    assert path == BACKEND_ROOT / "prompts" / f"{name}.md"


def test_resolution_does_not_depend_on_repo_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The container layout has no repo-root prompts/ dir — resolution still works."""
    monkeypatch.delenv("PROMPTS_DIR", raising=False)
    monkeypatch.chdir("/")
    assert resolve_prompts_dir() == BACKEND_ROOT / "prompts"
    path, _ = find_prompt_file("documentation-generation")
    assert path is not None


def test_prompts_dir_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PROMPTS_DIR takes priority, so templates can be mounted elsewhere."""
    (tmp_path / "analysis-summary.md").write_text(
        "Custom template for {{name}}", encoding="utf-8"
    )
    monkeypatch.setenv("PROMPTS_DIR", str(tmp_path))

    result = service_loader.load_prompt_result("analysis-summary", {"name": "abc"})

    assert result.source == tmp_path / "analysis-summary.md"
    assert result.used_fallback is False
    assert result.content == "Custom template for abc"


# --- Substitution: the core regression ---


@pytest.mark.parametrize("name", ENRICHMENT_PROMPTS)
@pytest.mark.parametrize("loader", [service_loader, agent_loader])
def test_loaded_prompt_has_no_unsubstituted_placeholders(loader, name: str) -> None:
    """A rendered prompt never carries raw {{...}} template syntax."""
    prompt = loader.load_prompt(name, FULL_CONTEXT)
    leftovers = PLACEHOLDER_RE.findall(prompt)
    assert leftovers == [], f"{name} left placeholders unsubstituted: {leftovers}"


@pytest.mark.parametrize("name", ENRICHMENT_PROMPTS)
@pytest.mark.parametrize("loader", [service_loader, agent_loader])
def test_supplied_context_appears_in_prompt(loader, name: str) -> None:
    """The analysis context actually reaches the prompt sent to the model."""
    prompt = loader.load_prompt(name, FULL_CONTEXT)

    for key in ("file_stats", "dependencies", "folder_structure", "source_url"):
        assert FULL_CONTEXT[key] in prompt, f"{name} dropped context value '{key}'"


@pytest.mark.parametrize("name", ENRICHMENT_PROMPTS)
def test_fallback_defaults_inject_context(
    name: str, tmp_path: Path, monkeypatch
) -> None:
    """Even with no template files present, the defaults carry the context."""
    monkeypatch.setenv("PROMPTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.prompt_paths.candidate_prompt_dirs", lambda: [tmp_path], raising=True
    )

    for loader in (service_loader, agent_loader):
        result = loader.load_prompt_result(name, FULL_CONTEXT)
        assert result.used_fallback is True
        assert result.has_context is True
        assert PLACEHOLDER_RE.findall(result.content) == []
        assert FULL_CONTEXT["file_stats"] in result.content
        assert FULL_CONTEXT["dependencies"] in result.content


def test_unfilled_placeholder_is_neutralised() -> None:
    """A placeholder with no supplied value is replaced, not left raw."""
    result = service_loader.render_template(
        "unit", "Repo {{source_url}} framework {{framework}}", {"source_url": "u"}
    )
    assert "{{" not in result.content
    assert result.substituted == ("source_url",)
    assert result.unresolved == ("framework",)


# --- Loud fallback ---


def test_missing_template_logs_warning_with_paths_tried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing template is reported at WARNING, naming template and paths."""
    with caplog.at_level(logging.WARNING, logger="services.prompt_loader"):
        result = service_loader.load_prompt_result("no-such-template")

    assert result.used_fallback is True
    assert result.tried_paths
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("no-such-template" in m for m in messages)
    assert any(str(result.tried_paths[0]) in m for m in messages)


# --- Degradation reporting ---


def test_degradation_none_when_prompt_is_grounded() -> None:
    """A file-backed prompt with substituted context is not degraded."""
    result = service_loader.load_prompt_result("analysis-summary", FULL_CONTEXT)
    assert service_loader.describe_prompt_degradation(result) is None


def test_degradation_reported_for_fallback_prompt() -> None:
    """A built-in fallback prompt is flagged as degraded."""
    result = service_loader.load_prompt_result("no-such-template", FULL_CONTEXT)
    message = service_loader.describe_prompt_degradation(result)
    assert message is not None
    assert "no-such-template" in message


def test_degradation_reported_when_no_context_substituted() -> None:
    """A prompt rendered without any context is flagged as degraded."""
    result = service_loader.render_template("unit", "No placeholders here", None)
    message = service_loader.describe_prompt_degradation(result)
    assert message is not None
    assert "no analysis context" in message


# --- Property: substitution is total and faithful ---


@hyp_settings(max_examples=75, deadline=None)
@given(
    values=st.dictionaries(
        keys=st.sampled_from(sorted(FULL_CONTEXT)),
        values=st.text(
            alphabet=st.characters(blacklist_characters="{}"), min_size=1, max_size=40
        ),
        min_size=1,
    ),
    name=st.sampled_from(ENRICHMENT_PROMPTS),
)
def test_property_substitution_is_total_and_faithful(
    values: dict[str, str], name: str
) -> None:
    """For any context subset: no raw placeholders remain, and used values appear.

    Validates: prompts must be fully rendered and must carry the supplied
    analysis context.
    """
    result = service_loader.load_prompt_result(name, values)

    assert PLACEHOLDER_RE.findall(result.content) == []
    for key in result.substituted:
        assert values[key] in result.content


def test_degradation_reported_when_context_values_are_empty() -> None:
    """Placeholders filled with empty strings do not count as real context."""
    result = service_loader.render_template(
        "unit", "Repo {{source_url}} target {{target_framework}}", {"source_url": ""}
    )
    assert result.substituted == ("source_url",)
    assert result.has_context is False
    assert service_loader.describe_prompt_degradation(result) is not None
