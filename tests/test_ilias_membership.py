"""Membership-Parser-Tests gegen HHN-typische HTML-Fixtures.

Deckt die Parser ab, die :meth:`IliasBackend.courses` für die ILIAS-Instanzen
(z. B. HHN) verwendet. Reine Parser-Tests, kein I/O, keine echten Logins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ilias_mcp_core.ilias_html.membership import (
    has_membership_structure,
    is_empty_membership,
    parse_memberships,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ilias_hhn"
HHN_BASE_URL = "https://ilias.hs-heilbronn.de"


@pytest.fixture
def membership_html() -> str:
    return (FIXTURES / "membership.html").read_text(encoding="utf-8")


@pytest.fixture
def membership_empty_html() -> str:
    return (FIXTURES / "membership_empty.html").read_text(encoding="utf-8")


def test_parse_memberships_hhn_fixture(membership_html: str) -> None:
    """Drei Mitgliedschaften aus HHN-Liste (Kurs, Gruppe offline, Kurs SoSe)."""

    memberships = parse_memberships(membership_html, HHN_BASE_URL)
    assert [m.ref_id for m in memberships] == [900101, 900105, 900102]
    first, group, third = memberships
    assert first.type == "crs"
    assert first.fullname == "Mathematik A (WiSe 2026/27)"
    assert first.semester == "WiSe 2026/27"
    assert first.description == "Beispielbeschreibung"
    assert first.url == f"{HHN_BASE_URL}/go/crs/900101"
    assert first.visible is True
    assert group.type == "grp"
    assert group.fullname == "Lerngruppe"
    assert group.visible is False
    assert third.semester == "SoSe 2026"


def test_membership_structure_and_empty(
    membership_html: str, membership_empty_html: str
) -> None:
    assert has_membership_structure(membership_html) is True
    assert is_empty_membership(membership_html) is False
    assert is_empty_membership(membership_empty_html) is True
    assert parse_memberships(membership_empty_html, HHN_BASE_URL) == []
    assert has_membership_structure("<h1>Wartung</h1>") is False


def test_metabar_notification_is_not_a_membership(membership_html: str) -> None:
    """``Neue Nachricht`` aus dem Metabar ist KEINE Mitgliedschaft (Spec §13.2)."""

    titles = [m.fullname for m in parse_memberships(membership_html, HHN_BASE_URL)]
    assert "Neue Nachricht" not in titles