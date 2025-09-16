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
                    description="Move an email to trash by ID",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message_id": {
                                "type": "string",
                                "description": "The Gmail message ID to move to trash"
                            }
                        },
                        "required": ["message_id"]
                    }
                ),
                Tool(
                    name="archive_email", 
                    description="Archive an email (remove from inbox) by ID",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message_id": {
                                "type": "string",
                                "description": "The Gmail message ID to archive"
                            }
                        },
                        "required": ["message_id"]
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
                    
                    result = f"Found {len(emails)} unread email(s):\n\n"
                    for i, email in enumerate(emails, 1):
                        result += f"Email {i}:\n"
                        result += f"  ID: {email['id']}\n"
                        result += f"  Subject: {email['subject']}\n"
                        result += f"  From: {email['sender']}\n"
                        result += f"  Date: {email['date']}\n"
                        result += f"  Snippet: {email['snippet']}\n"
                        result += f"  Body:\n{email['body']}\n"
                        result += "-" * 80 + "\n\n"
                    
                    return [TextContent(type="text", text=result)]
                
                elif name == "delete_email":
                    if not arguments or "message_id" not in arguments:
                        raise ValueError("message_id is required")

                    message_id = arguments["message_id"]
                    result = self.gmail_client.delete_email(message_id)

                    if result["success"]:
                        return [TextContent(
                            type="text",
                            text=f"Email with ID {message_id} has been moved to trash."
                        )]
                    else:
                        return [TextContent(
                            type="text",
                            text=f"Failed to move email with ID {message_id} to trash. Error: {result['error']}"
                        )]
                
                elif name == "archive_email":
                    if not arguments or "message_id" not in arguments:
                        raise ValueError("message_id is required")
                    
                    message_id = arguments["message_id"]
                    success = self.gmail_client.archive_email(message_id)
                    
                    if success:
                        return [TextContent(
                            type="text",
                            text=f"Email with ID {message_id} has been archived (removed from inbox)."
                        )]
                    else:
                        return [TextContent(
                            type="text",
                            text=f"Failed to archive email with ID {message_id}."
                        )]
                
                else:
                    raise ValueError(f"Unknown tool: {name}")
            
            except Exception as e:
                return [TextContent(
                    type="text",
                    text=f"Error executing {name}: {str(e)}"
                )]
    
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