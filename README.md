# Gmail MCP Server

A purpose-built Model Context Protocol (MCP) server for Gmail integration, allowing AI assistants to review unread emails and perform email management operations.

## Features

- **List Unread Emails**: Retrieve unread emails from Gmail inbox with optional subject filtering
- **Email Content**: Access complete email content including headers, body, and metadata
- **Delete Emails**: Permanently delete emails by ID
- **Archive Emails**: Archive emails (remove from inbox) by ID

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
gmail-mcp-auth
```

This will:
1. Open a browser window for OAuth 2.0 authentication
2. Request permission to access your Gmail account
3. Save the authentication token to `token.json` for future use

If you installed in development mode, you can also run:
```bash
python -m gmail_mcp_server.auth
```

### How It Works

- The server checks for an existing authentication token (`token.json`) on startup
- If the token exists and is valid, the server uses it automatically
- If the token is expired but has a refresh token, it refreshes automatically
- If no token exists, the server will request authentication using the `gmail-mcp-auth` command

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
