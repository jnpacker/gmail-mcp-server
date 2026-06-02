# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

Two loosely coupled systems that share the Gmail API client:

1. **MCP Server** (`gmail_mcp_server/`) — a stdio MCP server exposing Gmail tools to AI assistants (Claude, Gemini). Runs headlessly via `.mcp.json`.
2. **Web Dashboard** (`app.py`) — a Flask app that invokes triage by spawning `gemini -p /triage` as a subprocess, parses the structured output, and caches results for the browser UI.

The triage command itself lives in `commands/triage.md` (symlinked into `.claude/commands/` and converted to `.gemini/commands/` TOML). The command instructs the AI to call Gmail MCP tools directly.

## Commands

```bash
# Auth setup (requires credentials.json from Google Cloud Console)
make auth

# Run tests
python -m pytest                        # all tests
python -m pytest tests/test_server.py  # single file
python -m pytest -k "test_format"      # single test pattern

# Start web dashboard (auto-restarts on crash)
make dashboard

# Run triage manually
make triage MODEL=haiku     # default; also: sonnet, opus
make watch MODEL=haiku      # run every 5 min

# Dashboard PIN management
make set-pin

# Re-link slash commands after editing commands/*.md
make link-commands

# Container
make podman-build
make podman-push
```

## Architecture

### MCP Server (`gmail_mcp_server/`)

- `server.py` — `GmailMCPServer` wraps `mcp.server.Server`. Maintains an **in-memory position map** (`email_position_map`) that maps 1-based integer positions (shown in `list_unread_emails` output) to Gmail message IDs. This map is rebuilt on every `list_unread_emails` call, so tools like `delete_emails` and `archive_emails` accept `positions[]` referencing the last fetch. Also holds an in-memory action log (capped at 100).
- `gmail_client.py` — `GmailClient` wraps `google-api-python-client`. Auth is lazy: `_ensure_authenticated()` is called before every API call. Token is read from `token.json`; interactive OAuth only runs when `GMAIL_INTERACTIVE_AUTH=1`. `modify_labels` automatically removes all other `Triage/*` labels when a new one is added — this enforces the one-label-per-email invariant.
- `auth.py` — standalone auth helper invoked by `make auth`.

### Web Dashboard (`app.py`)

- Single-file Flask server. On startup it spawns a background thread that runs `run_triage()` immediately.
- `run_triage()` launches `gemini -p /triage --model <model>` as a subprocess with a 20-minute timeout and 30-second heartbeat logging.
- `parse_triage_output()` tries JSON-block extraction first (the ` ```json ` block emitted by Step 5 of `commands/triage.md`), falling back to regex parsing of the ASCII dashboard.
- **Thread safety**: `httplib2` (used internally by google-api-python-client) is not thread-safe. All `gmail_client` calls in `app.py` must hold `gmail_client_lock`.
- Triage results are cached in `triage_cache` (dict in module scope). Cache is never written to disk.
- PIN protection: PBKDF2-SHA256 hash stored in `.pincode`. Sessions last 4 hours. `@require_pin` decorator guards all `/api/*` routes except `/api/pin/*`.

### Slash Commands

`commands/triage.md` and `commands/emails.md` are the authoritative sources. `make link-commands` symlinks them into `.claude/commands/` and generates `.gemini/commands/*.toml` equivalents. The triage command's Step 5 outputs a structured JSON block that `app.py` parses — do not change that format without updating `parse_triage_output()`.

### Kubernetes Deployment

`k8s/` contains manifests for a single-pod deployment. `entrypoint.sh` copies secrets (`.pincode`, credentials) from the `/secrets/` volume mount into `/app/` before starting Flask. `FLASK_SECRET_KEY` is injected as an env var to keep sessions stable across pod restarts.

## Authentication

`credentials.json` — OAuth 2.0 client secret from Google Cloud Console (gitignored, required).  
`token.json` — OAuth token, auto-refreshed (gitignored, created by `make auth`).

The MCP server will surface a clear error if the token is missing or expired.

## Key Invariants

- Each email gets **exactly one** `Triage/*` label. `GmailClient.modify_labels` enforces this when the Gmail API is called; `commands/triage.md` enforces it at the AI prompt level.
- `delete_emails` moves to TRASH (reversible); it does not call `messages.delete`.
- `email_position_map` in the MCP server is per-session and per-fetch — positions are only valid after a `list_unread_emails` call.

## CLI Availability

- `gh` CLI is **not installed** — use `mcp__github-*` MCP tools for GitHub operations.
- `jira` CLI is **not installed** — use `mcp__jira-mcp-server__*` MCP tools for Jira operations.

## Fleet Engineering Skills

All skills are available as slash commands. See the [Fleet Engineering skills catalog](https://github.com/OpenShift-Fleet/agentic-sdlc/blob/main/skills/README.md) for the full list with when-to-use guidance.

## Personal configuration

Read `.claude/user.local.md` at the start of any task that needs an assignee, email, or project key.
If the file does not exist, fall back to Claude memory (`user-config`), then placeholders.
Run `make personalize` to generate it (if this repo uses Fleet Engineering tooling).
