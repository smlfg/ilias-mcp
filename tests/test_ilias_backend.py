"""Integrationstest für :class:`IliasBackend.courses` und :class:`IliasBackend.course_contents`.

Verkabelt das echte Backend mit gemockten HTTP-Antworten, die aus lokalen
HTML-Fixtures stammen. So prüfen wir den End-to-End-Pfad (Konstruktion des
``Fetcher``, Parsen, Mapping auf ``Course``/``Section``/``Module``) ohne echten
ILIAS-Login und ohne Netz.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import httpx
import pytest

from ilias_mcp_core.backends.ilias import IliasBackend
from ilias_mcp_core.config import load_instance

FIXTURES = Path(__file__).parent / "fixtures" / "ilias_hhn"
HHN_BASE_URL = "https://ilias.hs-heilbronn.de"


class _MockTransport(httpx.BaseTransport):
    """Antwortet auf eine Liste von (URL-Fragment, HTML)-Paaren in Reihenfolge."""

    def __init__(self) -> None:
        self._responses: list[tuple[str, str]] = []
        self.calls: list[str] = []

    def queue(self, fragment: str, html: str) -> None:
        self._responses.append((fragment, html))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        needle = str(request.url)
        for fragment, html in self._responses:
            if fragment in needle:
                return httpx.Response(
                    200,
                    content=html.encode("utf-8"),
                    headers={"content-type": "text/html; charset=utf-8"},
                    request=request,
                )
        return httpx.Response(
            404,
            content=b"not mocked",
            headers={"content-type": "text/plain"},
            request=request,
        )


@pytest.fixture
def hhn_backend() -> Iterator[tuple[IliasBackend, _MockTransport]]:
    """Baut einen echten ``IliasBackend`` mit gemocktem HTTP-Client und Dummy-Cookies."""

    instance = load_instance("hhn")
    backend = IliasBackend(instance)
    transport = _MockTransport()
    real_client = httpx.Client(
        transport=transport,
        follow_redirects=False,
        headers={"User-Agent": "ilias-mcp-test"},
    )
    with patch(
        "ilias_mcp_core.ilias_html.fetch.Fetcher._build_client", return_value=real_client
    ), patch.object(
        backend.client.store, "load", return_value={"ilias-test": "1"}
    ):
        yield backend, transport


def test_courses_returns_hhn_memberships(hhn_backend: tuple[IliasBackend, _MockTransport]) -> None:
    backend, transport = hhn_backend
    transport.queue(
        "baseClass=ilmembershipoverviewgui",
        (FIXTURES / "membership.html").read_text(encoding="utf-8"),
    )
    transport.calls.clear()

    courses = backend.courses()

    # Sortierung nach Semester (neueste zuerst): WiSe 2026/27 > SoSe 2026 > None
    assert [c.id for c in courses] == [900101, 900102, 900105]
    first = courses[0]
    assert first.fullname == "Mathematik A (WiSe 2026/27)"
    assert first.type == "crs"
    assert first.semester == "WiSe 2026/27"
    assert first.url == f"{HHN_BASE_URL}/go/crs/900101"
    assert first.visible is True

    third = courses[1]
    assert third.fullname == "Physik SoSe 2026"
    assert third.semester == "SoSe 2026"

    offline_group = courses[2]
    assert offline_group.visible is False
    assert offline_group.type == "grp"

    assert any("ilmembershipoverviewgui" in url for url in transport.calls)


def test_courses_empty_membership_returns_empty_list(
    hhn_backend: tuple[IliasBackend, _MockTransport]
) -> None:
    """Bei einem ``alert-info``-Hinweis wird die Liste leer zurückgegeben (kein Fehler)."""

    backend, transport = hhn_backend
    transport.queue(
        "baseClass=ilmembershipoverviewgui",
        (FIXTURES / "membership_empty.html").read_text(encoding="utf-8"),
    )

    courses = backend.courses()
    assert courses == []


def test_totp_callback_uses_pyotp_secret() -> None:
    """``_totp_callback`` liefert einen pyotp-Code für ein base32-Secret (HHN Keycloak)."""

    from ilias_mcp.server import _totp_callback

    callback = _totp_callback("JBSWY3DPEHPK3PXP")
    assert callback is not None
    code = callback()
    # pyotp-Codes sind 6-stellige Zahlen
    assert isinstance(code, str)
    assert len(code) == 6
    assert code.isdigit()


def test_totp_callback_none_for_empty_secret() -> None:
    """Ohne Secret wird kein Callback gebaut (Server fordert dann nichts an)."""

    from ilias_mcp.server import _totp_callback

    assert _totp_callback(None) is None
    assert _totp_callback("") is None
    assert _totp_callback("   ") is None


def test_course_contents_hhn_blocks_modules_files(
    hhn_backend: tuple[IliasBackend, _MockTransport]
) -> None:
    """``IliasBackend.course_contents`` baut die Baumstruktur aus HHN-HTML auf."""

    backend, transport = hhn_backend
    transport.queue(
        "ref_id=900101",
        (FIXTURES / "course_900101.html").read_text(encoding="utf-8"),
    )

    sections = backend.course_contents(900101, depth=2)
    assert len(sections) == 1
    section = sections[0]
    assert section.name == "Objektblock"

    modules = section.modules
    names = [m.name for m in modules]
    assert names == ["Klausurordner", "Skript", "Kurslink"]

    folder, file_module, crsr = modules
    assert folder.modname == "fold"
    assert folder.ref_id == 900203
    # Tiefe 2 -> Ordner wird nicht aufgeklappt; children == [] (nicht None)
    assert folder.children == []

    assert file_module.modname == "file"
    assert file_module.ref_id == 900301
    assert file_module.suffix == "pdf"
    assert file_module.size == round(203.45 * 1024)
    assert file_module.size_text == "203.45 KB"
    assert file_module.timemodified is not None
    assert file_module.fileurl and "sendfile" in file_module.fileurl

    assert crsr.modname == "crsr"
    assert crsr.visible is False
    # COLLAPSED_TYPES -> children is None
    assert crsr.children is None


def test_course_contents_hhn_descends_into_folder(
    hhn_backend: tuple[IliasBackend, _MockTransport]
) -> None:
    """Tiefe ``None`` klappt den Ordner auf und liefert die Datei darin."""

    backend, transport = hhn_backend
    transport.queue(
        "ref_id=900101",
        (FIXTURES / "course_900101.html").read_text(encoding="utf-8"),
    )
    transport.queue(
        "ref_id=900203",
        (FIXTURES / "folder_900203.html").read_text(encoding="utf-8"),
    )

    sections = backend.course_contents(900101, depth=None)
    folder = sections[0].modules[0]
    assert folder.modname == "fold"
    assert folder.ref_id == 900203
    assert folder.children is not None
    assert len(folder.children) == 1
    file_node = folder.children[0]
    # Im Ordner liegt nur eine Datei -> FileNode
    from ilias_mcp_core.models import FileNode

    assert isinstance(file_node, FileNode)
    assert file_node.name == "Klausur.pdf"
    assert file_node.suffix == "pdf"
    assert file_node.size == 500 * 1024
    assert file_node.ref_id == 900401
    assert file_node.fileurl and "sendfile" in file_node.fileurl