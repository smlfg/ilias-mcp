"""Shared fixtures: isolated ilias-cli config dir, localhost fake ILIAS, in-memory client.

Network sandbox: every socket connect outside 127.0.0.1/::1 fails the test.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from .fake_ilias import CANARY_COOKIE, FakeIlias

_real_connect = socket.socket.connect


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if host not in ("127.0.0.1", "::1", "localhost") and not str(host).startswith("/"):
        raise AssertionError(f"external network access in tests: {host!r}")
    return _real_connect(self, address)


@pytest.fixture(autouse=True)
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = tmp_path / "ilias-cli"
    cfg.mkdir(mode=0o700)
    monkeypatch.setenv("ILIAS_CLI_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    monkeypatch.setenv("ILIAS_CLI_REQUEST_INTERVAL", "0")
    monkeypatch.delenv("ILIAS_CLI_MAX_REQUESTS", raising=False)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    yield cfg


@pytest.fixture
def config_dir(_sandbox) -> Path:
    return _sandbox


def write_private(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, mode)


@pytest.fixture
def fake(config_dir: Path):
    world = FakeIlias().start()
    write_private(
        config_dir / "config.toml",
        f'instance = "hhn"\n[instances.hhn]\nbase_url = "{world.base}"\n',
    )
    yield world
    world.stop()


@pytest.fixture
def logged_in(fake: FakeIlias, config_dir: Path) -> FakeIlias:
    write_private(config_dir / "session-hhn.json", json.dumps({"PHPSESSID": CANARY_COOKIE}))
    return fake
