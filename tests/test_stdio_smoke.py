"""Q3: start the real console script over stdio and list tools."""

from __future__ import annotations

import os
import shutil
import sys

import anyio
from mcp import Client, StdioServerParameters

from ilias_mcp.schemas_loader import load_all_tool_schemas


def test_stdio_initialize_and_list_tools(config_dir):
    exe = shutil.which("ilias-mcp")
    command, args = (exe, []) if exe else (sys.executable, ["-m", "ilias_mcp.server"])
    params = StdioServerParameters(
        command=command,
        args=args,
        env={**os.environ, "ILIAS_CLI_CONFIG_DIR": str(config_dir)},
    )

    async def _run():
        async with Client(params) as client:
            tools = (await client.list_tools()).tools
            result = await client.call_tool("ilias_list_instances", {})
            return tools, result

    tools, result = anyio.run(_run)
    assert sorted(t.name for t in tools) == sorted(load_all_tool_schemas())
    assert not result.is_error
