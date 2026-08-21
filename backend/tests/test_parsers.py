"""Tests for the Tree-sitter parser system and typed dataclass output."""

from parsers.base_parser import ClassInfo, MethodInfo, ParseResult
from parsers.java_parser import JavaParser
from parsers.python_parser import PythonParser
from parsers.javascript_parser import JavaScriptParser
from parsers.c_parser import CParser
from parsers.csharp_parser import CSharpParser
from parsers.abinitio_parser import AbInitioParser
from parsers.parser_manager import ParserManager
from parsers.mermaid_parser import MermaidParser
from services.diagram_generator import DiagramGenerator, DiagramSet


# --- Java Parser ---


SAMPLE_JAVA = """\
import java.util.List;
import java.util.Map;

public class UserService extends BaseService implements Serializable {
    public List<User> getUsers(String filter) {
        if (filter != null) {
            return filterUsers(filter);
        }
        return allUsers();
    }

    private User findById(int id) {
        for (User u : users) {
            if (u.getId() == id) {
                return u;
            }
        }
        return null;
    }
}

class HelperClass {
    void doWork() {}
}
"""


def test_java_parser_returns_parse_result():
    """JavaParser.parse() returns a ParseResult dataclass."""
    parser = JavaParser()
    result = parser.parse(SAMPLE_JAVA, "UserService.java")

    assert isinstance(result, ParseResult)
    assert result.language == "java"
    assert result.line_count > 0


def test_java_parser_extracts_classes():
    """JavaParser extracts class names, methods, and parent classes."""
    parser = JavaParser()
    result = parser.parse(SAMPLE_JAVA, "UserService.java")

    assert len(result.classes) >= 2
    user_service = next(c for c in result.classes if c.name == "UserService")
    assert isinstance(user_service, ClassInfo)
    assert "getUsers" in user_service.methods
    assert "findById" in user_service.methods
    assert "BaseService" in user_service.parent_classes
    assert "Serializable" in user_service.parent_classes


def test_java_parser_extracts_methods():
    """JavaParser extracts method signatures with parameters."""
    parser = JavaParser()
    result = parser.parse(SAMPLE_JAVA, "UserService.java")

    assert len(result.methods) >= 2
    get_users = next(m for m in result.methods if m.name == "getUsers")
    assert isinstance(get_users, MethodInfo)
    assert get_users.class_name == "UserService"
    assert len(get_users.parameters) == 1
    assert get_users.line_number > 0


def test_java_parser_extracts_imports():
    """JavaParser extracts import statements."""
    parser = JavaParser()
    result = parser.parse(SAMPLE_JAVA, "UserService.java")

    assert len(result.imports) == 2
    assert "java.util.List" in result.imports


def test_java_parser_calculates_complexity():
    """JavaParser calculates cyclomatic complexity."""
    parser = JavaParser()
    result = parser.parse(SAMPLE_JAVA, "UserService.java")

    # Base 1 + if + for + if = at least 4
    assert result.complexity >= 4


# --- Python Parser ---


SAMPLE_PYTHON = """\
from typing import Optional
import os

class Animal:
    def speak(self) -> str:
        return ""

class Dog(Animal):
    def speak(self) -> str:
        return "woof"

    def fetch(self, item: str) -> bool:
        if item:
            return True
        return False

def standalone_function(x: int, y: int) -> int:
    return x + y
"""


def test_python_parser_extracts_classes():
    """PythonParser extracts classes with inheritance."""
    parser = PythonParser()
    result = parser.parse(SAMPLE_PYTHON, "animals.py")

    assert isinstance(result, ParseResult)
    assert result.language == "python"
    dog = next(c for c in result.classes if c.name == "Dog")
    assert "Animal" in dog.parent_classes
    assert "speak" in dog.methods
    assert "fetch" in dog.methods


def test_python_parser_extracts_imports():
    """PythonParser extracts import statements."""
    parser = PythonParser()
    result = parser.parse(SAMPLE_PYTHON, "animals.py")

    assert len(result.imports) == 2


