"""ilias-mcp: MCP-Server für ILIAS und Moodle (siehe :mod:`ilias_mcp.server`)."""

from __future__ import annotations

from .server import build_server, main, mcp

__all__ = ["build_server", "main", "mcp"]