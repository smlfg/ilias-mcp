"""Agentenfreundlicher Feedback-Kanal für ilias-mcp.

Dieses Modul liefert genau das, was das ``report_issue``-Tool und das
``report``-Feld in Fehlerantworten befüllt - getrennt vom Server, damit Tests
beide Pfade (Tool und Fehler-Payload) ohne FastMCP prüfen können.

Drei Bausteine:

* :func:`redact` ersetzt bekannte Secret-Muster (Username, Passwort, TOTP,
  Cookies, Tokens, Kursnamen, Dateinamen, URLs mit Query, E-Mail) durch
  ``[redacted]``. Die Regeln sind absichtlich eng: ein false-positive ist
  schlechter als ein false-negative, das im Agenten-Pfad nachträglich noch
  ergänzt werden kann.
* :func:`build_report` baut ein strukturiertes ``report``-Dict, das in jede
  Fehlerantwort eingebettet wird. Es ist anonymisiert (nur Profil-Schlüssel
  wie ``hhn`` statt Hostnamen), trägt aber genug Kontext, um den Fehler
  nachzustellen.
* :func:`build_issue_url` und :func:`build_mailto` erzeugen vorausgefüllte
  Links - GitHub-Issue bzw. ``mailto:`` - ohne irgendetwas zu übertragen.
"""

from __future__ import annotations

import os
import platform
import re
import sys
import urllib.parse
from dataclasses import asdict, dataclass, field
from typing import Any

from ilias_mcp_core import __version__ as ILIAS_MCP_VERSION
from ilias_mcp_core.version import __version__ as CORE_VERSION

GITHUB_REPO = "smlfg/ilias-mcp"
GITHUB_NEW_ISSUE_URL = f"https://github.com/{GITHUB_REPO}/issues/new"

# ---------------------------------------------------------------------------
# Redaktion
# ---------------------------------------------------------------------------

# Kommentare bleiben absichtlich knapp; die Helfer sind untereinander
# kombinierbar und bewusst defensiv - alles, was wie ein Secret aussieht,
# wird ersetzt.

# High-Entropy-Secret-Muster: lange alphanumerische Token (GitHub-PAT,
# OAuth-Code, Keycloak-Code, JWT). Werden zuerst angewandt, damit die nach-
# folgenden URL/Kurs-Filter nicht versehentlich einen Teilstring übrig lassen.
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{20,}\b")

# TOTP-Codes: 6-stellige Ziffernfolge, die NICHT am Wortanfang steht
# (Kurs-IDs, Seitenzahlen, Fehlercodes dürfen durch).
_TOTP_RE = re.compile(r"(?<![A-Za-z0-9])\d{6}(?![A-Za-z0-9])")

# Basic-Auth / Schlüssel=Wert in URLs: ``?session=abc&token=...`` usw.
_QUERY_RE = re.compile(r"([?&][^&#\s=]+)=([^&#\s]+)")

# Vollständige http(s)-URLs (Hostname + Pfad + Query) – vor Query-Einzelersetzung.
_HTTP_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)

# E-Mail (sehr eng, damit keine Kursnamen verschluckt werden).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Dateiname am Pfad-Ende (typisch in Fehlermeldungen, würde Kursinhalte leaken).
_FILENAME_RE = re.compile(
    r"(/|\\)([^/\s\\]+\.(?:pdf|docx?|pptx?|xlsx?|zip|tar\.gz|tgz|htmlx?|json|xml|csv|txt))",
    re.IGNORECASE,
)

# Datei-Token mit Keyring-/Moodle-/Shibboleth-/Keycloak-Bezug.
# Wichtig: Wörter wie ``passend`` oder Sätze mit "Passwort" im Fließtext
# dürfen NICHT getriggert werden - wir matchen daher nur die
# ``key=value``/``key: value``-Form (oder den Key direkt gefolgt von einer
# typischen Secret-Form wie Bearer/base64). Alles ohne expliziten Trenner
# lassen wir stehen; die späteren Pattern (Token, E-Mail, Query) fangen
# den konkreten Wert.
_KEYWORD_SECRET_RE = re.compile(
    r"(?i)\b("
    r"password|passwort|pwd|"
    r"username|benutzername|user(?:name)?|login[-_]?name|"
    r"totp|otp|2fa|mfa|"
    r"session[-_ ]?(?:id|token|key)|"
    r"api[-_ ]?token|access[-_ ]?token|refresh[-_ ]?token|"
    r"cookie|sid|phpsessid|jsessionid|"
    r"authorization|bearer|client_secret"
    r")\b\s*([=:])\s*([^\s,;\"'}{)\]]+)"
)

