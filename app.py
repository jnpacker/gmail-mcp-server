#!/usr/bin/env python3
"""
Gmail Triage Dashboard - Web UI
A clean web interface for Gmail inbox triage with auto-refresh every 15 minutes.
"""

import os
import re
import json
import subprocess
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request
from pathlib import Path

from gmail_mcp_server.gmail_client import GmailClient

app = Flask(__name__, static_folder='static', template_folder='templates')
gmail_client = GmailClient()

# Store triage results in memory with timestamp
TRIAGE_MODEL = 'claude-haiku-4-5'
triage_model = TRIAGE_MODEL  # mutable runtime selection

# Keywords that indicate an authentication/authorization failure
_AUTH_KEYWORDS = ['auth', 'token', 'credential', 'unauthorized', 'unauthenticated',
                  '401', '403', 'refresh', 'login', 'permission', 'access denied']

class AuthError(Exception):
    pass

triage_cache = {
    'data': None,
    'timestamp': None,
    'next_sync': None,
    'model': None,
    'last_unread_count': None,
    'error': None,  # {'type': 'auth'|'other', 'message': str} when set
}
triage_lock = threading.Lock()

def _is_auth_error(exc: Exception) -> bool:
    """Return True if the exception looks like an authentication failure."""
    msg = str(exc).lower()
    return any(kw in msg for kw in _AUTH_KEYWORDS)

def get_inbox_unread_count():
    """Return the number of unread emails in INBOX.

    Raises AuthError if authentication fails; returns None for other errors.
    """
    try:
        gmail_client._ensure_authenticated()
        result = gmail_client.service.users().labels().get(userId='me', id='INBOX').execute()
        return result.get('messagesUnread', 0)
    except Exception as e:
        if _is_auth_error(e):
            raise AuthError(str(e))
        print(f"[unread_count] Error: {e}")
        return None

def run_triage():
    """Execute the triage command and parse results"""
    try:
        result = subprocess.run(
            ['claude', '-p', '/triage', '--model', triage_model],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )

        if result.returncode != 0:
            print(f"Triage error (code {result.returncode})")
            combined_output = (result.stderr or '') + (result.stdout or '')
            combined_lower = combined_output.lower()
            if any(kw in combined_lower for kw in _AUTH_KEYWORDS):
                raise AuthError(f"Claude CLI authentication error (code {result.returncode}): {combined_output.strip()}")
            return {
                'labeled_groups': [],
                'auto_cleaned': {'archived': [], 'deleted': []},
                'summary': {'total': 0, 'labeled': 0, 'archived': 0, 'deleted': 0},
                'raw_output': f"Error running triage (code {result.returncode}): {combined_output}",
                'model': triage_model,
            }

        triage_text = result.stdout
        model = triage_model

        print(f"[triage] model={model}")

        parsed = parse_triage_output(triage_text)
        if not parsed:
            print("Failed to parse triage output")
            return {
                'labeled_groups': [],
                'auto_cleaned': {'archived': [], 'deleted': []},
                'summary': {'total': 0, 'labeled': 0, 'archived': 0, 'deleted': 0},
                'raw_output': 'Failed to parse triage output',
                'model': model,
            }
        parsed['model'] = model
        return parsed
    except AuthError:
        raise  # propagate auth errors to the caller
    except FileNotFoundError:
        print("Error: 'claude' command not found. Make sure Claude Code CLI is installed.")
        return None
    except subprocess.TimeoutExpired:
        print("Triage timed out after 180 seconds")
        return None
    except Exception as e:
        print(f"Error running triage: {e}")
        import traceback
        traceback.print_exc()
        return None

