"""Container-Parser: ILIAS-Blöcke und Objekte einer Kurs-/Ordner-/Gruppenseite.

Reine Funktion (HTML + base_url -> Dataclasses), kein I/O. Markup nach Spec
§6.2/§13.3: Legacy-Liste (``.ilContainerBlock`` → Abschnitte,
``.ilContainerListItemOuter`` → Objekte). Symbole sind nicht verlässlich, der
Typ kommt primär aus dem Link (Fallback Symbol). Doppelte Einträge (Dropdown-,
``href="#"``- und ``.glyph``-Links) werden vermieden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from . import links, props

BLOCK_SELECTOR = "div.ilContainerBlock"
HEADER_SELECTORS = ("h2.ilContainerBlockHeader", ".ilContainerBlockHeader", "h2.ilHeader")
ITEM_SELECTOR = "div.ilContainerListItemOuter"
TITLE_LINK_SELECTORS = (
    "a.il_ContainerItemTitle",
    "h3.il_ContainerItemTitle a",
    ".il_ContainerItemTitle a",
)
PROPERTY_SELECTORS = ("div.il_ItemProperties span.il_ItemProperty", "span.il_ItemProperty")
ALERT_SELECTOR = ".il_ItemAlertProperty"
ICON_SELECTORS = ("img.ilListItemIcon", "div.ilContainerListItemIcon img", "img")
EMPTY_SELECTORS = (".ilNoItems", ".alert-info")

_STORE_URL_RE = re.compile(r"cont_block_id=itgr_(\d+)", re.IGNORECASE)
_DATA_LIST_ITEM_RE = re.compile(r"lg_div_(\d+)_pref_(\d+)")
_ICON_TYPE_RE = re.compile(r"icon_([a-z0-9]+)\.svg", re.IGNORECASE)


@dataclass
class ContainerItem:
    """Ein Eintrag ``.ilContainerListItemOuter`` (Typ primär aus dem Link)."""

    ref_id: int | None
    name: str
    type: str
    href: str = ""
    target: str = ""
    props: list[str] = field(default_factory=list)
    offline: bool = False
    icon_alt: str = ""
    icon_src: str = ""


@dataclass
class ContainerBlock:
    """Ein Abschnitt ``.ilContainerBlock`` (Objektgruppen ``itgr`` inklusive)."""

    name: str
    ref_id: int | None
    items: list[ContainerItem] = field(default_factory=list)


def _text(element) -> str:
    return props.decode_text(element.get_text(" ", strip=True)) if element is not None else ""


def _first_icon(element):
    for selector in ICON_SELECTORS:
        found = element.select_one(selector)
        if found is not None:
            return found
    return None


def _data_ref(element) -> int | None:
    raw = element.get("data-list-item-id") or ""
    match = _DATA_LIST_ITEM_RE.search(raw)
    return int(match.group(1)) if match else None


def _parse_item(element, base_url: str) -> ContainerItem:
    del base_url  # parse_link wertet nur Pfad/Query aus
    anchor = None
    href = ""
    target = ""
    for selector in TITLE_LINK_SELECTORS:
        for candidate in element.select(selector):
            candidate_href = candidate.get("href") or ""
            if candidate_href in ("", "#"):
                continue
            anchor = candidate
            href = candidate_href
            target = candidate.get("target") or ""
            break
        if anchor is not None:
            break

    name = _text(anchor)
    link_type, link_ref = links.parse_link(href)
    data_ref = _data_ref(element)

    icon = _first_icon(element)
    icon_src = icon.get("src", "") if icon is not None else ""
    icon_alt = icon.get("alt", "") if icon is not None else ""
    icon_match = _ICON_TYPE_RE.search(icon_src)
    icon_type = icon_match.group(1).lower() if icon_match else None

    item_type = link_type or icon_type or "other"
    ref_id = data_ref if data_ref is not None else link_ref

    properties: list[str] = []
    for selector in PROPERTY_SELECTORS:
        found = element.select(selector)
        if found:
            properties = [props.decode_text(node.get_text(" ", strip=True)) for node in found]
            break

    offline = element.select_one(ALERT_SELECTOR) is not None

    return ContainerItem(
        ref_id=ref_id,
        name=name,
        type=item_type,
        href=href,
        target=target,
        props=properties,
        offline=offline,
        icon_alt=icon_alt,
        icon_src=icon_src,
    )


def _content(soup: BeautifulSoup):
    return soup.select_one("#ilContentContainer") or soup


def has_container_structure(html: str) -> bool:
    """Enthält die Seite mindestens einen ``.ilContainerBlock``?"""

    return BeautifulSoup(html, "html.parser").select_one(BLOCK_SELECTOR) is not None


def is_empty_container(html: str) -> bool:
    """Leerer Kurs/Ordner mit Leer-Hinweis (``.ilNoItems``/``.alert-info``)."""

    soup = BeautifulSoup(html, "html.parser")
    return any(soup.select_one(selector) is not None for selector in EMPTY_SELECTORS)


def parse_container(html: str, base_url: str = "") -> list[ContainerBlock]:
    """Alle Blöcke (Abschnitte) der Seite in Seitenreihenfolge."""

    soup = BeautifulSoup(html, "html.parser")
    content = _content(soup)
    blocks: list[ContainerBlock] = []
    for block in content.select(BLOCK_SELECTOR):
        header = None
        for selector in HEADER_SELECTORS:
            header = block.select_one(selector)
            if header is not None:
                break
        name = _text(header)

        itgr_ref = None
        store_url = block.get("data-store-url") or ""
        match = _STORE_URL_RE.search(store_url)
        if match:
            itgr_ref = int(match.group(1))

        items = [_parse_item(item, base_url) for item in block.select(ITEM_SELECTOR)]
        if not name and not items:
            continue
        blocks.append(ContainerBlock(name=name, ref_id=itgr_ref, items=items))
    return blocks


def flatten_items(blocks: list[ContainerBlock]) -> list[ContainerItem]:
    """Alle Objekte der Blöcke in Seitenreihenfolge (für Ordnerebenen)."""

    return [item for block in blocks for item in block.items]
