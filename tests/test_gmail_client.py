"""Tests for GmailClient - gmail_client.py logic."""

import base64
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from gmail_mcp_server.gmail_client import GmailClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client():
    """Create a GmailClient with mocked auth and service."""
    with patch.object(GmailClient, '_authenticate'):
        client = GmailClient()
    client._authenticated = True
    client.service = MagicMock()
    return client


def _b64(text):
    """Base64url-encode a string the way Gmail API does."""
    return base64.urlsafe_b64encode(text.encode('utf-8')).decode('utf-8')


def _make_gmail_message(message_id, subject="Test", sender="a@b.com",
                        body_text="Hello", thread_id=None, label_ids=None):
    """Build a fake Gmail API message response."""
    return {
        'id': message_id,
        'threadId': thread_id or message_id,
        'labelIds': label_ids or ['INBOX', 'UNREAD'],
        'snippet': body_text[:40],
        'payload': {
            'mimeType': 'multipart/alternative',
            'headers': [
                {'name': 'Subject', 'value': subject},
                {'name': 'From', 'value': sender},
                {'name': 'Date', 'value': 'Mon, 1 Jan 2024 00:00:00 +0000'},
            ],
            'parts': [
                {
                    'mimeType': 'text/plain',
                    'body': {'data': _b64(body_text)},
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# _get_email_details
# ---------------------------------------------------------------------------

class TestGetEmailDetails:
    def test_returns_threadid_and_labelids(self):
        client = _make_client()
        msg = _make_gmail_message('m1', thread_id='t1', label_ids=['INBOX', 'UNREAD', 'Label_1'])
        client.service.users().messages().get().execute.return_value = msg
        result = client._get_email_details('m1')
        assert result['threadId'] == 't1'
        assert result['labelIds'] == ['INBOX', 'UNREAD', 'Label_1']

    def test_extracts_subject_sender_date(self):
        client = _make_client()
        msg = _make_gmail_message('m1', subject='Hello World', sender='test@example.com')
        client.service.users().messages().get().execute.return_value = msg
        result = client._get_email_details('m1')
        assert result['subject'] == 'Hello World'
        assert result['sender'] == 'test@example.com'
        assert result['date'] == 'Mon, 1 Jan 2024 00:00:00 +0000'

    def test_extracts_body(self):
        client = _make_client()
        msg = _make_gmail_message('m1', body_text='Body content here')
        client.service.users().messages().get().execute.return_value = msg
        result = client._get_email_details('m1')
        assert result['body'] == 'Body content here'

    def test_missing_headers_defaults(self):
        client = _make_client()
        msg = _make_gmail_message('m1')
        msg['payload']['headers'] = []  # no headers at all
        client.service.users().messages().get().execute.return_value = msg
        result = client._get_email_details('m1')
        assert result['subject'] == 'No Subject'
        assert result['sender'] == 'Unknown Sender'
        assert result['date'] == 'Unknown Date'


# ---------------------------------------------------------------------------
# _extract_email_body
# ---------------------------------------------------------------------------

class TestExtractEmailBody:
    def setup_method(self):
        self.client = _make_client()

    def test_plain_text_part(self):
        payload = {
            'mimeType': 'multipart/alternative',
            'parts': [
                {'mimeType': 'text/plain', 'body': {'data': _b64('Plain text')}},
                {'mimeType': 'text/html', 'body': {'data': _b64('<b>HTML</b>')}},
            ]
        }
        assert self.client._extract_email_body(payload) == 'Plain text'

    def test_html_fallback(self):
        payload = {
            'mimeType': 'multipart/alternative',
            'parts': [
                {'mimeType': 'text/plain', 'body': {'data': ''}},
                {'mimeType': 'text/html', 'body': {'data': _b64('<b>HTML</b>')}},
            ]
        }
        assert self.client._extract_email_body(payload) == '<b>HTML</b>'

    def test_single_part_plain(self):
        payload = {
            'mimeType': 'text/plain',
            'body': {'data': _b64('Single part')},
        }
        assert self.client._extract_email_body(payload) == 'Single part'

    def test_no_content(self):
        payload = {
            'mimeType': 'text/plain',
            'body': {'data': ''},
        }
        assert self.client._extract_email_body(payload) == 'No readable content'


# ---------------------------------------------------------------------------
# delete_emails / delete_email
# ---------------------------------------------------------------------------

class TestDeleteEmails:
    def setup_method(self):
        self.client = _make_client()

    def test_single_delete(self):
        msg = _make_gmail_message('m1', subject='Delete me')
        self.client.service.users().messages().get().execute.return_value = msg
        self.client.service.users().messages().modify().execute.return_value = {}

        results = self.client.delete_emails(['m1'])
        assert len(results) == 1
        assert results[0]['success'] is True
        assert results[0]['subject'] == 'Delete me'
        assert results[0]['message_id'] == 'm1'

    def test_batch_delete(self):
        msg = _make_gmail_message('m1', subject='A')
        self.client.service.users().messages().get().execute.return_value = msg
        self.client.service.users().messages().modify().execute.return_value = {}

        results = self.client.delete_emails(['m1', 'm2', 'm3'])
        assert len(results) == 3
        assert all(r['success'] for r in results)

    def test_delete_email_wrapper(self):
        msg = _make_gmail_message('m1', subject='Wrapper test')
        self.client.service.users().messages().get().execute.return_value = msg
        self.client.service.users().messages().modify().execute.return_value = {}

        result = self.client.delete_email('m1')
        assert result['success'] is True

    def test_long_subject_truncated(self):
        long_subject = "A" * 70
        msg = _make_gmail_message('m1', subject=long_subject)
        self.client.service.users().messages().get().execute.return_value = msg
        self.client.service.users().messages().modify().execute.return_value = {}

        result = self.client.delete_emails(['m1'])
        assert len(result[0]['subject']) == 60
        assert result[0]['subject'].endswith("...")


# ---------------------------------------------------------------------------
# archive_emails / archive_email
# ---------------------------------------------------------------------------

class TestArchiveEmails:
    def setup_method(self):
        self.client = _make_client()

    def test_single_archive(self):
        msg = _make_gmail_message('m1', subject='Archive me')
        self.client.service.users().messages().get().execute.return_value = msg
        self.client.service.users().messages().modify().execute.return_value = {}

        results = self.client.archive_emails(['m1'])
        assert len(results) == 1
        assert results[0]['success'] is True

    def test_archive_email_wrapper(self):
        msg = _make_gmail_message('m1', subject='Wrapper')
        self.client.service.users().messages().get().execute.return_value = msg
        self.client.service.users().messages().modify().execute.return_value = {}

        result = self.client.archive_email('m1')
        assert result['success'] is True


# ---------------------------------------------------------------------------
# list_labels / create_label / _resolve_label_name_to_id
# ---------------------------------------------------------------------------

class TestLabels:
    def setup_method(self):
        self.client = _make_client()

    def test_list_labels(self):
        self.client.service.users().labels().list().execute.return_value = {
            'labels': [
                {'id': 'L1', 'name': 'INBOX', 'type': 'system'},
                {'id': 'L2', 'name': 'Triage/Jira', 'type': 'user'},
            ]
        }
        result = self.client.list_labels()
        assert len(result) == 2
        assert result[1]['name'] == 'Triage/Jira'
        assert result[1]['id'] == 'L2'

    def test_create_label(self):
        self.client.service.users().labels().create().execute.return_value = {
            'id': 'L3', 'name': 'Triage/New'
        }
        result = self.client.create_label('Triage/New')
        assert result == {'id': 'L3', 'name': 'Triage/New'}

    def test_resolve_label_name_to_id(self):
        self.client.service.users().labels().list().execute.return_value = {
            'labels': [
                {'id': 'L1', 'name': 'INBOX', 'type': 'system'},
                {'id': 'L2', 'name': 'Triage/Jira', 'type': 'user'},
            ]
        }
        assert self.client._resolve_label_name_to_id('Triage/Jira') == 'L2'
        assert self.client._resolve_label_name_to_id('triage/jira') == 'L2'  # case insensitive

    def test_resolve_label_not_found(self):
        self.client.service.users().labels().list().execute.return_value = {
            'labels': [{'id': 'L1', 'name': 'INBOX', 'type': 'system'}]
        }
        with pytest.raises(ValueError, match="Label not found"):
            self.client._resolve_label_name_to_id('Nonexistent')


# ---------------------------------------------------------------------------
# modify_labels
# ---------------------------------------------------------------------------

class TestModifyLabels:
    def setup_method(self):
        self.client = _make_client()
        self.client.service.users().labels().list().execute.return_value = {
            'labels': [
                {'id': 'L1', 'name': 'Triage/Jira', 'type': 'user'},
                {'id': 'L2', 'name': 'Triage/Security', 'type': 'user'},
            ]
        }

    def test_add_labels(self):
        self.client.service.users().messages().modify().execute.return_value = {}
        results = self.client.modify_labels(['m1'], add_labels=['Triage/Jira'])
        assert len(results) == 1
        assert results[0]['success'] is True

    def test_add_and_remove(self):
        self.client.service.users().messages().modify().execute.return_value = {}
        results = self.client.modify_labels(
            ['m1', 'm2'],
            add_labels=['Triage/Jira'],
            remove_labels=['Triage/Security']
        )
        assert len(results) == 2
        assert all(r['success'] for r in results)

    def test_label_not_found_raises(self):
        with pytest.raises(ValueError, match="Label not found"):
            self.client.modify_labels(['m1'], add_labels=['Nonexistent'])


# ---------------------------------------------------------------------------
# mark_as_read
# ---------------------------------------------------------------------------

class TestMarkAsRead:
    def test_batch_mark_as_read(self):
        client = _make_client()
        client.service.users().messages().modify().execute.return_value = {}
        results = client.mark_as_read(['m1', 'm2'])
        assert len(results) == 2
        assert all(r['success'] for r in results)
