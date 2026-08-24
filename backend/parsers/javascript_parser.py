"""JavaScript/TypeScript parser using tree-sitter bindings.

Handles .js, .ts, .jsx, .tsx files using the JavaScript grammar (sufficient for
structural extraction; TypeScript-specific type annotations are treated as
identifiers).
"""

from __future__ import annotations

import tree_sitter
import tree_sitter_javascript as tsjs

from parsers.base_parser import BaseParser, ClassInfo, MethodInfo, ParseResult

JS_LANGUAGE = tree_sitter.Language(tsjs.language())

_COMPLEXITY_NODES = frozenset(
    {
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "catch_clause",
        "ternary_expression",
        "switch_case",
        "binary_expression",  # covers && and ||
    }
)


class JavaScriptParser(BaseParser):
    """Tree-sitter based JavaScript/TypeScript parser."""

    def __init__(self) -> None:
        self._parser = tree_sitter.Parser()
        self._parser.language = JS_LANGUAGE

    def parse(self, source_code: str, filename: str) -> ParseResult:
        tree = self._parser.parse(source_code.encode())
        lang = "typescript" if filename.endswith((".ts", ".tsx")) else "javascript"
        return ParseResult(
            classes=self.extract_classes(tree),
            methods=self.extract_methods(tree),
            imports=self.extract_imports(tree),
            complexity=self.calculate_complexity(tree),
            language=lang,
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
                elif child.type == "class_heritage":
                    for hc in child.children:
                        if hc.type == "identifier":
                            parents.append(hc.text.decode())
                elif child.type == "class_body":
                    for member in child.children:
                        if member.type == "method_definition":
                            for mc in member.children:
                                if mc.type == "property_identifier":
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

        if node.type == "function_declaration":  # type: ignore[attr-defined]
            name = ""
            params: list[str] = []
            for child in node.children:  # type: ignore[attr-defined]
                if child.type == "identifier":
                    name = child.text.decode()
                elif child.type == "formal_parameters":
                    for p in child.children:
                        if p.type in ("identifier", "assignment_pattern"):
                            params.append(p.text.decode())
            if name:
                methods.append(
                    MethodInfo(
                        name=name,
                        line_number=node.start_point[0] + 1,  # type: ignore[attr-defined]
                        parameters=params,
                        return_type=None,
                        class_name=current_class,
                    )
                )

        elif node.type == "method_definition":  # type: ignore[attr-defined]
            name = ""
            params: list[str] = []
            for child in node.children:  # type: ignore[attr-defined]
                if child.type == "property_identifier":
                    name = child.text.decode()
                elif child.type == "formal_parameters":
                    for p in child.children:
                        if p.type in ("identifier", "assignment_pattern"):
                            params.append(p.text.decode())
            if name:
                methods.append(
                    MethodInfo(
                        name=name,
                        line_number=node.start_point[0] + 1,  # type: ignore[attr-defined]
                        parameters=params,
                        return_type=None,
                        class_name=current_class,
                    )
                )

        for child in node.children:  # type: ignore[attr-defined]
            self._walk_methods(child, methods, current_class)

    def extract_imports(self, tree: object) -> list[str]:
        root = tree.root_node  # type: ignore[attr-defined]
        imports: list[str] = []
        for child in root.children:
            if child.type == "import_statement":
                source = child.text.decode()
                imports.append(source)
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
