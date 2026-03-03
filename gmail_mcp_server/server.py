"""Gmail MCP Server - Main server implementation."""

import asyncio
import json
import os
import re
from collections import OrderedDict
from datetime import datetime
from typing import Any, Sequence
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource
)
from pydantic import AnyUrl
from .gmail_client import GmailClient

# Standard Gmail labels to hide from display
_HIDDEN_LABELS = {
    'INBOX', 'UNREAD', 'SPAM', 'TRASH', 'DRAFT', 'SENT', 'STARRED',
    'IMPORTANT', 'CHAT', 'CATEGORY_PERSONAL', 'CATEGORY_SOCIAL',
    'CATEGORY_PROMOTIONS', 'CATEGORY_UPDATES', 'CATEGORY_FORUMS',
}


class GmailMCPServer:
    """Gmail MCP Server implementation."""

    def __init__(self):
        self.server = Server("gmail-mcp-server")
        self.gmail_client = None
        self.email_position_map = {}  # Maps position numbers to email IDs
        self.recent_actions = []  # In-memory action log
        self._setup_handlers()

    def _record_action(self, action: str, subject: str, message_id: str):
        """Record an action to the in-memory log (capped at 100)."""
        self.recent_actions.append({
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'subject': subject,
            'message_id': message_id,
        })
        if len(self.recent_actions) > 100:
            self.recent_actions = self.recent_actions[-100:]

    def _resolve_message_ids(self, arguments: dict) -> list[str]:
        """Resolve positions[] and/or message_ids[] into a flat list of message IDs."""
        ids = list(arguments.get('message_ids', []) or [])
        for pos in (arguments.get('positions', []) or []):
            if pos not in self.email_position_map:
                raise ValueError(f"Position {pos} not found in current email list. Please run 'list_unread_emails' first.")
            ids.append(self.email_position_map[pos])
        if not ids:
            # Fallback: support single position/message_id for backwards compat
            mid = arguments.get('message_id')
            pos = arguments.get('position')
            if mid:
                ids.append(mid)
            elif pos is not None:
                if pos not in self.email_position_map:
                    raise ValueError(f"Position {pos} not found in current email list. Please run 'list_unread_emails' first.")
                ids.append(self.email_position_map[pos])
        if not ids:
            raise ValueError("No message IDs or positions provided.")
        return ids

    def _setup_handlers(self):
        """Set up MCP server handlers."""

        @self.server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            """List available tools."""
            return [
                Tool(
                    name="list_unread_emails",
                    description="List unread emails in Gmail inbox with optional subject filtering",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "subject_filter": {
                                "type": "string",
                                "description": "Optional filter to search for emails with specific subject content"
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum number of emails to return (default: 50)",
                                "default": 50
                            }
                        }
                    }
                ),
                Tool(
                    name="delete_emails",
                    description="Move emails to trash and mark as read. Accepts positions[] from email list and/or message_ids[].",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "positions": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "Position numbers from the email list"
                            },
                            "message_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Gmail message IDs"
                            }
                        }
                    }
                ),
                Tool(
                    name="archive_emails",
                    description="Archive emails (remove from inbox). Accepts positions[] from email list and/or message_ids[].",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "positions": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "Position numbers from the email list"
                            },
                            "message_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Gmail message IDs"
                            }
                        }
                    }
                ),
                Tool(
                    name="list_labels",
                    description="List all Gmail labels",
                    inputSchema={
                        "type": "object",
                        "properties": {}
                    }
                ),
                Tool(
                    name="create_label",
                    description="Create a new Gmail label",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "The label name to create"
                            },
                            "background_color": {
                                "type": "string",
                                "description": "Hex background color (e.g. '#4a86e8'). Must be used with text_color. Only predefined Gmail colors are accepted."
                            },
                            "text_color": {
                                "type": "string",
                                "description": "Hex text color (e.g. '#ffffff'). Must be used with background_color. Only predefined Gmail colors are accepted."
                            }
                        },
                        "required": ["name"]
                    }
                ),
                Tool(
                    name="modify_labels",
                    description="Batch add/remove labels on emails. Accepts positions[] and/or message_ids[], plus add_labels[] and/or remove_labels[] (label names).",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "positions": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "Position numbers from the email list"
                            },
                            "message_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Gmail message IDs"
                            },
                            "add_labels": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Label names to add"
                            },
                            "remove_labels": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Label names to remove"
                            }
                        }
                    }
                ),
                Tool(
                    name="list_recent_actions",
                    description="Show recent actions taken on emails (delete, archive, label changes, etc.)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "limit": {
                                "type": "integer",
                                "description": "Number of recent actions to show (default: 20)",
                                "default": 20
                            }
                        }
                    }
                ),
            ]

        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent | ImageContent | EmbeddedResource]:
            """Handle tool calls."""
            if not self.gmail_client:
                self.gmail_client = GmailClient()

            try:
                if name == "list_unread_emails":
                    subject_filter = arguments.get("subject_filter") if arguments else None
                    max_results = arguments.get("max_results", 50) if arguments else 50

                    try:
                        emails = self.gmail_client.list_unread_emails(
                            subject_filter=subject_filter,
                            max_results=max_results
                        )
                    except Exception as auth_error:
                        if "Authentication required but no valid token found" in str(auth_error):
                            return [TextContent(
                                type="text",
                                text=f"Gmail authentication setup required:\n\n{str(auth_error)}"
                            )]
                        else:
                            raise

                    if not emails:
                        return [TextContent(
                            type="text",
                            text="No unread emails found matching the criteria."
                        )]

                    formatted_output = self._format_email_list(emails)
                    return [TextContent(type="text", text=formatted_output)]

                elif name == "delete_emails":
                    if not arguments:
                        raise ValueError("positions or message_ids required")
                    ids = self._resolve_message_ids(arguments)
                    results = self.gmail_client.delete_emails(ids)
                    lines = []
                    for r in results:
                        if r['success']:
                            self._record_action('delete', r.get('subject', ''), r['message_id'])
                            lines.append(f"Deleted: {r.get('subject', 'Unknown Subject')}")
                        else:
                            lines.append(f"Failed to delete {r['message_id']}: {r['error']}")
                    return [TextContent(type="text", text="\n".join(lines))]

                elif name == "archive_emails":
                    if not arguments:
                        raise ValueError("positions or message_ids required")
                    ids = self._resolve_message_ids(arguments)
                    results = self.gmail_client.archive_emails(ids)
                    lines = []
                    for r in results:
                        if r['success']:
                            self._record_action('archive', r.get('subject', ''), r['message_id'])
                            lines.append(f"Archived: {r.get('subject', 'Unknown Subject')}")
                        else:
                            lines.append(f"Failed to archive {r['message_id']}: {r['error']}")
                    return [TextContent(type="text", text="\n".join(lines))]

                elif name == "list_labels":
                    labels = self.gmail_client.list_labels()
                    lines = [f"{l['name']} (id: {l['id']}, type: {l['type']})" for l in labels]
                    return [TextContent(type="text", text="\n".join(lines))]

                elif name == "create_label":
                    if not arguments or 'name' not in arguments:
                        raise ValueError("name is required")
                    result = self.gmail_client.create_label(
                        arguments['name'],
                        background_color=arguments.get('background_color'),
                        text_color=arguments.get('text_color')
                    )
                    self._record_action('create_label', arguments['name'], '')
                    return [TextContent(type="text", text=f"Created label: {result['name']} (id: {result['id']})")]

                elif name == "modify_labels":
                    if not arguments:
                        raise ValueError("positions or message_ids required, plus add_labels and/or remove_labels")
                    ids = self._resolve_message_ids(arguments)
                    add_labels = arguments.get('add_labels', [])
                    remove_labels = arguments.get('remove_labels', [])
                    results = self.gmail_client.modify_labels(ids, add_labels=add_labels, remove_labels=remove_labels)
                    lines = []
                    for r in results:
                        if r['success']:
                            self._record_action('modify_labels', f"+{add_labels} -{remove_labels}", r['message_id'])
                            lines.append(f"Labels modified: {r['message_id']}")
                        else:
                            lines.append(f"Failed to modify labels {r['message_id']}: {r['error']}")
                    return [TextContent(type="text", text="\n".join(lines))]

                elif name == "list_recent_actions":
                    limit = (arguments or {}).get('limit', 20)
                    actions = self.recent_actions[-limit:]
                    if not actions:
                        return [TextContent(type="text", text="No recent actions recorded.")]
                    lines = []
                    for a in actions:
                        subj = f" - {a['subject']}" if a['subject'] else ""
                        lines.append(f"[{a['timestamp']}] {a['action']}{subj}")
                    return [TextContent(type="text", text="\n".join(lines))]

                else:
                    raise ValueError(f"Unknown tool: {name}")

            except Exception as e:
                return [TextContent(
                    type="text",
                    text=f"Error executing {name}: {str(e)}"
                )]

    @staticmethod
    def _clean_jira_body(body: str) -> str:
        """Strip Jira email boilerplate and extract the actual comment content."""
        lines = body.split('\n')
        cleaned = []
        skip_patterns = [
            r'^\s*This message was sent by Atlassian Jira',
            r'^\s*\[https?://.*jira.*\]',
            r'^\s*-{3,}',
            r'^\s*View this issue:',
            r'^\s*You are receiving this',
            r'^\s*If you think it was sent incorrectly',
            r'^\s*Manage notifications',
            r'^\s*\[jira\]',
            r'^\s*For more information on JIRA',
            r'^\s*This email.*confidential',
        ]
        for line in lines:
            if any(re.search(p, line, re.IGNORECASE) for p in skip_patterns):
                continue
            cleaned.append(line)

        # Trim trailing blank lines
        while cleaned and not cleaned[-1].strip():
            cleaned.pop()
        return '\n'.join(cleaned)

    def _format_email_list(self, emails: list) -> str:
        """Format email list with thread grouping and full body content."""
        if not emails:
            return "No unread emails found."

        # Clear previous position mapping
        self.email_position_map = {}

        # Assign flat position numbers and build position map
        for i, email in enumerate(emails, 1):
            self.email_position_map[i] = email['id']
            email['_position'] = i

        # Resolve label IDs to names (fetch once)
        try:
            all_labels = {l['id']: l['name'] for l in self.gmail_client.list_labels()}
        except Exception:
            all_labels = {}

        # Group by threadId
        threads = OrderedDict()
        for email in emails:
            tid = email.get('threadId', email['id'])
            threads.setdefault(tid, []).append(email)

        result = f"Found {len(emails)} unread emails:\n\n"

        for tid, thread_emails in threads.items():
            # Thread header for multi-message threads
            if len(thread_emails) > 1:
                subject = thread_emails[0].get('subject', 'No Subject')
                result += f"--- Thread: {subject} ({len(thread_emails)} messages) ---\n"

            for email in thread_emails:
                pos = email['_position']
                subject = email.get('subject', 'No Subject')
                sender = email.get('sender', 'Unknown Sender')
                date = email.get('date', 'Unknown Date')
                body = email.get('body', '')

                # Resolve user labels (filter out standard ones)
                user_labels = []
                for lid in email.get('labelIds', []):
                    if lid not in _HIDDEN_LABELS:
                        label_name = all_labels.get(lid, lid)
                        user_labels.append(label_name)

                result += f"{pos}: {subject}\n"
                result += f"   From: {sender}\n"
                result += f"   Date: {date}\n"
                if user_labels:
                    result += f"   Labels: {', '.join(user_labels)}\n"

                if body and body != "No readable content":
                    # Check if this is a Jira email
                    is_jira = bool(re.search(r'\[RH Jira\]|[A-Z]+-\d+', subject))
                    if is_jira:
                        body = self._clean_jira_body(body)
                    result += f"   Body: {body}\n"
                result += "\n"

        return result.rstrip()

    async def run(self):
        """Run the MCP server."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="gmail-mcp-server",
                    server_version="0.2.0",
                    capabilities=self.server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={}
                    ),
                ),
            )


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Gmail MCP Server")
    args = parser.parse_args()

    server = GmailMCPServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
