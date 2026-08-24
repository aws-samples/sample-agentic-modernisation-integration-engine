"""Mermaid diagram generation from typed ParseResult data.

Consumes list[ParseResult] and generates class, sequence, and integration
diagrams using typed field access (cls.methods, cls.parent_classes,
method.class_name, method.parameters).

All identifiers emitted into the Mermaid source are routed through
:func:`sanitize_mermaid_id` so that raw source text (wildcard imports,
generics, punctuation, reserved words) can never break the diagram parse.
"""

from __future__ import annotations

import re

from parsers.base_parser import ParseResult

# Flowchart/diagram keywords that break the parse when used as a bare node id.
# Compared case-insensitively.
MERMAID_RESERVED_WORDS: frozenset[str] = frozenset(
    {
        "graph",
        "subgraph",
        "end",
        "class",
        "classdef",
        "style",
        "click",
        "linkstyle",
        "direction",
        "default",
        "flowchart",
        "classdiagram",
        "sequencediagram",
        "participant",
        "note",
        "state",
    }
)

# Maximum number of dependency edges emitted in the integration diagram.
MAX_INTEGRATION_EDGES = 150

_INVALID_ID_CHARS = re.compile(r"[^A-Za-z0-9_]")
_UNDERSCORE_RUN = re.compile(r"_+")
_WILDCARD_TAIL = re.compile(r"[./\\]?\*+\s*$")
_LABEL_WHITESPACE = re.compile(r"\s+")


def sanitize_mermaid_id(raw: object) -> str:
    """Return a guaranteed-valid Mermaid identifier for ``raw``.

    The result always matches ``^[A-Za-z_][A-Za-z0-9_]*$`` or is the empty
    string, which callers must treat as "skip this node".
    """
    if raw is None:
        return ""

    cleaned = _INVALID_ID_CHARS.sub("_", str(raw))
    cleaned = _UNDERSCORE_RUN.sub("_", cleaned).strip("_")
    if not cleaned:
        return ""

    # An identifier may not start with a digit.
    if cleaned[0].isdigit():
        cleaned = f"n_{cleaned}"

    # Reserved words must never appear bare.
    if cleaned.lower() in MERMAID_RESERVED_WORDS:
        cleaned = f"{cleaned}_node"

    return cleaned


def escape_mermaid_label(text: object) -> str:
    """Escape ``text`` for use inside a double-quoted Mermaid node label."""
    flattened = _LABEL_WHITESPACE.sub(" ", str(text or "")).strip()
    # Order matters: '#' is escaped first so the entities inserted below are
    # not themselves rewritten.
    return flattened.replace("#", "#35;").replace('"', "#quot;")


