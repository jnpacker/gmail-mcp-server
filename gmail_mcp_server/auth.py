"""Authentication helper for Gmail MCP Server."""

import os
import sys
from .gmail_client import GmailClient


def main():
    """Interactive authentication setup for Gmail MCP Server."""
    print("Gmail MCP Server - Interactive Authentication Setup")
    print("=" * 50)
    print()
    print("This will open a browser window for OAuth 2.0 authentication.")
    print("You'll be asked to authorize access to your Gmail account.")
    print()

    try:
        os.environ['GMAIL_INTERACTIVE_AUTH'] = '1'
        client = GmailClient(auto_authenticate=True)
        print()
        print("✓ Authentication successful!")
        print("✓ Token saved to token.json")
        print()
        print("You can now run the Gmail MCP Server:")
        print("  python -m gmail_mcp_server.server")
        print("or")
        print("  gmail-mcp-server")
        sys.exit(0)
    except Exception as e:
        print()
        print(f"✗ Authentication failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
