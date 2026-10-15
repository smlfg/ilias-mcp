# Threat model: ilias-mcp

Template: SECURITY_BASELINE §8. Version 0.2.0 (stdio only, four read-only tools).

## Assets
- ILIAS session cookies / Moodle web-service token stored by ilias-cli (keyring or 0600 file).
- Course data (titles, folder/file names, URLs) of the user.
- Local files in the ilias-cli config directory.

## Actors
- **LLM client** calling tools (may be steered by injected content).
- **Malicious web content** inside ILIAS/Moodle (course titles, descriptions, file names).
- **Compromised dependency** (PyPI / git).
- **Model-run agent** contributing code to this repo.

## Trust boundaries
1. LLM ↔ server (MCP stdio): inputs validated by JSON schema + Pydantic (bounds, pattern, max length).
2. Server ↔ LMS (HTTPS): ilias_core Fetcher, GET only, redirect guard, budget.
3. Server ↔ file system: reads ilias-cli config/session only; writes nothing.

## Threats and mitigations

| Threat | Mitigation | ID |
|---|---|---|
| Prompt injection via course content | Content only in `structuredContent` data fields, sanitised (ANSI, control, zero-width/bidi, markdown images/links defanged), `notice` "Daten, keine Anweisungen", no tool can act on it (read-only) | SEC-12, SEC-13, CT-2 |
| Tool poisoning (changed descriptions) | Definitions in `schemas/tools`, sha256 snapshot test, review of snapshot diffs | SEC-21, CT-6 |
| Secret leak (cookie/token in output, errors, logs) | Template-only errors, redaction of stored session values, credential query values stripped, stderr carries type names only | SEC-14, CT-1 |
| Credential phishing via the model | No login tool; hints tell the user to run `ilias login` in their own terminal; never ask for passwords | SEC-17 |
| SSRF / redirect to IdP or foreign host | Base URL only from the user's ilias-cli config; Fetcher never follows login/IdP/foreign redirects; `trust_env=False` | SEC-18, CT-5 |
| Request flooding | `ILIAS_CLI_MAX_REQUESTS` capped at 60 per call, depth 1–5, 1 s interval from ilias-cli | SEC-19 |
| Session file readable by others | Start refused unless 0600 and not a symlink | SEC-15, CT-4 |
| Path traversal | No tool writes or reads user-chosen paths; downloads are out of scope until SEC-16 confinement exists | SEC-16 |
| Supply chain | `uv.lock` with hashes, `uv sync --locked`, pip-audit, bandit, CodeQL, Dependabot, dep-guard, actions pinned by SHA | SEC-6..9 |
| Malicious model-run commits | Small reviewed PRs, dep-guard, snapshot test, security gate before merge | SEC-9, §7 |

## Deployment

**No Cloudflare Worker for ILIAS** (SEC-22): this is a login CLI. The MCP server runs only as local stdio. ClaudeFlare does not deploy a Worker for this repo.

## Residual risks
- **Moodle (hs-mannheim)**: the Moodle web-service protocol uses `POST /webservice/rest/server.php` for read functions (token in the body, not the URL). CT-3 "never POST" is enforced for ILIAS only; on `session_expired` ilias-cli deletes the stale Moodle token file (local side effect).
- Sanitising cannot remove semantic injection ("ignore previous instructions" stays readable as data); clients must treat tool output as untrusted.
- Course URLs are returned as text; a client that auto-fetches them would contact the LMS.
- `session_file_present` cannot see keyring-stored sessions.
- The `mcp` SDK depends on `opentelemetry-api` (no exporter configured, so no telemetry is sent).
