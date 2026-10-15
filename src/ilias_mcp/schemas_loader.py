"""Load tool definitions from ``schemas/tools/*.json`` (STANDARD J1, single source)."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

_REPO_SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "tools"


def tools_dir() -> Path:
    if _REPO_SCHEMAS.is_dir():
        return _REPO_SCHEMAS
    # Installed wheel: force-include puts schemas under ilias_mcp/schemas/tools.
    return Path(str(resources.files("ilias_mcp").joinpath("schemas").joinpath("tools")))


@lru_cache(maxsize=1)
def load_all_tool_schemas() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(tools_dir().glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out[data["name"]] = data
    return out


def load_tool_schema(name: str) -> dict[str, Any]:
    return load_all_tool_schemas()[name]
