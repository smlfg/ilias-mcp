"""ilias-mcp: MCP-Server für ILIAS (HHN, Uni Mannheim) und Moodle (HS Mannheim).

Stdio-Transport, FastMCP. Die Geschäftslogik (Login, Sessions, Kurse, Inhalte)
liegt im geforkten Core-Paket :mod:`ilias_mcp_core` (eigenständige Kopie der
Bibliothek aus ``ilias-cli``); dieser Server übersetzt nur die Aufrufe in
MCP-Tools.

Sicherheit:

- Zugangsdaten kommen ausschließlich aus Env-Variablen (``ILIAS_USERNAME`` /
  ``ILIAS_PASSWORD`` / ``ILIAS_TOTP_SECRET``) oder - wenn das Tool sie
  unterstützt - aus Laufzeit-Argumenten. Es gibt **keine** Datei im Repo, in
  der Credentials stehen.
- Ergebnisse werden über die ``to_*_dict``-Helfer der Core-Modelle
  serialisiert; diese enthalten bewusst weder Passwörter noch Tokens
  (``ilias_mcp_core.secrets.Secret`` maskiert sich selbst).
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from ilias_mcp_core import debuglog
from ilias_mcp_core.config import (
    AUTH_MOODLE_TOKEN,
    BUILTIN_INSTANCES,
    ENV_CONFIG_DIR,
    KNOWN_BACKENDS,
    load_instance,
)
from ilias_mcp_core.errors import (
    ConfigError,
    CourseAmbiguousError,
    CourseNotFoundError,
    IliasError,
    NotSupportedError,
)
from ilias_mcp_core.models import (
    Credentials,
    ErrorResult,
    MoodleLoginResult,
    MoodleStatusResult,
)
from ilias_mcp_core.secrets import Secret
from ilias_mcp_core.service import Service, open_service

from .feedback import (
    AGENT_HINT,
    Report,
    build_report,
    redact,
    redact_dict,
    render_issue_links,
)

__all__ = ["main", "mcp", "build_server"]


# ---------------------------------------------------------------- Konstanten
DEFAULT_INSTANCE = "hhn"

ENV_INSTANCE = "ILIAS_INSTANCE"
ENV_USERNAME = "ILIAS_USERNAME"
ENV_PASSWORD = "ILIAS_PASSWORD"
ENV_TOTP_SECRET = "ILIAS_TOTP_SECRET"
ENV_LOGIN_BROWSER = "ILIAS_LOGIN_BROWSER"

# Verhindert, dass ``Secret``-Werte versehentlich in eine Fehlermeldung
# geraten: jede Ausnahme wird durch diesen Wrapper geleitet, bevor sie den
# Server verlässt.
_ERROR_HINT_KEYS = ("hint", "candidates")


# ---------------------------------------------------------------- Hilfen
def _resolve_instance(instance: str | None) -> str:
    """Instanz-Schlüssel aus Argument oder Env bestimmen.

    Wird ein leerer String übergeben, fällt die Auswahl auf die Env-Variable.
    """

    if instance:
        return instance.strip().lower()
    env_value = os.environ.get(ENV_INSTANCE, "").strip().lower()
    if env_value:
        return env_value
    return DEFAULT_INSTANCE


def _resolve_browser_flag() -> bool:
    raw = os.environ.get(ENV_LOGIN_BROWSER, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _load_credentials(
    username_override: str | None,
    password_override: str | None,
    totp_secret: str | None,
) -> Credentials:
    """Credentials aus Override-Args oder Env-Variablen zusammensetzen."""

    user = (username_override or os.environ.get(ENV_USERNAME) or "").strip()
    password = password_override if password_override is not None else os.environ.get(ENV_PASSWORD, "")
    if not user:
        raise ConfigError(
            "Kein Benutzername: weder als Argument noch in "
            f"${ENV_USERNAME} gesetzt.",
            hint="Beispiel: export ILIAS_USERNAME=... oder das Tool mit username=... aufrufen.",
        )
    if not password:
        raise ConfigError(
            "Kein Passwort: weder als Argument noch in "
            f"${ENV_PASSWORD} gesetzt.",
            hint="Beispiel: export ILIAS_PASSWORD=... (oder --browser ohne Passwort nutzen).",
        )
    return Credentials(username=user, password=Secret(password))


def _totp_callback(secret: str | None) -> Callable[[], str] | None:
    """TOTP-Callback für den Core bauen.

    Liefert ``None``, wenn kein Secret konfiguriert ist; der Core fordert dann
    keinen Code an (Backends, die TOTP benötigen, würden so fehlschlagen).
    """

    secret_value = (secret or "").strip()
    if not secret_value:
        return None

    def _code() -> str:
        # Lazy import, damit TOTP nur dann geladen wird, wenn es wirklich
        # genutzt wird - und damit ein fehlendes ``pyotp`` nicht alle Tools
        # blockiert, die kein TOTP brauchen.
        try:
            import pyotp  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ConfigError(
                "TOTP konfiguriert, aber pyotp ist nicht installiert.",
                hint="uv add pyotp oder ILIAS_TOTP_SECRET leeren.",
            ) from exc
        return pyotp.TOTP(secret_value).now()

    return _code


def _error_to_payload(command: str, exc: IliasError, service: Service | None) -> dict[str, Any]:
    """Fehler in ein stabiles JSON-Dict für den Client übersetzen.

    Die Antwort trägt zusätzlich ein ``report``-Feld (siehe
    :func:`ilias_mcp.feedback.build_report`) und einen ``agent_hint``, der den
    Agenten auffordert, vor einer Übertragung die ausdrückliche Zustimmung
    des Nutzers einzuholen.
    """

    safe_message = redact(exc.message or str(exc))
    safe_hint = redact(exc.hint) if exc.hint else None
    if safe_hint == "[empty]":
        safe_hint = None
    safe_candidates: list[dict[str, Any]] | None = None
    if exc.candidates:
        safe_candidates = [
            redact_dict(c) if isinstance(c, dict) else c for c in exc.candidates
        ]

    result = ErrorResult(
        command=command,
        error_code=exc.code,
        error_type=type(exc).__name__,
        message=safe_message,
        exit_code=exc.exit_code,
        hint=safe_hint,
        instance=service.instance.key if service else None,
        lms=service.instance.lms if service else None,
        candidates=safe_candidates,
    )
    payload = result.to_json_dict()
    report: Report = build_report(
        command=command,
        exc=exc,
        service=service,
        extra={"candidates": safe_candidates} if safe_candidates else None,
    )
    payload["report"] = report.to_dict()
    payload["agent_hint"] = AGENT_HINT
    return payload


def _safe_call(command: str, op: Callable[[Service], Any]) -> dict[str, Any]:
    """Service-Operation ausführen und Core-Fehler in JSON verpacken."""

    service = open_service(os.environ.get(ENV_INSTANCE) or None)
    debuglog.debug(
        "ilias-mcp: %s instanz=%s lms=%s auth=%s base=%s",
        command,
        service.instance.key,
        service.instance.lms,
        service.instance.auth,
        debuglog.redact_url(service.instance.base_url),
    )
    try:
        return _normalize(op(service))
    except IliasError as exc:
        payload = _error_to_payload(command, exc, service)
        debuglog.debug(
            "ilias-mcp: %s -> fehler %s (%s)",
            command,
            exc.code,
            type(exc).__name__,
        )
        return payload


def _normalize(result: Any) -> dict[str, Any]:
    """Core-Ergebnis in ein dict verwandeln, ohne Secrets preiszugeben."""

    if isinstance(result, MoodleLoginResult):
        return result.to_json_dict()
    if isinstance(result, MoodleStatusResult):
        return result.to_json_dict()
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if hasattr(result, "to_json_dict"):
        return result.to_json_dict()
    if isinstance(result, dict):
        return result
    return {"value": str(result)}


_PLAUSIBLE_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def _is_plausible_email(value: str) -> bool:
    """Sehr einfache E-Mail-Validierung: ein @, Punkte, keine Leer-/Listen-Trennzeichen."""

    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate or " " in candidate or "," in candidate or ";" in candidate:
        return False
    return bool(_PLAUSIBLE_EMAIL_RE.match(candidate))


# ---------------------------------------------------------------- Server
def build_server() -> FastMCP:
    """FastMCP-Instanz mit allen Tools bauen."""

    server = FastMCP(
        name="ilias-mcp",
        instructions=(
            "MCP-Server für ILIAS (HHN, Uni Mannheim) und Moodle (HS Mannheim). "
            "Erlaubt das Anmelden, das Prüfen der Session und das Auflisten von "
            "Kursen sowie deren Inhalten (Abschnitte, Module, Dateien). "
            "Zugangsdaten kommen aus ILIAS_USERNAME/ILIAS_PASSWORD (oder als "
            "Tool-Argument); es werden keine Dateien im Repo benutzt."
        ),
    )

    @server.tool()
    def ilias_login(
        instance: str | None = None,
        username: str | None = None,
        password: str | None = None,
        browser: bool | None = None,
    ) -> dict[str, Any]:
        """Loggt sich bei der gewählten Instanz ein.

        Args:
            instance: Instanz-Schlüssel (``hhn``, ``uni-mannheim``, ``hs-mannheim``).
                Default: ``$ILIAS_INSTANCE`` oder ``hhn``.
            username: Optionaler Override für ``$ILIAS_USERNAME``.
            password: Optionaler Override für ``$ILIAS_PASSWORD``. Wird **nicht**
                in Logs oder Antworten aufgenommen.
            browser: Optionaler Override; ``True`` erzwingt Browser-Login
                (Playwright muss installiert sein). Default: ``$ILIAS_LOGIN_BROWSER``.

        Returns:
            ``{"ok": True, ...}`` mit Verifikationsstatus oder
            ``{"ok": False, "error": {...}}`` mit stabilem ``error.code``.
        """

        instance_key = _resolve_instance(instance)

        def _op(service: Service) -> Any:
            use_browser = _resolve_browser_flag() if browser is None else bool(browser)
            backend = service.backend
            if use_browser:
                if not getattr(backend, "login_with_browser", None):
                    raise NotSupportedError(
                        "Browser-Login ist für dieses Backend nicht implementiert.",
                        hint="Für ILIAS HHN / Uni Mannheim: uv sync --extra browser && "
                        "uv run playwright install chromium.",
                    )
                return backend.login_with_browser()  # type: ignore[attr-defined]
            secret_env = os.environ.get(ENV_TOTP_SECRET, "").strip()
            credentials = _load_credentials(username, password, None)
            callback = _totp_callback(secret_env)
            return service.login(credentials, callback)

        return _safe_call("login", _op)

    @server.tool()
    def ilias_status(instance: str | None = None) -> dict[str, Any]:
        """Prüft die gespeicherte Session (Exit 2: keine Session, Exit 3: abgelaufen).

        Args:
            instance: Optionaler Instanz-Schlüssel. Default: ``$ILIAS_INSTANCE``.

        Returns:
            ``{"authenticated": True, ...}`` oder ein ``{"ok": False, ...}``.
        """

        _ = _resolve_instance(instance)

        def _op(service: Service) -> Any:
            return service.status()

        return _safe_call("status", _op)

    @server.tool()
    def ilias_logout(instance: str | None = None) -> dict[str, Any]:
        """Entfernt die gespeicherte Session / den Token.

        Args:
            instance: Optionaler Instanz-Schlüssel. Default: ``$ILIAS_INSTANCE``.
        """

        _ = _resolve_instance(instance)

        def _op(service: Service) -> Any:
            return service.logout()

        return _safe_call("logout", _op)

    @server.tool()
    def ilias_list_courses(instance: str | None = None) -> dict[str, Any]:
        """Listet die Kurse des angemeldeten Nutzers.

        Backends, die ``courses`` nicht implementieren (z. B. ILIAS in dieser
        Version), antworten mit einem ``not_supported``-Fehler - damit der
        Client weiß, dass die Information strukturell fehlt, nicht "leer".

        Args:
            instance: Optionaler Instanz-Schlüssel. Default: ``$ILIAS_INSTANCE``.
        """

        _ = _resolve_instance(instance)

        def _op(service: Service) -> Any:
            return service.courses()

        return _safe_call("courses", _op)

    @server.tool()
    def ilias_list_course_contents(
        course_query: str,
        instance: str | None = None,
        depth: int | None = None,
    ) -> dict[str, Any]:
        """Liefert den Inhalt eines Kurses als Baum (Abschnitte, Module, Dateien).

        Args:
            course_query: Kurs-ID, Kursnummer oder Teilstring von Kurzname /
                Name (case-insensitiv). Eine Zahl, die keine ID ist, wird erst
                im Titel, dann in der Beschreibung gesucht.
            instance: Optionaler Instanz-Schlüssel. Default: ``$ILIAS_INSTANCE``.
            depth: Optionale Baumtiefe (``1``=Abschnitte, ``2``=+Module,
                ``3``=+Dateien/erste Ordnerebene, ...). Default: alles.
        """

        if depth is not None and depth < 1:
            return _error_to_payload(
                "ls",
                ConfigError("--depth muss mindestens 1 sein."),
                None,
            )
        _ = _resolve_instance(instance)

        def _op(service: Service) -> Any:
            return service.ls(course_query, depth)

        return _safe_call("ls", _op)

    @server.tool()
    def report_issue(
        report: dict[str, Any] | None = None,
        user_note: str | None = None,
        consent: bool = False,
        mailto_to: str | None = None,
        previous_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Bereitet einen Fehlerbericht für Nicht-Techniker auf.

        Args:
            report: Optionaler Report aus einer früheren Tool-Antwort (das
                Feld ``report`` aus dem Fehler-JSON). Wird hier durchgereicht,
                damit die Round-Trip-Logik das Format sauber validieren kann.
            user_note: Optionale freie Beschreibung des Nutzers (was wurde
                gemacht, was war das erwartete Ergebnis). Wird mit
                :func:`redact` gefiltert, bevor sie in einen Link wandert.
            consent: **Pflicht:** nur wenn ``True`` werden Links gebaut.
                Ohne Zustimmung antwortet das Tool mit einem klaren
                ``ok=False, status=consent_required``-Payload, inklusive
                Hinweis an den Agenten.
            mailto_to: Optionaler Empfänger für den ``mailto:``-Link;
                überschreibt ``$ILIAS_MCP_REPORT_MAILTO``. Wird an Ort und
                Stelle validiert (maximal eine E-Mail-Adresse, sonst
                ``invalid_mailto``).
            previous_report: Alias für ``report``, falls der Agent das Feld
                in einer früheren Antwort unter diesem Namen gespeichert hat.

        Returns:
            Bei ``consent=False``: ``{"ok": False, "status": "consent_required", ...}``.
            Bei gültiger Eingabe: ``{"ok": True, "redacted_note": ..., "links": {"github": ..., "mailto": ...}}``.

        Notes:
            Das Tool sendet nichts. Es liefert ausschließlich URLs, die der
            Agent (oder der Nutzer) im Browser bzw. Mail-Client öffnet.
        """

        chosen_report = report or previous_report
        if not consent:
            return {
                "ok": False,
                "status": "consent_required",
                "message": (
                    "report_issue sendet nichts automatisch. Bitte den Nutzer "
                    "fragen, ob er den Bericht abgeben möchte, und das Tool "
                    "dann erneut mit consent=true aufrufen."
                ),
                "agent_hint": AGENT_HINT,
            }

        if not isinstance(chosen_report, dict) or not chosen_report:
            return {
                "ok": False,
                "status": "invalid_report",
                "message": (
                    "report_issue braucht das report-Feld aus einer vorherigen "
                    "Fehlerantwort. Ohne diese Daten kann kein sinnvoller "
                    "Bericht erzeugt werden."
                ),
                "agent_hint": AGENT_HINT,
            }

        try:
            report_obj = Report.from_dict(chosen_report)  # type: ignore[attr-defined]
        except (TypeError, ValueError, AttributeError):
            # Sehr defensiv: falls das Format nicht genau passt, das Original
            # übernehmen und nur das Nötigste füllen.
            report_obj = Report(
                tool=str(chosen_report.get("tool", "unknown")),
                error_code=str(chosen_report.get("error_code", "error")),
                institution=chosen_report.get("institution"),
                lms=chosen_report.get("lms"),
                lms_version=chosen_report.get("lms_version"),
                auth_method=chosen_report.get("auth_method"),
                http_status=chosen_report.get("http_status"),
                step=chosen_report.get("step", "execute"),
                error_class=chosen_report.get("error_class", "other"),
            )

        # mailto_To prüfen: leere / offensichtlich ungültige Werte ignorieren.
        if mailto_to is not None and not _is_plausible_email(mailto_to):
            return {
                "ok": False,
                "status": "invalid_mailto",
                "message": "mailto_to enthält keine plausible einzelne E-Mail-Adresse.",
                "agent_hint": AGENT_HINT,
            }

        links = render_issue_links(report_obj, user_note=user_note, to=mailto_to)
        # user_note noch einmal defensiv redigieren, damit nichts Ungewolltes
        # in einen Issue-Body landet.
        from .feedback import redact
        redacted_note = redact(user_note) if user_note else None
        return {
            "ok": True,
            "status": "links_ready",
            "redacted_note": redacted_note,
            "links": links,
            "agent_hint": AGENT_HINT,
        }

    @server.resource("ilias://instances")
    def _instances_resource() -> str:
        """Bekannte Instanzen, ihre Backends und Defaults."""

        import json

        payload = {
            "default": DEFAULT_INSTANCE,
            "instances": [
                {
                    "key": profile.name,
                    "lms": profile.lms,
                    "auth": profile.auth,
                    "base_url": profile.base_url,
                    "client_id": profile.client_id,
                    "label": profile.label,
                    "username_label": profile.username_label,
                }
                for profile in sorted(BUILTIN_INSTANCES.values(), key=lambda p: p.name)
            ],
            "backends": list(KNOWN_BACKENDS),
            "config_dir_env": ENV_CONFIG_DIR,
            "credential_env_vars": [ENV_INSTANCE, ENV_USERNAME, ENV_PASSWORD, ENV_TOTP_SECRET],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    return server


# Entrypoint für ``ilias-mcp``-Console-Skript.
mcp = build_server()


def main() -> None:
    """Entry-Point für ``uv run ilias-mcp``."""

    try:
        mcp.run(transport="stdio")
    except IliasError as exc:  # pragma: no cover - beim Start fast nie erreicht
        print(
            f"ilias-mcp: {exc.message or exc}",
            file=sys.stderr,
        )
        raise SystemExit(exc.exit_code) from exc


if __name__ == "__main__":  # pragma: no cover
    main()