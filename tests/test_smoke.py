"""Smoke test: start the MCP server via stdio and verify tool listing."""

import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_mcp_server_lists_nine_tools():
    """Start the MCP server as a subprocess and verify all 9 tools are registered."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gmail_mcp_server.server"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = {t.name for t in tools_result.tools}

    expected = {
        "list_unread_emails",
        "list_all_emails",
        "search_emails",
        "delete_emails",
        "archive_emails",
        "list_labels",
        "create_label",
        "modify_labels",
        "list_recent_actions",
    }
    assert tool_names == expected
    assert len(tool_names) == 9
