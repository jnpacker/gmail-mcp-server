"""Tests for gmail_mcp_server/auth.py."""

import os
from unittest.mock import MagicMock, patch

import pytest

from gmail_mcp_server.auth import main


class TestAuthMain:
    def test_successful_auth_exits_zero(self, capsys):
        mock_client = MagicMock()
        with patch("gmail_mcp_server.auth.GmailClient", return_value=mock_client):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "successful" in captured.out.lower()

    def test_failed_auth_exits_one(self, capsys):
        with patch("gmail_mcp_server.auth.GmailClient", side_effect=Exception("OAuth failed")):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "failed" in captured.out.lower()

    def test_sets_interactive_auth_env(self):
        """Verify GMAIL_INTERACTIVE_AUTH=1 is set before constructing the client."""
        env_val_during_call = {}

        def capture_env(**kwargs):
            env_val_during_call["value"] = os.environ.get("GMAIL_INTERACTIVE_AUTH")
            return MagicMock()

        with patch("gmail_mcp_server.auth.GmailClient", side_effect=capture_env):
            with pytest.raises(SystemExit):
                main()
        assert env_val_during_call["value"] == "1"
