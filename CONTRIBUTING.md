# Contributing

- Small, atomic commits; one issue per PR; reference the issue number.
- Domain logic belongs in [ilias-cli](https://github.com/smlfg/ilias-cli), not here. This server only maps tools → `ilias_core` → sanitised results.
- Read-only only. A new tool needs: a JSON file in `schemas/tools/`, the name pattern `ilias_<verb>_<object>`, a description with Zweck / Wann nutzen / Wann nicht / Seiteneffekte / Rückgabe (≤ 600 chars), tests against `tests/fake_ilias.py`, a README table row and `uv run python scripts/update_snapshot.py`.
- No new dependencies without the `deps-approved` label. No real HTML, names, cookies or credentials in fixtures.
- Before pushing: `uv run ruff check . && uv run pyright && uv run pytest --cov`.
