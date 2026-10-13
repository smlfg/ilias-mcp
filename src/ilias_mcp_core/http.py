"""HTTP-Helfer für alle Backends: eigener User-Agent, Timeouts, sicheres Debug-Logging.

- ``build_client``: httpx-Client für den ILIAS-Web-Login (Redirects, Cookies, HTML).
- ``HttpClient`` + ``json_*``: JSON-Webservice-Aufrufe (Moodle REST). Folgt nie
  Redirects, ignoriert Proxy-Variablen und schreibt keine Secrets in Fehlertexte.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from . import debuglog
from .config import Config
from .errors import NetworkError, ParserError
from .version import __version__

USER_AGENT = f"ilias-cli/{__version__}"
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def user_agent() -> str:
    return USER_AGENT


def build_client(config: Config, *, follow_redirects: bool = True) -> httpx.Client:
    """Erzeugt einen ``httpx.Client`` mit eigenem User-Agent und Timeout (ILIAS)."""

    return httpx.Client(
        follow_redirects=follow_redirects,
        timeout=DEFAULT_TIMEOUT,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        event_hooks=debuglog.EVENT_HOOKS,
    )


class HttpClient:
    """Dünner httpx-Wrapper. `follow_redirects=False`: wir folgen nie blind einem
    Redirect (dort könnten Zugangsdaten oder der Token landen)."""

    def __init__(self, base_url: str, timeout: float | httpx.Timeout = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"User-Agent": user_agent(), "Accept": "application/json"},
            follow_redirects=False,
            trust_env=False,  # kein Proxy aus der Umgebung: nichts verlässt den Rechner ungefragt
        )

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- Requests -------------------------------------------------------
    def post_form(self, path: str, data: dict[str, str], *, label: str = "Anfrage") -> httpx.Response:
        try:
            response = self._client.post(path, data=data)
        except httpx.RequestError as exc:
            raise NetworkError(
                f"{label} fehlgeschlagen: {type(exc).__name__} ({exc}).",
                hint=f"Erreichbarkeit von {self.base_url} prüfen (ggf. VPN).",
            ) from None
        if response.status_code >= 500:
            raise NetworkError(f"{label}: Serverfehler HTTP {response.status_code} von {self.base_url}.")
        if 300 <= response.status_code < 400:
            raise ParserError(
                f"{label}: unerwartete Weiterleitung (HTTP {response.status_code}) von {self.base_url}.",
                hint="Die Basis-URL dürfte auf die Moodle-Wurzel zeigen (ohne /login/index.php).",
            )
        return response

    def get(self, path: str, *, label: str = "Anfrage") -> httpx.Response:
        try:
            response = self._client.get(path)
        except httpx.RequestError as exc:
            raise NetworkError(f"{label} fehlgeschlagen: {type(exc).__name__} ({exc}).") from None
        if response.status_code >= 500:
            raise NetworkError(f"{label}: Serverfehler HTTP {response.status_code} von {self.base_url}.")
        return response


def json_value(response: httpx.Response, *, label: str) -> Any:
    """JSON-Wert (Objekt oder Liste) aus einer Antwort - sonst Parser-Fehler (Exit 5)."""
    text = response.text
    if not text.strip():
        raise ParserError(f"{label}: leere Antwort (HTTP {response.status_code}).")
    if response.status_code >= 400 and not text.lstrip().startswith(("{", "[")):
        raise ParserError(
            f"{label}: unerwartete Antwort (HTTP {response.status_code}, {response.headers.get('Content-Type', 'unbekannter Typ')}).",
            hint="Die Adresse liefert kein JSON - vermutlich falsche Basis-URL oder Wartungsseite.",
        )
    try:
        data = json.loads(text)
    except ValueError:
        raise ParserError(
            f"{label}: Antwort ist kein JSON (HTTP {response.status_code}).",
            hint="Erwartet wird eine JSON-Antwort des Moodle-Webservice.",
        ) from None
    return data


def json_body(response: httpx.Response, *, label: str) -> dict[str, Any]:
    """JSON-Objekt aus einer Antwort - sonst Parser-Fehler (Exit 5)."""
    data = json_value(response, label=label)
    if not isinstance(data, dict):
        raise ParserError(f"{label}: JSON ist kein Objekt (Typ {type(data).__name__}).")
    return data


def json_list_body(response: httpx.Response, *, label: str) -> list[Any]:
    """JSON-Liste aus einer Antwort - sonst Parser-Fehler (Exit 5)."""
    data = json_value(response, label=label)
    if not isinstance(data, list):
        raise ParserError(f"{label}: JSON ist keine Liste (Typ {type(data).__name__}).")
    return data
