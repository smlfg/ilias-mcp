"""Refuse to start when ilias-cli session/config files are readable by others (SEC-15, CT-4).

Checked files (all inside the ilias-cli config directory, ``ILIAS_CLI_CONFIG_DIR``
or ``~/.config/ilias-cli``):

* ``config.toml``
* ``session-<instance>.json`` (ILIAS cookie fallback file)
* ``sessions/<instance>.json`` (Moodle token fallback file)

A file whose mode grants any group/other bit, or that is a symlink, is reported.
Sessions kept in the OS keyring are not files and are not affected.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

from ilias_core.config import default_config_dir


@dataclass(frozen=True)
class InsecureFile:
    path: Path
    mode: int | None
    reason: str

    def describe(self) -> str:
        mode = f"{self.mode:04o}" if self.mode is not None else "?"
        return f"{self.path} ({self.reason}, mode {mode}) -> chmod 600 {self.path}"


def candidate_files(config_dir: Path | None = None) -> list[Path]:
    root = config_dir or default_config_dir()
    files: list[Path] = []
    if not root.exists():
        return files
    for pattern in ("config.toml", "session-*.json", "sessions/*.json"):
        files.extend(sorted(root.glob(pattern)))
    return files


def insecure_files(config_dir: Path | None = None) -> list[InsecureFile]:
    problems: list[InsecureFile] = []
    for path in candidate_files(config_dir):
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode):
            problems.append(InsecureFile(path, None, "Symlink nicht erlaubt"))
            continue
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o077:
            problems.append(InsecureFile(path, mode, "für Gruppe/andere lesbar"))
    return problems