# --- JavaScript Parser ---


SAMPLE_JS = """\
import { useState } from 'react';

class Component extends BaseComponent {
    constructor(props) {
        super(props);
    }

    render() {
        return null;
    }
}

function helper(x, y) {
    if (x > y) {
        return x;
    }
    return y;
}
"""


def test_javascript_parser_extracts_classes():
    """JavaScriptParser extracts class with heritage."""
    parser = JavaScriptParser()
    result = parser.parse(SAMPLE_JS, "component.jsx")

    assert result.language == "javascript"
    comp = next(c for c in result.classes if c.name == "Component")
    assert "BaseComponent" in comp.parent_classes
    assert "render" in comp.methods


def test_javascript_parser_ts_language():
    """JavaScriptParser reports 'typescript' for .ts files."""
    parser = JavaScriptParser()
    result = parser.parse(SAMPLE_JS, "component.ts")
    assert result.language == "typescript"


# --- C Parser ---


SAMPLE_C = """\
#include <stdio.h>
#include "myheader.h"

struct Point {
    int x;
    int y;
};

int add(int a, int b) {
    if (a > 0) {
        return a + b;
    }
    return b;
}

void print_hello() {
    printf("hello");
}
"""


def test_c_parser_extracts_structs_as_classes():
    """CParser extracts struct definitions as ClassInfo."""
    parser = CParser()
    result = parser.parse(SAMPLE_C, "utils.c")

    assert result.language == "c"
    assert any(c.name == "Point" for c in result.classes)


def test_c_parser_extracts_functions():
    """CParser extracts top-level function definitions."""
    parser = CParser()
    result = parser.parse(SAMPLE_C, "utils.c")

    assert len(result.methods) >= 2
    add_fn = next(m for m in result.methods if m.name == "add")
    assert add_fn.return_type == "int"
    assert len(add_fn.parameters) == 2


def test_c_parser_extracts_includes():
    """CParser extracts #include directives."""
    parser = CParser()
    result = parser.parse(SAMPLE_C, "utils.c")

    assert len(result.imports) == 2


# --- C# Parser ---


SAMPLE_CSHARP = """\
using System;
using System.Collections.Generic;

namespace MyApp {
    class UserController : BaseController {
        void GetAll() {
            if (true) { }
        }

        int Count() {
            return 0;
        }
    }
}
"""


def test_csharp_parser_extracts_classes():
    """CSharpParser extracts C# class declarations."""
    parser = CSharpParser()
    result = parser.parse(SAMPLE_CSHARP, "UserController.cs")

    assert result.language == "csharp"
    assert any(c.name == "UserController" for c in result.classes)


def test_csharp_parser_extracts_usings():
    """CSharpParser extracts using directives."""
    parser = CSharpParser()
    result = parser.parse(SAMPLE_CSHARP, "UserController.cs")

    assert len(result.imports) >= 2


# --- Ab Initio Parser ---


SAMPLE_ABINITIO = """\
include "common.dml"
source "utils.mp"

type CustomerRecord

component LoadPhase
component TransformPhase

define process_record(input_rec, output_rec)
    if input_rec.valid
        out.valid :: reformat :: transform_data(input_rec)
    else
        out.invalid :: reformat :: reject_record(input_rec)
    end if
end define

function calculate_total(amount, tax)
    return amount + tax
end function
"""


def test_abinitio_parser_extracts_components():
    """AbInitioParser extracts type/component definitions."""
    parser = AbInitioParser()
    result = parser.parse(SAMPLE_ABINITIO, "pipeline.mp")

    assert result.language == "abinitio"
    names = [c.name for c in result.classes]
    assert "CustomerRecord" in names
    assert "LoadPhase" in names
    assert "TransformPhase" in names


