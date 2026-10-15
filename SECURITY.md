# Security policy

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** (Security → Report a vulnerability) on this repository. Do not open a public issue for security problems. Expect a first answer within 7 days.

## Scope and guarantees

- Read-only: no tool logs in, logs out, downloads, uploads or changes anything on ILIAS/Moodle.
- No credentials through MCP. Login, password and 2FA happen only in the user's own terminal via `ilias login`; the server uses the stored session (SEC-17).
- Session/config files must be `0600` and not symlinks, otherwise the server does not start (SEC-15).
- ILIAS: GET only, no proxy from the environment, redirects to login pages, Keycloak/OIDC/SAML or other hosts are never followed (`session_expired`, CT-5); request budget ≤ 60 per tool call.
- Output: every string is sanitised and known session values are redacted (SEC-13, CT-1). Error texts come from fixed templates, never from raw exceptions or response bodies (SEC-14).
- Changes to `.github/`, `schemas/tools/`, `sanitize.py`, the fetcher bridge (`core.py`), `pyproject.toml` and `uv.lock` require review (CODEOWNERS, SEC-OWN-1).
- **No Cloudflare Worker** (login CLI stays local stdio, SEC-22).
- No telemetry, no logs of content; diagnostics on stderr contain only exception type names.
- Tool definitions are snapshot-tested (SEC-21); dependency changes are guarded in CI (SEC-9).

## Supported versions

Only the latest release on `main` receives fixes.