def parse_triage_output(output):
    """
    Parse the triage dashboard output and extract structured data.

    Parses three sections:
      1. LABELED   — group headers (┌─ Triage/Name) with items (│  · ...)
      2. AUTO-CLEANED — archived count and deleted item summaries
      3. QUICK LINKS — group name + count (fallback / count source)
    """
    try:
        lines = output.split('\n')

        result = {
            'labeled_groups': [],
            'auto_cleaned': {'archived': [], 'deleted': []},
            'summary': {'total': 0, 'labeled': 0, 'archived': 0, 'deleted': 0},
            'raw_output': output
        }

        # ── Summary line ──
        for line in lines:
            if 'Processed' in line and 'emails' in line:
                parts = line.split('·')
                try:
                    result['summary']['total'] = int(parts[0].split('Processed')[1].split('emails')[0].strip())
                    result['summary']['labeled'] = int(parts[1].split('labeled')[0].strip())
                    result['summary']['archived'] = int(parts[2].split('archived')[0].strip())
                    result['summary']['deleted'] = int(parts[3].split('deleted')[0].strip())
                except Exception as e:
                    print(f"Error parsing summary line: {e}")

        # ── Pass 1: LABELED section — groups with items ──
        groups_by_name = {}
        current_group = None

        for line in lines:
            # Group header: ┌─ Triage/GroupName ──── Priority · N emails
            if '┌' in line and 'Triage/' in line:
                name_match = re.search(r'(Triage/\S+)', line)
                if name_match:
                    group_name = name_match.group(1).strip()

                    priority = 'Info'
                    line_lower = line.lower()
                    if 'critical' in line_lower:
                        priority = 'Critical'
                    elif 'important' in line_lower:
                        priority = 'Important'

                    count = 0
                    count_match = re.search(r'(\d+)\s*emails?', line)
                    if count_match:
                        count = int(count_match.group(1))

                    current_group = {
                        'name': group_name,
                        'priority': priority,
                        'count': count,
                        'items': [],
                        'description': ''
                    }
                    groups_by_name[group_name] = current_group

            elif current_group and '│' in line:
                content = line.split('│', 1)[-1].strip()
                if not content:
                    continue
                if content.startswith('·'):
                    current_group['items'].append(content[1:].strip())
                elif not content.startswith('─'):
                    if current_group['description']:
                        current_group['description'] += ' ' + content
                    else:
                        current_group['description'] = content

            elif current_group and '└' in line:
                current_group = None

        # ── Pass 2: QUICK LINKS — fill in any missing groups ──
        in_quick_links = False
        for line in lines:
            if 'QUICK LINKS' in line:
                in_quick_links = True
                continue
            if in_quick_links and line.strip().startswith('Triage/'):
                try:
                    match = re.search(r'(Triage/\S+)\s+[(\[](\d+)[)\]]', line)
                    if match:
                        group_name = match.group(1).strip()
                        count = int(match.group(2))
                        if group_name not in groups_by_name:
                            groups_by_name[group_name] = {
                                'name': group_name,
                                'priority': 'Info',
                                'count': count,
                                'items': [],
                                'description': f'{count} emails'
                            }
                        else:
                            groups_by_name[group_name]['count'] = count
                except Exception as e:
                    print(f"Error parsing quick link line '{line}': {e}")

        # ── Pass 3: AUTO-CLEANED section ──
        in_auto_cleaned = False
        in_archived_sub = False
        for line in lines:
            if 'AUTO-CLEANED' in line or 'AUTO-CLEAN' in line:
                in_auto_cleaned = True
                continue
            if in_auto_cleaned and 'QUICK LINKS' in line:
                break
            if in_auto_cleaned:
                stripped = line.strip()
                if re.search(r'Archived\s*[\(\[]', stripped, re.IGNORECASE):
                    in_archived_sub = True
                elif re.search(r'Deleted\s*[\(\[]', stripped, re.IGNORECASE):
                    in_archived_sub = False
                elif stripped.startswith('·'):
                    item = stripped[1:].strip()
                    if item:
                        if in_archived_sub:
                            result['auto_cleaned']['archived'].append(item)
                        else:
                            result['auto_cleaned']['deleted'].append(item)

        # ── Assign priority by keywords (fallback) ──
        for group in groups_by_name.values():
            if group['priority'] == 'Info':
                name = group['name'].lower()
                if any(k in name for k in ['security', 'critical', 'urgent', 'alert']):
                    group['priority'] = 'Critical'
                elif any(k in name for k in ['jira', 'team', 'review', 'important']):
                    group['priority'] = 'Important'

        # Sort by priority
        priority_order = {'Critical': 0, 'Important': 1, 'Info': 2}
        result['labeled_groups'] = sorted(
            groups_by_name.values(),
            key=lambda g: priority_order.get(g['priority'], 99)
        )

        return result
    except Exception as e:
        print(f"Error parsing triage output: {e}")
        import traceback
        traceback.print_exc()
        return None

@app.route('/')
def index():
    """Serve the dashboard HTML"""
    import time
    return render_template('dashboard.html', cache_bust=int(time.time()))

@app.route('/api/triage', methods=['GET'])
def get_triage():
    """API endpoint to get current triage data"""
    next_sync = None
    if triage_cache['timestamp']:
        last_time = datetime.fromisoformat(triage_cache['timestamp'])
        next_sync = (last_time + timedelta(minutes=15)).isoformat()

    return jsonify({
        'data': triage_cache['data'],
        'timestamp': triage_cache['timestamp'],
        'next_sync': next_sync,
        'model': triage_cache.get('model'),
        'error': triage_cache.get('error'),
    })

