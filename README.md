# Gmail MCP Server

A purpose-built Model Context Protocol (MCP) server for Gmail integration, allowing AI assistants to review unread emails and perform email management operations.

## Features

- **List Unread Emails**: Retrieve unread emails from Gmail inbox with optional subject filtering
- **List All Emails**: Retrieve all emails from Gmail (defaults to inbox, option for all mail)
- **Search Emails**: Search emails using full Gmail query syntax (`from:`, `to:`, `subject:`, `has:attachment`, `after:`, `label:`, `is:starred`)
- **Email Content**: Access complete email content including headers, body, and metadata
- **Delete Emails**: Permanently delete emails by ID
- **Archive Emails**: Archive emails (remove from inbox) by ID
- **Web Dashboard**: Beautiful, responsive dashboard for intelligent inbox management
- **Auto-Triage**: Automatic email classification and organization every 15 minutes
- **Auto-Cleanup**: Intelligent deletion of trivial emails and archiving of calendar invites

## Installation

1. Clone this repository:
```bash
git clone <repository-url>
cd gmail-mcp-server
```

2. Set up Google OAuth 2.0 credentials:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select an existing one
   - Enable the Gmail API
   - Create OAuth 2.0 credentials (Desktop application)
   - Download the credentials JSON file and save as `credentials.json` in the project root

