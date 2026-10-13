"""Headless-Login-Flow gegen ILIAS + Keycloak (``auth = "oidc-keycloak"``).

Der Ablauf bildet den normalen Browser-Flow nach:

1. ``GET {base_url}/openidconnect.php`` (302 zum Keycloak-Login)
2. Keycloak-Login-Formular parsen (action-URL + alle hidden inputs)
3. ``POST username/password``
4. Falls nötig: Keycloak-TOTP-Formular parsen und ``POST otp``
5. Redirects zurück zu ILIAS folgen und den Login verifizieren
   (siehe :mod:`ilias_core.auth.verify`)

Es werden ausschließlich die Cookies der ILIAS-Domain übernommen.
"""

from __future__ import annotations

import httpx

from ..config import AUTH_OIDC_KEYCLOAK
from ..errors import AuthenticationError, ParserError
from . import parsers
from .base import BaseLoginFlow, OtpCallback
from .verify import same_origin, verify_login


class KeycloakLoginFlow(BaseLoginFlow):
    """Führt den OIDC-Login (Keycloak, optional TOTP) aus."""

    name = AUTH_OIDC_KEYCLOAK
    start_path = "openidconnect.php"
    uses_totp = True

    # -- öffentliche API -------------------------------------------------
    def authenticate(
        self,
        username: str,
        password: str,
        otp_callback: OtpCallback | None = None,
    ) -> dict[str, str]:
        response = self._get(self.start_url)
        login_form = parsers.parse_keycloak_login(response.text, str(response.url))
        if login_form is None:
            self._log_unknown_page("Keycloak-Login", response)
            raise ParserError(
                "Keycloak-Login-Formular konnte nicht geparst werden "
                "(unerwartetes HTML)."
            )
        cookies_before = self._ilias_cookies()

        response = self._submit_credentials(login_form, username, password)
        response = self._complete_totp_if_required(response, otp_callback)

        if not same_origin(response.url, self.base_url):
            html = response.text
            if parsers.is_keycloak_login(html) or parsers.is_keycloak_totp(html):
                message = parsers.extract_keycloak_error(html)
                raise AuthenticationError(message or "Anmeldung fehlgeschlagen.")
            self._log_unknown_page("Keycloak nach Anmeldung", response)
        return verify_login(self.client, self.base_url, response, cookies_before)

    # -- interne Schritte ------------------------------------------------
    def _submit_credentials(
        self, form: parsers.HtmlForm, username: str, password: str
    ) -> httpx.Response:
        self._log_form("Keycloak-Login", form)
        self._ensure_safe_credential_target(form.action)
        data = dict(form.fields)
        data["username"] = username
        data["password"] = password
        return self._post(form.action, data)

    def _complete_totp_if_required(
        self,
        response: httpx.Response,
        otp_callback: OtpCallback | None,
    ) -> httpx.Response:
        html = response.text

        if parsers.is_keycloak_login(html):
            message = parsers.extract_keycloak_error(html)
            raise AuthenticationError(message or "Benutzername oder Passwort ist falsch.")

        totp_form = parsers.parse_keycloak_totp(html, str(response.url))
        if totp_form is None:
            return response

        self._log_form("Keycloak-TOTP", totp_form)
        if otp_callback is None:
            raise AuthenticationError("TOTP-Code erforderlich.")
        otp = otp_callback() or ""
        if not otp.strip():
            raise AuthenticationError("Kein TOTP-Code eingegeben.")

        data = dict(totp_form.fields)
        data["otp"] = otp
        response = self._post(totp_form.action, data)

        if parsers.is_keycloak_totp(response.text) or parsers.is_keycloak_login(
            response.text
        ):
            message = parsers.extract_keycloak_error(response.text)
            raise AuthenticationError(message or "TOTP-Code ist ungültig.")
        return response
