"""Mitgliedschafts-Parser: "Meine Kurse und Gruppen" (``ilmembershipoverviewgui``).

Reine Funktion, kein I/O. Nur der Hauptinhalt (``#ilContentContainer
.panel-body``) wird gelesen – die Benachrichtigungen im Metabar nutzen ebenfalls
``.il-item`` und tragen eine Eigenschaft "Zeit", die nie als Semester zählt
(Spec §13.2). Datenfelder: ref_id, Typ (crs/grp), Titel, Beschreibung,
Sichtbarkeit, Semester, URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from . import links, props

PANEL_BODY_SELECTOR = "#ilContentContainer .panel-body"
ITEM_SELECTORS = ("li.il-std-item-container", "div.il-item.il-std-item", "div.il-item")
TITLE_LINK_SELECTORS = ("h4.il-item-title a", ".il-item-title a", "a")
DESC_SELECTOR = ".il-item-description"
PROPERTY_NAME_SELECTOR = ".il-item-property-name"
PROPERTY_VALUE_SELECTOR = ".il-item-property-value"
ICON_SELECTORS = ("div.media-left img.icon", "img.icon")
EMPTY_SELECTORS = (".alert-info",)

_ICON_TYPE_RE = re.compile(r"icon_([a-z0-9]+)\.svg", re.IGNORECASE)
_ZEITRAUM_NAMES = ("zeitraum", "kurszeitraum", "period")

_KNOWN_TYPES = ("crs", "grp")


@dataclass
class Membership:
    """Ein Kurs/eine Gruppe aus "Meine Kurse und Gruppen"."""

    ref_id: int
    type: str
    fullname: str
    description: str = ""
    visible: bool = True
    semester: str | None = None
    url: str = ""
    properties: list[tuple[str, str]] = field(default_factory=list)


def _text(element) -> str:
    return props.decode_text(element.get_text(" ", strip=True)) if element is not None else ""


def _properties(element) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for name_node in element.select(PROPERTY_NAME_SELECTOR):
        parent = name_node.parent
        value_node = parent.select_one(PROPERTY_VALUE_SELECTOR) if parent is not None else None
        if value_node is None:
            value_node = name_node.find_next_sibling(PROPERTY_VALUE_SELECTOR)
        pairs.append((_text(name_node), _text(value_node)))
    return pairs


def _icon_type(element) -> str | None:
    for selector in ICON_SELECTORS:
        icon = element.select_one(selector)
        if icon is not None:
            match = _ICON_TYPE_RE.search(icon.get("src", ""))
            if match:
                return match.group(1).lower()
    return None


def _parse_item(element, base_url: str) -> Membership | None:
    anchor = None
    for selector in TITLE_LINK_SELECTORS:
        anchor = element.select_one(selector)
        if anchor is not None and anchor.get("href"):
            break
    if anchor is None:
        return None

    href = anchor.get("href") or ""
    link_type, ref_id = links.parse_link(href, base_url)
    if ref_id is None:
        return None
    item_type = link_type if link_type in _KNOWN_TYPES else (_icon_type(element) or "crs")

    title = _text(anchor)
    description = _text(element.select_one(DESC_SELECTOR))
    properties = _properties(element)

    visible = True
    zeitraum = None
    for name, value in properties:
        if name.lower() == "status" and value.lower() == "offline":
            visible = False
        if name.lower() in _ZEITRAUM_NAMES and value:
            zeitraum = value

    semester = props.semester_from_title(title) or props.semester_from_zeitraum(zeitraum)
    url = f"{base_url.rstrip('/')}/go/{item_type}/{ref_id}"

    return Membership(
        ref_id=ref_id,
        type=item_type,
        fullname=title,
        description=description,
        visible=visible,
        semester=semester,
        url=url,
        properties=properties,
    )


def _panel_body(html: str):
    return BeautifulSoup(html, "html.parser").select_one(PANEL_BODY_SELECTOR)


def has_membership_structure(html: str) -> bool:
    """Enthält die Seite den Hauptinhalt der Kursliste?"""

    return _panel_body(html) is not None


def is_empty_membership(html: str) -> bool:
    """Leer-Hinweis (``.alert-info``) im Hauptinhalt der Kursliste."""

    body = _panel_body(html)
    if body is None:
        return False
    return any(body.select_one(selector) is not None for selector in EMPTY_SELECTORS)


def parse_memberships(html: str, base_url: str = "") -> list[Membership]:
    """Alle Mitgliedschaften des Hauptinhalts; genau ein Eintrag pro ref_id."""

    body = _panel_body(html)
    if body is None:
        return []
    memberships: list[Membership] = []
    seen: set[int] = set()
    for element in body.select(", ".join(ITEM_SELECTORS)):
        membership = _parse_item(element, base_url)
        if membership is None or membership.ref_id in seen:
            continue
        seen.add(membership.ref_id)
        memberships.append(membership)
    return memberships