class MermaidParser:
    """Generates Mermaid diagram source code from parsed code results."""

    def generate_class_diagram(self, results: list[ParseResult]) -> str:
        """Generate a Mermaid class diagram from parse results."""
        lines = ["classDiagram"]
        seen_classes: set[str] = set()

        for result in results:
            for cls in result.classes:
                class_id = sanitize_mermaid_id(cls.name)
                if not class_id or class_id in seen_classes:
                    continue
                seen_classes.add(class_id)
                lines.append(f"    class {class_id} {{")
                for method in cls.methods:
                    method_id = sanitize_mermaid_id(method)
                    if not method_id:
                        continue
                    lines.append(f"        +{method_id}()")
                lines.append("    }")

                # Inheritance relationships.
                for parent in cls.parent_classes:
                    parent_id = sanitize_mermaid_id(parent)
                    if not parent_id:
                        continue
                    lines.append(f"    {parent_id} <|-- {class_id}")

        if len(lines) == 1:
            lines.append("    class NoClassesFound")

        return "\n".join(lines)

    def generate_sequence_diagram(self, results: list[ParseResult]) -> str:
        """Generate a Mermaid sequence diagram from parse results.

        Shows interactions between classes based on method calls.
        """
        lines = ["sequenceDiagram"]
        participants: set[str] = set()
        interactions: list[str] = []

        for result in results:
            for method in result.methods:
                if not method.class_name:
                    continue
                caller_id = sanitize_mermaid_id(method.class_name)
                if not caller_id:
                    continue
                participants.add(caller_id)
                method_id = sanitize_mermaid_id(method.name) or "call"
                # If method has parameters referencing known classes, show a call.
                for param in method.parameters:
                    for other_result in results:
                        for cls in other_result.classes:
                            if cls.name not in param or cls.name == method.class_name:
                                continue
                            callee_id = sanitize_mermaid_id(cls.name)
                            if not callee_id or callee_id == caller_id:
                                continue
                            participants.add(callee_id)
                            interactions.append(
                                f"    {caller_id}->>+{callee_id}: {method_id}()"
                            )
                            interactions.append(
                                f"    {callee_id}-->>-{caller_id}: response"
                            )

        # Add participant declarations.
        for p in sorted(participants):
            lines.append(f"    participant {p}")

        if interactions:
            lines.extend(interactions)
        else:
            # Fallback: show classes as participants with a note.
            if not participants:
                for result in results:
                    for cls in result.classes:
                        class_id = sanitize_mermaid_id(cls.name)
                        if not class_id or class_id in participants:
                            continue
                        participants.add(class_id)
                        lines.append(f"    participant {class_id}")
            lines.append(
                "    Note over "
                + ",".join(sorted(participants)[:2] or ["System"])
                + ": No inter-class calls detected"
            )

        return "\n".join(lines)

    def generate_integration_diagram(self, results: list[ParseResult]) -> str:
        """Generate a Mermaid integration/flowchart diagram from parse results.

        Shows imports/dependencies between modules. Node ids are sanitized and
        the human-readable import string is preserved as a quoted label.
        """
        declarations: dict[str, str] = {}
        edges: set[tuple[str, str]] = set()
        edge_lines: list[str] = []
        truncated = False

        for result in results:
            lang_label = f"{result.language}_module"
            lang_node = sanitize_mermaid_id(lang_label) or "unknown_module"
            declarations.setdefault(lang_node, lang_label)

            for imp in result.imports:
                label, node_id = self._resolve_import(imp)
                if not node_id:
                    continue

                edge = (lang_node, node_id)
                if edge in edges:
                    continue
                if len(edges) >= MAX_INTEGRATION_EDGES:
                    truncated = True
                    break

                declarations.setdefault(node_id, label)
                edges.add(edge)
                edge_lines.append(f"    {lang_node} --> {node_id}")

            if truncated:
                break

        lines = ["graph TD"]
        for node_id, label in declarations.items():
            lines.append(f'    {node_id}["{escape_mermaid_label(label)}"]')
        lines.extend(edge_lines)

        if not edges:
            lines.append("    Application --> Dependencies")

        if truncated:
            lines.append(
                f'    diagram_truncated["... truncated at '
                f'{MAX_INTEGRATION_EDGES} dependencies"]'
            )

        return "\n".join(lines)

    @staticmethod
    def _resolve_import(imp: object) -> tuple[str, str]:
        """Return ``(label, node_id)`` for a raw import string.

        ``label`` is the human-readable original; ``node_id`` is a safe Mermaid
        identifier, or the empty string when the import cannot yield one.
        """
        label = str(imp or "").strip().rstrip(";").strip().strip("\"'<>").strip()
        if not label:
            return "", ""

        if _WILDCARD_TAIL.search(label):
            # Wildcard import: keep the package path so `java.util.*` becomes
            # `java_util_all` instead of a bare `*`.
            base = _WILDCARD_TAIL.sub("", label).strip()
            raw_id = f"{base}_all" if base else "wildcard_all"
        else:
            raw_id = label.split(".")[-1].split("/")[-1].split("\\")[-1]

        return label, sanitize_mermaid_id(raw_id)
