# Changelog

## 0.2.0 (unreleased)

Rebuild on top of ilias-cli (breaking).

- Removed the vendored copy `ilias_mcp_core`; depends on `ilias-cli` (git, pinned) and imports `ilias_core`.
- Removed `ilias_login`, `ilias_logout` and all password/TOTP environment handling (`.env.example`). Login only via `ilias login` in the terminal.
- Migrated to the `mcp` SDK 2.x (`MCPServer`, snake_case annotations).
- Tools: `ilias_list_instances`, `ilias_get_status`, `ilias_list_courses`, `ilias_list_contents` (renamed from `ilias_list_course_contents`; `ilias_status` → `ilias_get_status`).
- Tool definitions in `schemas/tools/*.json` with snapshot test.
- Stable error codes from ilias-cli exit codes 1–5; output sanitising and secret redaction; refuse start on non-0600 session files.
- SECURITY.md, THREAT_MODEL.md, server.json, glama.json, Dockerfile, CI (tests, pip-audit, bandit, CodeQL, gitleaks, dep-guard).

## 0.1.0

Initial FastMCP server with a copy of the ilias-cli core (superseded).