def test_abinitio_parser_extracts_functions():
    """AbInitioParser extracts define/function definitions."""
    parser = AbInitioParser()
    result = parser.parse(SAMPLE_ABINITIO, "pipeline.mp")

    method_names = [m.name for m in result.methods]
    assert "process_record" in method_names
    assert "calculate_total" in method_names


def test_abinitio_parser_extracts_imports():
    """AbInitioParser extracts include/source directives."""
    parser = AbInitioParser()
    result = parser.parse(SAMPLE_ABINITIO, "pipeline.mp")

    assert "common.dml" in result.imports
    assert "utils.mp" in result.imports


# --- Parser Manager ---


def test_parser_manager_routes_by_extension():
    """ParserManager routes files to the correct parser."""
    mgr = ParserManager()

    assert mgr.supports_file("App.java")
    assert mgr.supports_file("main.py")
    assert mgr.supports_file("index.ts")
    assert mgr.supports_file("lib.c")
    assert mgr.supports_file("Controller.cs")
    assert mgr.supports_file("flow.dml")
    assert not mgr.supports_file("readme.md")
    assert not mgr.supports_file("data.json")


def test_parser_manager_parse_file():
    """ParserManager.parse_file() returns ParseResult for supported files."""
    mgr = ParserManager()
    result = mgr.parse_file(SAMPLE_JAVA, "UserService.java")

    assert result is not None
    assert isinstance(result, ParseResult)
    assert result.language == "java"


def test_parser_manager_returns_none_for_unsupported():
    """ParserManager returns None for unsupported extensions."""
    mgr = ParserManager()
    assert mgr.parse_file("# Hello", "README.md") is None


# --- Mermaid Parser ---


def test_mermaid_class_diagram():
    """MermaidParser generates a class diagram from ParseResults."""
    results = [
        ParseResult(
            classes=[
                ClassInfo(
                    name="Dog",
                    line_number=1,
                    methods=["bark", "fetch"],
                    parent_classes=["Animal"],
                ),
                ClassInfo(
                    name="Animal", line_number=10, methods=["speak"], parent_classes=[]
                ),
            ],
            methods=[],
            imports=[],
            complexity=1,
            language="python",
            line_count=20,
        )
    ]
    parser = MermaidParser()
    diagram = parser.generate_class_diagram(results)

    assert "classDiagram" in diagram
    assert "class Dog" in diagram
    assert "+bark()" in diagram
    assert "Animal <|-- Dog" in diagram


def test_mermaid_integration_diagram():
    """MermaidParser generates an integration diagram from imports."""
    results = [
        ParseResult(
            classes=[],
            methods=[],
            imports=["java.util.List", "java.util.Map"],
            complexity=1,
            language="java",
            line_count=5,
        )
    ]
    parser = MermaidParser()
    diagram = parser.generate_integration_diagram(results)

    assert "graph TD" in diagram


# --- Diagram Generator ---


def test_diagram_generator_returns_diagram_set():
    """DiagramGenerator returns a DiagramSet with all diagram types."""
    results = [
        ParseResult(
            classes=[
                ClassInfo(name="App", line_number=1, methods=["run"], parent_classes=[])
            ],
            methods=[
                MethodInfo(name="run", line_number=2, parameters=[], class_name="App")
            ],
            imports=["os"],
            complexity=2,
            language="python",
            line_count=10,
        )
    ]
    gen = DiagramGenerator()
    diagram_set = gen.generate(results)

    assert isinstance(diagram_set, DiagramSet)
    assert "classDiagram" in diagram_set.class_diagram
    assert "sequenceDiagram" in diagram_set.sequence_diagram
    assert "graph TD" in diagram_set.integration_diagram


def test_diagram_generator_handles_empty_results():
    """DiagramGenerator handles empty results without crashing."""
    gen = DiagramGenerator()
    diagram_set = gen.generate([])

    assert isinstance(diagram_set, DiagramSet)
    assert diagram_set.class_diagram != ""
    assert diagram_set.sequence_diagram != ""
    assert diagram_set.integration_diagram != ""
