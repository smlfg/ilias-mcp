"""Sicheres Debug-Logging (``--debug``).

Protokolliert werden ausschließlich URLs (Query-Werte geschwärzt, nur die
Parameternamen bleiben stehen), HTTP-Statuscodes, Formular-IDs und die
**Namen** von Formularfeldern. Nie Werte, Cookies, Header oder Seiteninhalte.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlsplit

import httpx

logger = logging.getLogger("ilias_core.debug")
logger.addHandler(logging.NullHandler())
logger.propagate = False

REDACTED = "…"
# Reine Routing-Parameter von ILIAS/Shibboleth; alle anderen Query-Werte
# (code, state, SAMLRequest, session_code, …) werden geschwärzt.
SAFE_QUERY_KEYS = frozenset({"baseClass", "cmd", "cmdClass", "execution", "client_id", "lang"})


def enable(stream=None) -> None:  # noqa: ANN001 - TextIO
    """Schaltet das Debug-Logging auf stderr (bzw. ``stream``) ein."""

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter("[debug] %(message)s"))
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)


def enabled() -> bool:
    return logger.isEnabledFor(logging.DEBUG)


def redact_url(url: str | httpx.URL) -> str:
    """``https://host/pfad?code=…&state=…``: Werte (außer Routing) und Fragment entfernt."""

    parts = urlsplit(str(url))
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    base = f"{parts.scheme}://{netloc}{parts.path}" if parts.scheme else parts.path
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if pairs:
        base += "?" + "&".join(
            f"{key}={_safe_value(value) if key in SAFE_QUERY_KEYS else REDACTED}"
            for key, value in pairs
        )
    return base


def _safe_value(value: str) -> str:
    return value if value.replace("_", "").replace("-", "").isalnum() and len(value) <= 40 else REDACTED


def debug(message: str, *args: object) -> None:
    logger.debug(message, *args)


def log_form(step: str, form_id: str | None, action: str, field_names: Iterable[str]) -> None:
    if not enabled():
        return
    names = ", ".join(sorted(set(field_names))) or "-"
    logger.debug(
        "%s: Formular id=%s action=%s Felder=[%s]",
        step,
        form_id or "-",
        redact_url(action),
        names,
    )


def _on_request(request: httpx.Request) -> None:
    if enabled():
        logger.debug("-> %s %s", request.method, redact_url(request.url))


def _on_response(response: httpx.Response) -> None:
    if not enabled():
        return
    location = response.headers.get("location")
    if location:
        logger.debug(
            "<- %s %s (Location: %s)",
            response.status_code,
            redact_url(response.request.url),
            redact_url(location),
        )
    else:
        logger.debug("<- %s %s", response.status_code, redact_url(response.request.url))


EVENT_HOOKS = {"request": [_on_request], "response": [_on_response]}
