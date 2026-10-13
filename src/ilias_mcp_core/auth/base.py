"""Gemeinsame Basis der Auth-Adapter (``auth = "..."`` in der Konfiguration)."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from .. import debuglog
from ..errors import NetworkError, ParserError
from . import parsers
from .verify import ilias_cookies

OtpCallback = Callable[[], str]

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class BaseLoginFlow:
    """Headless-Login in einem ``httpx.Client``. Liefert nur ILIAS-Cookies."""

    name: str = ""
    #: Pfad relativ zur Basis-URL, an dem der Login beginnt (auch für ``--browser``).
    start_path: str = ""
    #: Ob der Adapter einen TOTP-Code abfragen kann.
    uses_totp: bool = False

    def __init__(self, base_url: str, client: httpx.Client) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client

    def authenticate(
        self,
        username: str,
        password: str,
        otp_callback: OtpCallback | None = None,
    ) -> dict[str, str]:
        raise NotImplementedError

    @property
    def start_url(self) -> str:
        return f"{self.base_url}/{self.start_path}"

    # -- HTTP-Helfer -----------------------------------------------------
    def _get(self, url: str) -> httpx.Response:
        try:
            response = self.client.get(url)
        except httpx.HTTPError as exc:
            raise NetworkError("Netzwerkfehler beim Login.") from exc
        self._ensure_no_server_error(response)
        return response

    def _post(self, url: str, data: dict[str, str]) -> httpx.Response:
        try:
            response = self.client.post(url, data=data)
        except httpx.HTTPError as exc:
            raise NetworkError("Netzwerkfehler während des Logins.") from exc
        self._ensure_no_server_error(response)
        return response

    @staticmethod
    def _ensure_no_server_error(response: httpx.Response) -> None:
        if response.status_code >= 500:
            raise NetworkError(f"Serverfehler (HTTP {response.status_code}).")

    @staticmethod
    def _ensure_safe_credential_target(url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme == "https" or (parts.hostname or "").lower() in _LOOPBACK:
            return
        raise ParserError(
            "Anmeldeformular würde das Passwort unverschlüsselt senden "
            f"({debuglog.redact_url(url)}). Abgebrochen."
        )

    def _ilias_cookies(self) -> dict[str, str]:
        return ilias_cookies(self.client, self.base_url)

    @staticmethod
    def _log_form(step: str, form: parsers.HtmlForm) -> None:
        debuglog.log_form(step, form.form_id, form.action, form.field_names)

    @staticmethod
    def _log_unknown_page(step: str, response: httpx.Response) -> None:
        if not debuglog.enabled():
            return
        debuglog.debug(
            "%s: unerwartete Seite HTTP %s %s",
            step,
            response.status_code,
            debuglog.redact_url(response.url),
        )
        forms = parsers.all_forms(response.text, str(response.url))
        if not forms:
            debuglog.debug("%s: keine Formulare auf der Seite", step)
        for form in forms:
            debuglog.log_form(f"{step} (gefunden)", form.form_id, form.action, form.field_names)
