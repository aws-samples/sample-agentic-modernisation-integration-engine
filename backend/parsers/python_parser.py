"""Python parser using tree-sitter bindings."""

from __future__ import annotations

import tree_sitter
import tree_sitter_python as tspython

from parsers.base_parser import BaseParser, ClassInfo, MethodInfo, ParseResult

PYTHON_LANGUAGE = tree_sitter.Language(tspython.language())

_COMPLEXITY_NODES = frozenset(
    {
        "if_statement",
        "elif_clause",
        "for_statement",
        "while_statement",
        "except_clause",
        "with_statement",
        "assert_statement",
        "boolean_operator",
        "conditional_expression",
        "list_comprehension",
        "set_comprehension",
        "dictionary_comprehension",
        "generator_expression",
    }
)


class PythonParser(BaseParser):
    """Tree-sitter based Python parser."""

    def __init__(self) -> None:
        self._parser = tree_sitter.Parser()
        self._parser.language = PYTHON_LANGUAGE

    def parse(self, source_code: str, filename: str) -> ParseResult:
        tree = self._parser.parse(source_code.encode())
        return ParseResult(
            classes=self.extract_classes(tree),
            methods=self.extract_methods(tree),
            imports=self.extract_imports(tree),
            complexity=self.calculate_complexity(tree),
            language="python",
            line_count=source_code.count("\n") + 1,
        )

    def extract_classes(self, tree: object) -> list[ClassInfo]:
        root = tree.root_node  # type: ignore[attr-defined]
        classes: list[ClassInfo] = []
        self._walk_classes(root, classes)
        return classes

    def _walk_classes(self, node: object, classes: list[ClassInfo]) -> None:
        if node.type == "class_definition":  # type: ignore[attr-defined]
            name = ""
            parents: list[str] = []
            methods: list[str] = []
            for child in node.children:  # type: ignore[attr-defined]
                if child.type == "identifier":
                    name = child.text.decode()
                elif child.type == "argument_list":
                    for arg in child.children:
                        if arg.type == "identifier":
                            parents.append(arg.text.decode())
                        elif arg.type == "attribute":
                            parents.append(arg.text.decode())
                elif child.type == "block":
                    for stmt in child.children:
                        if stmt.type == "function_definition":
                            for fc in stmt.children:
                                if fc.type == "identifier":
                                    methods.append(fc.text.decode())
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
        if node.type == "class_definition":  # type: ignore[attr-defined]
            for child in node.children:  # type: ignore[attr-defined]
                if child.type == "identifier":
                    current_class = child.text.decode()
                    break

        if node.type == "function_definition":  # type: ignore[attr-defined]
            name = ""
            params: list[str] = []
            return_type: str | None = None
            for child in node.children:  # type: ignore[attr-defined]
                if child.type == "identifier":
                    name = child.text.decode()
                elif child.type == "parameters":
                    for p in child.children:
                        if p.type in (
                            "identifier",
                            "typed_parameter",
                            "default_parameter",
                        ):
                            param_text = p.text.decode()
                            if param_text != "self" and param_text != "cls":
                                params.append(param_text)
                elif child.type == "type":
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
            if child.type == "import_statement":
                imports.append(child.text.decode())
            elif child.type == "import_from_statement":
                imports.append(child.text.decode())
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