3. Authenticate (see [Authentication](#authentication) below):
```bash
make auth
```
No separate install step is needed — `make auth` (and every other `make` target that needs Python
deps, e.g. `test`, `lint`, `dashboard`) automatically creates a local `.venv/` and installs the
project into it on first run. You never need to `pip install` anything system-wide (many distros
ship an "externally managed" system Python that refuses direct `pip install` anyway).

Start the server:
```bash
.venv/bin/python -m gmail_mcp_server.server
```

## Web Dashboard & Inbox Management

The Gmail MCP Server includes a powerful web-based dashboard for intelligent inbox management with automatic triaging and organization.

### Quick Start

Start the dashboard with:
```bash
make dashboard
```

Or manually:
```bash
python3 app.py
```

The dashboard will be available at `http://localhost:5000`

### Dashboard Features

- **Auto-Triage Every 15 Minutes**: Automatically classifies and organizes emails
- **Intelligent Organization**: Groups emails by priority (Critical → Important → Info)
- **Auto-Cleanup**: Automatically deletes trivial field changes and archives calendar invites
- **Real-time Stats**: View total emails, last sync time, and next sync countdown
- **Quick Navigation**: Click email groups to preview Gmail search results
- **Responsive Design**: Works on desktop, tablet, and mobile devices
- **Manual Refresh**: Trigger triage immediately with the refresh button

### Using with Claude Code

When using Claude Code, you can leverage this Gmail MCP server to manage your email directly from your development environment:

1. **Inbox Triaging**: Use the `/triage` command to automatically organize and clean your inbox
2. **Integration in Workflows**: Claude Code can help analyze email content and suggest actions
3. **Automated Management**: Set up the dashboard to run in the background and manage emails while you code
4. **Easy Access**: Check your organized inbox without leaving your IDE

To use with Claude Code:
1. Ensure the MCP server is configured in your `.mcp.json`
2. Claude Code will have access to the Gmail tools for email management
3. Use natural language commands to manage emails (e.g., "delete these spam emails", "archive calendar invites")

See [DASHBOARD.md](DASHBOARD.md) for comprehensive dashboard documentation.

## MCP Configuration

To use this Gmail MCP server with Claude or gemini-cli, you need to configure a `.mcp.json` file. This file tells the AI assistant how to connect to your MCP server.

### .mcp.json Configuration

Create a `.mcp.json` file in your home directory or project directory with the following configuration:

```json
{
  "mcpServers": {
    "gmail": {
      "command": "/path/to/gmail-mcp-server/.venv/bin/python3",
      "args": ["-m", "gmail_mcp_server.server"],
      "cwd": "/path/to/gmail-mcp-server"
    }
  }
}
```

**Configuration Details:**
- `command`: The Python interpreter to use. Point this at `.venv/bin/python3` (created automatically
  by `make auth`) so the server has access to its installed dependencies — a bare `python`/`python3`
  will fail with `ModuleNotFoundError` unless those packages happen to be installed system-wide.
- `args`: Arguments to pass to the Gmail MCP server module
- `cwd`: The working directory where the Gmail MCP server is installed

**For Claude Desktop:**
Place the `.mcp.json` file in your Claude Desktop configuration directory:
- **macOS**: `~/Library/Application Support/Claude/`
- **Windows**: `%APPDATA%\Claude\`
- **Linux**: `~/.config/claude/`

**For gemini-cli:**
Place the `.mcp.json` file in your home directory or specify the path when running gemini-cli.

### Example Usage

Once configured, you can use the Gmail MCP server with AI assistants by passing it in your client configuration.

## Dashboard PIN Security

The dashboard can be protected with a 4-digit PIN. When configured, the dashboard shows a PIN entry screen on every new session (sessions last 4 hours).

### Setting a PIN

```bash
make set-pin
# Enter new PIN: ****
# Confirm PIN: ****
# PIN saved.
```

Or use the Python CLI directly:
```bash
python3 app.py --set-pin
```

This writes a PBKDF2-SHA256 hashed PIN to `.pincode` in the project root. The raw PIN is never stored. Both `.pincode` and `.flask_secret` are gitignored.

To remove PIN protection, delete `.pincode`:
```bash
rm .pincode
```

### Running in Kubernetes

All secrets are consolidated in a single `gmail-mcp-secrets` Kubernetes Secret (see `k8s/secret.yaml_example`). When using PIN protection, include the pre-hashed `.pincode` value there rather than generating it on-disk.

**1. Generate the PIN hash locally:**
```bash
make set-pin        # writes .pincode to repo root
cat .pincode        # copy the "salt:hash" string
```

Or generate it directly:
```bash
python3 -c "
import secrets, hashlib
pin = '1234'  # replace with your PIN
salt = secrets.token_hex(16)
h = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt.encode(), 260000).hex()
print(f'{salt}:{h}')
"
```

**2. Add it to your `k8s/secret.yaml` (alongside the other secrets):**
```yaml
stringData:
  .pincode: "salt:hash-from-above"
  FLASK_SECRET_KEY: "$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  # ... other fields from k8s/secret.yaml_example
```

**3. Apply and deploy:**
```bash
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/deployment.yaml
```

The entrypoint copies `.pincode` from the read-only `/secrets/` mount to `/app/` on startup. `FLASK_SECRET_KEY` is injected as an environment variable to keep sessions stable across pod restarts.

## Make Commands

Use the included Makefile for quick access to common tasks:

```bash
# Display available commands
make help

# Initialize Gmail OAuth authentication (requires credentials.json)
make auth

# Set or change the dashboard PIN
make set-pin

# Start the web dashboard
make dashboard

# Stop the running dashboard
make kill-dashboard

# Run inbox triage once (email classification and organization)
make triage

# Watch inbox every 10 minutes (runs triage repeatedly)
make watch
```

You can specify which Claude model to use with the `MODEL` variable:
```bash
make triage MODEL=haiku        # Fast triage with Haiku (default)
make triage MODEL=sonnet       # Balanced triage with Sonnet
make triage MODEL=opus         # Most capable triage with Opus
make watch MODEL=opus
```

## Available Tools

#### 1. list_unread_emails
Lists unread emails in Gmail inbox with optional filtering. Rebuilds the in-memory position map used by delete/archive/modify tools.

**Parameters:**
- `subject_filter` (optional): Filter emails by subject text
- `max_results` (optional): Maximum number of emails to return (default: 50)

#### 2. list_all_emails
Lists emails in Gmail (defaults to inbox, including both read and unread messages). Rebuilds the in-memory position map.

**Parameters:**
- `inbox_only` (optional): Whether to list only emails currently in inbox (default: `true`). Set to `false` to list all emails across all folders.
- `max_results` (optional): Maximum number of emails to return (default: 50)

#### 3. search_emails
Searches emails using standard Gmail search query syntax. Rebuilds the in-memory position map.

**Parameters:**
- `query` (required): Gmail search query string (e.g. `from:user@example.com`, `has:attachment`, `subject:report`, `after:2024/01/01`, `is:starred`, `label:work`)
- `max_results` (optional): Maximum number of emails to return (default: 50)

#### 4. delete_emails
Moves emails to trash and marks them as read. Accepts position numbers from the last email list/search call and/or explicit Gmail message IDs.

**Parameters:**
- `positions` (optional): Array of 1-based position numbers from the email list
- `message_ids` (optional): Array of Gmail message IDs

#### 5. archive_emails
Archives emails (removes from inbox) and marks them as read.

**Parameters:**
- `positions` (optional): Array of 1-based position numbers
- `message_ids` (optional): Array of Gmail message IDs

#### 6. list_labels
Returns all Gmail labels (system + user-defined).

**Parameters:** None

#### 7. create_label
Creates a new Gmail label with optional color.

**Parameters:**
- `name` (required): Label name (e.g., `Triage/Security`)
- `background_color` (optional): Hex color (e.g., `#4a86e8`) — must be a predefined Gmail color
- `text_color` (optional): Hex text color — must be paired with `background_color`

#### 8. modify_labels
Adds and/or removes labels on emails. When adding a `Triage/*` label, all other `Triage/*` labels on the email are automatically removed (one-label-per-email invariant).

**Parameters:**
- `positions` (optional): Array of 1-based position numbers
- `message_ids` (optional): Array of Gmail message IDs
- `add_labels` (optional): Array of label names to add
- `remove_labels` (optional): Array of label names to remove

#### 9. list_recent_actions
Returns the in-memory log of recent email operations (capped at 100).

**Parameters:**
- `limit` (optional): Maximum number of actions to return (default: 10)

## Authentication

### Initial Setup

On first run, the server requires authentication. Use the provided authentication helper:

```bash
make auth
```

This automatically creates `.venv` (if it doesn't exist yet) and installs dependencies into it
before running the auth flow, so no manual `pip install` step is required.

Or manually, using the project's virtualenv:
```bash
.venv/bin/python -m gmail_mcp_server.auth
```

This will:
1. Check that `credentials.json` exists in the project root
2. Open a browser window for OAuth 2.0 authentication
3. Request permission to access your Gmail account
4. Save the authentication token to `token.json` for future use

### Getting Credentials

Before running `make auth`, you need to set up Google OAuth 2.0 credentials:
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the Gmail API
4. Create OAuth 2.0 credentials (Desktop application)
5. Download the credentials JSON file and save as `credentials.json` in the project root

### How It Works

- The server checks for an existing authentication token (`token.json`) on startup
- If the token exists and is valid, the server uses it automatically
- If the token is expired but has a refresh token, it refreshes automatically
- If no token exists, the server will request authentication using the `make auth` command

### Required Gmail API Scopes

- `https://www.googleapis.com/auth/gmail.readonly` - Read emails
- `https://www.googleapis.com/auth/gmail.modify` - Delete and archive emails

## Security Notes

- Keep your `credentials.json` and `token.json` files secure
- These files are automatically ignored by git
- The server only requests minimal required permissions
- All operations are performed through official Gmail API

## Development

`make test`, `make lint`, `make format`, and `make auth` all automatically create `.venv/` (with dev
dependencies) on first run, so there's no separate setup step.

Run tests:
```bash
make test          # run all tests
make test-cov      # run with coverage report
```

Lint and format:
```bash
make lint          # check with ruff
make format        # auto-format and fix imports with ruff
```

Run the MCP server directly:
```bash
.venv/bin/python -m gmail_mcp_server        # short form (via __main__.py)
.venv/bin/python -m gmail_mcp_server.server # explicit
.venv/bin/gmail-mcp-server                  # installed entry point
```

Test the server interactively with the MCP Inspector:
```bash
npx @modelcontextprotocol/inspector .venv/bin/python3 -m gmail_mcp_server.server
```
