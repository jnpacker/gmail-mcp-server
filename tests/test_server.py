"""Tests for GmailMCPServer - server.py logic."""

import asyncio
import base64
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

import mcp.types as mcp_types
from gmail_mcp_server.server import GmailMCPServer, _HIDDEN_LABELS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_email(id, subject, sender="alice@example.com", thread_id=None,
                label_ids=None, body="Hello world", date="Mon, 1 Jan 2024"):
    """Build an email dict matching what gmail_client returns."""
    return {
        'id': id,
        'threadId': thread_id or id,
        'labelIds': label_ids or ['INBOX', 'UNREAD'],
        'subject': subject,
        'sender': sender,
        'date': date,
        'body': body,
        'snippet': body[:40],
    }


def _text(result):
    """Extract the text string from a tool-call result list."""
    return result[0].text


def _call_tool_sync(srv, name, arguments=None):
    """Call a tool handler on the MCP server synchronously."""
    req = mcp_types.CallToolRequest(
        method='tools/call',
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments or {}),
    )
    handler = srv.server.request_handlers[mcp_types.CallToolRequest]
    resp = asyncio.get_event_loop().run_until_complete(handler(req))
    # ServerResult wraps the actual result in .root
    return resp.root if hasattr(resp, 'root') else resp


def _list_tools_sync(srv):
    """List tools from the MCP server synchronously."""
    req = mcp_types.ListToolsRequest(method='tools/list')
    handler = srv.server.request_handlers[mcp_types.ListToolsRequest]
    resp = asyncio.get_event_loop().run_until_complete(handler(req))
    return resp.root if hasattr(resp, 'root') else resp


# ---------------------------------------------------------------------------
# _record_action
# ---------------------------------------------------------------------------

class TestRecordAction:
    def test_appends_action(self):
        srv = GmailMCPServer()
        srv._record_action('delete', 'Test Subject', 'msg-1')
        assert len(srv.recent_actions) == 1
        a = srv.recent_actions[0]
        assert a['action'] == 'delete'
        assert a['subject'] == 'Test Subject'
        assert a['message_id'] == 'msg-1'
        assert 'timestamp' in a

    def test_caps_at_100(self):
        srv = GmailMCPServer()
        for i in range(110):
            srv._record_action('archive', f'Subj {i}', f'msg-{i}')
        assert len(srv.recent_actions) == 100
        assert srv.recent_actions[0]['subject'] == 'Subj 10'
        assert srv.recent_actions[-1]['subject'] == 'Subj 109'


# ---------------------------------------------------------------------------
# _resolve_message_ids
# ---------------------------------------------------------------------------

class TestResolveMessageIds:
    def setup_method(self):
        self.srv = GmailMCPServer()
        self.srv.email_position_map = {1: 'id-a', 2: 'id-b', 3: 'id-c'}

    def test_resolves_positions(self):
        ids = self.srv._resolve_message_ids({'positions': [1, 3]})
        assert ids == ['id-a', 'id-c']

    def test_resolves_message_ids_directly(self):
        ids = self.srv._resolve_message_ids({'message_ids': ['id-x', 'id-y']})
        assert ids == ['id-x', 'id-y']

    def test_combines_both(self):
        ids = self.srv._resolve_message_ids({
            'positions': [2],
            'message_ids': ['id-x']
        })
        assert set(ids) == {'id-x', 'id-b'}

    def test_fallback_single_position(self):
        ids = self.srv._resolve_message_ids({'position': 1})
        assert ids == ['id-a']

    def test_fallback_single_message_id(self):
        ids = self.srv._resolve_message_ids({'message_id': 'id-z'})
        assert ids == ['id-z']

    def test_invalid_position_raises(self):
        with pytest.raises(ValueError, match="Position 99"):
            self.srv._resolve_message_ids({'positions': [99]})

    def test_invalid_fallback_position_raises(self):
        with pytest.raises(ValueError, match="Position 99"):
            self.srv._resolve_message_ids({'position': 99})

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="No message IDs"):
            self.srv._resolve_message_ids({})

    def test_none_lists_raises(self):
        with pytest.raises(ValueError, match="No message IDs"):
            self.srv._resolve_message_ids({'positions': None, 'message_ids': None})


# ---------------------------------------------------------------------------
# _clean_jira_body
# ---------------------------------------------------------------------------