@app.route('/api/triage/refresh', methods=['POST'])
def refresh_triage():
    """API endpoint to manually trigger triage"""
    if not triage_lock.acquire(blocking=False):
        return jsonify({'success': False, 'error': 'Triage already in progress'}), 409

    try:
        print("=== Triage refresh triggered ===")

        try:
            unread_count = get_inbox_unread_count()
        except AuthError as e:
            msg = f"Gmail authentication failed: {e}"
            print(f"[triage] Auth error during unread count: {e}")
            triage_cache['error'] = {'type': 'auth', 'message': msg}
            return jsonify({'success': False, 'auth_error': True, 'error': msg}), 401

        print(f"[triage] inbox unread count: {unread_count}")
        if unread_count is not None:
            if unread_count == 0:
                print("[triage] Skipping — no unread emails")
                return jsonify({'success': False, 'skipped': True, 'reason': 'No unread emails found'})

        try:
            data = run_triage()
        except AuthError as e:
            msg = f"Claude CLI authentication failed: {e}"
            print(f"[triage] Auth error during triage run: {e}")
            triage_cache['error'] = {'type': 'auth', 'message': msg}
            return jsonify({'success': False, 'auth_error': True, 'error': msg}), 401

        if data:
            current_time = datetime.now()
            triage_cache['data'] = data
            triage_cache['timestamp'] = current_time.isoformat()
            triage_cache['next_sync'] = (current_time + timedelta(minutes=15)).isoformat()
            triage_cache['model'] = data.get('model')
            triage_cache['last_unread_count'] = unread_count
            triage_cache['error'] = None
            print("Triage succeeded")
            return jsonify({
                'success': True,
                'data': data,
                'timestamp': triage_cache['timestamp'],
                'next_sync': triage_cache['next_sync'],
                'model': triage_cache['model'],
            })
        else:
            print("Triage failed - no data returned")
            return jsonify({
                'success': False,
                'error': 'Failed to run triage. Check console output for details.'
            }), 500
    finally:
        triage_lock.release()

@app.route('/api/emails/counts', methods=['GET'])
def get_email_counts():
    """Fetch total and unread counts for multiple labels via a single Gmail API batch request."""
    labels = request.args.getlist('label')
    if not labels:
        return jsonify({'error': 'label parameter(s) required'}), 400

    results = {}
    try:
        gmail_client._ensure_authenticated()

        # Resolve label names to IDs, skipping any that don't exist
        label_ids = {}
        for label_name in labels:
            try:
                label_ids[label_name] = gmail_client._resolve_label_name_to_id(label_name)
            except Exception as e:
                print(f"Label not found, skipping '{label_name}': {e}")
                results[label_name] = None

        count_data = {name: {'total': 0, 'unread': 0} for name in label_ids}
        error_labels = set()

        def make_callback(label_name, key):
            def callback(request_id, response, exception):
                if exception:
                    print(f"Batch error for '{label_name}' ({key}): {exception}")
                    error_labels.add(label_name)
                elif response:
                    count_data[label_name][key] = len(response.get('messages', []))
            return callback

        # Use label name in q= search to avoid labelIds quirks with system labels (e.g. External)
        # Split into batches of 5 labels (10 requests each) to avoid 429 rate limits
        label_items = list(label_ids.items())
        BATCH_SIZE = 5
        for chunk_start in range(0, len(label_items), BATCH_SIZE):
            chunk = label_items[chunk_start:chunk_start + BATCH_SIZE]
            batch = gmail_client.service.new_batch_http_request()
            for label_name, label_id in chunk:
                q_label = label_name.replace('/', '-').lower()  # Gmail search uses dashes: triage-security
                batch.add(
                    gmail_client.service.users().messages().list(
                        userId='me', q=f'label:{q_label} in:inbox', maxResults=100
                    ),
                    callback=make_callback(label_name, 'total')
                )
                batch.add(
                    gmail_client.service.users().messages().list(
                        userId='me', q=f'label:{q_label} in:inbox is:unread', maxResults=100
                    ),
                    callback=make_callback(label_name, 'unread')
                )
            batch.execute()
        results.update(count_data)
        # Mark labels that errored as null so the frontend won't hide them
        for label_name in error_labels:
            results[label_name] = None
        print(f"[counts] results: { {k: v for k, v in results.items() if v and (v.get('total', 0) > 0 or k in error_labels)} }")

    except Exception as e:
        print(f"Error fetching counts: {e}")
        return jsonify({'error': str(e)}), 500

    return jsonify({'counts': results})


