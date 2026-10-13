# ilias-mcp

A [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server for the
**ILIAS** learning platform (Hochschule Heilbronn HHN, Uni Mannheim) and
**Moodle** (HS Mannheim). Lets LLM-based agents in Claude Desktop, Cursor, Codex
and other MCP clients log in, check the session, list courses and walk a course's
content as a tree of sections, modules and files.

This is an independent fork of [`ilias-cli`](https://github.com/smlfg/ilias-cli);
the core library (`src/ilias_mcp_core/`) is vendored rather than imported as a
dependency so the project can evolve on its own.

## Features

| MCP tool                       | What it does                                                          |
| ------------------------------ | --------------------------------------------------------------------- |
| `ilias_login`                  | Log in via credentials (env / runtime args) or browser                 |
| `ilias_status`                 | Check whether a saved session is still valid                          |
| `ilias_logout`                 | Remove the saved session / token                                      |
| `ilias_list_courses`           | List enrolled courses                                                 |
| `ilias_list_course_contents`   | Walk one course's sections / modules / files (`query` + optional depth) |

> Assignments / deadlines and assignment uploads are **not** implemented yet -
> the underlying CLI does not support them. Once that changes they will be
> added here without breaking the existing tool names.

## Transport

`stdio` (default) only. Suitable for local MCP clients like Claude Desktop,
Cursor, Codex, Hermes, Claude Code, OpenCode etc.

## Setup

### 1. Install

```bash
git clone <this-repo> ilias-mcp
cd ilias-mcp
uv sync
```

Requires Python 3.11 or 3.12 (the vendored core was written against 3.11; MCP
SDK 1.9+ still supports 3.12). [`uv`](https://docs.astral.sh/uv/) handles the
venv automatically.

Optional, for browser-based login on ILIAS HHN / Uni Mannheim:

```bash
uv sync --extra browser
uv run playwright install chromium
```

### 2. Configure credentials

**Never** put credentials in code or commit them. Set them via environment
variables (`.env`, shell, or whatever your host application supports):

```bash
cp .env.example .env
# then edit .env
```

| Variable              | Purpose                                                         |
| --------------------- | --------------------------------------------------------------- |
| `ILIAS_INSTANCE`      | `hhn`, `uni-mannheim` or `hs-mannheim` (default: `hhn`)         |
| `ILIAS_USERNAME`      | Username                                                        |
| `ILIAS_PASSWORD`      | Password (for ILIAS) or web-service token (for HS-Mannheim)     |
| `ILIAS_TOTP_SECRET`   | Optional: base32 TOTP secret for HHN Keycloak (else runtime)    |
| `ILIAS_CLI_CONFIG_DIR` | Optional: override `~/.config/ilias-cli` for the config / session storage |

The token / session is persisted in the OS keyring when available (preferred)
or in `chmod 600` JSON inside the config dir as fallback.

### 3. Run

```bash
uv run ilias-mcp              # stdio, ready for MCP clients
```

Or as a one-shot (for debugging):

```bash
uv run ilias-mcp --help
```

## MCP client configuration

### Claude Desktop

`~/.config/Claude/claude_desktop_config.json` (Linux) or
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "ilias": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/ilias-mcp", "run", "ilias-mcp"],
      "env": {
        "ILIAS_INSTANCE": "hhn",
        "ILIAS_USERNAME": "your-username",
        "ILIAS_PASSWORD": "your-password"
      }
    }
  }
}
```

### Cursor

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "ilias": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/ilias-mcp", "run", "ilias-mcp"],
      "env": {
        "ILIAS_INSTANCE": "hhn",
        "ILIAS_USERNAME": "your-username",
        "ILIAS_PASSWORD": "your-password"
      }
    }
  }
}
```

### Codex / OpenCode / Hermes

Same `command` + `args` + `env` shape as above. Codex reads
`~/.codex/config.toml`; Hermes reads `~/.hermes/profiles/<profile>/config.yaml`
or per-session settings.

## Testing

```bash
uv run pytest
```

Tests use `respx` for HTTP and `pytest-asyncio` for async MCP I/O. No real
ILIAS login is performed.

## Architecture

```
src/ilias_mcp_core/   vendored fork of ilias-cli core library
src/ilias_mcp/        MCP server (FastMCP, stdio)
tests/                pytest, all mocked
```

The vendored core is intentional: the server needs to evolve independently of
the upstream CLI. Re-vendoring from upstream is a deliberate, manual step.

## Security

- Credentials only ever come from environment variables or runtime tool
  arguments; never from disk, never from the user-facing config that lives in
  the repo.
- The session token is stored in the OS keyring when available (preferred) or
  in a `0600` JSON file under the config directory.
- No credentials, cookies, tokens or passwords appear in tool output,
  exception messages, traces or logs. The `Secret` type from the vendored core
  enforces this.

## License

MIT, see [`LICENSE`](LICENSE).