# Anzeigenamen, die in Real-Fixtures typischerweise vorkommen (auch wenn
# Tests eigene einsetzen). Liste ist klein und explizit; Erweiterung nur,
# wenn Samuel eine bestimmte Liste bestätigt hat.
_KNOWN_COURSE_NAMES = (
    "mathematik 1",
    "mathematik 2",
    "einführung in die informatik",
)


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def redact(value: str | None) -> str:
    """Secrets, Tokens, Personen- und Kontext-Leaks aus einem String entfernen.

    Die Funktion ist bewusst restriktiv: jeder Treffer wird durch
    ``[redacted]`` ersetzt. Bleibt am Ende ein leerer String (oder nur
    Whitespace), gibt sie ``"[empty]"`` zurück, damit der Agent weiß, dass
    der gerundete Text keinen Sinn mehr ergibt.
    """

    if not value:
        return "[empty]"
    text = str(value)

    # 1. Schlüssel=Wert-Treffer (z. B. "password=hunter2" oder "Bearer abc").
    # Nur wenn der Key explizit von einem Trennzeichen gefolgt wird, ist es
    # ein Secret - andernfalls lassen wir den Text stehen und überlassen den
    # späteren Pattern (Token, E-Mail) die Arbeit.
    text = _KEYWORD_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)

    # 1b. "Bearer <Token>" (auch wenn der Token kein langes base64-Stück ist)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+", r"\1[redacted]", text)

    # 1c. Fließtext-Form: "Passwort ist hunter2", "Token = abc123def".
    # Greift nur, wenn dem Secret-Wort ein nicht-kurzes, alphanumeric-gemischtes
    # Wort folgt (mind. 6 Zeichen). Damit verschlucken wir das Geheimnis im
    # natürlichen Satz, ohne harmlose Sätze wie "Mein Passwort geht nicht"
    # zu treffen (nach "Passwort" steht da kein kryptischer Wert).
    _FLOW_SECRET_WORDS = (
        r"passwort|password|pwd|"
        r"benutzername|username|"
        r"totp|otp|code|pin|"
        r"token|secret|"
        r"session|cookie"
    )
    # Wichtig: kein f-string hier, weil ``{6,}`` nicht durch doppelte Klammern
    # in einem f-string durchrutscht. Stattdessen explizit zusammensetzen.
    flow_pattern = (
        r"(?i)\b(" + _FLOW_SECRET_WORDS + r")\b"
        r"(?:\s+(?:ist|war|waren|=|:|hei\u00dft))?\s+([A-Za-z0-9._\-/+=]{6,})"
    )
    flow_re = re.compile(flow_pattern)

    def _flow_sub(m: re.Match[str]) -> str:
        joined = m.group(0)
        # nur den Wert ersetzen; Schlüsselwort + "ist"/"=" stehen lassen.
        return joined[: -len(m.group(2))] + "[redacted]"

    text = flow_re.sub(_flow_sub, text)

    # 2. TOTP-Codes (vor Tokens, sonst verschlucken Tokens die Ziffern)
    text = _TOTP_RE.sub("[redacted]", text)

    # 3. Lange alphanumerische Token
    text = _TOKEN_RE.sub("[redacted]", text)

    # 3b. Ganze URLs (Instanz-Hostnames, Pfade mit Kurs-IDs)
    text = _HTTP_URL_RE.sub("[redacted-url]", text)

    # 4. Query-String-Bestandteile (``?key=value`` → ``?key=[redacted]``)
    text = _QUERY_RE.sub(lambda m: f"{m.group(1)}=[redacted]", text)

    # 5. E-Mail-Adressen
    text = _EMAIL_RE.sub("[redacted]", text)

    # 6. Dateinamen mit typischen Endungen
    text = _FILENAME_RE.sub(lambda m: f"{m.group(1)}[redacted]", text)

    # 7. Bekannte Kursnamen (sehr gezielt; Whitespace-tolerant)
    for name in _KNOWN_COURSE_NAMES:
        text = re.sub(re.escape(name), "[redacted]", text, flags=re.IGNORECASE)

    text = _normalize_whitespace(text)
    return text or "[empty]"


def redact_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    """Wendet :func:`redact` rekursiv auf alle String-Felder eines Dicts an."""

    if not value:
        return {}
    out: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, str):
            out[key] = redact(item)
        elif isinstance(item, dict):
            out[key] = redact_dict(item)
        elif isinstance(item, list):
            out[key] = [redact_dict(v) if isinstance(v, dict) else (redact(v) if isinstance(v, str) else v) for v in item]
        else:
            out[key] = item
    return out


# ---------------------------------------------------------------------------
# Report-Bau
# ---------------------------------------------------------------------------

# Schritt-Katalog: welche Phase des Tool-Aufrufs schlug fehl? Wird aus dem
# Befehlsnamen plus Exception-Klasse abgeleitet, hilft aber beim
# manuellen Setzen in Tests.

