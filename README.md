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

Once configured, you can use the Gmail MCP server with AI assistants with these commands:

#### Basic Email Management
- **"list emails"** - Quick command to show all unread emails with smart categorization
- **"Show me my unread emails"** - Full command to display unread emails
- **"List emails with 'urgent' in the subject"** - Filter emails by subject content

#### Email Actions by Position Number
- **"delete email 1, 4, 6"** - Delete specific emails using their position numbers from the email list
- **"archive email 3"** - Archive a single email by position number
- **"delete emails 2, 5, 7"** - Delete multiple emails by their list positions

#### Bulk Operations by Category
- **"archive all meeting updates"** - Archive all emails in the "Meeting Updates" group
- **"delete all social media notifications"** - Delete all emails in the "Social Media" group
- **"apply recommendations"** - Execute all suggested actions (archive/delete) that the AI has identified

#### Advanced Filtering
- **"Delete the email with ID 123456789abcdef"** - Delete by specific Gmail message ID
- **"Archive all emails from john@example.com"** - Archive emails from specific sender

## AI Assistant Commands

This project includes pre-configured command files for different AI assistants to provide intelligent Gmail management capabilities:

### Claude CLI
The `.claude/commands/emails.md` file provides comprehensive Gmail management instructions for Claude CLI. This command enables:
- Smart email categorization and grouping
- Priority rating (Minor/Good to Know/Major)
- Automated action suggestions (archive/delete)
- Dynamic email summaries
- Intelligent filtering for different email types

### Gemini CLI
The `.gemini/commands/emails.toml` file provides the same Gmail management capabilities for Gemini CLI users. The configuration includes:
- Email listing and analysis workflows
- Dynamic grouping based on email content
- Priority-based action recommendations
- Support for bulk operations and individual email actions

### Cursor
*Coming soon* - Cursor integration is planned for future implementation.

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

On first run, the server will:
1. Check for existing authentication token (`token.json`)
2. If not found, open a browser for OAuth 2.0 authentication
3. Save the authentication token for future use

For headless operation, you can authenticate manually:
```bash
python -c "from gmail_mcp_server.gmail_client import GmailClient; import os; os.environ['GMAIL_INTERACTIVE_AUTH'] = '1'; GmailClient()"
```

Required Gmail API scopes:
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
