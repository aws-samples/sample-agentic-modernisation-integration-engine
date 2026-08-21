"""Diagram generator service.

Orchestrates MermaidParser to produce a DiagramSet dataclass.
Each diagram type has independent try/except for graceful fallback, and every
generated diagram is structurally validated before it is handed to the
frontend — invalid Mermaid is replaced with a valid placeholder rather than
being allowed to fail at render time in the browser.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from parsers.base_parser import ParseResult
from parsers.mermaid_parser import MERMAID_RESERVED_WORDS, MermaidParser

logger = logging.getLogger(__name__)

# Recognised opening directives for the diagram types this service emits.
VALID_DIRECTIVES = ("classDiagram", "sequenceDiagram", "graph", "flowchart")

_QUOTED_SEGMENT = re.compile(r'"[^"]*"')
_BRACKET_LABEL = re.compile(r"\[[^\]]*\]")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class DiagramSet:
    """Collection of generated Mermaid diagrams with fallback support."""

    class_diagram: str
    sequence_diagram: str
    integration_diagram: str


def _placeholder(diagram_type: str, reason: str) -> str:
    """Return a syntactically valid Mermaid diagram describing a failure."""
    return (
        "graph TD\n"
        f'    diagram_unavailable["{diagram_type} diagram unavailable: {reason}"]'
    )


def validate_mermaid(diagram: str) -> tuple[bool, str]:
    """Lightweight structural check on generated Mermaid source.

    Returns ``(is_valid, reason)``. The check is deliberately cheap: it catches
    the classes of defect this service can actually produce (empty output,
    missing directive, bare ``*``, unquoted reserved-word node ids) rather than
    attempting a full grammar parse.
    """
    if not diagram or not diagram.strip():
        return False, "empty output"

    lines = [line for line in diagram.splitlines() if line.strip()]
    directive = lines[0].strip()
    if not directive.startswith(VALID_DIRECTIVES):
        return False, "unrecognised diagram directive"
    if len(lines) < 2:
        return False, "no diagram body"

    is_flowchart = directive.startswith(("graph", "flowchart"))

    for line in lines[1:]:
        # Labels are quoted or bracketed; their contents are free text and must
        # not be inspected for identifier violations.
        bare = _BRACKET_LABEL.sub("[]", _QUOTED_SEGMENT.sub('""', line))

        if "*" in bare:
            return False, "bare '*' in node id"

        if is_flowchart:
            for token in _TOKEN.findall(bare):
                if token.lower() in MERMAID_RESERVED_WORDS:
                    return False, f"reserved word '{token}' used as node id"

    return True, ""


class DiagramGenerator:
    """Orchestrates diagram generation with per-diagram error handling."""

    def __init__(self) -> None:
        self._mermaid = MermaidParser()

    def generate(self, results: list[ParseResult]) -> DiagramSet:
        """Generate all diagrams from parse results.

        Each diagram is generated independently — a failure in one does not
        block the others.
        """
        class_diagram = self._safe_generate(
            lambda: self._mermaid.generate_class_diagram(results),
            "class",
        )
        sequence_diagram = self._safe_generate(
            lambda: self._mermaid.generate_sequence_diagram(results),
            "sequence",
        )
        integration_diagram = self._safe_generate(
            lambda: self._mermaid.generate_integration_diagram(results),
            "integration",
        )

        return DiagramSet(
            class_diagram=class_diagram,
            sequence_diagram=sequence_diagram,
            integration_diagram=integration_diagram,
        )

    def _safe_generate(
        self,
        generator: object,
        diagram_type: str,
    ) -> str:
        """Execute a diagram generator, then validate its output."""
        try:
            diagram = generator()  # type: ignore[operator]
        except Exception as exc:
            logger.warning("Failed to generate %s diagram: %s", diagram_type, exc)
            return _placeholder(diagram_type, "generation failed")

        is_valid, reason = validate_mermaid(diagram)
        if not is_valid:
            logger.warning(
                "Generated %s diagram failed validation (%s); returning placeholder",
                diagram_type,
                reason,
            )
            return _placeholder(diagram_type, f"invalid output ({reason})")

        return diagram