@app.route('/api/emails', methods=['GET'])
def get_emails_by_label():
    """Fetch emails from Gmail matching a label, returning subjects + message IDs."""
    label_name = request.args.get('label', '')
    if not label_name:
        return jsonify({'error': 'label parameter required'}), 400

    try:
        gmail_client._ensure_authenticated()
        # Resolve label name to ID for reliable search
        label_id = gmail_client._resolve_label_name_to_id(label_name)
        # Search using label ID and INBOX
        result = gmail_client.service.users().messages().list(
            userId='me', labelIds=[label_id, 'INBOX'], maxResults=50
        ).execute()

        messages = result.get('messages', [])
        emails = []
        for msg in messages:
            details = gmail_client._get_email_details(msg['id'])
            if details:
                emails.append({
                    'id': details['id'],
                    'subject': details['subject'],
                    'sender': details['sender'],
                    'date': details['date'],
                    'snippet': details['snippet'],
                    'body': details.get('body', ''),
                    'isUnread': 'UNREAD' in details.get('labelIds', []),
                })

        return jsonify({'emails': emails})
    except Exception as e:
        print(f"Error fetching emails for label '{label_name}': {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/labels/triage', methods=['GET'])
def get_triage_labels():
    """Return all Triage/* label names currently in Gmail."""
    try:
        gmail_client._ensure_authenticated()
        labels = gmail_client.list_labels()
        triage_labels = sorted(l['name'] for l in labels if l['name'].startswith('Triage/'))
        return jsonify({'labels': triage_labels})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


ALLOWED_MODELS = {
    'claude-haiku-4-5',
    'claude-sonnet-4-6',
    'claude-opus-4-6',
}

@app.route('/api/model', methods=['GET'])
def get_model():
    return jsonify({'model': triage_model})

@app.route('/api/model', methods=['POST'])
def set_model():
    global triage_model
    data = request.get_json()
    model = data.get('model') if data else None
    if not model or model not in ALLOWED_MODELS:
        return jsonify({'error': f'Invalid model. Allowed: {sorted(ALLOWED_MODELS)}'}), 400
    triage_model = model
    print(f"[model] Switched to {triage_model}")
    return jsonify({'model': triage_model})


@app.route('/api/emails/archive', methods=['POST'])
def archive_email():
    """Archive an email by message ID."""
    data = request.get_json()
    message_id = data.get('message_id') if data else None
    if not message_id:
        return jsonify({'error': 'message_id required'}), 400

    try:
        result = gmail_client.archive_email(message_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/emails/delete', methods=['POST'])
def delete_email():
    """Delete an email by message ID."""
    data = request.get_json()
    message_id = data.get('message_id') if data else None
    if not message_id:
        return jsonify({'error': 'message_id required'}), 400

    try:
        result = gmail_client.delete_email(message_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    print("Starting Flask app on http://localhost:5000")
    print("Press Ctrl+C to stop")

    def run_initial_triage():
        import time
        time.sleep(1)
        print("Running initial triage in background...")
        with triage_lock:
            try:
                unread_count = get_inbox_unread_count()
            except AuthError as e:
                msg = f"Gmail authentication failed: {e}"
                print(f"[triage] Auth error during initial unread count: {e}")
                triage_cache['error'] = {'type': 'auth', 'message': msg}
                triage_cache['timestamp'] = datetime.now().isoformat()
                return
            print(f"[triage] inbox unread count: {unread_count}")
            if unread_count == 0:
                print("[triage] Skipping initial triage — no unread emails")
                triage_cache['timestamp'] = datetime.now().isoformat()
                triage_cache['data'] = {'labeled_groups': [], 'summary': {}, 'auto_cleaned': {}}
                return
            try:
                data = run_triage()
            except AuthError as e:
                msg = f"Claude CLI authentication failed: {e}"
                print(f"[triage] Auth error during initial triage run: {e}")
                triage_cache['error'] = {'type': 'auth', 'message': msg}
                triage_cache['timestamp'] = datetime.now().isoformat()
                return
            if data:
                triage_cache['data'] = data
                triage_cache['timestamp'] = datetime.now().isoformat()
                triage_cache['next_sync'] = (datetime.now() + timedelta(minutes=15)).isoformat()
                triage_cache['model'] = data.get('model')
                triage_cache['last_unread_count'] = unread_count
                print(f"Triage complete: {data['summary']['total']} emails processed")
                print(f"Found {len(data['labeled_groups'])} email groups")
                for g in data['labeled_groups']:
                    print(f"  {g['name']}: {g['count']} emails, {len(g['items'])} items")
                if not data['labeled_groups']:
                    print("WARNING: No labeled groups found.")
                    print(f"Raw output length: {len(data.get('raw_output', ''))}")
            else:
                print("Triage failed - no data returned")

    triage_thread = threading.Thread(target=run_initial_triage, daemon=True)
    triage_thread.start()

    app.run(debug=False, port=5000)
