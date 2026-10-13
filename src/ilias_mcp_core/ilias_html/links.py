"""Link-Parser: ``parse_link(href, base_url) -> (typ | None, ref_id | None)``.

Reine Funktion, kein I/O. Reihenfolge und Typtabelle nach Spec §13.5. Es wird
nur der Pfad/die Query ausgewertet, Host und Session-Parameter sind egal.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

_GO_RE = re.compile(r"/go/([a-z]+)/(\d+)(?:/|$)", re.IGNORECASE)
_GOTO_PATH_RE = re.compile(r"goto\.php/([a-z]+)/(\d+)(?:/|$)", re.IGNORECASE)
_TARGET_RE = re.compile(r"^([a-z]+)_(\d+)", re.IGNORECASE)

# Typtabelle für ``ilias.php?…ref_id=<ref>`` (Spec §13.5). Query-Werte
# case-insensitiv. ``None`` -> Typ erst über das Symbol bestimmbar.
_QUERY_TYPE_TABLE: tuple[tuple[tuple[str, str], str], ...] = (
    (("cmdclass", "ilobjfilegui"), "file"),
    (("cmd", "sendfile"), "file"),
    (("cmdclass", "ilobjtestgui"), "tst"),
    (("baseclass", "illinkresourcehandlergui"), "webr"),
    (("baseclass", "ilwikihandlergui"), "wiki"),
    (("baseclass", "ilexercisehandlergui"), "exc"),
)


def _query_type(query: dict[str, str]) -> str | None:
    for (key, needle), typ in _QUERY_TYPE_TABLE:
        if query.get(key, "").lower() == needle:
            return typ
    return None


def parse_link(href: str | None, base_url: str = "") -> tuple[str | None, int | None]:
    """Typ und ref_id aus einem ILIAS-Link.

    ``#``, leere und unbekannte Links -> ``(None, None)``. Unbekannte Typen
    (z. B. Plugins wie ``xvid``) behalten ihr Kürzel.
    """

    del base_url  # Pfad/Query genügen; Host spielt für die Erkennung keine Rolle
    if not href:
        return (None, None)
    href = href.strip()
    if not href or href == "#" or href.startswith("#"):
        return (None, None)

    parts = urlsplit(href)
    path = parts.path or ""
    query = {k.lower(): v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items() if v}

    match = _GO_RE.search(path)
    if match:
        return (match.group(1).lower(), int(match.group(2)))

    match = _GOTO_PATH_RE.search(path)
    if match:
        return (match.group(1).lower(), int(match.group(2)))

    if path.endswith(("goto.php", "/goto.php")):
        target = query.get("target", "")
        match = _TARGET_RE.match(target)
        if match:
            return (match.group(1).lower(), int(match.group(2)))
        return (None, None)

    if path.endswith(("ilias.php", "/ilias.php")):
        ref = query.get("ref_id", "")
        if not ref.isdigit():
            return (None, None)
        return (_query_type(query), int(ref))

    return (None, None)
