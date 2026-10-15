import os
from pathlib import Path

from ilias_mcp.permissions import insecure_files
from ilias_mcp.server import check_startup


def test_0600_ok_0644_refuses(config_dir: Path, monkeypatch):
    session = config_dir / "session-hhn.json"
    fd = os.open(session, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, b"{}")
    os.close(fd)
    assert insecure_files(config_dir) == []
    os.chmod(session, 0o644)
    problems = insecure_files(config_dir)
    assert len(problems) == 1 and problems[0].mode == 0o644
    monkeypatch.setenv("ILIAS_CLI_CONFIG_DIR", str(config_dir))
    assert check_startup() == 78
    os.chmod(session, 0o600)
    assert check_startup() == 0
