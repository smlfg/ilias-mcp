"""Prüfung, ob ein Login bzw. eine gespeicherte Session wirklich gültig ist.

Ein Login gilt erst als erfolgreich, wenn

1. die Redirect-Kette nach dem letzten IdP-Schritt wieder bei ILIAS endet,
2. ILIAS ein neues bzw. geändertes Session-Cookie gesetzt hat und
3. ``ilias.php?baseClass=ilDashboardGUI`` nicht auf ``login.php`` umleitet und
   ein Login-Merkmal (Abmelde-Link) zeigt.
"""

from __future__ import annotations

from enum import Enum
from urllib.parse import urlsplit

import httpx

from .. import debuglog
from ..errors import AuthenticationError, NetworkError, ParserError
from .parsers import is_ilias_logged_in, is_ilias_login_page

SESSION_COOKIE_NAME = "PHPSESSID"
NON_SESSION_COOKIES = frozenset({"ilClientId"})
# Cookies von Identity Providern, die nie als ILIAS-Cookies übernommen werden,
# auch wenn sie für eine gemeinsame Eltern-Domain gesetzt sind.
FOREIGN_COOKIES = frozenset({
    "KEYCLOAK_IDENTITY",
    "KEYCLOAK_IDENTITY_LEGACY",
    "KEYCLOAK_SESSION",
    "KEYCLOAK_SESSION_LEGACY",
    "KEYCLOAK_REMEMBER_ME",
    "AUTH_SESSION_ID",
    "AUTH_SESSION_ID_LEGACY",
    "KC_RESTART",
    "KC_AUTH_SESSION_HASH",
    "JSESSIONID",
    "shib_idp_session",
    "shib_idp_session_ss",
    "shib_idp_persistent_ss",
})


class DashboardState(str, Enum):
    OK = "ok"
    LOGIN = "login"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


def dashboard_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/ilias.php?baseClass=ilDashboardGUI"


def _origin(url: str | httpx.URL) -> tuple[str, int | None]:
    parts = urlsplit(str(url))
    default = {"http": 80, "https": 443}.get(parts.scheme)
    return (parts.hostname or "").lower(), parts.port or default


def same_origin(url: str | httpx.URL, base_url: str) -> bool:
    return _origin(url) == _origin(base_url)


def is_ilias_host_cookie(domain: str, name: str, base_url: str) -> bool:
    host = (urlsplit(base_url).hostname or "").lower()
    domain = (domain or "").lstrip(".").lower()
    if not domain or not name or name in FOREIGN_COOKIES:
        return False
    return host == domain or host.endswith("." + domain)


def ilias_cookies(client: httpx.Client, base_url: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for cookie in client.cookies.jar:
        if is_ilias_host_cookie(cookie.domain, cookie.name, base_url):
            cookies[cookie.name] = cookie.value or ""
    return cookies


def session_changed(before: dict[str, str], after: dict[str, str]) -> bool:
    if SESSION_COOKIE_NAME in after:
        return after[SESSION_COOKIE_NAME] != before.get(SESSION_COOKIE_NAME)

    def relevant(cookies: dict[str, str]) -> dict[str, str]:
        return {
            k: v
            for k, v in cookies.items()
            if k not in NON_SESSION_COOKIES and not k.startswith("SimpleSAML")
        }

    old = relevant(before)
    return any(old.get(name) != value for name, value in relevant(after).items())


def check_dashboard(client: httpx.Client, base_url: str) -> DashboardState:
    """Ruft das Dashboard ab und klassifiziert die Antwort."""

    try:
        response = client.get(dashboard_url(base_url))
    except httpx.HTTPError as exc:
        raise NetworkError("Netzwerkfehler bei der Session-Prüfung.") from exc

    state = _classify_dashboard(response, base_url)
    debuglog.debug(
        "Prüfung Dashboard: HTTP %s, Ziel %s -> %s",
        response.status_code,
        debuglog.redact_url(response.url),
        state.value,
    )
    return state


def _classify_dashboard(response: httpx.Response, base_url: str) -> DashboardState:
    if response.status_code in (401, 403):
        return DashboardState.LOGIN
    if response.status_code >= 400:
        raise NetworkError(f"Serverfehler (HTTP {response.status_code}).")
    if not same_origin(response.url, base_url):
        return DashboardState.FOREIGN
    html = response.text
    if urlsplit(str(response.url)).path.endswith("login.php") or is_ilias_login_page(html):
        return DashboardState.LOGIN
    if is_ilias_logged_in(html):
        return DashboardState.OK
    return DashboardState.UNKNOWN


def _yes_no(value: bool) -> str:
    return "ja" if value else "nein"


def verify_login(
    client: httpx.Client,
    base_url: str,
    final_response: httpx.Response,
    cookies_before: dict[str, str],
) -> dict[str, str]:
    """Bestätigt den Login und liefert die zu speichernden ILIAS-Cookies."""

    returned = same_origin(final_response.url, base_url)
    debuglog.debug("Prüfung: Redirect-Kette endet bei ILIAS: %s", _yes_no(returned))
    if not returned:
        raise ParserError(
            "Nach dem Login wurde nicht zu ILIAS zurückgeleitet "
            "(unerwartete Seite beim Identity Provider)."
        )

    changed = session_changed(cookies_before, ilias_cookies(client, base_url))
    debuglog.debug("Prüfung: neues ILIAS-Session-Cookie: %s", _yes_no(changed))
    if not changed:
        raise AuthenticationError(
            "ILIAS hat nach dem Login keine neue Session ausgestellt. "
            "Login nicht bestätigt, nichts gespeichert."
        )

    state = check_dashboard(client, base_url)
    if state in (DashboardState.LOGIN, DashboardState.FOREIGN):
        raise AuthenticationError(
            "ILIAS leitet das Dashboard weiterhin zur Anmeldung um. "
            "Login nicht bestätigt, nichts gespeichert."
        )
    if state is DashboardState.UNKNOWN:
        raise ParserError(
            "Dashboard ohne Login-Merkmal (unerwartetes HTML). "
            "Login nicht bestätigt, nichts gespeichert."
        )

    cookies = ilias_cookies(client, base_url)
    if not cookies:
        raise ParserError("Keine ILIAS-Session-Cookies nach dem Login gefunden.")
    return cookies
