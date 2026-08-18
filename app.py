#!/usr/bin/env python3
"""
Gmail Triage Dashboard - Web UI
A clean web interface for Gmail inbox triage with auto-refresh every 15 minutes.
"""

import getpass
import hashlib
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, make_response, redirect, render_template, request, session
from googleapiclient.errors import HttpError

from gmail_mcp_server.gmail_client import GmailClient


def _load_or_generate_secret():
    """Return the Flask secret key, generating and persisting it on first run."""
    secret_file = Path(".flask_secret")
    if secret_file.exists():
        return secret_file.read_text().strip()
    key = secrets.token_hex(32)
    secret_file.write_text(key)
    return key


app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or _load_or_generate_secret()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=4)
gmail_client = GmailClient()
# httplib2 (used inside google-api-python-client) is not thread-safe.
# All gmail_client calls must hold this lock to prevent concurrent SSL access → SIGSEGV.
gmail_client_lock = threading.Lock()

# ── PIN helpers ───────────────────────────────────────────────


def _load_pin_hash():
    """Read the stored PBKDF2 salt and hash from .pincode. Returns (None, None) if unset."""
    p = Path(".pincode")
    if not p.exists():
        return None, None
    salt, h = p.read_text().strip().split(":")
    return salt, h


def _verify_pin(pin):
    """Return True if pin matches the stored hash, or if no PIN is configured."""
    salt, stored = _load_pin_hash()
    if salt is None:
        return True  # no PIN configured
    candidate = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 260000).hex()
    return secrets.compare_digest(candidate, stored)


def _pin_configured():
    """Return True if a .pincode file exists."""
    return Path(".pincode").exists()


