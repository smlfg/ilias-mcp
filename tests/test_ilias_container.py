"""Container-Parser-Tests gegen HHN-typische Kurs-/Ordner-HTML-Fixtures.

Deckt den Parser ab, den :meth:`IliasBackend.course_contents` für ILIAS-Kurs-
und Ordnerseiten verwendet. Reine Parser-Tests, kein I/O, keine echten Logins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ilias_mcp_core.ilias_html.container import (
    flatten_items,
    has_container_structure,
    is_empty_container,
    parse_container,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ilias_hhn"
HHN_BASE_URL = "https://ilias.hs-heilbronn.de"


@pytest.fixture
def course_html() -> str:
    return (FIXTURES / "course_900101.html").read_text(encoding="utf-8")


def test_parse_container_hhn_fixture(course_html: str) -> None:
    """Ein Block (``Objektblock``) mit Ordner, Datei und offline Kurslink."""

    blocks = parse_container(course_html, HHN_BASE_URL)
    assert [b.name for b in blocks] == ["Objektblock"]
    assert blocks[0].ref_id == 900181

    items = flatten_items(blocks)
    names = [i.name for i in items]
    assert names == ["Klausurordner", "Skript", "Kurslink"]

    folder, file_item, crsr = items
    assert folder.type == "fold" and folder.ref_id == 900203
    assert file_item.type == "file" and file_item.ref_id == 900301
    assert crsr.type == "crsr" and crsr.ref_id == 900901
    assert crsr.offline is True


def test_container_structure_and_empty(course_html: str) -> None:
    assert has_container_structure(course_html) is True
    assert is_empty_container(course_html) is False
    empty = (
        '<div class="ilContainerBlock" id="bl_cntr_1">'
        '<div class="ilNoItems">leer</div></div>'
    )
    assert parse_container(empty, HHN_BASE_URL) == []
    assert is_empty_container(empty) is True
    assert has_container_structure("<h1>Wartung</h1>") is False