class TestCleanJiraBody:
    def test_strips_boilerplate(self):
        body = (
            "John Smith commented:\n"
            "This is the actual comment.\n"
            "\n"
            "---\n"
            "This message was sent by Atlassian Jira\n"
            "View this issue: https://issues.redhat.com/browse/ACM-123\n"
            "You are receiving this because you are subscribed.\n"
            "Manage notifications: https://issues.redhat.com/preferences\n"
        )
        cleaned = GmailMCPServer._clean_jira_body(body)
        assert "John Smith commented:" in cleaned
        assert "This is the actual comment." in cleaned
        assert "Atlassian Jira" not in cleaned
        assert "View this issue" not in cleaned
        assert "You are receiving this" not in cleaned
        assert "Manage notifications" not in cleaned
        assert "---" not in cleaned

    def test_preserves_normal_content(self):
        body = "Just a normal comment\nwith multiple lines."
        cleaned = GmailMCPServer._clean_jira_body(body)
        assert cleaned == body

    def test_strips_trailing_blanks(self):
        body = "Content here\n\n\n\n"
        cleaned = GmailMCPServer._clean_jira_body(body)
        assert cleaned == "Content here"

    def test_handles_empty_body(self):
        assert GmailMCPServer._clean_jira_body("") == ""

    def test_strips_jira_link_lines(self):
        body = (
            "Comment text\n"
            "[https://issues.redhat.com/jira/browse/ACM-100]\n"
            "[jira] notification footer\n"
            "For more information on JIRA, see docs.\n"
        )
        cleaned = GmailMCPServer._clean_jira_body(body)
        assert cleaned == "Comment text"

    def test_case_insensitive_matching(self):
        body = "content\nTHIS MESSAGE WAS SENT BY ATLASSIAN JIRA\nmore content"
        cleaned = GmailMCPServer._clean_jira_body(body)
        assert "ATLASSIAN JIRA" not in cleaned
        assert "content" in cleaned
        assert "more content" in cleaned


# ---------------------------------------------------------------------------
# _format_email_list
# ---------------------------------------------------------------------------

class TestFormatEmailList:
    def setup_method(self):
        self.srv = GmailMCPServer()
        self.srv.gmail_client = MagicMock()
        self.srv.gmail_client.list_labels.return_value = [
            {'id': 'Label_1', 'name': 'Triage/Jira', 'type': 'user'},
            {'id': 'Label_2', 'name': 'Triage/Security', 'type': 'user'},
        ]

    def test_empty_list(self):
        assert self.srv._format_email_list([]) == "No unread emails found."

    def test_single_email_basic(self):
        emails = [_make_email('m1', 'Test Subject')]
        output = self.srv._format_email_list(emails)
        assert "Found 1 unread emails" in output
        assert "1: Test Subject" in output
        assert "From: alice@example.com" in output
        assert "Body: Hello world" in output

    def test_position_map_built(self):
        emails = [_make_email('m1', 'A'), _make_email('m2', 'B')]
        self.srv._format_email_list(emails)
        assert self.srv.email_position_map == {1: 'm1', 2: 'm2'}

    def test_thread_grouping_header(self):
        emails = [
            _make_email('m1', 'Thread Subject', thread_id='t1'),
            _make_email('m2', 'Re: Thread Subject', thread_id='t1'),
        ]
        output = self.srv._format_email_list(emails)
        assert "--- Thread: Thread Subject (2 messages) ---" in output
        assert "1: Thread Subject" in output
        assert "2: Re: Thread Subject" in output

    def test_no_thread_header_for_single(self):
        emails = [_make_email('m1', 'Solo Email', thread_id='t1')]
        output = self.srv._format_email_list(emails)
        assert "--- Thread:" not in output

    def test_user_labels_shown(self):
        emails = [_make_email('m1', 'Labeled', label_ids=['INBOX', 'UNREAD', 'Label_1'])]
        output = self.srv._format_email_list(emails)
        assert "Labels: Triage/Jira" in output

    def test_hidden_labels_filtered(self):
        emails = [_make_email('m1', 'X', label_ids=['INBOX', 'UNREAD', 'SPAM', 'IMPORTANT'])]
        output = self.srv._format_email_list(emails)
        assert "Labels:" not in output

    def test_full_body_not_truncated(self):
        long_body = "A" * 500
        emails = [_make_email('m1', 'Long', body=long_body)]
        output = self.srv._format_email_list(emails)
        assert long_body in output
        assert "..." not in output

    def test_jira_body_cleaned(self):
        jira_body = (
            "Real comment\n"
            "---\n"
            "This message was sent by Atlassian Jira\n"
        )
        emails = [_make_email('m1', '[RH Jira] ACM-123 update', body=jira_body)]
        output = self.srv._format_email_list(emails)
        assert "Real comment" in output
        assert "Atlassian Jira" not in output

    def test_jira_detected_by_ticket_pattern(self):
        jira_body = "Comment\n---\nThis message was sent by Atlassian Jira\n"
        emails = [_make_email('m1', 'ACM-456 something broke', body=jira_body)]
        output = self.srv._format_email_list(emails)
        assert "Atlassian Jira" not in output

    def test_non_jira_body_not_cleaned(self):
        body = "Some text\nA normal separator line\nMore content"
        emails = [_make_email('m1', 'Regular email', body=body)]
        output = self.srv._format_email_list(emails)
        assert "Some text" in output
        assert "A normal separator line" in output
        assert "More content" in output

    def test_no_readable_content_hidden(self):
        emails = [_make_email('m1', 'Empty', body='No readable content')]
        output = self.srv._format_email_list(emails)
        assert "Body:" not in output

    def test_label_fetch_failure_graceful(self):
        self.srv.gmail_client.list_labels.side_effect = Exception("API error")
        emails = [_make_email('m1', 'Test', label_ids=['Label_1'])]
        output = self.srv._format_email_list(emails)
        assert "Labels: Label_1" in output

    def test_multiple_threads(self):
        emails = [
            _make_email('m1', 'Thread A msg 1', thread_id='t1'),
            _make_email('m2', 'Thread A msg 2', thread_id='t1'),
            _make_email('m3', 'Solo email', thread_id='t2'),
            _make_email('m4', 'Thread B msg 1', thread_id='t3'),
            _make_email('m5', 'Thread B msg 2', thread_id='t3'),
        ]
        output = self.srv._format_email_list(emails)
        assert "--- Thread: Thread A msg 1 (2 messages) ---" in output
        assert "--- Thread: Thread B msg 1 (2 messages) ---" in output
        assert output.count("--- Thread:") == 2
        for i in range(1, 6):
            assert f"{i}:" in output


