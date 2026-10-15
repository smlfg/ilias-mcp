"""Stable tool error codes derived from ilias-cli exit codes (DESIGN B3, STANDARD T3).

Messages and hints are built **only** from the fixed templates below, never from
raw exceptions or upstream response bodies (SEC-14). The only interpolated value
is the instance key, which ``ilias_core.config`` has already validated against
``^[a-z0-9][a-z0-9_-]{0,63}$``.

ilias-cli exit codes: 1 auth/config/usage · 2 not logged in · 3 session expired
· 4 network · 5 parse error / crawl limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .sanitize import clean_str

_INSTANCE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

LOGIN_HINT = (
    "Der Nutzer muss sich selbst im eigenen Terminal anmelden: "
    "`ilias login --instance {instance}`. Zugangsdaten nie im Chat abfragen."
)

#: code -> (exit_code, message, hint). ``{instance}`` is the only placeholder.
TEMPLATES: dict[str, tuple[int, str, str]] = {
    "auth_failed": (
        1,
        "Die Anmeldung bei {instance} wurde abgelehnt.",
        LOGIN_HINT,
    ),
    "config_error": (
        1,
        "Die ilias-cli-Konfiguration ist ungültig oder die Instanz ist unbekannt.",
        "Mit ilias_list_instances die gültigen Instanz-Schlüssel abrufen.",
    ),
    "not_supported": (
        1,
        "Diese Funktion wird für {instance} noch nicht unterstützt.",
        "Eine andere Instanz wählen (ilias_list_instances).",
    ),
    "course_not_found": (
        1,
        "Kein Kurs in {instance} passt auf die Angabe.",
        "Mit ilias_list_courses eine gültige ref_id holen und diese als course übergeben.",
    ),
    "course_ambiguous": (
        1,
        "Mehrere Kurse in {instance} passen auf die Angabe.",
        "Einen der Kandidaten per ref_id (Feld id) erneut abfragen.",
    ),
    "permission_denied": (
        1,
        "Kein Zugriff auf dieses Objekt in {instance}.",
        "Mitgliedschaft mit ilias_list_courses prüfen.",
    ),
    "not_logged_in": (
        2,
        "Für {instance} ist keine Session gespeichert.",
        LOGIN_HINT,
    ),
    "session_expired": (
        3,
        "Die gespeicherte Session für {instance} ist abgelaufen.",
        LOGIN_HINT + " Nicht automatisch erneut versuchen.",
    ),
    "network_error": (
        4,
        "{instance} ist nicht erreichbar oder hat einen Serverfehler gemeldet.",
        "Später einmal erneut versuchen (ggf. VPN), keine Wiederholungsschleife.",
    ),
    "parse_error": (
        5,
        "Die Seite von {instance} hatte eine unerwartete Struktur.",
        "Nicht wiederholen; bitte als Issue in smlfg/ilias-cli melden.",
    ),
    "crawl_limit": (
        5,
        "Das Request-Budget für diesen Aufruf ist aufgebraucht.",
        "Mit kleinerem depth erneut aufrufen.",
    ),
    "internal_error": (
        1,
        "Interner Fehler im ilias-mcp-Server.",
        "Bitte als Issue in smlfg/ilias-mcp melden.",
    ),
}

#: Fallback by exit code for core errors whose ``code`` has no template.
EXIT_TO_CODE: dict[int, str] = {
    1: "auth_failed",
    2: "not_logged_in",
    3: "session_expired",
    4: "network_error",
    5: "parse_error",
}


@dataclass
class ToolFailure(Exception):
    """Expected failure with a stable code; rendered from :data:`TEMPLATES`."""

    code: str
    instance: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.code not in TEMPLATES:
            self.code = "internal_error"
        if not _INSTANCE_RE.fullmatch(self.instance or ""):
            self.instance = ""

    @property
    def exit_code(self) -> int:
        return TEMPLATES[self.code][0]

    def payload(self) -> dict[str, Any]:
        exit_code, message, hint = TEMPLATES[self.code]
        name = self.instance or "die Instanz"
        login_name = self.instance or "<instanz>"
        body: dict[str, Any] = {
            "code": self.code,
            "exit_code": exit_code,
            "message": message.format(instance=name),
            "hint": hint.format(instance=login_name),
        }
        if self.instance:
            body["instance"] = self.instance
        if self.candidates:
            body["candidates"] = self.candidates
        return body


def _clean_candidates(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        out.append({
            "id": cid if isinstance(cid, int) else None,
            "fullname": clean_str(str(item.get("fullname") or ""), 300),
            "shortname": clean_str(str(item.get("shortname") or ""), 100),
        })
    return out


def from_core_error(exc: BaseException, instance: str = "") -> ToolFailure:
    """Map an ``ilias_core.errors.IliasError`` to a :class:`ToolFailure`.

    Uses only ``code``/``exit_code`` and (for ambiguous courses) the structured
    ``candidates``; the exception text is never forwarded.
    """
    code = getattr(exc, "code", None)
    exit_code = getattr(exc, "exit_code", None)
    if not isinstance(code, str) or code not in TEMPLATES or code == "internal_error":
        code = EXIT_TO_CODE.get(exit_code, "internal_error") if isinstance(exit_code, int) else (
            "internal_error"
        )
    candidates: list[dict[str, Any]] = []
    if code == "course_ambiguous":
        candidates = _clean_candidates(getattr(exc, "candidates", None))
    return ToolFailure(code, instance=instance, candidates=candidates)
