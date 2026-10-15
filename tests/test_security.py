"""Mandatory contract tests CT-1 … CT-5 (SECURITY_BASELINE §6)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from .fake_ilias import CANARY_COOKIE, CANARY_SET_COOKIE, FakeIlias
from .helpers import call

ALL_CALLS = [
    ("ilias_list_instances", {}),
    ("ilias_get_status", {}),
    ("ilias_list_courses", {}),
    ("ilias_list_contents", {"course": "900101", "depth": 3}),
    ("ilias_list_contents", {"course": "nope"}),
]


def _dump(result) -> str:
    return json.dumps(result.model_dump(mode="json", by_alias=True), ensure_ascii=False)


# CT-1 -------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["normal", "injection", "expired", "error500", "garbage"])
def test_ct1_canary_never_in_output_or_logs(logged_in: FakeIlias, mode, capfd):
    logged_in.mode = mode
    for tool, args in ALL_CALLS:
        text = _dump(call(tool, args))
        assert CANARY_COOKIE not in text, (tool, mode)
        assert CANARY_SET_COOKIE not in text, (tool, mode)
    out, err = capfd.readouterr()
    assert CANARY_COOKIE not in out + err
    assert CANARY_SET_COOKIE not in out + err


# CT-2 -------------------------------------------------------------------------
def test_ct2_prompt_injection_is_sanitised_data(logged_in: FakeIlias):
    logged_in.mode = "injection"
    r = call("ilias_list_courses")
    assert not r.is_error
    titles = [c["title"] for c in r.structured_content["courses"]]
    evil = next(t for t in titles if t.startswith("Ignore previous instructions"))
    assert "\x1b" not in evil and "\u200b" not in evil
    assert "![x](" not in evil and "[Bild: x]" in evil
    assert "[REDACTED]" in evil
    # only in a data field, never in tool definitions
    from .helpers import list_tools

    for tool in list_tools():
        assert "Ignore previous" not in (tool.description or "")


# CT-3 -------------------------------------------------------------------------
def test_ct3_never_post(logged_in: FakeIlias):
    for tool, args in ALL_CALLS:
        call(tool, args)
    methods = logged_in.methods()
    assert set(methods) == {"GET"}, methods


# CT-4 -------------------------------------------------------------------------
def test_ct4_refuses_start_with_0644_session(config_dir: Path):
    session = config_dir / "session-hhn.json"
    session.write_text('{"PHPSESSID": "x"}')
    os.chmod(session, 0o644)
    env = {**os.environ, "ILIAS_CLI_CONFIG_DIR": str(config_dir)}
    proc = subprocess.run(
        [sys.executable, "-c", "from ilias_mcp.server import main; main()"],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 78
    assert proc.stdout == ""
    assert "chmod 600" in proc.stderr


def test_ct4_symlinked_session_refused(config_dir: Path, tmp_path: Path):
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    os.chmod(target, 0o600)
    (config_dir / "session-hhn.json").symlink_to(target)
    from ilias_mcp.permissions import insecure_files

    assert insecure_files(config_dir)


# CT-5 -------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["expired", "keycloak", "oidc", "saml", "foreign"])
@pytest.mark.parametrize("tool", ["ilias_get_status", "ilias_list_courses"])
def test_ct5_redirect_guard_one_request(logged_in: FakeIlias, config_dir: Path, mode, tool):
    session = config_dir / "session-hhn.json"
    before = session.read_bytes()
    logged_in.mode = mode
    r = call(tool)
    assert r.is_error
    assert r.structured_content["error"]["code"] == "session_expired"
    assert len(logged_in.requests) == 1
    assert session.read_bytes() == before
