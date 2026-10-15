from __future__ import annotations

from typing import Any

import anyio
from mcp import Client

from ilias_mcp.server import build_server


def call(tool: str, args: dict[str, Any] | None = None, server=None):
    async def _run():
        async with Client(server or build_server()) as client:
            return await client.call_tool(tool, args or {})

    return anyio.run(_run)


def list_tools(server=None):
    async def _run():
        async with Client(server or build_server()) as client:
            return (await client.list_tools()).tools

    return anyio.run(_run)
