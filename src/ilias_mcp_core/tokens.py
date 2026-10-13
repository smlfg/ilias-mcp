"""Session-/Token-Speicher (A4).

Pro Instanz wird der Token gespeichert, bevorzugt im OS-Schlüsselbund (`keyring`),
sonst in einer Datei, die **von Anfang an** mit `os.open(..., 0o600)` erzeugt wird.
Nie im Arbeitsverzeichnis, nie im Repo, nie im JSON-Output, nie im Log.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import config_dir
from .timeutil import now_iso

KEYRING_SERVICE = "ilias-cli"
SESSION_SUBDIR = "sessions"
_FILE_MODE = 0o600
_DIR_MODE = 0o700


@dataclass(frozen=True)
class StoredSession:
    instance: str
    lms: str
    base_url: str
    token: str
    created_at: str
    username: str | None = None
    source: str = "file"  # file | keyring

    def __repr__(self) -> str:
        return (
            f"StoredSession(instance={self.instance!r}, lms={self.lms!r}, "
            f"base_url={self.base_url!r}, token=***, created_at={self.created_at!r}, source={self.source!r})"
        )


def _keyring_module():
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:  # pragma: no cover - keyring ist Pflicht-Abhängigkeit
        return None


def keyring_usable() -> bool:
    """Keyring nur nehmen, wenn er wirklich lesbar ist (sonst Datei-Fallback)."""
    keyring = _keyring_module()
    if keyring is None:
        return False
    try:
        backend = keyring.get_keyring()
    except Exception:
        return False
    if backend is None or type(backend).__name__ in ("fail", "null"):
        return False
    try:
        backend.get_password(KEYRING_SERVICE, "__probe__")
    except Exception:
        return False
    return True


class TokenStore:
    """Token-Speicher für eine Instanz (Keyring zuerst, Datei als Fallback)."""

    def __init__(self, instance: str, lms: str, base_url: str, directory: Path | None = None) -> None:
        self.instance = instance
        self.lms = lms
        self.base_url = base_url
        self.directory = Path(directory) if directory is not None else config_dir() / SESSION_SUBDIR
        self.keyring = _keyring_module()
        self._keyring_ok = keyring_usable() if self.keyring is not None else False
        self.path = self.directory / f"{_safe_name(instance)}.json"

    # -- interne Helfer ------------------------------------------------
    @property
    def uses_keyring(self) -> bool:
        return self._keyring_ok

    def _keyring_get(self) -> str | None:
        if not self._keyring_ok or self.keyring is None:
            return None
        try:
            value = self.keyring.get_password(KEYRING_SERVICE, self.instance)
        except Exception:
            self._keyring_ok = False
            return None
        return value

    def _keyring_set(self, token: str) -> bool:
        if not self._keyring_ok or self.keyring is None:
            return False
        try:
            self.keyring.set_password(KEYRING_SERVICE, self.instance, token)
        except Exception:
            self._keyring_ok = False
            return False
        return True

    def _keyring_delete(self) -> bool:
        if self.keyring is None:
            return False
        try:
            self.keyring.delete_password(KEYRING_SERVICE, self.instance)
            return True
        except Exception:
            return False

    def _write_file(self, payload: dict[str, str]) -> None:
        """Datei mit 0600 anlegen - ohne Fenster, in dem die Rechte zu weit sind."""
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, _DIR_MODE)
        except OSError:
            pass
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        fd, tmp = tempfile.mkstemp(prefix=".session-", dir=str(self.directory))
        try:
            os.fchmod(fd, _FILE_MODE)
            with os.fdopen(fd, "wb", closefd=True) as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, _FILE_MODE)  # mkstemp erzwingt 0600, os.replace erhält sie
            os.replace(tmp, self.path)
            tmp = ""
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        os.chmod(self.path, _FILE_MODE)

    def _read_file(self) -> dict[str, str] | None:
        try:
            raw = self.path.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError:
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("token"), str):
            return None
        return {k: v for k, v in data.items() if isinstance(v, str)}

    # -- öffentliche API ------------------------------------------------
    def load(self) -> StoredSession | None:
        token = self._keyring_get()
        source = "keyring"
        payload: dict[str, str] | None = None
        if not token:
            payload = self._read_file()
            if payload is None:
                return None
            token = payload["token"]
            source = "file"
        return StoredSession(
            instance=self.instance,
            lms=self.lms,
            base_url=self.base_url,
            token=token,
            created_at=(payload or {}).get("created_at", ""),
            username=(payload or {}).get("username"),
            source=source,
        )

    def save(self, token: str, username: str | None = None) -> str:
        """Token speichern; liefert "keyring" oder "file" als Quelle."""
        if self._keyring_set(token):
            self.delete_file()
            return "keyring"
        self._write_file(
            {
                "instance": self.instance,
                "lms": self.lms,
                "base_url": self.base_url,
                "username": username or "",
                "created_at": now_iso(),
                "token": token,
            }
        )
        self._keyring_delete()  # veralteten Keyring-Eintrag entfernen (best effort)
        return "file"

    def delete_file(self) -> bool:
        try:
            self.path.unlink()
            return True
        except (FileNotFoundError, NotADirectoryError):
            return False
        except OSError:
            return False

    def delete(self) -> bool:
        """Löscht den Token aus Keyring **und** Datei; True, wenn etwas da war."""
        removed = self._keyring_delete()
        removed = self.delete_file() or removed
        return removed

    def file_mode(self) -> int | None:
        """Rechte der Token-Datei (None, wenn keine existiert)."""
        try:
            return stat.S_IMODE(self.path.stat().st_mode)
        except OSError:
            return None


def _safe_name(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name)[:64] or "default"


def store_for(instance) -> TokenStore:
    """TokenStore für ein `ilias_core.config.Instance`."""
    return TokenStore(instance.key, instance.lms, instance.base_url)
