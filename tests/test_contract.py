"""Contract, snapshot (CT-6/SEC-21) and Glama lint (STANDARD Q2, G1-G5) tests."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ilias_mcp.schemas_loader import load_all_tool_schemas

from .helpers import list_tools

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path(__file__).parent / "snapshots" / "tools.sha256.json"
NAME_RE = re.compile(r"^ilias_(list|get|search|check|download|prepare|confirm)_[a-z]+(_[a-z]+)*$")


def _published() -> dict[str, dict]:
    return {
        t.name: t.model_dump(mode="json", by_alias=True, exclude_none=True)
        for t in list_tools()
    }


def _digest(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def test_tools_list_equals_json_files():
    published = _published()
    files = load_all_tool_schemas()
    assert sorted(published) == sorted(files)
    for name, spec in files.items():
        got = published[name]
        for key in ("name", "title", "description", "annotations", "inputSchema", "outputSchema"):
            assert got[key] == spec[key], (name, key)


def test_snapshot_unchanged():
    """Fails on any change to a tool definition until tests/snapshots is updated in the same PR."""
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    actual = {name: _digest(spec) for name, spec in sorted(load_all_tool_schemas().items())}
    assert actual == expected, (
        "Tool definitions changed. Review the diff, then run "
        "`uv run python scripts/update_snapshot.py` and commit the snapshot."
    )


def test_glama_lint():
    tools = load_all_tool_schemas()
    assert 3 <= len(tools) <= 10
    for name, spec in tools.items():
        assert NAME_RE.fullmatch(name), name
        desc = spec["description"]
        assert len(desc) <= 600, name
        for part in ("Zweck:", "Wann nutzen:", "Wann nicht:", "Seiteneffekte:", "Rückgabe:"):
            assert part in desc, (name, part)
        ann = spec["annotations"]
        assert ann["readOnlyHint"] is True and ann["destructiveHint"] is False
        assert ann["idempotentHint"] is True and isinstance(ann["openWorldHint"], bool)
        schema = spec["inputSchema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        for pname, prop in schema["properties"].items():
            assert prop.get("description"), (name, pname)
            if pname not in schema.get("required", []):
                assert "default" in prop, (name, pname)


def test_other_tools_named_in_descriptions_exist():
    tools = load_all_tool_schemas()
    for spec in tools.values():
        for ref in re.findall(r"ilias_[a-z_]+", spec["description"]):
            assert ref in tools, ref


def test_no_write_or_login_tools():
    names = set(load_all_tool_schemas())
    assert not {n for n in names if re.search(r"login|logout|setup|download|delete|send", n)}


def test_readme_tool_table_matches():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    table_names = re.findall(r"^\| `(ilias_[a-z_]+)` \|", readme, flags=re.M)
    assert sorted(table_names) == sorted(load_all_tool_schemas())


def test_versions_match():
    import tomllib

    from ilias_mcp import __version__

    py = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    server = json.loads((ROOT / "server.json").read_text())
    assert py == __version__ == server["version"] == server["packages"][0]["version"]
