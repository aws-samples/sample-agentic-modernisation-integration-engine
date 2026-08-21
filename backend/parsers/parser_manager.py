"""Parser manager — routes files by extension to the appropriate parser."""

from __future__ import annotations

import os

from parsers.abinitio_parser import ABINITIO_EXTENSIONS, AbInitioParser
from parsers.base_parser import BaseParser, ParseResult
from parsers.c_parser import CParser
from parsers.csharp_parser import CSharpParser
from parsers.java_parser import JavaParser
from parsers.javascript_parser import JavaScriptParser
from parsers.python_parser import PythonParser

# Extension → parser class (instantiated lazily).
_EXTENSION_MAP: dict[str, type[BaseParser]] = {
    ".java": JavaParser,
    ".py": PythonParser,
    ".cs": CSharpParser,
    ".c": CParser,
    ".h": CParser,
    ".js": JavaScriptParser,
    ".jsx": JavaScriptParser,
    ".ts": JavaScriptParser,
    ".tsx": JavaScriptParser,
    ".mjs": JavaScriptParser,
    ".cjs": JavaScriptParser,
}

# Add Ab Initio extensions.
for ext in ABINITIO_EXTENSIONS:
    _EXTENSION_MAP[ext] = AbInitioParser


class ParserManager:
    """Routes source files to the correct parser based on file extension."""

    def __init__(self) -> None:
        self._parser_cache: dict[type[BaseParser], BaseParser] = {}

    def _get_parser(self, parser_cls: type[BaseParser]) -> BaseParser:
        if parser_cls not in self._parser_cache:
            self._parser_cache[parser_cls] = parser_cls()
        return self._parser_cache[parser_cls]

    def get_parser_for_file(self, filename: str) -> BaseParser | None:
        """Return the appropriate parser for a file, or None if unsupported."""
        ext = os.path.splitext(filename)[1].lower()
        parser_cls = _EXTENSION_MAP.get(ext)
        if parser_cls is None:
            return None
        return self._get_parser(parser_cls)

    def parse_file(self, source_code: str, filename: str) -> ParseResult | None:
        """Parse a source file and return a ParseResult, or None if unsupported."""
        parser = self.get_parser_for_file(filename)
        if parser is None:
            return None
        return parser.parse(source_code, filename)

    def supports_file(self, filename: str) -> bool:
        """Check if the parser manager can handle a given file extension."""
        ext = os.path.splitext(filename)[1].lower()
        return ext in _EXTENSION_MAP

    @staticmethod
    def supported_extensions() -> list[str]:
        """Return all supported file extensions."""
        return sorted(_EXTENSION_MAP.keys())
