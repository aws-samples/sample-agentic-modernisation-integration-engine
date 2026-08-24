"""Base parser with typed dataclass output.

All parsers MUST return ParseResult (not raw dicts).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ClassInfo:
    """Information about a class extracted from source code."""

    name: str
    line_number: int
    methods: list[str] = field(default_factory=list)
    parent_classes: list[str] = field(default_factory=list)


@dataclass
class MethodInfo:
    """Information about a method/function extracted from source code."""

    name: str
    line_number: int
    parameters: list[str] = field(default_factory=list)
    return_type: str | None = None
    class_name: str | None = None


@dataclass
class ParseResult:
    """Typed result returned by all parsers."""

    classes: list[ClassInfo]
    methods: list[MethodInfo]
    imports: list[str]
    complexity: int
    language: str
    line_count: int


class BaseParser(ABC):
    """Abstract base class for all language parsers."""

    @abstractmethod
    def parse(self, source_code: str, filename: str) -> ParseResult:
        """Parse source code and return a typed ParseResult."""
        ...

    @abstractmethod
    def extract_classes(self, tree: object) -> list[ClassInfo]:
        """Extract class information from the parse tree."""
        ...

    @abstractmethod
    def extract_methods(self, tree: object) -> list[MethodInfo]:
        """Extract method/function information from the parse tree."""
        ...

    @abstractmethod
    def extract_imports(self, tree: object) -> list[str]:
        """Extract import statements from the parse tree."""
        ...

    @abstractmethod
    def calculate_complexity(self, tree: object) -> int:
        """Calculate cyclomatic complexity from the parse tree."""
        ...
