# Gmail MCP Server

A purpose-built Model Context Protocol (MCP) server for Gmail integration, allowing AI assistants to review unread emails and perform email management operations.

## Features

- **List Unread Emails**: Retrieve unread emails from Gmail inbox with optional subject filtering
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

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up Google OAuth 2.0 credentials:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select an existing one
   - Enable the Gmail API
   - Create OAuth 2.0 credentials (Desktop application)
   - Download the credentials JSON file and save as `credentials.json` in the project root

Start the server:
```bash
python -m gmail_mcp_server.server
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
      "command": "python",
      "args": ["-m", "gmail_mcp_server.server"],
      "cwd": "/path/to/gmail-mcp-server"
    }
  }
}
```

**Configuration Details:**
- `command`: The Python interpreter to use
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

## Make Commands

Use the included Makefile for quick access to common tasks:

```bash
# Display available commands
make help

# Initialize Gmail OAuth authentication (requires credentials.json)
make auth

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
Lists unread emails in Gmail inbox with optional filtering.

**Parameters:**
- `subject_filter` (optional): Filter emails containing specific text in subject
- `max_results` (optional): Maximum number of emails to return (default: 50)

**Example:**
```json
{
  "subject_filter": "important",
  "max_results": 10
}
```

#### 2. delete_email
Permanently deletes an email by ID.

**Parameters:**
- `message_id` (required): Gmail message ID to delete

**Example:**
```json
{
  "message_id": "123456789abcdef"
}
```

#### 3. archive_email
Archives an email (removes from inbox) by ID.

**Parameters:**
- `message_id` (required): Gmail message ID to archive

**Example:**
```json
{
  "message_id": "123456789abcdef"
}
```

## Authentication

### Initial Setup

On first run, the server requires authentication. Use the provided authentication helper:

```bash
make auth
```

Or manually:
```bash
python -m gmail_mcp_server.auth
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

Install in development mode:
```bash
pip install -e .
```

Run the server:
```bash
gmail-mcp-server
```