_ERROR_STEP_BY_CODE: dict[str, str] = {
    "auth_failed": "authenticate",
    "not_logged_in": "verify_session",
    "session_expired": "verify_session",
    "network_error": "http_request",
    "parse_error": "parse_response",
    "config_error": "configure",
    "not_supported": "check_capability",
    "course_not_found": "resolve_course",
    "course_ambiguous": "resolve_course",
    "permission_denied": "request",
    "path_not_found": "request",
    "error": "execute",
}

_ERROR_CLASS_BY_CODE: dict[str, str] = {
    "auth_failed": "auth",
    "not_logged_in": "session",
    "session_expired": "session",
    "network_error": "network",
    "parse_error": "parser",
    "config_error": "config",
    "not_supported": "capability",
    "course_not_found": "course",
    "course_ambiguous": "course",
    "permission_denied": "permission",
    "path_not_found": "request",
    "error": "other",
}


@dataclass
class Report:
    """Strukturierter, anonymisierter Fehlerbericht."""

    institution: str | None = None  # Profilname (z. B. "hhn") - KEIN Hostname
    lms: str | None = None  # "ilias" oder "moodle"
    lms_version: str | None = None  # aus SiteInfo, falls erkennbar
    auth_method: str | None = None  # "oidc-keycloak" / "saml-shibboleth" / "moodle-token"
    tool: str = ""  # interner Befehlsname (z. B. "login", "ls")
    step: str = "execute"  # siehe _ERROR_STEP_BY_CODE
    error_class: str = "other"
    error_code: str = "error"
    http_status: int | None = None
    ilias_mcp_version: str = ILIAS_MCP_VERSION
    core_version: str = CORE_VERSION
    python_version: str = platform.python_version()
    os: str = platform.platform(terse=True)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # extra noch einmal defensiv redigieren
        data["extra"] = redact_dict(self.extra)
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Report:
        """Stellt ein :class:`Report` aus einem (gerundeten / vom Client
        weitergereichten) Dict wieder her. Unbekannte Felder werden ignoriert,
        fehlende mit ihren Defaults gefüllt. Weckt keine Exceptions, falls
        Felder den falschen Typ haben - dann wird der Default genutzt."""

        if not isinstance(payload, dict):
            raise TypeError("Report.from_dict braucht ein dict")

        def _opt_str(key: str) -> str | None:
            value = payload.get(key)
            return None if value is None else str(value)

        def _opt_int(key: str) -> int | None:
            value = payload.get(key)
            if value is None:
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        extra = payload.get("extra")
        if not isinstance(extra, dict):
            extra = None

        return cls(
            institution=_opt_str("institution"),
            lms=_opt_str("lms"),
            lms_version=_opt_str("lms_version"),
            auth_method=_opt_str("auth_method"),
            tool=str(payload.get("tool", "") or ""),
            step=str(payload.get("step", "execute") or "execute"),
            error_class=str(payload.get("error_class", "other") or "other"),
            error_code=str(payload.get("error_code", "error") or "error"),
            http_status=_opt_int("http_status"),
            ilias_mcp_version=str(payload.get("ilias_mcp_version", "") or cls().ilias_mcp_version),
            core_version=str(payload.get("core_version", "") or cls().core_version),
            python_version=str(payload.get("python_version", "") or cls().python_version),
            os=str(payload.get("os", "") or cls().os),
            extra=extra or {},
        )


def _detect_http_status(message: str) -> int | None:
    """Sucht im Exception-Message nach ``HTTP <code>`` - falls vorhanden."""

    match = re.search(r"HTTP\s+(\d{3})", message or "")
    return int(match.group(1)) if match else None


def _resolve_lms_version(service: Any) -> str | None:
    """LMS-Version aus dem aktiven Service ableiten - falls vorhanden.

    Bisher nur Moodle (``SiteInfo``); ILIAS liefert keine Versionsangabe,
    also ``None``.
    """

    if service is None:
        return None
    info = getattr(service, "site_info", None)
    if info is None:
        return None
    release = getattr(info, "release", None)
    version = getattr(info, "version", None)
    if release and version:
        return f"{release} ({version})"
    return release or version


def build_report(
    *,
    command: str,
    exc: BaseException,
    service: Any | None,
    extra: dict[str, Any] | None = None,
) -> Report:
    """Report für eine fehlgeschlagene Tool-Operation bauen."""

    code = getattr(exc, "code", "error") or "error"
    message = getattr(exc, "message", None) or str(exc) or ""
    http_status = getattr(exc, "http_status", None) or _detect_http_status(message)
    instance = service.instance if service is not None else None

    return Report(
        institution=instance.key if instance else None,
        lms=instance.lms if instance else None,
        lms_version=_resolve_lms_version(service),
        auth_method=instance.auth if instance else None,
        tool=command,
        step=_ERROR_STEP_BY_CODE.get(code, "execute"),
        error_class=_ERROR_CLASS_BY_CODE.get(code, "other"),
        error_code=code,
        http_status=http_status,
        extra=extra or {},
    )


