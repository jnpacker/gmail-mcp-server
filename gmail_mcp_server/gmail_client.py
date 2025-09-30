"""Gmail API client wrapper for MCP server."""

import os
import json
import base64
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


class GmailClient:
    """Gmail API client for managing emails."""
    
    SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.modify'
    ]
    
    def __init__(self, credentials_path: str = "credentials.json", token_path: str = "token.json", auto_authenticate: bool = False):
        """Initialize Gmail client with authentication."""
        # Get the directory where this code file resides
        code_dir = Path(__file__).parent.parent

        # Convert relative paths to absolute paths based on code location
        if not os.path.isabs(credentials_path):
            self.credentials_path = str(code_dir / credentials_path)
        else:
            self.credentials_path = credentials_path

        if not os.path.isabs(token_path):
            self.token_path = str(code_dir / token_path)
        else:
            self.token_path = token_path

        self.service = None
        self._authenticated = False

        if auto_authenticate:
            self._authenticate()
    
    def _authenticate(self):
        """Authenticate and build Gmail service."""
        creds = None

        if os.path.exists(self.token_path):
            try:
                creds = Credentials.from_authorized_user_file(self.token_path, self.SCOPES)
            except Exception as e:
                raise Exception(f"Failed to load existing token: {e}")

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    with open(self.token_path, 'w') as token:
                        token.write(creds.to_json())
                except Exception as e:
                    raise Exception(f"Failed to refresh token: {e}")
            else:
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(
                        f"Credentials file not found at {self.credentials_path}. "
                        "Please download your OAuth 2.0 credentials from Google Cloud Console."
                    )

                if os.environ.get('GMAIL_INTERACTIVE_AUTH') == '1':
                    try:
                        flow = InstalledAppFlow.from_client_secrets_file(
                            self.credentials_path, self.SCOPES
                        )
                        creds = flow.run_local_server(port=0)
                        with open(self.token_path, 'w') as token:
                            token.write(creds.to_json())
                    except Exception as e:
                        raise Exception(f"Interactive authentication failed: {e}")
                else:
                    # Detect which Python command was used
                    python_cmd = "python3" if "python3" in sys.executable or sys.version_info >= (3, 0) else "python"

                    raise Exception(
                        f"Authentication required but no valid token found. "
                        f"Please run initial authentication manually:\n"
                        f"{python_cmd} -c \"from gmail_mcp_server.gmail_client import GmailClient; "
                        f"import os; os.environ['GMAIL_INTERACTIVE_AUTH'] = '1'; GmailClient(auto_authenticate=True)\"\n"
                        f"This will create the required token.json file for headless operation."
                    )

        self.service = build('gmail', 'v1', credentials=creds)
        self._authenticated = True

    def _ensure_authenticated(self):
        """Ensure the client is authenticated before making API calls."""
        if not self._authenticated:
            self._authenticate()
    
    def list_unread_emails(self, subject_filter: Optional[str] = None, max_results: int = 50) -> List[Dict[str, Any]]:
        """
        List unread emails in inbox, optionally filtered by subject.

        Args:
            subject_filter: Optional subject filter string
            max_results: Maximum number of emails to return

        Returns:
            List of email dictionaries with id, subject, sender, date, and body
        """
        self._ensure_authenticated()
        try:
            query = "is:unread in:inbox"
            if subject_filter:
                query += f' subject:"{subject_filter}"'
            
            result = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()
            
            messages = result.get('messages', [])
            emails = []
            
            for message in messages:
                email_data = self._get_email_details(message['id'])
                if email_data:
                    emails.append(email_data)
            
            return emails
            
        except HttpError as error:
            raise Exception(f"An error occurred while listing emails: {error}")
    
    def _get_email_details(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific email."""
        self._ensure_authenticated()
        try:
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()
            
            headers = message['payload'].get('headers', [])
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')
            date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Unknown Date')
            
            body = self._extract_email_body(message['payload'])
            
            return {
                'id': message_id,
                'subject': subject,
                'sender': sender,
                'date': date,
                'body': body,
                'snippet': message.get('snippet', '')
            }
            
        except HttpError as error:
            print(f"An error occurred while getting email details: {error}")
            return None
    
    def _extract_email_body(self, payload: Dict[str, Any]) -> str:
        """Extract email body from message payload."""
        body = ""
        
        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain':
                    data = part['body'].get('data', '')
                    if data:
                        body = base64.urlsafe_b64decode(data).decode('utf-8')
                        break
                elif part['mimeType'] == 'text/html' and not body:
                    data = part['body'].get('data', '')
                    if data:
                        body = base64.urlsafe_b64decode(data).decode('utf-8')
        else:
            if payload['mimeType'] == 'text/plain':
                data = payload['body'].get('data', '')
                if data:
                    body = base64.urlsafe_b64decode(data).decode('utf-8')
        
        return body or "No readable content"
    
    def delete_email(self, message_id: str) -> dict:
        """
        Move an email to trash and mark it as read.

        Args:
            message_id: The ID of the email to move to trash

        Returns:
            Dict with 'success' bool, 'subject' string, and 'error' string if failed
        """
        self._ensure_authenticated()
        try:
            # Get email subject before deleting
            email_details = self._get_email_details(message_id)
            subject = email_details.get('subject', 'No Subject') if email_details else 'Unknown Subject'

            # Truncate subject to prevent line wrapping (max 60 characters)
            if len(subject) > 60:
                subject = subject[:57] + "..."

            self.service.users().messages().modify(
                userId='me',
                id=message_id,
                body={
                    'addLabelIds': ['TRASH'],
                    'removeLabelIds': ['UNREAD']
                }
            ).execute()
            return {"success": True, "subject": subject, "error": None}

        except HttpError as error:
            error_details = f"HTTP {error.resp.status}: {error.error_details if hasattr(error, 'error_details') else str(error)}"
            print(f"An error occurred while moving email to trash: {error_details}")
            return {"success": False, "subject": None, "error": error_details}
        except Exception as error:
            error_details = f"Unexpected error: {str(error)} ({type(error).__name__})"
            print(f"An error occurred while moving email to trash: {error_details}")
            return {"success": False, "subject": None, "error": error_details}
    
    def archive_email(self, message_id: str) -> dict:
        """
        Archive an email (remove from inbox).

        Args:
            message_id: The ID of the email to archive

        Returns:
            Dict with 'success' bool, 'subject' string, and 'error' string if failed
        """
        self._ensure_authenticated()
        try:
            # Get email subject before archiving
            email_details = self._get_email_details(message_id)
            subject = email_details.get('subject', 'No Subject') if email_details else 'Unknown Subject'

            # Truncate subject to prevent line wrapping (max 60 characters)
            if len(subject) > 60:
                subject = subject[:57] + "..."

            self.service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'removeLabelIds': ['INBOX', 'UNREAD']}
            ).execute()
            return {"success": True, "subject": subject, "error": None}

        except HttpError as error:
            error_details = f"HTTP {error.resp.status}: {error.error_details if hasattr(error, 'error_details') else str(error)}"
            print(f"An error occurred while archiving email: {error_details}")
            return {"success": False, "subject": None, "error": error_details}
        except Exception as error:
            error_details = f"Unexpected error: {str(error)} ({type(error).__name__})"
            print(f"An error occurred while archiving email: {error_details}")
            return {"success": False, "subject": None, "error": error_details}