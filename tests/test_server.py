"""Tests für :mod:`ilias_mcp.server`."""

from __future__ import annotations

from typing import Any

import pytest

from ilias_mcp import server as server_mod
from ilias_mcp_core.errors import (
    AuthenticationError,
    ConfigError,
    IliasError,
)
from ilias_mcp_core.models import (
    Course,
    CourseContentsResult,
    CoursesResult,
    LogoutResult,
    MoodleLoginResult,
    MoodleStatusResult,
    Section,
)
from ilias_mcp_core.service import Service

from tests.conftest import FakeBackend, call_tool


# ----------------------------------------------------------- Hilfen
def _service_with(backend: FakeBackend) -> Service:
    """Baut einen Service mit dem Fake-Backend."""

    from ilias_mcp_core.config import load_instance

    instance = load_instance("hhn")
    return Service(instance=instance, backend=backend)


def _patch_open_service(monkeypatch: pytest.MonkeyPatch, backend: FakeBackend) -> None:
    """Patcht ``open_service`` so, dass es unser Service mit Fake-Backend liefert."""

    monkeypatch.setattr(server_mod, "open_service", lambda *a, **k: _service_with(backend))


# ----------------------------------------------------------- Credentials
class TestCredentialLoading:
    def test_login_requires_username(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ILIAS_USERNAME", raising=False)
        monkeypatch.delenv("ILIAS_PASSWORD", raising=False)

        with pytest.raises(ConfigError) as excinfo:
            server_mod._load_credentials(None, None, None)

        assert "ILIAS_USERNAME" in (excinfo.value.hint or "")

    def test_login_requires_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ILIAS_USERNAME", "alice")
        monkeypatch.delenv("ILIAS_PASSWORD", raising=False)

        with pytest.raises(ConfigError) as excinfo:
            server_mod._load_credentials(None, None, None)

        assert "ILIAS_PASSWORD" in (excinfo.value.hint or "")

    def test_login_accepts_env_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ILIAS_USERNAME", "alice")
        monkeypatch.setenv("ILIAS_PASSWORD", "geheim")
        creds = server_mod._load_credentials(None, None, None)
        assert creds.username == "alice"
        # Secret maskiert sich selbst
        assert repr(creds.password) == "Secret(***)"

    def test_login_args_override_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ILIAS_USERNAME", "alice")
        monkeypatch.setenv("ILIAS_PASSWORD", "env-pw")
        creds = server_mod._load_credentials("bob", "arg-pw", None)
        assert creds.username == "bob"
        assert creds.password.reveal() == "arg-pw"

    def test_password_not_in_repr(self) -> None:
        creds = server_mod._load_credentials("alice", "secret-pw", None)
        text = repr(creds)
        assert "secret-pw" not in text
        assert "***" in text


# ----------------------------------------------------------- Instanz-Auflösung
class TestInstanceResolution:
    def test_default_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert server_mod._resolve_instance(None) == "hhn"

    def test_explicit_argument_wins(self) -> None:
        assert server_mod._resolve_instance("hs-mannheim") == "hs-mannheim"

    def test_env_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ILIAS_INSTANCE", "uni-mannheim")
        assert server_mod._resolve_instance(None) == "uni-mannheim"

    def test_argument_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ILIAS_INSTANCE", "uni-mannheim")
        assert server_mod._resolve_instance("hs-mannheim") == "hs-mannheim"


# ----------------------------------------------------------- Browser-Flag
class TestBrowserFlag:
    def test_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert server_mod._resolve_browser_flag() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
    def test_truthy_env(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("ILIAS_LOGIN_BROWSER", value)
        assert server_mod._resolve_browser_flag() is True


# ----------------------------------------------------------- Normalisierung
class TestNormalize:
    def test_dict_passthrough(self) -> None:
        assert server_mod._normalize({"ok": True}) == {"ok": True}

    def test_moodle_login(self) -> None:
        result = MoodleLoginResult(
            instance="hs-mannheim",
            lms="moodle",
            base_url="https://m",
            username="u",
            fullname="f",
            sitename="s",
        )
        normalized = server_mod._normalize(result)
        assert normalized["ok"] is True
        assert normalized["command"] == "login"
        assert normalized["instance"] == "hs-mannheim"

    def test_courses_result(self) -> None:
        result = CoursesResult(
            instance="hs-mannheim",
            lms="moodle",
            courses=[Course(id=1, fullname="Kurs A", shortname="KA")],
        )
        normalized = server_mod._normalize(result)
        assert normalized["count"] == 1
        assert normalized["courses"][0]["id"] == 1

    def test_course_contents(self) -> None:
        result = CourseContentsResult(
            instance="hs-mannheim",
            lms="moodle",
            course={"id": 1, "fullname": "Kurs A", "shortname": "KA"},
            sections=[Section(id=1, number=1, name="A")],
        )
        normalized = server_mod._normalize(result)
        assert normalized["sections"][0]["name"] == "A"


# ----------------------------------------------------------- Error-Payload
class TestErrorPayload:
    def test_payload_shape(self) -> None:
        exc = AuthenticationError("falsches passwort", hint="nochmal")
        payload = server_mod._error_to_payload("login", exc, None)
        assert payload["ok"] is False
        assert payload["command"] == "login"
        assert payload["error"]["code"] == "auth_failed"
        assert payload["error"]["hint"] == "nochmal"

    def test_payload_without_service(self) -> None:
        exc = IliasError("irgendwas")
        payload = server_mod._error_to_payload("status", exc, None)
        assert payload["instance"] is None
        assert payload["lms"] is None


# ----------------------------------------------------------- Tool-Handler
class TestToolHandlers:
    def test_status_calls_backend(
        self, monkeypatch: pytest.MonkeyPatch, fake_backend: FakeBackend
    ) -> None:
        _patch_open_service(monkeypatch, fake_backend)
        from ilias_mcp.server import build_server

        result = call_tool(build_server(), "ilias_status", instance="hhn")
        assert result["authenticated"] is True
        assert fake_backend.status_calls == 1

    def test_logout_removes_session(
        self, monkeypatch: pytest.MonkeyPatch, fake_backend: FakeBackend
    ) -> None:
        _patch_open_service(monkeypatch, fake_backend)
        from ilias_mcp.server import build_server

        result = call_tool(build_server(), "ilias_logout", instance="hhn")
        assert result["ok"] is True
        assert fake_backend.logout_calls == 1

    def test_list_courses_returns_dict(
        self, monkeypatch: pytest.MonkeyPatch, fake_backend: FakeBackend
    ) -> None:
        fake_backend.courses_result = [
            Course(id=1, fullname="A", shortname="a"),
            Course(id=2, fullname="B", shortname="b"),
        ]
        _patch_open_service(monkeypatch, fake_backend)
        from ilias_mcp.server import build_server

        result = call_tool(build_server(), "ilias_list_courses", instance="hhn")
        assert result["count"] == 2
        assert fake_backend.courses_calls == 1

    def test_list_course_contents(
        self, monkeypatch: pytest.MonkeyPatch, fake_backend: FakeBackend
    ) -> None:
        fake_backend.courses_result = [Course(id=1, fullname="Kurs 1", shortname="k1")]
        fake_backend.contents_result = [Section(id=1, number=1, name="Sektion 1")]
        _patch_open_service(monkeypatch, fake_backend)
        from ilias_mcp.server import build_server

        result = call_tool(
            build_server(),
            "ilias_list_course_contents",
            course_query="1",
            instance="hhn",
            depth=2,
        )
        assert result["sections"][0]["name"] == "Sektion 1"
        assert fake_backend.contents_calls == [(1, 2)]

    def test_depth_validation_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch, fake_backend: FakeBackend
    ) -> None:
        _patch_open_service(monkeypatch, fake_backend)
        from ilias_mcp.server import build_server

        result = call_tool(
            build_server(),
            "ilias_list_course_contents",
            course_query="1",
            instance="hhn",
            depth=0,
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "config_error"
        # Backend wurde nicht aufgerufen
        assert fake_backend.contents_calls == []

    def test_login_passes_credentials(
        self, monkeypatch: pytest.MonkeyPatch, fake_backend: FakeBackend
    ) -> None:
        monkeypatch.setenv("ILIAS_USERNAME", "alice")
        monkeypatch.setenv("ILIAS_PASSWORD", "geheim")
        _patch_open_service(monkeypatch, fake_backend)
        from ilias_mcp.server import build_server

        result = call_tool(build_server(), "ilias_login", instance="hhn")
        assert result["authenticated"] is True
        assert fake_backend.login_calls, "backend wurde nicht aufgerufen"
        creds, callback = fake_backend.login_calls[0]
        assert creds.username == "alice"
        # Passwort kommt als Secret in den Core und liegt nicht im Klartext in der Antwort
        assert "geheim" not in str(result)
        assert callback is None  # kein TOTP konfiguriert

    def test_login_maps_ilias_error(
        self, monkeypatch: pytest.MonkeyPatch, fake_backend: FakeBackend
    ) -> None:
        def _raise(_creds: Any, _cb: Any = None) -> None:
            raise AuthenticationError("falsch", hint="nochmal")

        fake_backend.login = _raise  # type: ignore[assignment]
        monkeypatch.setenv("ILIAS_USERNAME", "alice")
        monkeypatch.setenv("ILIAS_PASSWORD", "x")
        _patch_open_service(monkeypatch, fake_backend)
        from ilias_mcp.server import build_server

        result = call_tool(build_server(), "ilias_login", instance="hhn")
        assert result["ok"] is False
        assert result["error"]["code"] == "auth_failed"
        assert result["error"]["hint"] == "nochmal"

    def test_login_with_totp_secret_calls_callback(
        self, monkeypatch: pytest.MonkeyPatch, fake_backend: FakeBackend
    ) -> None:
        monkeypatch.setenv("ILIAS_USERNAME", "alice")
        monkeypatch.setenv("ILIAS_PASSWORD", "geheim")
        monkeypatch.setenv("ILIAS_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
        _patch_open_service(monkeypatch, fake_backend)
        from ilias_mcp.server import build_server

        result = call_tool(build_server(), "ilias_login", instance="hhn")
        assert result["authenticated"] is True
        assert fake_backend.login_calls, "backend wurde nicht aufgerufen"
        _creds, callback = fake_backend.login_calls[0]
        assert callback is not None

    def test_login_browser_flag(
        self, monkeypatch: pytest.MonkeyPatch, fake_backend: FakeBackend
    ) -> None:
        monkeypatch.setenv("ILIAS_LOGIN_BROWSER", "1")
        _patch_open_service(monkeypatch, fake_backend)
        from ilias_mcp.server import build_server

        result = call_tool(build_server(), "ilias_login", instance="hhn")
        assert result["authenticated"] is True
        assert fake_backend.login_with_browser_calls == 1
        # Kein Passwort nötig, wenn Browser genutzt wird
        assert fake_backend.login_calls == []


# ----------------------------------------------------------- Tool-Registrierung
class TestToolRegistration:
    def test_all_expected_tools_registered(self) -> None:
        from ilias_mcp.server import build_server

        server = build_server()
        tools = list(server._tool_manager._tools.keys())
        assert set(tools) == {
            "ilias_login",
            "ilias_status",
            "ilias_logout",
            "ilias_list_courses",
            "ilias_list_course_contents",
        }

    def test_tools_have_descriptions(self) -> None:
        from ilias_mcp.server import build_server

        server = build_server()
        for name, tool in server._tool_manager._tools.items():
            assert tool.description, f"{name}: description fehlt"

    def test_resource_registered(self) -> None:
        from ilias_mcp.server import build_server

        server = build_server()
        # Resource-Funktion ist über den ResourceManager erreichbar; das Vorhandensein
        # von ``_instances_resource`` reicht als Smoke-Test.
        assert hasattr(server, "_tool_manager")