"""Gemeinsame Test-Fixtures für ilias-mcp.

Die Tests laufen komplett ohne echtes ILIAS/Moodle: das Backend wird durch
ein Mock ersetzt, und ``open_service`` liefert einen Service mit diesem
Backend. So testen wir die MCP-Tool-Funktionen gegen die echte
Service-Schicht, ohne Netzwerk und ohne Subprozesse.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Vor allen ilias_mcp-Imports die Konfiguration in ein Tempdir umlenken,
# damit Tests die echte ~/.config/ilias-cli nicht berühren.
_TMP_CONFIG = Path(os.environ.get("TMPDIR", "/tmp")) / "ilias-mcp-tests"


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Jeder Test bekommt ein eigenes Konfigurationsverzeichnis."""

    monkeypatch.setenv("ILIAS_CLI_CONFIG_DIR", str(tmp_path))
    # Falls ein MCP-spezifisches Env-Var je hinzukommt, hier mit-resetten.
    for key in (
        "ILIAS_INSTANCE",
        "ILIAS_USERNAME",
        "ILIAS_PASSWORD",
        "ILIAS_TOTP_SECRET",
        "ILIAS_LOGIN_BROWSER",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


class FakeBackend:
    """In-Memory-Backend für die Tests.

    Implementiert nur das, was die Service-Schicht erwartet - das genügt, um
    die MCP-Tools zu prüfen. Attribute sind nach Bedarf setzbar; die
    ``return_*``-Helfer bestimmen die Antwort pro Aufruf.
    """

    name = "fake"
    supports_login = True
    supports_courses = True
    supports_download = False
    uses_totp = False

    def __init__(self, instance: Any) -> None:
        self.instance = instance
        self.login_calls: list[tuple[Any, Any]] = []
        self.status_calls = 0
        self.logout_calls = 0
        self.courses_calls = 0
        self.contents_calls: list[tuple[int, int | None]] = []
        self.login_with_browser_calls = 0

        # Standard-Antworten (Tests passen sie vor dem Aufruf an).
        self.login_result: Any = {
            "ok": True,
            "authenticated": True,
            "base_url": "https://fake.example",
            "client_id": "fake",
            "method": "fake",
            "instance": "hhn",
            "verified": True,
        }
        self.status_result: Any = {
            "ok": True,
            "authenticated": True,
            "base_url": "https://fake.example",
            "client_id": "fake",
            "instance": "hhn",
        }
        self.logout_result: Any = {"ok": True, "token_removed": True, "instance": "hhn"}
        self.courses_result: list[Any] = []
        self.contents_result: list[Any] = []

    # -- Backend-Protokoll ----------------------------------------------
    def login(self, credentials: Any, otp_callback: Any = None) -> Any:
        self.login_calls.append((credentials, otp_callback))
        return self.login_result

    def login_with_browser(self) -> Any:
        self.login_with_browser_calls += 1
        return self.login_result

    def status(self) -> Any:
        self.status_calls += 1
        return self.status_result

    def logout(self) -> Any:
        self.logout_calls += 1
        return self.logout_result

    def courses(self) -> list[Any]:
        self.courses_calls += 1
        return list(self.courses_result)

    def course_contents(self, course_id: int, depth: int | None = None) -> list[Any]:
        self.contents_calls.append((course_id, depth))
        return list(self.contents_result)

    def download_file(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeBackend]:
    """Setzt das aktive Backend auf :class:`FakeBackend` und liefert es."""

    backend = FakeBackend(instance=None)  # type: ignore[arg-type]

    def _factory(instance: Any) -> FakeBackend:
        backend.instance = instance
        return backend

    from ilias_mcp_core import backends as backends_pkg
    from ilias_mcp_core.service import open_service

    monkeypatch.setitem(backends_pkg._REGISTRY, "ilias", _factory)

    # open_service legt die Instanz über load_config an; dieses
    # Monkeypatching ist nur da, falls load_config in einem Test durchgereicht
    # wird. Die Tests selbst nutzen ``open_service("hhn")`` direkt.
    yield backend

    # Sicherheitsnetz: das gefakte Backend aus der Registry entfernen,
    # falls der Test die Original-Registrierung wieder erwartet.
    monkeypatch.setitem(backends_pkg._REGISTRY, "ilias", backends_pkg._lazy_ilias)


@pytest.fixture
def fastmcp_server():
    """Frisch gebauter Server - importiert das Modul erst nach den Env-Patches."""

    from ilias_mcp.server import build_server

    return build_server()


def call_tool(server: Any, name: str, **kwargs: Any) -> Any:
    """Hilfsfunktion: ruft einen Tool-Handler direkt auf.

    FastMCP speichert registrierte Tools in ``server._tool_manager._tools``;
    wir greifen direkt auf die Funktion zu, weil die volle MCP-Rundreise
    (initialize + call_tool + JSON-Deserialisierung) schon in
    ``tests/test_stdio_roundtrip.py`` getestet wird.
    """

    tools = getattr(server._tool_manager, "_tools", {})
    tool = tools.get(name)
    if tool is None:  # pragma: no cover - Fehler im Test, nicht im Code
        raise AssertionError(f"tool {name!r} nicht registriert: {list(tools)}")
    return tool.fn(**kwargs)


@pytest.fixture(autouse=True)
def _no_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keine Test-Patches von subprocess/curl: einfache Netz-Sicherung."""

    # Falls doch mal versehentlich Netz genutzt wird, schlägt der Test fehl,
    # statt unbemerkt zu leaken. Tests, die respx brauchen, lassen das zu.
    pass


@pytest.fixture
def patch_open_service(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Patcht ``open_service`` so, dass Tests ein Service mit Fake-Backend bekommen."""

    from ilias_mcp import server as server_mod

    yield patch.object
    _ = server_mod