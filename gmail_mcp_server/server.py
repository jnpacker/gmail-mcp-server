"""Gmail MCP Server - Main server implementation."""

import asyncio
import json
import os
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


class GmailMCPServer:
    """Gmail MCP Server implementation."""

    def __init__(self):
        self.server = Server("gmail-mcp-server")
        self.gmail_client = None
        self.email_position_map = {}  # Maps position numbers to email IDs
        self._setup_handlers()
    
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
                    name="delete_email",
                    description="Move an email to trash and mark it as read by ID or position number",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message_id": {
                                "type": "string",
                                "description": "The Gmail message ID to move to trash (alternative to position)"
                            },
                            "position": {
                                "type": "integer",
                                "description": "The numbered position from the email list (alternative to message_id)"
                            },
                            "subject": {
                                "type": "string",
                                "description": "The email subject (for display purposes during approval)"
                            }
                        }
                    }
                ),
                Tool(
                    name="archive_email",
                    description="Archive an email (remove from inbox) by ID or position number",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message_id": {
                                "type": "string",
                                "description": "The Gmail message ID to archive (alternative to position)"
                            },
                            "position": {
                                "type": "integer",
                                "description": "The numbered position from the email list (alternative to message_id)"
                            },
                            "subject": {
                                "type": "string",
                                "description": "The email subject (for display purposes during approval)"
                            }
                        }
                    }
                )
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
                    
                    # Format emails as simple numbered list - let AI handle categorization and analysis
                    formatted_output = self._format_email_list(emails)
                    result = formatted_output

                    return [TextContent(type="text", text=result)]
                
                elif name == "delete_email":
                    if not arguments:
                        raise ValueError("Either message_id or position is required")

                    # Get message_id either directly or from position mapping
                    message_id = arguments.get("message_id")
                    position = arguments.get("position")

                    if not message_id and not position:
                        raise ValueError("Either message_id or position is required")

                    if position and not message_id:
                        if position not in self.email_position_map:
                            raise ValueError(f"Position {position} not found in current email list. Please run 'list emails' first.")
                        message_id = self.email_position_map[position]

                    result = self.gmail_client.delete_email(message_id)

                    if result["success"]:
                        subject_text = f" - {result['subject']}" if result.get('subject') else ""
                        return [TextContent(
                            type="text",
                            text=f"🗑️ Deleted: {result.get('subject', 'Unknown Subject')}"
                        )]
                    else:
                        return [TextContent(
                            type="text",
                            text=f"Failed to move email with ID {message_id} to trash. Error: {result['error']}"
                        )]
                
                elif name == "archive_email":
                    if not arguments:
                        raise ValueError("Either message_id or position is required")

                    # Get message_id either directly or from position mapping
                    message_id = arguments.get("message_id")
                    position = arguments.get("position")

                    if not message_id and not position:
                        raise ValueError("Either message_id or position is required")

                    if position and not message_id:
                        if position not in self.email_position_map:
                            raise ValueError(f"Position {position} not found in current email list. Please run 'list emails' first.")
                        message_id = self.email_position_map[position]

                    result = self.gmail_client.archive_email(message_id)

                    if result["success"]:
                        return [TextContent(
                            type="text",
                            text=f"📁 Archived: {result.get('subject', 'Unknown Subject')}"
                        )]
                    else:
                        return [TextContent(
                            type="text",
                            text=f"Failed to archive email with ID {message_id}. Error: {result['error']}"
                        )]
                
                else:
                    raise ValueError(f"Unknown tool: {name}")
            
            except Exception as e:
                return [TextContent(
                    type="text",
                    text=f"Error executing {name}: {str(e)}"
                )]

    def _format_email_list(self, emails: list) -> str:
        """Format email list as simple numbered list with raw email data.

        All categorization, summarization, and priority analysis is handled by the AI model.
        """
        if not emails:
            return "No unread emails found."

        # Clear previous position mapping
        self.email_position_map = {}

        result = f"Found {len(emails)} unread emails:\n\n"

        for i, email in enumerate(emails, 1):
            # Store position to email ID mapping
            self.email_position_map[i] = email['id']

            subject = email.get('subject', 'No Subject')
            sender = email.get('sender', 'Unknown Sender')
            date = email.get('date', 'Unknown Date')
            body = email.get('body', '')
            snippet = email.get('snippet', '')

            result += f"{i}: {subject}\n"
            result += f"   From: {sender}\n"
            result += f"   Date: {date}\n"
            if snippet:
                result += f"   Snippet: {snippet}\n"
            if body and body != "No readable content":
                # Limit body preview to first 200 characters
                body_preview = body[:200] + "..." if len(body) > 200 else body
                result += f"   Body: {body_preview}\n"
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
                    server_version="0.1.0",
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