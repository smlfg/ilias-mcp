"""Rewrite tests/snapshots/tools.sha256.json after a reviewed tool-definition change."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = {}
for path in sorted((ROOT / "schemas" / "tools").glob("*.json")):
    spec = json.loads(path.read_text(encoding="utf-8"))
    out[spec["name"]] = hashlib.sha256(
        json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
target = ROOT / "tests" / "snapshots" / "tools.sha256.json"
target.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
print(f"wrote {target}")
