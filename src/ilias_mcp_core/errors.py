"""Exception-Hierarchie der ILIAS-/Moodle-CLI (ein gemeinsamer Satz für alle Backends).

Jede Exception bildet auf genau einen dokumentierten Exit-Code ab
(``IliasError.exit_code``, INTERFACE.md §3) und trägt einen stabilen,
maschinenlesbaren ``code`` für ``--json``. Die CLI fängt ausschließlich
``IliasError`` und übersetzt ihn in den Prozess-Exit-Code.

0 OK · 1 Login fehlgeschlagen/sonstiges · 2 nicht eingeloggt · 3 Session abgelaufen ·
4 Netzwerk-/Serverfehler · 5 Parser-Fehler.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_AUTH = 1
EXIT_NOT_LOGGED_IN = 2
EXIT_SESSION_EXPIRED = 3
EXIT_NETWORK = 4
EXIT_PARSE = 5


class IliasError(Exception):
    """Basisklasse aller Fehler. Standard-Exit-Code: 1 (allgemeiner Fehler).

    ``hint`` ist ein optionaler Hinweis für Menschen, ``candidates`` (nur bei
    mehrdeutigen Kursen) eine Liste ``{id, fullname, shortname}``.
    """

    exit_code: int = EXIT_AUTH
    code: str = "error"

    def __init__(
        self,
        message: str = "",
        *,
        hint: str | None = None,
        candidates: list[dict] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.candidates = candidates

    def __repr__(self) -> str:  # pragma: no cover - Diagnose
        return f"{type(self).__name__}({self.message!r})"


class AuthenticationError(IliasError):
    """Anmeldung fehlgeschlagen (falsches Passwort/TOTP, Moodle: invalidlogin)."""

    exit_code = EXIT_AUTH
    code = "auth_failed"


class NotLoggedInError(IliasError):
    """Es ist keine Session/kein Token gespeichert. Exit-Code 2."""

    exit_code = EXIT_NOT_LOGGED_IN
    code = "not_logged_in"


class SessionExpiredError(IliasError):
    """Gespeicherte Session ist abgelaufen (Moodle: invalidtoken). Exit-Code 3."""

    exit_code = EXIT_SESSION_EXPIRED
    code = "session_expired"


class NetworkError(IliasError):
    """Netzwerk- oder Serverfehler (HTTP >= 500). Exit-Code 4."""

    exit_code = EXIT_NETWORK
    code = "network_error"


class ParserError(IliasError):
    """Unerwartetes HTML/JSON, fehlende Felder. Exit-Code 5."""

    exit_code = EXIT_PARSE
    code = "parse_error"


class BrowserUnavailableError(IliasError):
    """Das optionale Browser-Extra (Playwright) ist nicht verfügbar."""

    exit_code = EXIT_AUTH
    code = "browser_unavailable"


class ConfigError(IliasError):
    """Ungültige oder fehlende Konfiguration, unbekannte Instanz."""

    exit_code = EXIT_AUTH
    code = "config_error"


class NotSupportedError(IliasError):
    """Funktion ist für dieses Backend (noch) nicht implementiert."""

    exit_code = EXIT_AUTH
    code = "not_supported"


class CourseNotFoundError(IliasError):
    """Kein Kurs passt auf die Angabe bei `ilias ls`."""

    exit_code = EXIT_AUTH
    code = "course_not_found"


class CourseAmbiguousError(IliasError):
    """Mehrere Kurse passen; ``candidates`` listet sie."""

    exit_code = EXIT_AUTH
    code = "course_ambiguous"


class PermissionDeniedError(IliasError):
    """Keine Berechtigung auf ein Objekt (ILIAS: ``.alert-danger``). Exit 1.

    Die Seite wird **nicht** geparst und nichts gecrawlt (Spec §13.7).
    """

    exit_code = EXIT_AUTH
    code = "permission_denied"


class CrawlLimitError(IliasError):
    """Harte Request-Obergrenze beim Crawl erreicht. Exit 5, keine Teilausgabe."""

    exit_code = EXIT_PARSE
    code = "crawl_limit"


class PathNotFoundError(IliasError):
    """`download <kurs> <pfad>`: Pfad oder ref_id passt auf keine Datei im Kurs."""

    exit_code = EXIT_AUTH
    code = "path_not_found"
