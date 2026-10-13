"""Headless-Login über SAML mit Shibboleth-IdP (``auth = "saml-shibboleth"``).

Ablauf (wie im Browser, ohne JavaScript):

1. ``GET {base_url}/saml.php`` und den Redirects zum IdP folgen
2. ``form#login-form`` parsen (action, hidden ``csrf_token``) und
   ``j_username``, ``j_password``, ``_eventId_proceed`` senden
3. Optional: Attributfreigabe (Consent) mit der vorausgewählten Option
   bestätigen, optional die Client-Storage-Seite abschicken
4. Auto-Submit-Formular mit ``SAMLResponse`` (+ ``RelayState``) an ILIAS senden
5. Redirects zu ILIAS folgen und den Login verifizieren
   (siehe :mod:`ilias_core.auth.verify`)

Falsches Passwort: Der IdP zeigt das Login-Formular erneut (mit Fehlermeldung).
"""

from __future__ import annotations

from .. import debuglog
from ..config import AUTH_SAML_SHIBBOLETH
from ..errors import AuthenticationError, ParserError
from . import parsers
from .base import BaseLoginFlow, OtpCallback
from .verify import verify_login

MAX_STEPS = 8


class ShibbolethLoginFlow(BaseLoginFlow):
    """SAML-Login über einen Shibboleth-IdP (ohne 2FA)."""

    name = AUTH_SAML_SHIBBOLETH
    start_path = "saml.php"

    def authenticate(
        self,
        username: str,
        password: str,
        otp_callback: OtpCallback | None = None,
    ) -> dict[str, str]:
        response = self._get(self.start_url)
        cookies_before = self._ilias_cookies()
        credentials_sent = False

        for _ in range(MAX_STEPS):
            url, html = str(response.url), response.text

            saml_form = parsers.parse_saml_post(html, url)
            if saml_form is not None:
                self._log_form("SAMLResponse an ILIAS", saml_form)
                response = self._post(saml_form.action, dict(saml_form.fields))
                return verify_login(self.client, self.base_url, response, cookies_before)

            login_form = parsers.parse_shibboleth_login(html, url)
            if login_form is not None:
                if credentials_sent:
                    message = parsers.extract_shibboleth_error(html)
                    debuglog.debug(
                        "IdP zeigt das Login-Formular erneut (Fehlermeldung erkannt: %s)",
                        "ja" if message else "nein",
                    )
                    raise AuthenticationError(message or "Benutzername oder Passwort ist falsch.")
                self._log_form("IdP-Login", login_form)
                self._ensure_safe_credential_target(login_form.action)
                data = self._with_proceed(login_form)
                data[parsers.SHIB_USERNAME_FIELD] = username
                data[parsers.SHIB_PASSWORD_FIELD] = password
                response = self._post(login_form.action, data)
                credentials_sent = True
                continue

            consent_form = parsers.parse_shibboleth_consent(html, url)
            if consent_form is not None:
                self._log_form("IdP-Attributfreigabe", consent_form)
                response = self._post(consent_form.action, self._with_proceed(consent_form))
                continue

            storage_form = parsers.parse_shibboleth_storage(html, url)
            if storage_form is not None:
                self._log_form("IdP-Client-Storage", storage_form)
                response = self._post(storage_form.action, self._with_proceed(storage_form))
                continue

            step = "IdP nach Anmeldung" if credentials_sent else "IdP-Login"
            self._log_unknown_page(step, response)
            if credentials_sent:
                raise ParserError(
                    "Unerwartete Seite nach der Anmeldung beim Shibboleth-IdP "
                    "(weder SAML-Weiterleitung noch bekanntes Formular)."
                )
            raise ParserError(
                "Shibboleth-Login-Formular konnte nicht gefunden werden (unerwartetes HTML)."
            )

        raise ParserError("Zu viele Zwischenschritte beim Shibboleth-Login.")

    @staticmethod
    def _with_proceed(form: parsers.HtmlForm) -> dict[str, str]:
        data = dict(form.fields)
        proceed = next(
            (name for name in form.submit_names if name.startswith(parsers.SHIB_PROCEED)),
            parsers.SHIB_PROCEED,
        )
        data.setdefault(proceed, "")
        return data
