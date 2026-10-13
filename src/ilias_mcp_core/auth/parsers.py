"""HTML-Parser für die Keycloak-, Shibboleth- und ILIAS-Seiten.

Ein Modul pro Seitentyp, gekapselt und ohne Seiteneffekte. Alle Funktionen
arbeiten auf einem HTML-String und geben strukturierte Daten zurück.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

KEYCLOAK_LOGIN_FORM_ID = "kc-form-login"
KEYCLOAK_TOTP_FORM_ID = "kc-otp-login-form"

SHIB_LOGIN_FORM_ID = "login-form"
SHIB_USERNAME_FIELD = "j_username"
SHIB_PASSWORD_FIELD = "j_password"
SHIB_PROCEED = "_eventId_proceed"
SHIB_CONSENT_FIELDS = ("_shib_idp_consentOptions", "_shib_idp_consentIds")
SHIB_STORAGE_PREFIX = "shib_idp_ls_"

ERROR_SELECTORS = (
    "#input-error",
    ".pf-c-form__helper-text.pf-m-error",
    ".pf-c-alert__title",
    ".alert-error",
    ".kc-feedback-text",
    ".pf-m-error",
)

SHIB_ERROR_SELECTORS = (
    ".form-error",
    ".output--error",
    ".alert-danger",
    ".alert-error",
    ".login-error",
    ".error-message",
    "#error",
    ".error",
)

LOGOUT_MARKERS = ("logout.php", "cmd=showlogout", "cmd=dologout")
LOGIN_LINK_MARKERS = ("saml.php", "openidconnect.php", "shib_login.php", "cmd=force_login")


@dataclass
class HtmlForm:
    """Ein geparstes HTML-Formular (nur Feldwerte, die ein Browser senden würde)."""

    action: str
    method: str
    fields: dict[str, str] = field(default_factory=dict)
    form_id: str | None = None
    field_names: list[str] = field(default_factory=list)
    submit_names: list[str] = field(default_factory=list)


def _parse_form(form) -> HtmlForm:  # noqa: ANN001 - bs4-Element
    action = form.get("action", "") or ""
    method = (form.get("method", "post") or "post").lower()
    fields: dict[str, str] = {}
    names: list[str] = []
    submits: list[str] = []
    for element in form.find_all(["input", "button", "select", "textarea"]):
        name = element.get("name")
        if not name:
            continue
        names.append(name)
        tag = element.name
        input_type = (element.get("type") or ("submit" if tag == "button" else "text")).lower()
        if input_type in ("submit", "image"):
            submits.append(name)
            continue
        if input_type in ("button", "file", "reset"):
            continue
        if input_type in ("checkbox", "radio"):
            if element.has_attr("checked"):
                fields[name] = element.get("value", "on") or "on"
            continue
        if tag == "select":
            option = element.find("option", selected=True) or element.find("option")
            fields[name] = (option.get("value", option.get_text()) if option else "") or ""
            continue
        if tag == "textarea":
            fields[name] = element.get_text()
            continue
        fields[name] = element.get("value", "") or ""
    return HtmlForm(
        action=action,
        method=method,
        fields=fields,
        form_id=form.get("id"),
        field_names=names,
        submit_names=submits,
    )


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _resolved(form, base_url: str) -> HtmlForm:  # noqa: ANN001 - bs4-Element
    parsed = _parse_form(form)
    parsed.action = urljoin(base_url, parsed.action)
    return parsed


def all_forms(html: str, base_url: str = "") -> list[HtmlForm]:
    """Alle Formulare einer Seite (für Debug-Ausgaben und Klassifikation)."""

    return [_resolved(form, base_url) for form in _soup(html).find_all("form")]


# -- Keycloak ----------------------------------------------------------------
def parse_keycloak_login(html: str, base_url: str = "") -> HtmlForm | None:
    """Parst das Keycloak-Login-Formular inklusive aller hidden inputs."""

    form = _soup(html).find("form", id=KEYCLOAK_LOGIN_FORM_ID)
    return _resolved(form, base_url) if form is not None else None


def parse_keycloak_totp(html: str, base_url: str = "") -> HtmlForm | None:
    """Parst das Keycloak-TOTP-Formular inklusive aller hidden inputs."""

    form = _soup(html).find("form", id=KEYCLOAK_TOTP_FORM_ID)
    return _resolved(form, base_url) if form is not None else None


def extract_keycloak_error(html: str) -> str | None:
    """Liefert die Fehlermeldung eines erneut angezeigten Keycloak-Formulars."""

    return _first_text(html, ERROR_SELECTORS)


def is_keycloak_login(html: str) -> bool:
    return _soup(html).find("form", id=KEYCLOAK_LOGIN_FORM_ID) is not None


def is_keycloak_totp(html: str) -> bool:
    return _soup(html).find("form", id=KEYCLOAK_TOTP_FORM_ID) is not None


# -- Shibboleth IdP / SAML ------------------------------------------------------
def parse_shibboleth_login(html: str, base_url: str = "") -> HtmlForm | None:
    """Parst ``form#login-form`` (bzw. ein Formular mit ``j_password``)."""

    soup = _soup(html)
    form = soup.find("form", id=SHIB_LOGIN_FORM_ID)
    if form is None or form.find("input", attrs={"name": SHIB_PASSWORD_FIELD}) is None:
        password = soup.find("input", attrs={"name": SHIB_PASSWORD_FIELD})
        form = password.find_parent("form") if password is not None else None
    return _resolved(form, base_url) if form is not None else None


