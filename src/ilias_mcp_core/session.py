"""Speichern und Laden der Session-Cookies.

Cookies landen bevorzugt im OS-Schlüsselbund (``keyring``). Ist kein
Schlüsselbund verfügbar, wird auf eine Datei mit den Rechten ``0600``
ausgewichen. Cookies werden niemals geloggt oder ausgegeben.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from .config import Config

SERVICE_NAME = "ilias-cli"


class KeyringLike(Protocol):  # pragma: no cover - reines Typing-Protokoll
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


def _default_keyring() -> KeyringLike | None:
    """Importiert ``keyring`` und verwirft funktionslose Backends.

    In headless-Umgebungen (CI, Container) liefert ``keyring`` das
    ``fail``-Backend, das keine Secrets speichern kann. In dem Fall wird
    ``None`` zurückgegeben und der Datei-Fallback greift.
    """

    try:
        import keyring
    except Exception:
        return None

    try:
        backend = keyring.get_keyring()
    except Exception:
        return None

    backend_name = f"{type(backend).__module__}.{type(backend).__name__}".lower()
    if "fail" in backend_name or ".null" in backend_name:
        return None
    return keyring


class SessionStore:
    """Persistiert Cookies im Keyring mit Datei-Fallback."""

    def __init__(
        self,
        config: Config,
        *,
        keyring_module: KeyringLike | None | object = None,
        force_file: bool = False,
    ) -> None:
        self.config = config
        self._keyring_module = keyring_module
        self._force_file = force_file

    @property
    def username(self) -> str:
        return f"session:{self.config.instance}|{self.config.base_url}|{self.config.client_id}"

    def _keyring(self) -> KeyringLike | None:
        if self._force_file:
            return None
        if self._keyring_module is not None:
            return self._keyring_module  # type: ignore[return-value]
        return _default_keyring()

    def save(self, cookies: dict[str, str]) -> None:
        payload = json.dumps(cookies, separators=(",", ":"))
        keyring = self._keyring()
        if keyring is not None:
            try:
                keyring.set_password(SERVICE_NAME, self.username, payload)
                return
            except Exception:
                pass
        self._write_file(payload)

    def load(self) -> dict[str, str] | None:
        keyring = self._keyring()
        if keyring is not None:
            try:
                payload = keyring.get_password(SERVICE_NAME, self.username)
            except Exception:
                payload = None
            if payload:
                try:
                    data = json.loads(payload)
                    if isinstance(data, dict):
                        return {str(k): str(v) for k, v in data.items()}
                except ValueError:
                    pass
        return self._read_file()

    def clear(self) -> None:
        keyring = self._keyring()
        if keyring is not None:
            try:
                keyring.delete_password(SERVICE_NAME, self.username)
            except Exception:
                pass
        self._delete_file()

    def _path(self) -> Path:
        return self.config.session_file

    def _write_file(self, payload: str) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = 0o600
        fd = os.open(path, flags, mode)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
        finally:
            os.chmod(path, mode)

    def _read_file(self) -> dict[str, str] | None:
        path = self._path()
        if not path.is_file():
            return None
        try:
            with path.open(encoding="utf-8") as handle:
                data: Any = json.load(handle)
        except (ValueError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        return {str(k): str(v) for k, v in data.items()}

    def _delete_file(self) -> None:
        try:
            self._path().unlink()
        except FileNotFoundError:
            pass
