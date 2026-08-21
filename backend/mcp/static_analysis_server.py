"""Static Analysis MCP Server — 9 in-process tools for agent access.

Provides callable functions (no HTTP) that agents use to access analysis data.
Each tool takes an analysis_id (or source code) and returns structured data.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from parsers.parser_manager import ParserManager
from utils.storage_manager import StorageManager

logger = logging.getLogger(__name__)


class StaticAnalysisServer:
    """In-process MCP server with 9 tools for static analysis data access.

    Tools:
    1. list_analyses — list all analysis IDs
    2. get_file_statistics — file type stats for an analysis
    3. get_folder_structure — folder tree for an analysis
    4. get_dependencies — dependency list for an analysis
    5. get_dependency_graph — graph nodes + links for an analysis
    6. get_code_metrics — metrics summary for an analysis
    7. get_upgrade_recommendations — version recommendations
    8. parse_code — parse source code and return ParseResult as dict
    9. get_analysis_summary — full summary of an analysis
    """

    def __init__(self, storage: StorageManager) -> None:
        self.storage = storage
        self._parser_manager = ParserManager()

    # --- Tool 1: list_analyses ---

    def list_analyses(self) -> list[dict[str, Any]]:
        """List all available analysis IDs with metadata.

        Returns:
            List of dicts with analysis_id, source_type, created_at, status.
        """
        items = self.storage.list_analyses()
        return [item.model_dump() for item in items]

    # --- Tool 2: get_file_statistics ---

    def get_file_statistics(self, analysis_id: str) -> dict[str, Any]:
        """Get file type statistics for an analysis.

        Args:
            analysis_id: The analysis to query.

        Returns:
            Dict with file_stats list and summary totals.
        """
        data = self.storage.load(analysis_id)
        if data is None:
            return {"error": f"Analysis {analysis_id} not found"}

        file_stats = data.get("file_stats", [])
        total_files = sum(s.get("count", 0) for s in file_stats)
        total_lines = sum(s.get("total_lines", 0) for s in file_stats)
        total_size = sum(s.get("total_size", 0) for s in file_stats)

        return {
            "analysis_id": analysis_id,
            "file_stats": file_stats,
            "totals": {
                "files": total_files,
                "lines": total_lines,
                "size_bytes": total_size,
            },
        }

    # --- Tool 3: get_folder_structure ---

    def get_folder_structure(self, analysis_id: str) -> dict[str, Any]:
        """Get folder tree structure for an analysis.

        Args:
            analysis_id: The analysis to query.

        Returns:
            Dict with the folder_structure tree.
        """
        data = self.storage.load(analysis_id)
        if data is None:
            return {"error": f"Analysis {analysis_id} not found"}

        return {
            "analysis_id": analysis_id,
            "folder_structure": data.get("folder_structure", {}),
        }

    # --- Tool 4: get_dependencies ---

    def get_dependencies(self, analysis_id: str) -> dict[str, Any]:
        """Get dependency list for an analysis.

        Args:
            analysis_id: The analysis to query.

        Returns:
            Dict with dependencies list and count.
        """
        data = self.storage.load(analysis_id)
        if data is None:
            return {"error": f"Analysis {analysis_id} not found"}

        dependencies = data.get("dependencies", [])
        return {
            "analysis_id": analysis_id,
            "dependencies": dependencies,
            "count": len(dependencies),
        }

    # --- Tool 5: get_dependency_graph ---

    def get_dependency_graph(self, analysis_id: str) -> dict[str, Any]:
        """Get dependency graph (nodes + links) for an analysis.

        Args:
            analysis_id: The analysis to query.

        Returns:
            Dict with nodes and links arrays.
        """
        data = self.storage.load(analysis_id)
        if data is None:
            return {"error": f"Analysis {analysis_id} not found"}

        graph = data.get("dependency_graph", {})
        nodes = graph.get("nodes", [])
        links = graph.get("links", [])

        return {
            "analysis_id": analysis_id,
            "nodes": nodes,
            "links": links,
            "node_count": len(nodes),
            "link_count": len(links),
        }

    # --- Tool 6: get_code_metrics ---

    def get_code_metrics(self, analysis_id: str) -> dict[str, Any]:
        """Get code metrics summary for an analysis.

        Args:
            analysis_id: The analysis to query.

        Returns:
            Dict with metrics including complexity, LOC, class/method counts.
        """
        data = self.storage.load(analysis_id)
        if data is None:
            return {"error": f"Analysis {analysis_id} not found"}

        parsed_files = data.get("parsed_files", [])
        file_stats = data.get("file_stats", [])

        total_complexity = 0
        total_classes = 0
        total_methods = 0
        total_imports = 0
        languages: dict[str, int] = {}

        for pf in parsed_files:
            if isinstance(pf, dict):
                total_complexity += pf.get("complexity", 0)
                total_classes += len(pf.get("classes", []))
                total_methods += len(pf.get("methods", []))
                total_imports += len(pf.get("imports", []))
                lang = pf.get("language", "unknown")
                languages[lang] = languages.get(lang, 0) + 1

        total_lines = sum(s.get("total_lines", 0) for s in file_stats)
        total_files = sum(s.get("count", 0) for s in file_stats)

        return {
            "analysis_id": analysis_id,
            "metrics": {
                "total_files": total_files,
                "total_lines": total_lines,
                "total_classes": total_classes,
                "total_methods": total_methods,
                "total_imports": total_imports,
                "total_complexity": total_complexity,
                "average_complexity": (
                    round(total_complexity / len(parsed_files), 2)
                    if parsed_files
                    else 0
                ),
                "languages": languages,
            },
        }

    # --- Tool 7: get_upgrade_recommendations ---

    def get_upgrade_recommendations(self, analysis_id: str) -> dict[str, Any]:
        """Get version upgrade recommendations for an analysis.

        Args:
            analysis_id: The analysis to query.

        Returns:
            Dict with upgrade_recommendations list.
        """
        data = self.storage.load(analysis_id)
        if data is None:
            return {"error": f"Analysis {analysis_id} not found"}

        recommendations = data.get("upgrade_recommendations", [])
        return {
            "analysis_id": analysis_id,
            "upgrade_recommendations": recommendations,
            "count": len(recommendations),
        }

    # --- Tool 8: parse_code ---

    def parse_code(self, source_code: str, filename: str) -> dict[str, Any]:
        """Parse source code and return ParseResult as dict.

        Args:
            source_code: The source code text to parse.
            filename: Filename (used to determine parser by extension).

        Returns:
            Dict representation of ParseResult, or error.
        """
        if not self._parser_manager.supports_file(filename):
            return {
                "error": f"Unsupported file type: {filename}",
                "supported_extensions": self._parser_manager.supported_extensions(),
            }

        try:
            result = self._parser_manager.parse_file(source_code, filename)
            if result is None:
                return {"error": f"Parser returned None for {filename}"}
            return asdict(result)
        except Exception as e:
            logger.warning("Parse error for %s: %s", filename, e)
            return {"error": f"Parse failed: {e}", "filename": filename}

    # --- Tool 9: get_analysis_summary ---

    def get_analysis_summary(self, analysis_id: str) -> dict[str, Any]:
        """Get a full summary of an analysis including all sections.

        Args:
            analysis_id: The analysis to summarize.

        Returns:
            Dict with comprehensive analysis summary.
        """
        data = self.storage.load(analysis_id)
        if data is None:
            return {"error": f"Analysis {analysis_id} not found"}

        file_stats = data.get("file_stats", [])
        dependencies = data.get("dependencies", [])

        total_lines = sum(s.get("total_lines", 0) for s in file_stats)
        total_files = sum(s.get("count", 0) for s in file_stats)

        # Language breakdown
        languages: dict[str, int] = {}
        for s in file_stats:
            if isinstance(s, dict):
                ext = s.get("extension", "")
                count = s.get("count", 0)
                if ext:
                    languages[ext] = count

        # Vulnerability count
        vuln_count = sum(
            len(d.get("vulnerabilities", []))
            for d in dependencies
            if isinstance(d, dict)
        )

        return {
            "analysis_id": analysis_id,
            "source_type": data.get("source_type", "unknown"),
            "source_url": data.get("source_url"),
            "completed_at": data.get("completed_at"),
            "summary": {
                "total_files": total_files,
                "total_lines": total_lines,
                "total_dependencies": len(dependencies),
                "total_vulnerabilities": vuln_count,
                "languages": languages,
                "has_diagrams": bool(data.get("diagrams")),
                "has_ai_summary": bool(data.get("ai_summary")),
                "has_ai_documentation": bool(data.get("ai_documentation")),
                "ai_enrichment_status": data.get("ai_enrichment_status"),
            },
            "file_stats": file_stats,
            "dependency_count": len(dependencies),
            "upgrade_recommendation_count": len(
                data.get("upgrade_recommendations", [])
            ),
        }
