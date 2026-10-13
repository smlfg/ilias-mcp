"""Orchestrierung: Login, Status und Logout.

Alle Kernfunktionen arbeiten ohne Prompts und ohne ``print``. Passwort und
TOTP werden als Parameter bzw. Callback übergeben. Die Cookies werden nur
im ``SessionStore`` abgelegt und nie zurückgegeben. Gespeichert wird erst,
wenn der Login verifiziert ist (siehe :mod:`ilias_core.auth.verify`).
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from . import browser as browser_module
from .auth import get_adapter
from .auth.verify import DashboardState, check_dashboard
from .config import Config, load_config
from .errors import AuthenticationError, NotLoggedInError, ParserError, SessionExpiredError
from .http import build_client
from .models import LoginResult, SessionStatus
from .session import SessionStore

OtpCallback = Callable[[], str]


class IliasClient:
    """Fassade über Auth, Session und Konfiguration."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        session_store: SessionStore | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config or load_config()
        self.store = session_store or SessionStore(self.config)
        self._http_client = http_client

    @property
    def uses_totp(self) -> bool:
        return get_adapter(self.config.auth).uses_totp

    # -- Login -----------------------------------------------------------
    def login(
        self,
        username: str,
        password: str,
        otp_callback: OtpCallback | None = None,
    ) -> LoginResult:
        adapter = get_adapter(self.config.auth)
        client = self._client()
        try:
            cookies = adapter(self.config.base_url, client).authenticate(
                username, password, otp_callback
            )
        finally:
            self._close_if_owned(client)

        self.store.save(cookies)
        return self._login_result(adapter.name, "Login erfolgreich. Session wurde gespeichert.")

    def login_with_browser(self, *, timeout: int = browser_module.DEFAULT_TIMEOUT) -> LoginResult:
        adapter = get_adapter(self.config.auth)
        cookies = browser_module.login_with_browser(
            self.config.base_url, start_path=adapter.start_path, timeout=timeout
        )
        state = self._dashboard_state(cookies)
        if state is DashboardState.UNKNOWN:
            raise ParserError(
                "Dashboard ohne Login-Merkmal (unerwartetes HTML). Nichts gespeichert."
            )
        if state is not DashboardState.OK:
            raise AuthenticationError(
                "Die Browser-Session wird von ILIAS nicht akzeptiert. Nichts gespeichert."
            )
        self.store.save(cookies)
        return self._login_result(
            "browser", "Login im Browser erfolgreich. Session wurde gespeichert."
        )

    def _login_result(self, method: str, message: str) -> LoginResult:
        return LoginResult(
            authenticated=True,
            base_url=self.config.base_url,
            client_id=self.config.client_id,
            method=method,
            message=message,
            instance=self.config.instance,
        )

    # -- Status ----------------------------------------------------------
    def status(self) -> SessionStatus:
        cookies = self.store.load()
        if not cookies:
            raise NotLoggedInError(
                "Nicht eingeloggt. Bitte zuerst `ilias login` ausführen."
            )

        state = self._dashboard_state(cookies)
        if state is DashboardState.UNKNOWN:
            raise ParserError(
                "Dashboard ohne Login-Merkmal (unerwartetes HTML). Session-Status unklar."
            )
        if state is not DashboardState.OK:
            raise SessionExpiredError(
                "Session abgelaufen. Bitte erneut `ilias login` ausführen."
            )

        return SessionStatus(
            authenticated=True,
            base_url=self.config.base_url,
            client_id=self.config.client_id,
            message="Session ist gültig.",
            instance=self.config.instance,
        )

    def _dashboard_state(self, cookies: dict[str, str]) -> DashboardState:
        client = self._client()
        try:
            host = (urlsplit(self.config.base_url).hostname or "").lower()
            for name, value in cookies.items():
                client.cookies.set(name, value, domain=host)
            return check_dashboard(client, self.config.base_url)
        finally:
            self._close_if_owned(client)

    # -- Logout ----------------------------------------------------------
    def logout(self) -> bool:
        """Löscht die gespeicherte Session. Gibt zurück, ob eine existierte."""

        existed = self.store.load() is not None
        self.store.clear()
        return existed

    # -- Helfer ----------------------------------------------------------
    def _client(self) -> httpx.Client:
        if self._http_client is not None:
            return self._http_client
        return build_client(self.config)

    def _close_if_owned(self, client: httpx.Client) -> None:
        if self._http_client is None:
            client.close()
