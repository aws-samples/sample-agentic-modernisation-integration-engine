"""Ab Initio parser — regex-based (no tree-sitter).

Handles 30+ Ab Initio extensions including .dml, .mp, .xfr, .plan, .pset,
.cfg, .dbc, .ksh, .dat, .mfs, .tf, .ai, .abc, .gde, .mql, .env, .prm,
.par, .cust, .sort, .filter, .reformat, .join, .rollup, .scan, .normalize,
.denormalize, .partition, .gather, .replicate, .broadcast, .metaprog.
"""

from __future__ import annotations

import re

from parsers.base_parser import BaseParser, ClassInfo, MethodInfo, ParseResult

# Extensions recognized by this parser.
ABINITIO_EXTENSIONS = frozenset(
    {
        ".dml",
        ".mp",
        ".xfr",
        ".plan",
        ".pset",
        ".cfg",
        ".dbc",
        ".ksh",
        ".dat",
        ".mfs",
        ".tf",
        ".ai",
        ".abc",
        ".gde",
        ".mql",
        ".env",
        ".prm",
        ".par",
        ".cust",
        ".sort",
        ".filter",
        ".reformat",
        ".join",
        ".rollup",
        ".scan",
        ".normalize",
        ".denormalize",
        ".partition",
        ".gather",
        ".replicate",
        ".broadcast",
        ".metaprog",
    }
)

# Regex patterns for Ab Initio constructs.
_TYPE_DECL_RE = re.compile(r"^\s*type\s+(\w+)", re.MULTILINE)
_RECORD_RE = re.compile(r"^\s*record\s*\((.*?)\)", re.MULTILINE | re.DOTALL)
_FUNCTION_RE = re.compile(
    r"^\s*(?:define|let|function)\s+(\w+)\s*\((.*?)\)", re.MULTILINE
)
_INCLUDE_RE = re.compile(
    r'^\s*(?:include|source|import)\s+["\']?([^"\';\s]+)', re.MULTILINE
)
_COMPONENT_RE = re.compile(r"^\s*(?:component|phase|subgraph)\s+(\w+)", re.MULTILINE)
_TRANSFORM_RE = re.compile(
    r"^\s*(?:out\s*\.\s*\w+|reformat|transform|rollup|join|scan)\s*::", re.MULTILINE
)
_CONDITIONAL_RE = re.compile(r"^\s*(?:if|else\s+if|case|while|for)\b", re.MULTILINE)


class AbInitioParser(BaseParser):
    """Regex-based Ab Initio parser for graph/DML/transform files."""

    def parse(self, source_code: str, filename: str) -> ParseResult:
        return ParseResult(
            classes=self.extract_classes(source_code),
            methods=self.extract_methods(source_code),
            imports=self.extract_imports(source_code),
            complexity=self.calculate_complexity(source_code),
            language="abinitio",
            line_count=source_code.count("\n") + 1,
        )

    def extract_classes(self, tree: object) -> list[ClassInfo]:
        """Extract type/record/component definitions as 'classes'."""
        source = tree if isinstance(tree, str) else ""
        classes: list[ClassInfo] = []

        for match in _TYPE_DECL_RE.finditer(source):
            line_num = source[: match.start()].count("\n") + 1
            classes.append(
                ClassInfo(
                    name=match.group(1),
                    line_number=line_num,
                    methods=[],
                    parent_classes=[],
                )
            )

        for match in _COMPONENT_RE.finditer(source):
            line_num = source[: match.start()].count("\n") + 1
            classes.append(
                ClassInfo(
                    name=match.group(1),
                    line_number=line_num,
                    methods=[],
                    parent_classes=[],
                )
            )

        return classes

    def extract_methods(self, tree: object) -> list[MethodInfo]:
        """Extract function/transform definitions."""
        source = tree if isinstance(tree, str) else ""
        methods: list[MethodInfo] = []

        for match in _FUNCTION_RE.finditer(source):
            line_num = source[: match.start()].count("\n") + 1
            params_raw = match.group(2).strip()
            params = (
                [p.strip() for p in params_raw.split(",") if p.strip()]
                if params_raw
                else []
            )
            methods.append(
                MethodInfo(
                    name=match.group(1),
                    line_number=line_num,
                    parameters=params,
                    return_type=None,
                    class_name=None,
                )
            )

        return methods

    def extract_imports(self, tree: object) -> list[str]:
        """Extract include/source/import directives."""
        source = tree if isinstance(tree, str) else ""
        return [match.group(1) for match in _INCLUDE_RE.finditer(source)]

    def calculate_complexity(self, tree: object) -> int:
        """Count branching constructs + transforms."""
        source = tree if isinstance(tree, str) else ""
        conditionals = len(_CONDITIONAL_RE.findall(source))
        transforms = len(_TRANSFORM_RE.findall(source))
        return 1 + conditionals + transforms
