"""C parser using tree-sitter bindings."""

from __future__ import annotations

import tree_sitter
import tree_sitter_c as tsc

from parsers.base_parser import BaseParser, ClassInfo, MethodInfo, ParseResult

C_LANGUAGE = tree_sitter.Language(tsc.language())

_COMPLEXITY_NODES = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "do_statement",
        "case_statement",
        "conditional_expression",
        "binary_expression",  # covers && and ||
    }
)


class CParser(BaseParser):
    """Tree-sitter based C parser."""

    def __init__(self) -> None:
        self._parser = tree_sitter.Parser()
        self._parser.language = C_LANGUAGE

    def parse(self, source_code: str, filename: str) -> ParseResult:
        tree = self._parser.parse(source_code.encode())
        return ParseResult(
            classes=self.extract_classes(tree),
            methods=self.extract_methods(tree),
            imports=self.extract_imports(tree),
            complexity=self.calculate_complexity(tree),
            language="c",
            line_count=source_code.count("\n") + 1,
        )

    def extract_classes(self, tree: object) -> list[ClassInfo]:
        """C has no classes — extract struct definitions instead."""
        root = tree.root_node  # type: ignore[attr-defined]
        classes: list[ClassInfo] = []
        self._walk_structs(root, classes)
        return classes

    def _walk_structs(self, node: object, classes: list[ClassInfo]) -> None:
        if node.type == "struct_specifier":  # type: ignore[attr-defined]
            name = ""
            for child in node.children:  # type: ignore[attr-defined]
                if child.type == "type_identifier":
                    name = child.text.decode()
                    break
            if name:
                classes.append(
                    ClassInfo(
                        name=name,
                        line_number=node.start_point[0] + 1,  # type: ignore[attr-defined]
                        methods=[],
                        parent_classes=[],
                    )
                )
        for child in node.children:  # type: ignore[attr-defined]
            self._walk_structs(child, classes)

    def extract_methods(self, tree: object) -> list[MethodInfo]:
        root = tree.root_node  # type: ignore[attr-defined]
        methods: list[MethodInfo] = []
        for child in root.children:
            if child.type == "function_definition":
                name = ""
                params: list[str] = []
                return_type: str | None = None
                for fc in child.children:
                    if fc.type == "function_declarator":
                        for fcc in fc.children:
                            if fcc.type == "identifier":
                                name = fcc.text.decode()
                            elif fcc.type == "parameter_list":
                                for p in fcc.children:
                                    if p.type == "parameter_declaration":
                                        params.append(p.text.decode())
                    elif fc.type in (
                        "primitive_type",
                        "type_identifier",
                        "sized_type_specifier",
                    ):
                        return_type = fc.text.decode()
                if name:
                    methods.append(
                        MethodInfo(
                            name=name,
                            line_number=child.start_point[0] + 1,
                            parameters=params,
                            return_type=return_type,
                            class_name=None,
                        )
                    )
        return methods

    def extract_imports(self, tree: object) -> list[str]:
        root = tree.root_node  # type: ignore[attr-defined]
        imports: list[str] = []
        for child in root.children:
            if child.type == "preproc_include":
                path = child.text.decode().replace("#include", "").strip()
                imports.append(path)
        return imports

    def calculate_complexity(self, tree: object) -> int:
        root = tree.root_node  # type: ignore[attr-defined]
        return 1 + self._count_complexity(root)

    def _count_complexity(self, node: object) -> int:
        count = 0
        if node.type in _COMPLEXITY_NODES:  # type: ignore[attr-defined]
            count += 1
        for child in node.children:  # type: ignore[attr-defined]
            count += self._count_complexity(child)
        return count
