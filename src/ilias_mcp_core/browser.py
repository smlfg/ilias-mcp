"""Optionales Browser-Login-Verfahren (Playwright).

Playwright ist ein optionales Extra (``[project.optional-dependencies].browser``)
und wird deshalb erst zur Laufzeit importiert. Der Browser ist **sichtbar**,
der Nutzer meldet sich selbst an (inkl. 2FA), danach werden ausschließlich die
ILIAS-Cookies übernommen. Ob die Session gültig ist, prüft danach
:class:`ilias_core.client.IliasClient` mit einem Dashboard-Request.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from . import debuglog
from .auth.verify import is_ilias_host_cookie, same_origin
from .errors import AuthenticationError, BrowserUnavailableError

DEFAULT_TIMEOUT = 300
INSTALL_HINT = (
    "Playwright ist nicht installiert. Installiere das optionale Extra mit "
    "`uv sync --extra browser && uv run playwright install chromium`."
)
_POST_LOGIN_PAGES = ("ilias.php", "goto.php")


def is_post_login_url(url: str, base_url: str) -> bool:
    """True, sobald der Browser nach der Anmeldung wieder auf einer ILIAS-Seite ist."""

    if not same_origin(url, base_url):
        return False
    lowered = url.lower()
    if "ilstartupgui" in lowered:
        return False
    path = urlsplit(url).path
    return path.endswith(_POST_LOGIN_PAGES) or "/goto.php/" in path


def login_with_browser(
    base_url: str,
    *,
    start_path: str = "openidconnect.php",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, str]:
    """Öffnet einen sichtbaren Browser und übernimmt nur die ILIAS-Cookies."""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserUnavailableError(INSTALL_HINT) from exc

    base_url = base_url.rstrip("/")
    start_url = f"{base_url}/{start_path}"
    timeout_ms = timeout * 1000

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=False)
        except PlaywrightError as exc:
            raise BrowserUnavailableError(
                "Chromium für Playwright fehlt. Bitte `uv run playwright install chromium` ausführen."
            ) from exc
        try:
            context = browser.new_context()
            page = context.new_page()
            debuglog.debug("Browser: öffne %s", debuglog.redact_url(start_url))
            page.goto(start_url)
            page.wait_for_url(lambda url: is_post_login_url(url, base_url), timeout=timeout_ms)
            debuglog.debug("Browser: zurück bei ILIAS %s", debuglog.redact_url(page.url))
            cookies = extract_ilias_cookies(context.cookies(), base_url)
        except PlaywrightError as exc:
            raise AuthenticationError(
                f"Browser-Login nicht abgeschlossen ({type(exc).__name__}, z. B. Fenster "
                f"geschlossen oder Zeitlimit von {timeout} s überschritten)."
            ) from None
        finally:
            browser.close()

    debuglog.debug("Browser: %s ILIAS-Cookies übernommen", len(cookies))
    if not cookies:
        raise AuthenticationError("Keine ILIAS-Cookies aus dem Browser übernommen.")
    return cookies


def extract_ilias_cookies(raw_cookies: list[dict], base_url: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for cookie in raw_cookies:
        name = cookie.get("name") or ""
        if is_ilias_host_cookie(cookie.get("domain") or "", name, base_url):
            cookies[name] = cookie.get("value", "")
    return cookies
