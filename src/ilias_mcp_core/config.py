"""Konfiguration der ILIAS-CLI.

Gelesen wird ``~/.config/ilias-cli/config.toml`` (bzw. der über die
Umgebungsvariable ``ILIAS_CLI_CONFIG_DIR`` überschriebene Ordner).

Eingebaute Instanz-Profile (``hhn``, ``uni-mannheim``: ILIAS; ``hs-mannheim``:
Moodle) liefern Lernplattform (``lms``), Basis-URL, Client-ID und Auth-Verfahren. Auswahl über ``--instance`` oder den
Schlüssel ``instance`` in der Datei. Alle Werte bleiben überschreibbar (N7):

.. code-block:: toml

    instance = "uni-mannheim"          # aktive Instanz (Default: hhn)
    base_url = "https://..."            # überschreibt die aktive Instanz
    client_id = "..."
    auth = "saml-shibboleth"           # oder "oidc-keycloak" (ILIAS), "moodle-token"

    [instances.hhn]                    # Overrides pro Instanz
    base_url = "https://..."

    [instances.hs-mannheim]            # Moodle (Login über den Mobile-Webservice)
    base_url = "https://moodle.hs-mannheim.de"

    [instances.meine-uni]              # eigene Instanz
    base_url = "https://ilias.example.org"
    client_id = "ILIAS"
    auth = "saml-shibboleth"

    [instances.mein-moodle]            # eigene Moodle-Instanz
    lms = "moodle"
    base_url = "https://moodle.example.org"

Die Schlüssel auf oberster Ebene gelten nur für die Instanz aus ``instance``
(bzw. ``hhn``), nicht für eine per ``--instance`` gewählte andere Instanz.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError

ENV_CONFIG_DIR = "ILIAS_CLI_CONFIG_DIR"

AUTH_OIDC_KEYCLOAK = "oidc-keycloak"
AUTH_SAML_SHIBBOLETH = "saml-shibboleth"
AUTH_MOODLE_TOKEN = "moodle-token"
ILIAS_AUTH_METHODS = (AUTH_OIDC_KEYCLOAK, AUTH_SAML_SHIBBOLETH)
AUTH_METHODS = (*ILIAS_AUTH_METHODS, AUTH_MOODLE_TOKEN)

# Lernplattform (Backend). ILIAS bleibt der Default.
BACKEND_ILIAS = "ilias"
BACKEND_MOODLE = "moodle"
KNOWN_BACKENDS = (BACKEND_ILIAS, BACKEND_MOODLE)
CONFIG_FILE_NAME = "config.toml"


@dataclass(frozen=True)
class InstanceProfile:
    """Eingebautes oder in der Konfiguration definiertes Instanz-Profil."""

    name: str
    base_url: str
    client_id: str
    auth: str
    username_label: str = "Benutzername"
    lms: str = BACKEND_ILIAS
    label: str = ""


BUILTIN_INSTANCES: dict[str, InstanceProfile] = {
    "hhn": InstanceProfile(
        name="hhn",
        base_url="https://ilias.hs-heilbronn.de",
        client_id="iliashhn",
        auth=AUTH_OIDC_KEYCLOAK,
    ),
    "uni-mannheim": InstanceProfile(
        name="uni-mannheim",
        base_url="https://ilias.uni-mannheim.de",
        client_id="ILIAS",
        auth=AUTH_SAML_SHIBBOLETH,
        username_label="Uni-ID (Kennung)",
    ),
    "hs-mannheim": InstanceProfile(
        name="hs-mannheim",
        base_url="https://moodle.hs-mannheim.de",
        client_id="",
        auth=AUTH_MOODLE_TOKEN,
        lms=BACKEND_MOODLE,
        label="Moodle Hochschule Mannheim (Lernplattform TH-MA)",
    ),
}

DEFAULT_INSTANCE = "hhn"
DEFAULT_BASE_URL = BUILTIN_INSTANCES[DEFAULT_INSTANCE].base_url
DEFAULT_CLIENT_ID = BUILTIN_INSTANCES[DEFAULT_INSTANCE].client_id

_INSTANCE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def default_config_dir() -> Path:
    """Liefert das Konfigurationsverzeichnis (per Env überschreibbar)."""

    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "ilias-cli"


def config_dir() -> Path:
    """Alias für :func:`default_config_dir` (vom Moodle-Token-Speicher genutzt)."""

    return default_config_dir()


def config_path() -> Path:
    return default_config_dir() / CONFIG_FILE_NAME


@dataclass(frozen=True)
class Config:
    """Aufgelöste Konfiguration für genau eine Instanz."""

    base_url: str = DEFAULT_BASE_URL
    client_id: str = DEFAULT_CLIENT_ID
    config_dir: Path = field(default_factory=default_config_dir)
    instance: str = DEFAULT_INSTANCE
    auth: str = AUTH_OIDC_KEYCLOAK
    username_label: str = "Benutzername"
    lms: str = BACKEND_ILIAS
    label: str = ""

    @property
    def key(self) -> str:
        """Instanz-Schlüssel (``hhn``, ``uni-mannheim``, ``hs-mannheim`` …)."""
        return self.instance

    @property
    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def session_file(self) -> Path:
        return self.config_dir / f"session-{self.instance}.json"


def _str_value(data: dict[str, Any], key: str) -> str | None:
    raw = data.get(key)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _validate_instance_name(name: str) -> str:
    name = name.strip().lower()
    if not _INSTANCE_NAME.match(name):
        raise ConfigError(
            f"Ungültiger Instanzname {name!r} (erlaubt: a-z, 0-9, '-', '_')."
        )
    return name


def load_config(path: Path | str | None = None, *, instance: str | None = None) -> Config:
    """Lädt die Konfiguration aus TOML und fällt auf die Profile zurück.

    ``instance`` (z. B. aus ``--instance``) hat Vorrang vor dem Schlüssel
    ``instance`` in der Datei.
    """

    config_dir = Path(path).expanduser() if path is not None else default_config_dir()
    config_file = config_dir / "config.toml"

    data: dict[str, Any] = {}
    if config_file.is_file():
        try:
            with config_file.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Konfiguration {config_file} ist ungültig: {exc}") from exc

    file_instance = _str_value(data, "instance")
    configured = _validate_instance_name(file_instance) if file_instance else DEFAULT_INSTANCE
    selected = _validate_instance_name(instance) if instance else configured

    tables = data.get("instances")
    tables = tables if isinstance(tables, dict) else {}
    overrides = tables.get(selected)
    overrides = overrides if isinstance(overrides, dict) else {}

    profile = BUILTIN_INSTANCES.get(selected)
    if profile is None and not overrides:
        known = ", ".join(sorted(set(BUILTIN_INSTANCES) | set(tables)))
        raise ConfigError(
            f"Unbekannte Instanz {selected!r}. Verfügbar: {known}. "
            f"Eigene Instanzen in {config_file} unter [instances.{selected}] anlegen."
        )

    base_url = profile.base_url if profile else None
    client_id = profile.client_id if profile else None
    auth = profile.auth if profile else None
    username_label = profile.username_label if profile else "Benutzername"
    lms = profile.lms if profile else BACKEND_ILIAS
    label = profile.label if profile else ""

    layers = [overrides]
    if selected == configured:
        layers.insert(0, data)
    for layer in layers:
        base_url = _str_value(layer, "base_url") or base_url
        client_id = _str_value(layer, "client_id") or client_id
        auth = _str_value(layer, "auth") or auth
        username_label = _str_value(layer, "username_label") or username_label
        label = _str_value(layer, "label") or label
        lms = (_str_value(layer, "lms") or lms).lower()

    if lms not in KNOWN_BACKENDS:
        raise ConfigError(
            f"Unbekanntes Backend {lms!r} für Instanz {selected!r} "
            f"(bekannt: {', '.join(KNOWN_BACKENDS)})."
        )
    if lms == BACKEND_MOODLE:
        auth = auth if auth == AUTH_MOODLE_TOKEN else AUTH_MOODLE_TOKEN
        client_id = client_id or ""
        if not base_url:
            raise ConfigError(f"Instanz {selected!r}: base_url muss in {config_file} gesetzt sein.")
    else:
        auth = auth or AUTH_OIDC_KEYCLOAK
        if not base_url or not client_id:
            raise ConfigError(
                f"Instanz {selected!r}: base_url und client_id müssen in {config_file} gesetzt sein."
            )
        if auth not in ILIAS_AUTH_METHODS:
            raise ConfigError(
                f"Unbekanntes Auth-Verfahren {auth!r}. Erlaubt: {', '.join(ILIAS_AUTH_METHODS)}."
            )

    return Config(
        base_url=base_url.rstrip("/"),
        client_id=client_id,
        config_dir=config_dir,
        instance=selected,
        auth=auth,
        username_label=username_label,
        lms=lms,
        label=label,
    )


# Kompatibilität zur Moodle-API (Backends/Service benutzen „Instance“).
Instance = Config


def load_instance(key: str | None = None, config: dict[str, Any] | None = None) -> Config:
    """Instanz auflösen (``--instance`` > ``instance`` in config.toml > ``hhn``).

    ``config`` wird ignoriert und existiert nur aus Kompatibilitätsgründen; maßgeblich
    ist immer die Datei aus :func:`default_config_dir`.
    """

    del config
    return load_config(instance=key)
