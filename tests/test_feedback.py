"""Tests für den Feedback-Kanal :mod:`ilias_mcp.feedback` und das Tool
``report_issue`` im Server.

Drei Schwerpunkte:

* Redaktion trifft keine false-negatives (Secrets, E-Mail, lange Tokens,
  Query-Strings, bekannte Kursnamen, Dateinamen werden ersetzt).
* :func:`build_report` baut einen vollständigen, anonymisierten Bericht aus
  einer :class:`IliasError`-Instanz.
* Das MCP-Tool ``report_issue`` blockiert ohne ``consent``, akzeptiert mit
  ``consent=True`` einen zuvor empfangenen ``report``-Dict und liefert einen
  vorausgefüllten GitHub-Issue- sowie ``mailto:``-Link - ohne irgendetwas
  zu übertragen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from ilias_mcp import server as server_mod
from ilias_mcp.feedback import (
    AGENT_HINT,
    GITHUB_NEW_ISSUE_URL,
    Report,
    build_issue_url,
    build_mailto,
    build_report,
    redact,
    redact_dict,
    render_issue_links,
)
from ilias_mcp_core.errors import (
    AuthenticationError,
    ConfigError,
    NetworkError,
    ParserError,
)


# ---------------------------------------------------------------- Redaktion
class TestRedact:
    def test_password_is_hidden(self) -> None:
        out = redact("password=hunter2 bitte prüfen")
        assert "hunter2" not in out
        assert "[redacted]" in out

    def test_username_with_colon_is_hidden(self) -> None:
        # deutschsprachige Eingabemuster
        out = redact("username: max.musterman")
        assert "max.musterman" not in out

    def test_totp_is_hidden(self) -> None:
        out = redact("Code 123456 eingegeben")
        assert "123456" not in out
        # sechsstellige Codes müssen verschwinden, andere Zahlen nicht zwingend
        out2 = redact("HTTP 500 nach 12s")
        assert "12s" in out2 or "12" in out2  # "12s" bzw. "12" bleibt; nur genau 6-stellige Codes verschwinden

    def test_bearer_token_is_hidden(self) -> None:
        token = "ghp_" + "x" * 36
        out = redact(f"Authorization: Bearer {token}")
        assert token not in out

    def test_long_query_token_is_hidden(self) -> None:
        url = "https://example.org/callback?code=abcdefghijklmnopqrstuvwxyz1234567890ABCDEF&state=abc"
        out = redact(url)
        # das lange ``code``-Query-Wert verschwindet
        assert "abcdefghijklmnopqrstuvwxyz1234567890ABCDEF" not in out

    def test_query_short_value_is_hidden(self) -> None:
        # Session-Cookies/Login-Redirects landen oft in der URL.
        out = redact("https://x.test/foo?session=abc")
        assert "abc" not in out
        assert "x.test" not in out
        assert "[redacted-url]" in out

    def test_query_path_values_also_redacted(self) -> None:
        # defensiv: ganze URLs verschwinden (Hostname, Pfad, Query).
        out = redact("https://x.test/foo?session=abc&next=/ok")
        assert "abc" not in out
        assert "/ok" not in out
        assert "x.test" not in out
        assert "[redacted-url]" in out

    def test_bare_query_string_values_redacted(self) -> None:
        # Ohne Schema bleibt die Query-Einzelersetzung aktiv.
        out = redact("redirect?session=abc&next=/ok")
        assert "abc" not in out
        assert "session=[redacted]" in out
        assert "next=[redacted]" in out

    def test_email_is_hidden(self) -> None:
        out = redact("mail an foo.bar@example.org bitte")
        assert "foo.bar@example.org" not in out

    def test_filename_with_extension_is_hidden(self) -> None:
        out = redact("/tmp/Skript-Aufgabenblatt.pdf war im Anhang")
        assert "Skript-Aufgabenblatt.pdf" not in out

    def test_known_course_name_is_hidden(self) -> None:
        out = redact("Im Kurs Mathematik 1 fehlt ein Modul")
        assert "Mathematik 1" not in out.lower()

    def test_full_url_is_hidden(self) -> None:
        out = redact("Fehler von https://ilias.hs-heilbronn.de/login?session=abc")
        assert "ilias.hs-heilbronn.de" not in out
        assert "session=abc" not in out
        assert "[redacted-url]" in out or "[redacted]" in out

    def test_phrase_without_secrets_is_kept(self) -> None:
        out = redact("Anmeldung an HHN klappt nicht, vielleicht Wartung?")
        # nichts soll ersetzt werden, was nicht wie ein Secret aussieht
        assert out == "Anmeldung an HHN klappt nicht, vielleicht Wartung?"

    def test_empty_input_returns_marker(self) -> None:
        assert redact("") == "[empty]"
        assert redact(None) == "[empty]"
        assert redact("    ") == "[empty]"

    def test_redact_dict_recurses(self) -> None:
        payload = redact_dict({"a": "password=hunter2", "b": {"c": "x@y.de"}})
        assert payload["a"] != "password=hunter2"
        assert payload["b"]["c"] != "x@y.de"


# ---------------------------------------------------------------- Report-Bau
@dataclass
class _FakeInstance:
    key: str = "hhn"
    lms: str = "ilias"
    auth: str = "oidc-keycloak"


@dataclass
class _FakeService:
    instance: _FakeInstance = None  # type: ignore[assignment]


@pytest.fixture
def fake_service() -> _FakeService:
    return _FakeService(instance=_FakeInstance())


class TestBuildReport:
    def test_auth_failed_maps_to_auth_step(self, fake_service: _FakeService) -> None:
        exc = AuthenticationError("Login fehlgeschlagen")
        report = build_report(command="login", exc=exc, service=fake_service)
        assert report.institution == "hhn"
        assert report.lms == "ilias"
        assert report.auth_method == "oidc-keycloak"
        assert report.tool == "login"
        assert report.step == "authenticate"
        assert report.error_class == "auth"
        assert report.error_code == "auth_failed"

    def test_network_error_extracts_http_status(self, fake_service: _FakeService) -> None:
        exc = NetworkError("Serverfehler HTTP 503 von https://h.example.")
        report = build_report(command="ls", exc=exc, service=fake_service)
        assert report.http_status == 503
        # LMS-Hostnamen dürfen NICHT übernommen werden - die Report.to_dict
        # enthält die Profile-Daten, nicht den URL
        assert "h.example" not in report.to_dict()["institution"]

    def test_parse_error_classification(self, fake_service: _FakeService) -> None:
        exc = ParserError("Unerwartetes HTML")
        report = build_report(command="courses", exc=exc, service=fake_service)
        assert report.error_class == "parser"
        assert report.step == "parse_response"

    def test_config_error_classification(self, fake_service: _FakeService) -> None:
        exc = ConfigError("Kein Passwort gesetzt")
        report = build_report(command="login", exc=exc, service=None)
        assert report.institution is None
        assert report.error_class == "config"
        assert report.error_code == "config_error"

    def test_report_carries_runtime_metadata(self, fake_service: _FakeService) -> None:
        report = build_report(
            command="login",
            exc=AuthenticationError("x"),
            service=fake_service,
            extra={"hint": "Passwort prüfen"},
        )
        d = report.to_dict()
        assert d["ilias_mcp_version"]  # nicht leer
        assert d["python_version"]  # nicht leer
        assert d["os"]              # nicht leer
        assert d["extra"]["hint"] == "Passwort prüfen"


# ---------------------------------------------------------------- Issue-Links
class TestLinks:
    def _fixture_report(self) -> Report:
        return Report(
            institution="hhn",
            lms="ilias",
            auth_method="oidc-keycloak",
            tool="login",
            step="authenticate",
            error_class="auth",
            error_code="auth_failed",
            http_status=401,
            extra={"hint": "Beispiel"},
        )

    def test_build_issue_url_contains_repo_and_labels(self) -> None:
        url = build_issue_url(self._fixture_report(), user_note="Hilfe")
        assert url.startswith(GITHUB_NEW_ISSUE_URL + "?")
        assert "labels=" in url
        assert "auto-report" in url
        assert "hhn" in url

    def test_build_issue_url_redacts_secrets_in_note(self) -> None:
        url = build_issue_url(
            self._fixture_report(),
            user_note="Mein Passwort ist hunter2 - bitte prüfen",
        )
        assert "hunter2" not in url
        assert "%5Bredacted%5D" in url or "[redacted]" in url

    def test_build_mailto_has_subject_and_body(self) -> None:
        url = build_mailto(
            self._fixture_report(),
            user_note="Hallo",
            to="support@example.org",
        )
        assert url.startswith("mailto:support@example.org?")
        assert "subject=" in url
        assert "body=" in url

    def test_render_issue_links_returns_both(self) -> None:
        links = render_issue_links(self._fixture_report())
        assert "github" in links and "mailto" in links

    def test_no_secrets_in_issue_url(self) -> None:
        rep = self._fixture_report()
        rep.extra = {"note": "password=hunter2 token=abcdefghijklmnopqrstuvwxyz1234567890XYZ"}
        url = build_issue_url(rep, user_note=None)
        assert "hunter2" not in url
        assert "abcdefghijklmnopqrstuvwxyz1234567890XYZ" not in url


# ---------------------------------------------------------------- Tool
class TestReportIssueTool:
    @pytest.fixture
    def tool_fn(self, fastmcp_server):
        tools = fastmcp_server._tool_manager._tools  # type: ignore[attr-defined]
        tool = tools.get("report_issue")
        assert tool is not None
        return tool.fn

    def test_blocks_without_consent(self, tool_fn) -> None:
        result = tool_fn(report={"tool": "login", "error_code": "auth_failed"})
        assert result["ok"] is False
        assert result["status"] == "consent_required"
        assert result["agent_hint"] == AGENT_HINT
        # KEINE Links in der Antwort
        assert "links" not in result

    def test_blocks_without_report(self, tool_fn) -> None:
        result = tool_fn(consent=True)
        assert result["ok"] is False
        assert result["status"] == "invalid_report"

    def test_builds_links_with_consent(self, tool_fn) -> None:
        report = {
            "institution": "hhn",
            "lms": "ilias",
            "auth_method": "oidc-keycloak",
            "tool": "login",
            "step": "authenticate",
            "error_class": "auth",
            "error_code": "auth_failed",
            "http_status": 401,
            "ilias_mcp_version": "0.1.0",
            "core_version": "0.1.0",
            "python_version": "3.12",
            "os": "Linux",
            "extra": {},
        }
        result = tool_fn(report=report, consent=True, user_note="Bitte helfen")
        assert result["ok"] is True
        assert result["status"] == "links_ready"
        assert result["redacted_note"]
        assert "github" in result["links"]
        assert "mailto" in result["links"]
        assert result["links"]["github"].startswith(GITHUB_NEW_ISSUE_URL)

    def test_redacts_user_note(self, tool_fn) -> None:
        report = {"tool": "login", "error_code": "auth_failed"}
        result = tool_fn(
            report=report,
            consent=True,
            user_note="Mein Passwort ist hunter2",
        )
        assert result["ok"] is True
        # gerundeter Hinweis im redacted_note, Original nirgendwo im Link
        assert "hunter2" not in result["redacted_note"]
        for url in result["links"].values():
            assert "hunter2" not in url

    def test_rejects_invalid_mailto_target(self, tool_fn) -> None:
        report = {"tool": "login", "error_code": "auth_failed"}
        result = tool_fn(report=report, consent=True, mailto_to="not-an-email; DROP TABLE")
        assert result["status"] == "invalid_mailto"

    def test_no_network_call_is_made(self, tool_fn) -> None:
        # das Tool darf unter keinen Umständen HTTP/IO machen.
        report = {"tool": "login", "error_code": "auth_failed"}
        with patch("urllib.request.urlopen") as urlopen:
            result = tool_fn(report=report, consent=True)
            urlopen.assert_not_called()
        assert result["ok"] is True


# ---------------------------------------------------------------- Integration: Fehlerantwort
class TestErrorPayloadHasReport:
    def test_safe_call_attaches_report(self) -> None:
        from ilias_mcp.server import _error_to_payload

        exc = AuthenticationError("Login fehlgeschlagen")
        service = _FakeService(instance=_FakeInstance())
        payload = _error_to_payload("login", exc, service)  # type: ignore[arg-type]
        assert payload["ok"] is False
        assert payload["agent_hint"] == AGENT_HINT
        assert "report" in payload
        report = payload["report"]
        assert report["tool"] == "login"
        assert report["institution"] == "hhn"
        assert report["error_class"] == "auth"

    def test_error_payload_does_not_leak_url(self) -> None:
        from ilias_mcp.server import _error_to_payload

        exc = NetworkError("Serverfehler HTTP 502 von https://ilias.hs-heilbronn.de.")
        service = _FakeService(instance=_FakeInstance())
        payload = _error_to_payload("login", exc, service)  # type: ignore[arg-type]
        # Profil-Schlüssel ja, Hostnamen nein
        assert payload["report"]["institution"] == "hhn"
        assert "ilias.hs-heilbronn.de" not in str(payload)