# ---------------------------------------------------------------------------
# Tool handlers (async, via MCP request objects)
# ---------------------------------------------------------------------------

class TestToolHandlers:
    """Test the tool handlers via the MCP request_handlers interface."""

    def setup_method(self):
        self.srv = GmailMCPServer()
        self.srv.gmail_client = MagicMock()
        self.srv.email_position_map = {1: 'id-a', 2: 'id-b', 3: 'id-c'}

    def _call(self, name, arguments=None):
        resp = _call_tool_sync(self.srv, name, arguments)
        return resp.content

    def test_delete_emails_by_positions(self):
        self.srv.gmail_client.delete_emails.return_value = [
            {'success': True, 'subject': 'Test', 'message_id': 'id-a', 'error': None},
            {'success': True, 'subject': 'Test2', 'message_id': 'id-b', 'error': None},
        ]
        result = self._call('delete_emails', {'positions': [1, 2]})
        text = _text(result)
        assert "Deleted: Test" in text
        assert "Deleted: Test2" in text
        self.srv.gmail_client.delete_emails.assert_called_once_with(['id-a', 'id-b'])
        assert len(self.srv.recent_actions) == 2

    def test_delete_emails_failure(self):
        self.srv.gmail_client.delete_emails.return_value = [
            {'success': False, 'subject': None, 'message_id': 'id-a', 'error': 'HTTP 404'},
        ]
        result = self._call('delete_emails', {'message_ids': ['id-a']})
        text = _text(result)
        assert "Failed to delete id-a" in text
        assert "HTTP 404" in text
        assert len(self.srv.recent_actions) == 0

    def test_archive_emails_by_positions(self):
        self.srv.gmail_client.archive_emails.return_value = [
            {'success': True, 'subject': 'Archived Subj', 'message_id': 'id-c', 'error': None},
        ]
        result = self._call('archive_emails', {'positions': [3]})
        text = _text(result)
        assert "Archived: Archived Subj" in text
        assert len(self.srv.recent_actions) == 1
        assert self.srv.recent_actions[0]['action'] == 'archive'

    def test_list_labels(self):
        self.srv.gmail_client.list_labels.return_value = [
            {'id': 'L1', 'name': 'Triage/Jira', 'type': 'user'},
            {'id': 'L2', 'name': 'INBOX', 'type': 'system'},
        ]
        result = self._call('list_labels', {})
        text = _text(result)
        assert "Triage/Jira" in text
        assert "INBOX" in text

    def test_create_label(self):
        self.srv.gmail_client.create_label.return_value = {'id': 'L3', 'name': 'Triage/New'}
        result = self._call('create_label', {'name': 'Triage/New'})
        text = _text(result)
        assert "Created label: Triage/New" in text
        assert "L3" in text
        assert len(self.srv.recent_actions) == 1
        assert self.srv.recent_actions[0]['action'] == 'create_label'

    def test_create_label_missing_name(self):
        result = self._call('create_label', {})
        text = _text(result)
        # MCP framework validates required fields before handler runs
        assert "required" in text.lower() or "name" in text.lower()

    def test_modify_labels(self):
        self.srv.gmail_client.modify_labels.return_value = [
            {'success': True, 'message_id': 'id-a', 'error': None},
        ]
        result = self._call('modify_labels', {
            'positions': [1],
            'add_labels': ['Triage/Jira'],
            'remove_labels': [],
        })
        text = _text(result)
        assert "Labels modified: id-a" in text
        self.srv.gmail_client.modify_labels.assert_called_once_with(
            ['id-a'], add_labels=['Triage/Jira'], remove_labels=[]
        )

    def test_list_recent_actions_empty(self):
        result = self._call('list_recent_actions', {})
        text = _text(result)
        assert "No recent actions" in text

    def test_list_recent_actions_with_data(self):
        self.srv._record_action('delete', 'Subj A', 'msg-1')
        self.srv._record_action('archive', 'Subj B', 'msg-2')
        result = self._call('list_recent_actions', {'limit': 10})
        text = _text(result)
        assert "delete" in text
        assert "Subj A" in text
        assert "archive" in text
        assert "Subj B" in text

    def test_list_recent_actions_respects_limit(self):
        for i in range(10):
            self.srv._record_action('delete', f'Subj {i}', f'msg-{i}')
        result = self._call('list_recent_actions', {'limit': 3})
        text = _text(result)
        lines = [l for l in text.strip().split('\n') if l.strip()]
        assert len(lines) == 3

    def test_unknown_tool(self):
        result = self._call('nonexistent_tool', {})
        text = _text(result)
        assert "Error" in text
        assert "Unknown tool" in text

    def test_delete_emails_no_args(self):
        result = self._call('delete_emails')
        text = _text(result)
        assert "Error" in text

    def test_list_unread_emails_no_results(self):
        self.srv.gmail_client.list_unread_emails.return_value = []
        result = self._call('list_unread_emails', {})
        text = _text(result)
        assert "No unread emails" in text

    def test_list_unread_emails_auth_error(self):
        self.srv.gmail_client.list_unread_emails.side_effect = Exception(
            "Authentication required but no valid token found"
        )
        result = self._call('list_unread_emails', {})
        text = _text(result)
        assert "authentication" in text.lower()


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------

class TestListTools:
    def test_expected_tools_registered(self):
        srv = GmailMCPServer()
        resp = _list_tools_sync(srv)
        names = {t.name for t in resp.tools}
        expected = {
            'list_unread_emails', 'delete_emails', 'archive_emails',
            'list_labels', 'create_label', 'modify_labels',
            'list_recent_actions',
        }
        assert names == expected

    def test_no_mark_as_read_tool(self):
        srv = GmailMCPServer()
        resp = _list_tools_sync(srv)
        names = {t.name for t in resp.tools}
        assert 'mark_as_read' not in names

    def test_seven_tools_total(self):
        srv = GmailMCPServer()
        resp = _list_tools_sync(srv)
        assert len(resp.tools) == 7


# ---------------------------------------------------------------------------
# _HIDDEN_LABELS
# ---------------------------------------------------------------------------

class TestHiddenLabels:
    def test_standard_labels_hidden(self):
        for label in ['INBOX', 'UNREAD', 'SPAM', 'TRASH', 'CATEGORY_SOCIAL']:
            assert label in _HIDDEN_LABELS

    def test_user_labels_not_hidden(self):
        assert 'Triage/Jira' not in _HIDDEN_LABELS
        assert 'Label_1' not in _HIDDEN_LABELS
