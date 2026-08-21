"""Java parser using tree-sitter bindings."""

from __future__ import annotations

import tree_sitter
import tree_sitter_java as tsjava

from parsers.base_parser import BaseParser, ClassInfo, MethodInfo, ParseResult

JAVA_LANGUAGE = tree_sitter.Language(tsjava.language())

# Node types that contribute to cyclomatic complexity.
_COMPLEXITY_NODES = frozenset(
    {
        "if_statement",
        "for_statement",
        "enhanced_for_statement",
        "while_statement",
        "do_statement",
        "catch_clause",
        "ternary_expression",
        "switch_expression",
        "case_label",
        "&&",
        "||",
    }
)


class JavaParser(BaseParser):
    """Tree-sitter based Java parser."""

    def __init__(self) -> None:
        self._parser = tree_sitter.Parser()
        self._parser.language = JAVA_LANGUAGE

    def parse(self, source_code: str, filename: str) -> ParseResult:
        tree = self._parser.parse(source_code.encode())
        return ParseResult(
            classes=self.extract_classes(tree),
            methods=self.extract_methods(tree),
            imports=self.extract_imports(tree),
            complexity=self.calculate_complexity(tree),
            language="java",
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
                elif child.type == "superclass":
                    for sc in child.children:
                        if sc.type == "type_identifier":
                            parents.append(sc.text.decode())
                elif child.type == "super_interfaces":
                    for iface in child.children:
                        if iface.type == "type_list":
                            for t in iface.children:
                                if t.type == "type_identifier":
                                    parents.append(t.text.decode())
                elif child.type == "class_body":
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
                elif child.type == "formal_parameters":
                    for p in child.children:
                        if p.type == "formal_parameter":
                            params.append(p.text.decode())
                elif child.type in (
                    "type_identifier",
                    "void_type",
                    "integral_type",
                    "floating_point_type",
                    "boolean_type",
                    "generic_type",
                    "array_type",
                ):
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
        for child in root.children:
            if child.type == "import_declaration":
                imports.append(
                    child.text.decode().rstrip(";").replace("import ", "").strip()
                )
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