# ---------------------------------------------------------------------------
# Issue- / mailto-Link-Bau
# ---------------------------------------------------------------------------

AGENT_HINT = (
    "Frag den Nutzer, ob du das melden darfst, dann ruf report_issue auf. "
    "Es wird nichts automatisch übertragen; der Nutzer bekommt einen Link "
    "zum Absenden."
)


def _slugify_title(value: str, *, max_len: int = 80) -> str:
    """Aus einem Fehlertext einen Issue-Titel bauen (kleine Buchstaben,
    Bindestriche, max. ``max_len`` Zeichen, ohne Secrets)."""

    text = redact(value)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:max_len].rstrip("-") or "ilias-mcp-fehler"


def _render_body(report: Report, user_note: str | None) -> str:
    """Body für GitHub-Issue oder Mailto (Plain-Text mit Markdown-Headern)."""

    safe_note = redact(user_note) if user_note else None
    lines = [
        "## Zusammenfassung",
        safe_note or "_keine Nutzer-Beschreibung_",
        "",
        "## Was ist passiert?",
        f"- Tool: `{report.tool}`",
        f"- Schritt: `{report.step}`",
        f"- Fehlerklasse: `{report.error_class}`",
        f"- Fehlercode: `{report.error_code}`",
    ]
    if report.http_status:
        lines.append(f"- HTTP-Status: `{report.http_status}`")
    lines += [
        "",
        "## Umgebung",
        f"- Hochschule/Instanz: `{report.institution or 'unbekannt'}`",
        f"- LMS: `{report.lms or 'unbekannt'}`",
        f"- LMS-Version: `{report.lms_version or 'unbekannt'}`",
        f"- Login-Art: `{report.auth_method or 'unbekannt'}`",
        f"- ilias-mcp Version: `{report.ilias_mcp_version}` (core {report.core_version})",
        f"- Python: `{report.python_version}`",
        f"- OS: `{report.os}`",
    ]
    if report.extra:
        # Reaktive Redaction: wir wissen, dass ``extra``-Felder aus dem
        # Programmcode gefüllt werden - aber ein Agent könnte hier eigenen
        # Text einschmuggeln. Darum noch einmal durch ``redact`` jagen.
        safe_extra = redact_dict(report.extra)
        lines += ["", "## Zusätzliche Daten", "```json", _safe_json(safe_extra), "```"]
    lines += [
        "",
        "## Reproduktion",
        "1. (vom Nutzer ergänzen)",
        "2. (vom Nutzer ergänzen)",
        "",
        "## Erwartet",
        "_vom Nutzer ergänzen_",
        "",
        "_Vom ilias-mcp Feedback-Kanal automatisch befüllt. "
        "Persönliche Daten wurden vor dem Senden redigiert._",
    ]
    return "\n".join(lines)


def _safe_json(payload: dict[str, Any]) -> str:
    """JSON ohne unnötige Zeilenumbrüche rendern (Issue-Body bleibt diff-freundlich)."""

    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def build_issue_url(
    report: Report,
    *,
    user_note: str | None = None,
    extra_labels: tuple[str, ...] = (),
) -> str:
    """Vorausgefüllten GitHub-Issue-Link erzeugen."""

    title = f"[auto-report] {report.tool}: {report.error_code}"
    body = _render_body(report, user_note)

    params = {
        "title": title,
        "body": body,
        "labels": ",".join(("auto-report", report.institution or "unknown", *extra_labels)),
    }
    # urllib baut deterministisch kleinschreibende Keys; gut für Tests.
    return f"{GITHUB_NEW_ISSUE_URL}?{urllib.parse.urlencode(params)}"


def build_mailto(report: Report, *, user_note: str | None = None, to: str | None = None) -> str:
    """mailto-Link als Alternative ohne GitHub-Konto."""

    subject = f"[ilias-mcp] {report.tool}: {report.error_code}"
    body = _render_body(report, user_note)
    params = {"subject": subject, "body": body}
    recipient = to or os.environ.get("ILIAS_MCP_REPORT_MAILTO") or "ilias-mcp@example.invalid"
    return f"mailto:{recipient}?{urllib.parse.urlencode(params)}"


def render_issue_links(
    report: Report,
    *,
    user_note: str | None = None,
    to: str | None = None,
) -> dict[str, str]:
    """Beide Links auf einmal liefern (Issue + mailto)."""

    return {
        "github": build_issue_url(report, user_note=user_note),
        "mailto": build_mailto(report, user_note=user_note, to=to),
    }
