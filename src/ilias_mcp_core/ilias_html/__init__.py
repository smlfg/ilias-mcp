"""Reine ILIAS-HTML-Parser (HTML + base_url -> Dataclasses), ohne I/O.

Ein Modul pro Seitentyp (``membership``, ``container``, ``links``, ``props``),
Selektoren als zentrale Konstanten mit Fallbacks. Diese Funktionen sind die
Basis für ``courses``/``ls`` des ILIAS-Backends und lassen sich isoliert testen.
"""

from __future__ import annotations

from . import container, links, membership, props

__all__ = ["container", "links", "membership", "props"]
