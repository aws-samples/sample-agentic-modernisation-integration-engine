"""C# parser using tree-sitter bindings."""

from __future__ import annotations

import tree_sitter
import tree_sitter_c_sharp as tscsharp

from parsers.base_parser import BaseParser, ClassInfo, MethodInfo, ParseResult

CSHARP_LANGUAGE = tree_sitter.Language(tscsharp.language())

_COMPLEXITY_NODES = frozenset(
    {
        "if_statement",
        "for_statement",
        "for_each_statement",
        "while_statement",
        "do_statement",
        "catch_clause",
        "case_switch_label",
        "conditional_expression",
        "binary_expression",  # covers && and ||
    }
)


class CSharpParser(BaseParser):
    """Tree-sitter based C# parser."""

    def __init__(self) -> None:
        self._parser = tree_sitter.Parser()
        self._parser.language = CSHARP_LANGUAGE

    def parse(self, source_code: str, filename: str) -> ParseResult:
        tree = self._parser.parse(source_code.encode())
        return ParseResult(
            classes=self.extract_classes(tree),
            methods=self.extract_methods(tree),
            imports=self.extract_imports(tree),
            complexity=self.calculate_complexity(tree),
            language="csharp",
            line_count=source_code.count("\n") + 1,
        )

    def extract_classes(self, tree: object) -> list[ClassInfo]:
        root = tree.root_node  # type: ignore[attr-defined]
        classes: list[ClassInfo] = []
        self._walk_classes(root, classes)
        return classes

    def _walk_classes(self, node: object, classes: list[ClassInfo]) -> None:
        if node.type == "class_declaration":  # type: ignore[attr-defined]
            name = ""
            parents: list[str] = []
            methods: list[str] = []
            for child in node.children:  # type: ignore[attr-defined]
                if child.type == "identifier":
                    name = child.text.decode()
                elif child.type == "base_list":
                    for base in child.children:
                        if base.type in (
                            "identifier",
                            "generic_name",
                            "qualified_name",
                        ):
                            parents.append(base.text.decode())
                elif child.type == "declaration_list":
                    for member in child.children:
                        if member.type == "method_declaration":
                            for mc in member.children:
                                if mc.type == "identifier":
                                    methods.append(mc.text.decode())
                                    break
            if name:
                classes.append(
                    ClassInfo(
                        name=name,
                        line_number=node.start_point[0] + 1,  # type: ignore[attr-defined]
                        methods=methods,
                        parent_classes=parents,
                    )
                )
        for child in node.children:  # type: ignore[attr-defined]
            self._walk_classes(child, classes)

    def extract_methods(self, tree: object) -> list[MethodInfo]:
        root = tree.root_node  # type: ignore[attr-defined]
        methods: list[MethodInfo] = []
        self._walk_methods(root, methods, None)
        return methods

    def _walk_methods(
        self, node: object, methods: list[MethodInfo], class_name: str | None
    ) -> None:
        current_class = class_name
        if node.type == "class_declaration":  # type: ignore[attr-defined]
            for child in node.children:  # type: ignore[attr-defined]
                if child.type == "identifier":
                    current_class = child.text.decode()
                    break

        if node.type == "method_declaration":  # type: ignore[attr-defined]
            name = ""
            params: list[str] = []
            return_type: str | None = None
            for child in node.children:  # type: ignore[attr-defined]
                if child.type == "identifier":
                    name = child.text.decode()
                elif child.type == "parameter_list":
                    for p in child.children:
                        if p.type == "parameter":
                            params.append(p.text.decode())
                elif child.type in (
                    "predefined_type",
                    "identifier",
                    "generic_name",
                    "void_keyword",
                ):
                    if not name:
                        # Return type comes before identifier in C#
                        return_type = child.text.decode()
            if name:
                methods.append(
                    MethodInfo(
                        name=name,
                        line_number=node.start_point[0] + 1,  # type: ignore[attr-defined]
                        parameters=params,
                        return_type=return_type,
                        class_name=current_class,
                    )
                )

        for child in node.children:  # type: ignore[attr-defined]
            self._walk_methods(child, methods, current_class)

    def extract_imports(self, tree: object) -> list[str]:
        root = tree.root_node  # type: ignore[attr-defined]
        imports: list[str] = []
        self._walk_imports(root, imports)
        return imports

    def _walk_imports(self, node: object, imports: list[str]) -> None:
        if node.type == "using_directive":  # type: ignore[attr-defined]
            imports.append(node.text.decode().rstrip(";").replace("using ", "").strip())
        for child in node.children:  # type: ignore[attr-defined]
            self._walk_imports(child, imports)

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
