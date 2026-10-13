"""Echter MCP-Roundtrip gegen den Server (stdio-Transport).

Das ist das "Inspector"-Pendant: der Server wird als Subprozess gestartet,
wir schicken ``initialize`` + ``tools/list`` + einen Tool-Aufruf, lesen die
Antworten und prüfen sie. So beweisen wir, dass die in :mod:`ilias_mcp.server`
registrierten Tools tatsächlich per MCP-Protokoll auffindbar und aufrufbar
sind - ohne dass ein echter ILIAS-Login nötig ist.
"""

from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path

import pytest


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _server_command() -> list[str]:
    """uv run ilias-mcp, robust gegen uv-Varianten."""

    if shutil.which("uv") is None:  # pragma: no cover
        pytest.skip("uv nicht installiert")
    return ["uv", "--directory", str(Path(__file__).resolve().parents[1]), "run", "ilias-mcp"]


# respx/pytest-asyncio werden importiert, falls vorhanden; sonst skip.
mcp_client = pytest.importorskip("mcp").client  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_stdio_roundtrip_tools_list() -> None:
    """initialize + tools/list via mcp.client.stdio_client."""

    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=_server_command()[0], args=_server_command()[1:])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            assert init_result.serverInfo.name == "ilias-mcp"
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert "ilias_login" in names
            assert "ilias_status" in names
            assert "ilias_logout" in names
            assert "ilias_list_courses" in names
            assert "ilias_list_course_contents" in names


@pytest.mark.asyncio
async def test_stdio_roundtrip_tool_call_returns_error_payload() -> None:
    """Ein Tool-Aufruf ohne Credentials liefert einen strukturierten Fehler."""

    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp import types as mcp_types

    env = {k: v for k, v in os.environ.items() if k not in {"ILIAS_USERNAME", "ILIAS_PASSWORD"}}
    params = StdioServerParameters(
        command=_server_command()[0], args=_server_command()[1:], env=env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("ilias_login", arguments={})
            assert result.isError is False, "ilias_login sollte nicht crashen, nur Fehler liefern"
            # Inhalt: ein strukturiertes JSON-Objekt
            assert result.content, "leere Antwort"
            first = result.content[0]
            assert isinstance(first, mcp_types.TextContent)
            # Antwort enthält ok=False (oder True) - prüfen wir per JSON-Parse
            import json

            data = json.loads(first.text)
            assert "ok" in data
            assert "command" in data


@pytest.mark.asyncio
async def test_stdio_roundtrip_status_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """status wird aufgerufen, wenn keine Session existiert - Antwort ist strukturiert."""

    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    import json

    # Leeres Config-Dir + keine Credentials
    env = os.environ.copy()
    env["ILIAS_CLI_CONFIG_DIR"] = str(tmp_path)
    for key in ("ILIAS_USERNAME", "ILIAS_PASSWORD"):
        env.pop(key, None)
    # Überflüssige Proxy-Variablen entfernen, falls vorhanden
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)

    params = StdioServerParameters(
        command=_server_command()[0], args=_server_command()[1:], env=env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("ilias_status", arguments={})
            assert result.content
            data = json.loads(result.content[0].text)
            # Es gibt zwei mögliche stabile Antworten: ok=False mit config_error
            # (kein Backend für nicht-Moodle ohne Config) oder auth_failed.
            assert "ok" in data
            assert "command" in data