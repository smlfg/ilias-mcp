"""Authentifizierung: austauschbare Adapter, ausgewählt per ``auth`` in der Konfiguration."""

from ..config import AUTH_OIDC_KEYCLOAK, AUTH_SAML_SHIBBOLETH
from ..errors import ConfigError
from .base import BaseLoginFlow
from .flow import KeycloakLoginFlow
from .parsers import (
    HtmlForm,
    extract_keycloak_error,
    extract_shibboleth_error,
    is_ilias_logged_in,
    is_ilias_login_page,
    is_keycloak_login,
    is_keycloak_totp,
    parse_keycloak_login,
    parse_keycloak_totp,
    parse_saml_post,
    parse_shibboleth_consent,
    parse_shibboleth_login,
)
from .shibboleth import ShibbolethLoginFlow

ADAPTERS: dict[str, type[BaseLoginFlow]] = {
    AUTH_OIDC_KEYCLOAK: KeycloakLoginFlow,
    AUTH_SAML_SHIBBOLETH: ShibbolethLoginFlow,
}


def get_adapter(auth: str) -> type[BaseLoginFlow]:
    try:
        return ADAPTERS[auth]
    except KeyError:
        raise ConfigError(
            f"Unbekanntes Auth-Verfahren {auth!r}. Erlaubt: {', '.join(ADAPTERS)}."
        ) from None


__all__ = [
    "ADAPTERS",
    "BaseLoginFlow",
    "KeycloakLoginFlow",
    "ShibbolethLoginFlow",
    "get_adapter",
    "HtmlForm",
    "extract_keycloak_error",
    "extract_shibboleth_error",
    "is_ilias_logged_in",
    "is_ilias_login_page",
    "is_keycloak_login",
    "is_keycloak_totp",
    "parse_keycloak_login",
    "parse_keycloak_totp",
    "parse_saml_post",
    "parse_shibboleth_consent",
    "parse_shibboleth_login",
]
