# ilias-mcp

Read-only [MCP](https://modelcontextprotocol.io) server (stdio) for **ILIAS** (HS Heilbronn, Uni Mannheim) and **Moodle** (HS Mannheim). It is a thin wrapper around [ilias-cli](https://github.com/smlfg/ilias-cli): the server imports its `ilias_core` package and reuses the session that `ilias login` stored. It never logs in and never sees a password or 2FA code.

## Tools

| Tool | Purpose | Network |
|---|---|---|
| `ilias_list_instances` | Known instances (built-in + `config.toml`) and whether a session file exists | none |
| `ilias_get_status` | Is the stored session for an instance valid? | 1 GET (ILIAS) |
| `ilias_list_courses` | Own courses and groups with `id` (ref_id), title, semester, URL | 1 GET (ILIAS) |
| `ilias_list_contents` | Content tree of one course (`course` = ref_id or unique title part, `depth` 1–5, default 2) | ~1 GET per folder level, budget ≤ 60 |

All tools are read-only (`readOnlyHint: true`, `destructiveHint: false`). Errors come back as `isError: true` with a stable `code`:

| ilias-cli exit | `code` | Meaning / next step |
|---|---|---|
| 1 | `auth_failed`, `config_error`, `not_supported`, `course_not_found`, `course_ambiguous` (with `candidates`), `permission_denied` | fix the input or use `ilias_list_courses` / `ilias_list_instances` |
| 2 | `not_logged_in` | the user runs `ilias login --instance <x>` in their own terminal |
| 3 | `session_expired` | same, do not retry automatically |
| 4 | `network_error` | try again later (VPN?) |
| 5 | `parse_error`, `crawl_limit` | unexpected page / request budget exhausted |

Tool definitions live in [`schemas/tools/`](schemas/tools) and are the single source for names, descriptions, annotations and schemas.

## Installation

```bash
# 1. CLI: log in once, in your own terminal (password/2FA never go through MCP)
uv tool install "git+https://github.com/smlfg/ilias-cli"
ilias login --instance hhn

# 2. MCP server
uvx --from "git+https://github.com/smlfg/ilias-mcp" ilias-mcp
```

After the PyPI release: `uvx ilias-mcp`.

## Client configuration

**Claude Desktop** (`claude_desktop_config.json`) / **Cursor** (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "ilias": { "command": "uvx", "args": ["--from", "git+https://github.com/smlfg/ilias-mcp", "ilias-mcp"] }
  }
}
```

**Claude Code:**

```bash
claude mcp add ilias -- uvx --from git+https://github.com/smlfg/ilias-mcp ilias-mcp
```

**OpenCode** (`opencode.json`):

```json
{
  "mcp": {
    "ilias": { "type": "local", "command": ["uvx", "--from", "git+https://github.com/smlfg/ilias-mcp", "ilias-mcp"] }
  }
}
```

## Login and prerequisites

- Log in with the CLI only: `ilias login --instance hhn` (HHN: Keycloak + TOTP), `--instance uni-mannheim` (Shibboleth), `--instance hs-mannheim` (Moodle).
- The session lives in the OS keyring or, as a fallback, in `~/.config/ilias-cli/session-<instance>.json` / `sessions/<instance>.json`. **The server refuses to start** (exit 78) if one of these files or `config.toml` is readable by group/others or is a symlink: `chmod 600 <file>`.
- `ILIAS_CLI_CONFIG_DIR` overrides the config directory, exactly as for the CLI.

## Security

See [SECURITY.md](SECURITY.md) and [THREAT_MODEL.md](THREAT_MODEL.md). In short: read-only, no login tool, GET-only against ILIAS, redirects to login/IdP/foreign hosts end the call with `session_expired` after one request, every returned string is sanitised (ANSI, control, zero-width/bidi, markdown images/links, credential query values) and course content is marked as data, not instructions. No telemetry.

## Development and tests

```bash
uv sync --locked
uv run pytest --cov            # localhost-only; a socket guard fails any external connect
uv run ruff check . && uv run pyright
npx @modelcontextprotocol/inspector uv run ilias-mcp   # manual check
```

Tests use a synthetic localhost ILIAS (`tests/fake_ilias.py`) and the in-memory `mcp.Client`. Changing a tool definition requires `uv run python scripts/update_snapshot.py` and a reviewed snapshot diff.

## License

MIT, see [LICENSE](LICENSE).

<!-- mcp-name: io.github.smlfg/ilias-mcp -->
