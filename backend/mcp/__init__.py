"""MCP (Model Context Protocol) servers for tool access.

Contains in-process MCP tools that agents can call directly.
"""

from mcp.static_analysis_server import StaticAnalysisServer

__all__ = ["StaticAnalysisServer"]