def parse_shibboleth_consent(html: str, base_url: str = "") -> HtmlForm | None:
    """Parst die Attributfreigabe-Seite (``_shib_idp_consentOptions``)."""

    for form in _soup(html).find_all("form"):
        if any(form.find("input", attrs={"name": name}) for name in SHIB_CONSENT_FIELDS):
            return _resolved(form, base_url)
    return None


def parse_shibboleth_storage(html: str, base_url: str = "") -> HtmlForm | None:
    """Parst die (sonst per JavaScript abgeschickte) Client-Storage-Seite."""

    for form in _soup(html).find_all("form"):
        for element in form.find_all("input"):
            if (element.get("name") or "").startswith(SHIB_STORAGE_PREFIX):
                return _resolved(form, base_url)
    return None


def parse_saml_post(html: str, base_url: str = "") -> HtmlForm | None:
    """Parst das Auto-Submit-Formular mit ``SAMLResponse`` (+ ``RelayState``)."""

    element = _soup(html).find("input", attrs={"name": "SAMLResponse"})
    form = element.find_parent("form") if element is not None else None
    if form is None:
        return None
    parsed = _resolved(form, base_url)
    return parsed if parsed.fields.get("SAMLResponse") else None


def extract_shibboleth_error(html: str) -> str | None:
    return _first_text(html, SHIB_ERROR_SELECTORS)


# -- ILIAS ---------------------------------------------------------------------
def is_ilias_logged_in(html: str) -> bool:
    """Erkennt eine Seite für eingeloggte Nutzer (Abmelde-Link im Benutzermenü)."""

    for element in _soup(html).find_all(["a", "form"]):
        target = (element.get("href") or element.get("action") or "").lower()
        if any(marker in target for marker in LOGOUT_MARKERS):
            return True
    return False


def is_ilias_login_page(html: str) -> bool:
    """Erkennt die ILIAS-Login-Seite (Hinweis auf abgelaufene Session)."""

    soup = _soup(html)
    if soup.find("form", id="login_form") is not None:
        return True
    if soup.find("form", attrs={"name": "login_form"}) is not None:
        return True
    for form in soup.find_all("form"):
        action = (form.get("action", "") or "").lower()
        if "login.php" in action or "ilstartupgui" in action:
            return True
    if is_ilias_logged_in(html):
        return False
    for link in soup.find_all("a"):
        href = (link.get("href") or "").lower()
        if any(marker in href for marker in LOGIN_LINK_MARKERS):
            return True
    text = soup.get_text(" ", strip=True)
    return "Ohne HHN-Konto anmelden" in text or "Bei ILIAS anmelden" in text


def _first_text(html: str, selectors: tuple[str, ...]) -> str | None:
    soup = _soup(html)
    for selector in selectors:
        for element in soup.select(selector):
            text = element.get_text(" ", strip=True)
            if text:
                return text
    return None