def require_pin(f):
    """Flask decorator: return 401 if a PIN is configured but the session is not authenticated."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if _pin_configured() and not session.get("pin_ok"):
            return jsonify({"error": "pin_required"}), 401
        return f(*args, **kwargs)

    return decorated


# ── Triage cache ───────────────────────────────────────────────

# Store triage results in memory with timestamp
TRIAGE_MODEL = "gemini-3.7-flash"
triage_model = TRIAGE_MODEL

# Keywords that indicate an authentication/authorization failure
_AUTH_KEYWORDS = [
    "auth",
    "token",
    "credential",
    "unauthorized",
    "unauthenticated",
    "401",
    "403",
    "refresh",
    "login",
    "permission",
    "access denied",
]


class AuthError(Exception):
    pass


def _is_ssl_error(exc: Exception) -> bool:
    """Return True if the exception looks like an SSL/TLS error."""
    return isinstance(exc, ssl.SSLError) or "ssl" in type(exc).__name__.lower() or "[ssl" in str(exc).lower()


def _is_retryable_gmail_error(exc: Exception) -> bool:
    """Return True for transient errors that are safe to retry (SSL, rate-limit, 5xx)."""
    if _is_ssl_error(exc):
        return True
    if isinstance(exc, HttpError) and getattr(exc, "resp", None):
        return exc.resp.status in {429, 500, 502, 503, 504}
    return False


def _with_ssl_retry(fn, retries=3, delay=1.0):
    """Call fn(), retrying up to `retries` times on transient SSL errors."""
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if _is_retryable_gmail_error(e) and attempt < retries - 1:
                print(f"[ssl_retry] SSL error (attempt {attempt + 1}/{retries}): {e}")
                time.sleep(delay * (attempt + 1))
            else:
                raise


triage_cache = {
    "data": None,
    "timestamp": None,
    "next_sync": None,
    "model": None,
    "last_unread_count": None,
    "error": None,  # {'type': 'auth'|'other', 'message': str} when set
}

# Tracks the currently-running triage subprocess for status reporting
triage_process_info = {
    "pid": None,
    "start_time": None,  # epoch float
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
        with gmail_client_lock:
            gmail_client._ensure_authenticated()
            result = gmail_client.service.users().labels().get(userId="me", id="INBOX").execute()
        return result.get("messagesUnread", 0)
    except Exception as e:
        if _is_auth_error(e):
            raise AuthError(str(e))
        print(f"[unread_count] Error: {e}")
        return None


TRIAGE_TIMEOUT = 1200  # 20 minutes — enough for large inboxes (63+ emails)
TRIAGE_HEARTBEAT = 30  # log "still running" every N seconds


def run_triage():
    """Execute the triage command and parse results.

    Uses Popen + polling so we can emit heartbeat log lines while the AI works
    and detect true hangs vs. slow-but-alive runs.  Output is captured silently
    on success; logged in full to the console on failure.
    """
    global triage_process_info
    try:
        cmd = ["gemini", "-p", "/triage", "--model", triage_model]
        cwd = os.path.dirname(os.path.abspath(__file__))

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )

        start = time.time()
        triage_process_info = {"pid": proc.pid, "start_time": start}
        print(f"[AI triage] Spawned pid={proc.pid} cmd={' '.join(cmd)} timeout={TRIAGE_TIMEOUT}s")

        last_heartbeat = start
        while proc.poll() is None:
            elapsed = time.time() - start
            if elapsed >= TRIAGE_TIMEOUT:
                proc.kill()
                proc.wait()
                triage_process_info = {"pid": None, "start_time": None}
                print(f"[AI triage] KILLED after {TRIAGE_TIMEOUT}s timeout (pid={proc.pid})")
                return None
            if time.time() - last_heartbeat >= TRIAGE_HEARTBEAT:
                print(f"[AI triage] Still running... ({elapsed:.0f}s elapsed, pid={proc.pid})")
                last_heartbeat = time.time()
            time.sleep(5)

        stdout, stderr = proc.communicate()
        elapsed = time.time() - start
        triage_process_info = {"pid": None, "start_time": None}

        if proc.returncode != 0:
            combined_output = (stderr or "") + (stdout or "")
            combined_lower = combined_output.lower()
            print(f"[AI triage] FAILED (code={proc.returncode}, {elapsed:.0f}s):\n{combined_output.strip()}")
            if any(kw in combined_lower for kw in _AUTH_KEYWORDS):
                raise AuthError(f"Gemini CLI authentication error (code {proc.returncode}): {combined_output.strip()}")
            return {
                "labeled_groups": [],
                "auto_cleaned": {"archived": [], "deleted": []},
                "summary": {"total": 0, "labeled": 0, "archived": 0, "deleted": 0},
                "raw_output": f"Error running triage (code {proc.returncode}): {combined_output}",
                "model": triage_model,
            }

        # Success — parse stdout, discard stderr (Gemini CLI writes progress there)
        parsed = parse_triage_output(stdout)
        if not parsed:
            print(f"[AI triage] Parse failed ({elapsed:.0f}s). stderr:\n{(stderr or '').strip()}")
            return {
                "labeled_groups": [],
                "auto_cleaned": {"archived": [], "deleted": []},
                "summary": {"total": 0, "labeled": 0, "archived": 0, "deleted": 0},
                "raw_output": "Failed to parse triage output",
                "model": triage_model,
            }

        print(f"[AI triage] Completed in {elapsed:.0f}s")
        parsed["model"] = triage_model
        return parsed

    except AuthError:
        raise
    except FileNotFoundError:
        cli_name = "Gemini CLI" if triage_model.startswith("gemini") else "Claude Code CLI"
        print(f"[AI triage] {cli_name} not found in PATH")
        triage_process_info = {"pid": None, "start_time": None}
        return None
    except Exception as e:
        print(f"[AI triage] Unexpected error: {e}")
        triage_process_info = {"pid": None, "start_time": None}
        return None


_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def parse_triage_output(output):
    """
    Parse the triage dashboard output and extract structured data.

    Tries JSON block extraction first (Step 5 of triage.md), then falls back
    to regex parsing of the ASCII-art dashboard for summary/groups/auto-cleaned.
    """
    try:
        # Strip ANSI escape codes — Gemini CLI emits them even when stdout is a pipe
        output = _ANSI_ESCAPE.sub("", output)

        # ── JSON-first path (reliable across Claude and Gemini) ──
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", output, re.DOTALL)
        raw_json_match = None if json_match else re.search(r'(\{"summary".*?\})\s*$', output, re.DOTALL)
        json_candidate = json_match.group(1) if json_match else (raw_json_match.group(1) if raw_json_match else None)
        if json_candidate:
            try:
                data = json.loads(json_candidate)
                priority_order = {"Critical": 0, "Important": 1, "Info": 2}
                groups = sorted(data.get("groups", []), key=lambda g: priority_order.get(g.get("priority", "Info"), 99))
                return {
                    "labeled_groups": groups,
                    "auto_cleaned": {
                        "archived": data.get("archived", []),
                        "deleted": data.get("deleted", []),
                    },
                    "summary": data.get("summary", {"total": 0, "labeled": 0, "archived": 0, "deleted": 0}),
                    "raw_output": output,
                }
            except (json.JSONDecodeError, KeyError):
                pass

        lines = output.split("\n")

        result = {
            "labeled_groups": [],
            "auto_cleaned": {"archived": [], "deleted": []},
            "summary": {"total": 0, "labeled": 0, "archived": 0, "deleted": 0},
            "raw_output": output,
        }

        # ── Summary line ──
        for line in lines:
            if "Processed" in line and "emails" in line:
                try:
                    m = re.search(r"Processed\s+(\d+)\s+emails", line)
                    if m:
                        result["summary"]["total"] = int(m.group(1))
                    m = re.search(r"(\d+)\s+labeled", line)
                    if m:
                        result["summary"]["labeled"] = int(m.group(1))
                    m = re.search(r"(\d+)\s+archived", line)
                    if m:
                        result["summary"]["archived"] = int(m.group(1))
                    m = re.search(r"(\d+)\s+deleted", line)
                    if m:
                        result["summary"]["deleted"] = int(m.group(1))
                except Exception as e:
                    print(f"Error parsing summary line: {e}")

        # ── Pass 1: LABELED section — groups with items ──
        groups_by_name = {}
        current_group = None

        for line in lines:
            # Group header: ┌─ Triage/GroupName ──── Priority · N emails
            if "┌" in line and "Triage/" in line:
                name_match = re.search(r"(Triage/\S+)", line)
                if name_match:
                    group_name = name_match.group(1).strip()

                    priority = "Info"
                    line_lower = line.lower()
                    if "critical" in line_lower:
                        priority = "Critical"
                    elif "important" in line_lower:
                        priority = "Important"

                    count = 0
                    count_match = re.search(r"(\d+)\]?\s*emails?", line)
                    if count_match:
                        count = int(count_match.group(1))

                    current_group = {
                        "name": group_name,
                        "priority": priority,
                        "count": count,
                        "items": [],
                        "description": "",
                    }
                    groups_by_name[group_name] = current_group

            elif current_group and "│" in line:
                content = line.split("│", 1)[-1].strip()
                if not content:
                    continue
                if content.startswith("·"):
                    current_group["items"].append(content[1:].strip())
                elif not content.startswith("─"):
                    if current_group["description"]:
                        current_group["description"] += " " + content
                    else:
                        current_group["description"] = content

            elif current_group and "└" in line:
                current_group = None

        # ── Pass 2: QUICK LINKS — fill in any missing groups ──
        in_quick_links = False
        for line in lines:
            if "QUICK LINKS" in line:
                in_quick_links = True
                continue
            if in_quick_links and line.strip().startswith("Triage/"):
                try:
                    match = re.search(r"(Triage/\S+)\s+[(\[](\d+)[)\]]", line)
                    if match:
                        group_name = match.group(1).strip()
                        count = int(match.group(2))
                        if group_name not in groups_by_name:
                            groups_by_name[group_name] = {
                                "name": group_name,
                                "priority": "Info",
                                "count": count,
                                "items": [],
                                "description": f"{count} emails",
                            }
                        else:
                            groups_by_name[group_name]["count"] = count
                except Exception as e:
                    print(f"Error parsing quick link line '{line}': {e}")

        # ── Pass 3: AUTO-CLEANED section ──
        in_auto_cleaned = False
        in_archived_sub = False
        for line in lines:
            if "AUTO-CLEANED" in line or "AUTO-CLEAN" in line:
                in_auto_cleaned = True
                continue
            if in_auto_cleaned and "QUICK LINKS" in line:
                break
            if in_auto_cleaned:
                stripped = line.strip()
                if re.search(r"Archived\s*[\(\[]", stripped, re.IGNORECASE):
                    in_archived_sub = True
                elif re.search(r"Deleted\s*[\(\[]", stripped, re.IGNORECASE):
                    in_archived_sub = False
                elif stripped.startswith("·"):
                    item = stripped[1:].strip()
                    if item:
                        if in_archived_sub:
                            result["auto_cleaned"]["archived"].append(item)
                        else:
                            result["auto_cleaned"]["deleted"].append(item)

        # ── Assign priority by keywords (fallback) ──
        for group in groups_by_name.values():
            if group["priority"] == "Info":
                name = group["name"].lower()
                if any(k in name for k in ["security", "critical", "urgent", "alert"]):
                    group["priority"] = "Critical"
                elif any(k in name for k in ["jira", "team", "review", "important"]):
                    group["priority"] = "Important"

        # Sort by priority
        priority_order = {"Critical": 0, "Important": 1, "Info": 2}
        result["labeled_groups"] = sorted(groups_by_name.values(), key=lambda g: priority_order.get(g["priority"], 99))

        return result
    except Exception as e:
        print(f"Error parsing triage output: {e}")
        import traceback

        traceback.print_exc()
        return None


@app.route("/api/pin/status")
def pin_status():
    return jsonify({"configured": _pin_configured(), "authenticated": bool(session.get("pin_ok"))})


@app.route("/api/pin/verify", methods=["POST"])
def pin_verify():
    pin = request.json.get("pin", "")
    if _verify_pin(pin):
        session.permanent = True
        session["pin_ok"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/")
def index():
    """Serve the dashboard HTML"""
    import time

    response = make_response(render_template("dashboard.html", cache_bust=int(time.time())))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/triage/running", methods=["GET"])
@require_pin
def get_triage_running():
    """Return whether a triage subprocess is currently running, with elapsed time."""
    locked = triage_lock.locked()
    pid = triage_process_info.get("pid") if locked else None
    start = triage_process_info.get("start_time")
    elapsed = round(time.time() - start, 1) if start and locked else None
    return jsonify({"running": locked, "pid": pid, "elapsed_seconds": elapsed, "timeout_seconds": TRIAGE_TIMEOUT})


@app.route("/api/triage", methods=["GET"])
@require_pin
def get_triage():
    """API endpoint to get current triage data"""
    next_sync = None
    if triage_cache["timestamp"]:
        last_time = datetime.fromisoformat(triage_cache["timestamp"])
        next_sync = (last_time + timedelta(minutes=5)).isoformat()

    return jsonify(
        {
            "data": triage_cache["data"],
            "timestamp": triage_cache["timestamp"],
            "next_sync": next_sync,
            "model": triage_cache.get("model"),
            "error": triage_cache.get("error"),
            "running": triage_lock.locked(),
        }
    )


@app.route("/api/triage/refresh", methods=["POST"])
@require_pin
def refresh_triage():
    """API endpoint to manually trigger triage"""
    if not triage_lock.acquire(blocking=False):
        return jsonify({"success": False, "error": "Triage already in progress"}), 409

    try:
        print(f"[AI triage] Starting with model: {triage_model}")
        triage_cache["error"] = None  # clear stale error so frontend doesn't show old failure

        try:
            unread_count = get_inbox_unread_count()
        except AuthError as e:
            msg = f"Gmail authentication failed: {e}"
            triage_cache["error"] = {"type": "auth", "message": msg}
            return jsonify({"success": False, "auth_error": True, "error": msg}), 401

        print(f"[AI triage] Unread: {unread_count}")
        if unread_count is not None and unread_count == 0:
            current_time = datetime.now(timezone.utc)
            triage_cache["timestamp"] = current_time.isoformat()
            triage_cache["next_sync"] = (current_time + timedelta(minutes=5)).isoformat()
            return jsonify(
                {
                    "success": False,
                    "skipped": True,
                    "reason": "No unread emails found",
                    "timestamp": triage_cache["timestamp"],
                    "next_sync": triage_cache["next_sync"],
                }
            )

        try:
            data = run_triage()
        except AuthError as e:
            msg = f"CLI authentication failed: {e}"
            triage_cache["error"] = {"type": "auth", "message": msg}
            return jsonify({"success": False, "auth_error": True, "error": msg}), 401

        if data:
            current_time = datetime.now(timezone.utc)
            triage_cache["data"] = data
            triage_cache["timestamp"] = current_time.isoformat()
            triage_cache["next_sync"] = (current_time + timedelta(minutes=5)).isoformat()
            triage_cache["model"] = data.get("model")
            triage_cache["last_unread_count"] = unread_count
            triage_cache["error"] = None

            summary = data["summary"]
            groups_summary = ", ".join([f"{g['name'].split('/')[-1]}({g['count']})" for g in data["labeled_groups"]])
            print(
                f"[AI triage] {summary['total']} emails processed: {summary['labeled']} labeled, {summary['archived']} archived, {summary['deleted']} deleted | Groups: {groups_summary}"
            )

            return jsonify(
                {
                    "success": True,
                    "data": data,
                    "timestamp": triage_cache["timestamp"],
                    "next_sync": triage_cache["next_sync"],
                    "model": triage_cache["model"],
                }
            )
        else:
            msg = "Triage timed out or failed. Check console output for details."
            triage_cache["error"] = {"type": "other", "message": msg}
            return jsonify({"success": False, "error": {"type": "other", "message": msg}}), 500
    finally:
        triage_lock.release()


@app.route("/api/emails/counts", methods=["GET"])
@require_pin
def get_email_counts():
    """Fetch total and unread counts for multiple labels via a single Gmail API batch request."""
    labels = request.args.getlist("label")
    if not labels:
        return jsonify({"error": "label parameter(s) required"}), 400

    results = {}
    try:
        with gmail_client_lock:
            gmail_client._ensure_authenticated()

        count_data = {name: {"total": 0, "unread": 0} for name in labels}
        error_labels = set()

        def make_callback(label_name, key):
            def callback(request_id, response, exception):
                if exception:
                    print(f"Batch error for '{label_name}' ({key}): {exception}")
                    error_labels.add(label_name)
                elif response:
                    count_data[label_name][key] = len(response.get("messages", []))

            return callback

        # Use label name in q= search to avoid labelIds quirks with system labels (e.g. External)
        # Split into batches of 5 labels (10 requests each) to avoid 429 rate limits
        BATCH_SIZE = 5
        for chunk_start in range(0, len(labels), BATCH_SIZE):
            chunk = labels[chunk_start : chunk_start + BATCH_SIZE]
            # Hold the lock for the entire build+execute cycle — httplib2 is not thread-safe
            # and releasing between build and execute allows another thread to corrupt the connection.
            try:
                with gmail_client_lock:
                    batch = gmail_client.service.new_batch_http_request()
                    for label_name in chunk:
                        q_label = label_name.replace("/", "-").lower()  # Gmail search uses dashes: triage-security
                        batch.add(
                            gmail_client.service.users()
                            .messages()
                            .list(userId="me", q=f"label:{q_label} in:inbox", maxResults=100),
                            callback=make_callback(label_name, "total"),
                        )
                        batch.add(
                            gmail_client.service.users()
                            .messages()
                            .list(userId="me", q=f"label:{q_label} in:inbox is:unread", maxResults=100),
                            callback=make_callback(label_name, "unread"),
                        )
                    _with_ssl_retry(batch.execute)
            except Exception as e:
                if not _is_retryable_gmail_error(e):
                    raise
                print(f"[counts] Batch execute error for chunk {chunk_start}: {e}")
                # Mark all labels in this chunk as errored so frontend shows them
                for label_name in chunk:
                    error_labels.add(label_name)
        results.update(count_data)
        # Mark labels that errored as null so the frontend won't hide them
        for label_name in error_labels:
            results[label_name] = None
        print(
            f"[counts] results: { {k: v for k, v in results.items() if v and (v.get('total', 0) > 0 or k in error_labels)} }"
        )

    except Exception as e:
        print(f"Error fetching counts: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify({"counts": results})


@app.route("/api/emails", methods=["GET"])
@require_pin
def get_emails_by_label():
    """Fetch emails from Gmail matching a label, returning subjects + message IDs."""
    label_name = request.args.get("label", "")
    if not label_name:
        return jsonify({"error": "label parameter required"}), 400

    try:
        with gmail_client_lock:
            gmail_client._ensure_authenticated()
        # Resolve label name to ID — if the label doesn't exist, return empty list
        try:
            with gmail_client_lock:
                label_id = gmail_client._resolve_label_name_to_id(label_name)
        except ValueError as e:
            print(f"Label not found, returning empty list for '{label_name}': {e}")
            return jsonify({"emails": []})

        # Search using label ID and INBOX, with SSL retry
        try:
            with gmail_client_lock:
                result = _with_ssl_retry(
                    lambda: (
                        gmail_client.service.users()
                        .messages()
                        .list(userId="me", labelIds=[label_id, "INBOX"], maxResults=50)
                        .execute()
                    )
                )
        except Exception as e:
            print(f"Error fetching emails for label '{label_name}': {e}")
            if _is_ssl_error(e):
                return jsonify({"emails": [], "error": "transient_ssl"}), 200
            return jsonify({"error": str(e)}), 500

        messages = result.get("messages", [])
        emails = []
        for msg in messages:
            try:
                with gmail_client_lock:
                    details = _with_ssl_retry(lambda mid=msg["id"]: gmail_client._get_email_details(mid))
            except Exception as e:
                print(f"Error fetching email details for {msg['id']}: {e}")
                continue
            if details:
                emails.append(
                    {
                        "id": details["id"],
                        "threadId": details.get("threadId", details["id"]),
                        "subject": details["subject"],
                        "sender": details["sender"],
                        "date": details["date"],
                        "snippet": details["snippet"],
                        "body": details.get("body", ""),
                        "isUnread": "UNREAD" in details.get("labelIds", []),
                    }
                )

        return jsonify({"emails": emails})
    except Exception as e:
        print(f"Error fetching emails for label '{label_name}': {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/labels/triage", methods=["GET"])
@require_pin
def get_triage_labels():
    """Return all Triage/* label names currently in Gmail."""
    try:
        with gmail_client_lock:
            gmail_client._ensure_authenticated()
            labels = gmail_client.list_labels()
        triage_labels = sorted(label["name"] for label in labels if label["name"].startswith("Triage/"))
        return jsonify({"labels": triage_labels})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/model", methods=["GET"])
@require_pin
def get_model():
    return jsonify({"model": triage_model})


@app.route("/api/emails/readstate", methods=["POST"])
@require_pin
def set_email_readstate():
    """Mark an email as read or unread."""
    data = request.get_json()
    message_id = data.get("message_id") if data else None
    unread = data.get("unread") if data else None
    if not message_id or unread is None:
        return jsonify({"error": "message_id and unread required"}), 400

    try:
        with gmail_client_lock:
            if unread:
                result = gmail_client.mark_as_unread([message_id])[0]
            else:
                result = gmail_client.mark_as_read([message_id])[0]
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/emails/archive", methods=["POST"])
@require_pin
def archive_email():
    """Archive an email by message ID."""
    data = request.get_json()
    message_id = data.get("message_id") if data else None
    if not message_id:
        return jsonify({"error": "message_id required"}), 400

    try:
        with gmail_client_lock:
            result = gmail_client.archive_email(message_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/emails/delete", methods=["POST"])
@require_pin
def delete_email():
    """Delete an email by message ID."""
    data = request.get_json()
    message_id = data.get("message_id") if data else None
    if not message_id:
        return jsonify({"error": "message_id required"}), 400

    try:
        with gmail_client_lock:
            result = gmail_client.delete_email(message_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/emails/unarchive", methods=["POST"])
@require_pin
def unarchive_email():
    """Move an archived email back to inbox."""
    data = request.get_json()
    message_id = data.get("message_id") if data else None
    if not message_id:
        return jsonify({"error": "message_id required"}), 400

    try:
        with gmail_client_lock:
            gmail_client._ensure_authenticated()
            gmail_client.service.users().messages().modify(
                userId="me", id=message_id, body={"addLabelIds": ["INBOX"]}
            ).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/config", methods=["GET"])
@require_pin
def get_config():
    return jsonify({"gmail_user": os.environ.get("GMAIL_USER", "")})


@app.route("/api/emails/undelete", methods=["POST"])
@require_pin
def undelete_email():
    """Move a trashed email back to inbox."""
    data = request.get_json()
    message_id = data.get("message_id") if data else None
    if not message_id:
        return jsonify({"error": "message_id required"}), 400

    try:
        with gmail_client_lock:
            gmail_client._ensure_authenticated()
            gmail_client.service.users().messages().modify(
                userId="me", id=message_id, body={"addLabelIds": ["INBOX"], "removeLabelIds": ["TRASH"]}
            ).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    if "--set-pin" in sys.argv:
        pin = getpass.getpass("Enter new PIN: ")
        salt = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 260000).hex()
        Path(".pincode").write_text(f"{salt}:{h}")
        print("PIN saved.")
        sys.exit(0)

    print("Starting Flask app on http://0.0.0.0:5000")
    print("Press Ctrl+C to stop")

    def run_initial_triage():
        import time

        time.sleep(1)
        with triage_lock:
            print(f"[AI triage] Starting at startup with model: {triage_model}")
            try:
                unread_count = get_inbox_unread_count()
            except AuthError as e:
                msg = f"Gmail authentication failed: {e}"
                triage_cache["error"] = {"type": "auth", "message": msg}
                triage_cache["timestamp"] = datetime.now(timezone.utc).isoformat()
                return

            print(f"[AI triage] Unread: {unread_count}")
            if unread_count == 0:
                triage_cache["timestamp"] = datetime.now(timezone.utc).isoformat()
                triage_cache["data"] = {"labeled_groups": [], "summary": {}, "auto_cleaned": {}}
                return

            try:
                data = run_triage()
            except AuthError as e:
                msg = f"CLI authentication failed: {e}"
                triage_cache["error"] = {"type": "auth", "message": msg}
                triage_cache["timestamp"] = datetime.now(timezone.utc).isoformat()
                return

            if data:
                triage_cache["data"] = data
                triage_cache["timestamp"] = datetime.now(timezone.utc).isoformat()
                triage_cache["next_sync"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
                triage_cache["model"] = data.get("model")
                triage_cache["last_unread_count"] = unread_count
                triage_cache["error"] = None

                summary = data["summary"]
                groups_summary = ", ".join(
                    [f"{g['name'].split('/')[-1]}({g['count']})" for g in data["labeled_groups"]]
                )
                print(
                    f"[AI triage] {summary['total']} emails processed: {summary['labeled']} labeled, {summary['archived']} archived, {summary['deleted']} deleted | Groups: {groups_summary}"
                )
            else:
                msg = "Triage timed out or failed at startup. Check console output for details."
                triage_cache["error"] = {"type": "other", "message": msg}
                triage_cache["timestamp"] = datetime.now(timezone.utc).isoformat()
                print(f"[AI triage] {msg}")

    triage_thread = threading.Thread(target=run_initial_triage, daemon=True)
    triage_thread.start()

    app.run(host="0.0.0.0", debug=False, port=5000, threaded=True)
