"""Tests for Mermaid identifier sanitization and diagram validation.

Guards the integration-diagram render failure: node ids were derived straight
from raw import text, so `import java.util.*` emitted `java_module --> *` and
broke the parse for the whole diagram.
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from parsers.base_parser import ClassInfo, MethodInfo, ParseResult
from parsers.mermaid_parser import (
    MAX_INTEGRATION_EDGES,
    MERMAID_RESERVED_WORDS,
    MermaidParser,
    sanitize_mermaid_id,
)
from services.diagram_generator import DiagramGenerator, validate_mermaid

VALID_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# `a --> b` and `id["label"]` are the only two shapes the integration diagram
# emits, so node ids are recoverable with a narrow pair of patterns.
EDGE_LINE = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)\s*$")
DECL_LINE = re.compile(r'^\s*(\S+?)\["')


def _import_result(imports: list[str], language: str = "java") -> ParseResult:
    return ParseResult(
        classes=[],
        methods=[],
        imports=imports,
        complexity=1,
        language=language,
        line_count=len(imports),
    )


def _node_ids(diagram: str) -> list[str]:
    """Extract every node id emitted in an integration diagram."""
    ids: list[str] = []
    for line in diagram.splitlines()[1:]:
        edge = EDGE_LINE.match(line)
        if edge:
            ids.extend(edge.groups())
            continue
        decl = DECL_LINE.match(line)
        if decl:
            ids.append(decl.group(1))
    return ids


def _strip_labels(diagram: str) -> str:
    """Remove quoted label contents so only structural text remains."""
    return re.sub(r'"[^"]*"', '""', diagram)


# --- sanitize_mermaid_id ---


def test_sanitize_replaces_hostile_characters():
    """Every character outside [A-Za-z0-9_] collapses to a single underscore."""
    assert sanitize_mermaid_id("foo-bar baz.qux") == "foo_bar_baz_qux"
    assert sanitize_mermaid_id("@angular/core") == "angular_core"
    assert sanitize_mermaid_id("List<User>") == "List_User"
    assert sanitize_mermaid_id("a$(b)[c]{d}#e%f&g+h=i|j;k:l,m!n'o\"p`q") is not None


def test_sanitize_prefixes_leading_digit():
    """An id may not start with a digit."""
    assert sanitize_mermaid_id("3d") == "n_3d"
    assert sanitize_mermaid_id("2fa-token") == "n_2fa_token"


def test_sanitize_returns_empty_for_punctuation_only():
    """Punctuation-only input yields the empty string so callers can skip it."""
    assert sanitize_mermaid_id("*") == ""
    assert sanitize_mermaid_id("...") == ""
    assert sanitize_mermaid_id("") == ""
    assert sanitize_mermaid_id(None) == ""


def test_sanitize_suffixes_reserved_words():
    """Reserved flowchart keywords never survive as bare ids."""
    for word in ("end", "graph", "class", "subgraph", "style", "default"):
        result = sanitize_mermaid_id(word)
        assert result.lower() not in MERMAID_RESERVED_WORDS
        assert VALID_ID.match(result)


# --- integration diagram ---


def test_wildcard_import_produces_no_bare_star():
    """`java.util.*` resolves to a readable id, not a bare `*`."""
    diagram = MermaidParser().generate_integration_diagram(
        [_import_result(["java.util.*", "java.util.List"])]
    )

    assert "*" not in _strip_labels(diagram)
    assert "java_util_all" in diagram
    for node_id in _node_ids(diagram):
        assert VALID_ID.match(node_id), node_id


def test_reserved_word_imports_are_not_bare_node_ids():
    """Imports whose last segment is a Mermaid keyword are suffixed."""
    diagram = MermaidParser().generate_integration_diagram(
        [_import_result(["com.acme.end", "com.acme.graph", "com.acme.class"])]
    )

    node_ids = _node_ids(diagram)
    assert node_ids
    for node_id in node_ids:
        assert node_id.lower() not in MERMAID_RESERVED_WORDS, node_id
        assert VALID_ID.match(node_id), node_id


def test_punctuated_imports_yield_valid_ids():
    """Imports with @, /, -, ., spaces, and punctuation all sanitize cleanly."""
    diagram = MermaidParser().generate_integration_diagram(
        [
            _import_result(
                [
                    "@angular/core",
                    "some-module",
                    "my module",
                    "pkg.sub.Thing",
                    "std::vector<int>",
                    "<stdio.h>",
                    "System.Collections.Generic",
                    "3d-engine",
                ],
                language="typescript",
            )
        ]
    )

    node_ids = _node_ids(diagram)
    assert len(node_ids) > 1
    for node_id in node_ids:
        assert VALID_ID.match(node_id), node_id


def test_unsanitizable_import_is_skipped():
    """An import that sanitizes to nothing emits no line at all."""
    parser = MermaidParser()
    diagram = parser.generate_integration_diagram(
        [_import_result(["...", "!!!", "pkg.Real"])]
    )

    edge_lines = [line for line in diagram.splitlines() if "-->" in line]
    assert len(edge_lines) == 1
    assert "Real" in edge_lines[0]
    for node_id in _node_ids(diagram):
        assert VALID_ID.match(node_id), node_id


def test_language_node_is_sanitized():
    """The `{language}_module` node goes through the same helper."""
    diagram = MermaidParser().generate_integration_diagram(
        [_import_result(["pkg.Thing"], language="c++/objective c")]
    )

    for node_id in _node_ids(diagram):
        assert VALID_ID.match(node_id), node_id


def test_original_import_kept_as_quoted_label():
    """Node declarations preserve the human-readable import string."""
    diagram = MermaidParser().generate_integration_diagram(
        [_import_result(["java.util.List"])]
    )

    assert '["java.util.List"]' in diagram


def test_integration_diagram_edge_cap():
    """The diagram is capped and notes the truncation."""
    imports = [f"pkg.mod{i}.Class{i}" for i in range(MAX_INTEGRATION_EDGES + 50)]
    diagram = MermaidParser().generate_integration_diagram([_import_result(imports)])

    edge_lines = [line for line in diagram.splitlines() if "-->" in line]
    assert len(edge_lines) == MAX_INTEGRATION_EDGES
    assert "truncated" in diagram
    assert validate_mermaid(diagram)[0]


# --- validator ---


def test_validator_rejects_broken_mermaid():
    """The validator catches the defects that reached the browser."""
    assert not validate_mermaid("")[0]
    assert not validate_mermaid("   ")[0]
    assert not validate_mermaid("graph TD")[0]
    assert not validate_mermaid("not a diagram\n  a --> b")[0]
    assert not validate_mermaid("graph TD\n    java_module --> *")[0]
    assert not validate_mermaid("graph TD\n    java_module --> end")[0]


def test_validator_accepts_generated_diagrams():
    """All three diagram types validate for a mixed-language input."""
    results = [
        ParseResult(
            classes=[
                ClassInfo(
                    name="UserService",
                    line_number=1,
                    methods=["getUsers", "<init>"],
                    parent_classes=["BaseService<User>"],
                ),
            ],
            methods=[
                MethodInfo(
                    name="getUsers",
                    line_number=2,
                    parameters=["Repo repo"],
                    class_name="UserService",
                ),
            ],
            imports=["java.util.*", "com.acme.end", "@angular/core"],
            complexity=3,
            language="java",
            line_count=40,
        ),
        ParseResult(
            classes=[ClassInfo(name="Repo", line_number=1, methods=["find"])],
            methods=[],
            imports=["<stdio.h>", "std::vector<int>"],
            complexity=1,
            language="c++",
            line_count=10,
        ),
    ]

    diagrams = DiagramGenerator().generate(results)

    for name, source in (
        ("class", diagrams.class_diagram),
        ("sequence", diagrams.sequence_diagram),
        ("integration", diagrams.integration_diagram),
    ):
        is_valid, reason = validate_mermaid(source)
        assert is_valid, f"{name} diagram invalid: {reason}\n{source}"
        assert "unavailable" not in source


def test_generator_returns_valid_placeholder_on_invalid_output(monkeypatch):
    """Invalid generated Mermaid is replaced, never forwarded to the frontend."""
    gen = DiagramGenerator()
    monkeypatch.setattr(
        gen._mermaid,
        "generate_integration_diagram",
        lambda _results: "graph TD\n    java_module --> *",
    )

    diagrams = gen.generate([])

    assert "unavailable" in diagrams.integration_diagram
    assert validate_mermaid(diagrams.integration_diagram)[0]


# --- property-based ---


@settings(max_examples=300)
@given(st.lists(st.text(max_size=40), min_size=1, max_size=8))
def test_property_all_node_ids_are_valid_identifiers(imports: list[str]):
    """For arbitrary import strings, every emitted node id is a valid id.

    **Validates: Requirements 1.2**
    """
    diagram = MermaidParser().generate_integration_diagram([_import_result(imports)])

    assert validate_mermaid(diagram)[0]
    for node_id in _node_ids(diagram):
        assert VALID_ID.match(node_id), node_id
