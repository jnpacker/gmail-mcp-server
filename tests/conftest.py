"""Shared test fixtures and helpers for gmail-mcp-server tests."""

import asyncio
import base64
from unittest.mock import MagicMock, patch

import mcp.types as mcp_types
import pytest

from gmail_mcp_server.gmail_client import GmailClient
from gmail_mcp_server.server import GmailMCPServer

# ---------------------------------------------------------------------------
# Data builders (called with different args per test — not fixtures)
# ---------------------------------------------------------------------------


def make_email(
    id,
    subject,
    sender="alice@example.com",
    thread_id=None,
    label_ids=None,
    body="Hello world",
    date="Mon, 1 Jan 2024",
):
    """Build an email dict matching what GmailClient returns."""
    return {
        "id": id,
        "threadId": thread_id or id,
        "labelIds": label_ids or ["INBOX", "UNREAD"],
        "subject": subject,
        "sender": sender,
        "date": date,
        "body": body,
        "snippet": body[:40],
    }


def b64(text):
    """Base64url-encode a string the way the Gmail API does."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8")


def make_gmail_message(
    message_id,
    subject="Test",
    sender="a@b.com",
    body_text="Hello",
    thread_id=None,
    label_ids=None,
):
    """Build a fake Gmail API message response."""
    return {
        "id": message_id,
        "threadId": thread_id or message_id,
        "labelIds": label_ids or ["INBOX", "UNREAD"],
        "snippet": body_text[:40],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "Date", "value": "Mon, 1 Jan 2024 00:00:00 +0000"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": b64(body_text)},
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gmail_server():
    """Create a GmailMCPServer with a mocked gmail_client."""
    srv = GmailMCPServer()
    srv.gmail_client = MagicMock()
    srv.gmail_client.list_labels.return_value = [
        {"id": "Label_1", "name": "Triage/Jira", "type": "user"},
        {"id": "Label_2", "name": "Triage/Security", "type": "user"},
    ]
    return srv


@pytest.fixture
def gmail_client():
    """Create a GmailClient with mocked auth and service."""
    with patch.object(GmailClient, "_authenticate"):
        client = GmailClient()
    client._authenticated = True
    client.service = MagicMock()
    return client


# ---------------------------------------------------------------------------
# MCP call helpers
# ---------------------------------------------------------------------------


def call_tool_sync(srv, name, arguments=None):
    """Call a tool handler on the MCP server synchronously."""
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments or {}),
    )
    handler = srv.server.request_handlers[mcp_types.CallToolRequest]
    resp = asyncio.run(handler(req))
    return resp.root if hasattr(resp, "root") else resp


def list_tools_sync(srv):
    """List tools from the MCP server synchronously."""
    req = mcp_types.ListToolsRequest(method="tools/list")
    handler = srv.server.request_handlers[mcp_types.ListToolsRequest]
    resp = asyncio.run(handler(req))
    return resp.root if hasattr(resp, "root") else resp


def text(result):
    """Extract the text string from a tool-call result list."""
    return result[0].text
