"""Tests for app.py — parse_triage_output, PIN helpers, SSL retry, Flask routes."""

import hashlib
import json
import secrets
import ssl
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

# ---------------------------------------------------------------------------
# parse_triage_output
# ---------------------------------------------------------------------------


class TestParseTriage:
    """Tests for the parse_triage_output function."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from app import parse_triage_output

        self.parse = parse_triage_output

    def _json_block(self, data):
        """Wrap a dict in a JSON code fence as triage.md Step 5 produces."""
        return f"```json\n{json.dumps(data)}\n```"

    def test_json_block_extraction(self):
        data = {
            "summary": {"total": 5, "labeled": 3, "archived": 1, "deleted": 1},
            "groups": [
                {"name": "Triage/Jira", "priority": "Important", "count": 3, "items": [], "description": "Jira updates"}
            ],
            "archived": ["Meeting invite"],
            "deleted": ["Status change"],
        }
        result = self.parse(self._json_block(data))
        assert result is not None
        assert result["summary"]["total"] == 5
        assert result["summary"]["labeled"] == 3
        assert len(result["labeled_groups"]) == 1
        assert result["labeled_groups"][0]["name"] == "Triage/Jira"
        assert result["auto_cleaned"]["archived"] == ["Meeting invite"]
        assert result["auto_cleaned"]["deleted"] == ["Status change"]

    def test_json_groups_sorted_by_priority(self):
        data = {
            "summary": {"total": 6, "labeled": 6, "archived": 0, "deleted": 0},
            "groups": [
                {"name": "Triage/General", "priority": "Info", "count": 2, "items": [], "description": ""},
                {"name": "Triage/Security", "priority": "Critical", "count": 1, "items": [], "description": ""},
                {"name": "Triage/Jira", "priority": "Important", "count": 3, "items": [], "description": ""},
            ],
            "archived": [],
            "deleted": [],
        }
        result = self.parse(self._json_block(data))
        priorities = [g["priority"] for g in result["labeled_groups"]]
        assert priorities == ["Critical", "Important", "Info"]

    def test_invalid_json_falls_through_to_regex(self):
        output = "```json\n{broken json\n```\nProcessed 3 emails — 2 labeled, 0 archived, 1 deleted"
        result = self.parse(output)
        assert result is not None
        assert result["summary"]["total"] == 3

    def test_regex_summary_line(self):
        output = "Processed 10 emails — 5 labeled, 2 archived, 3 deleted"
        result = self.parse(output)
        assert result["summary"]["total"] == 10
        assert result["summary"]["labeled"] == 5
        assert result["summary"]["archived"] == 2
        assert result["summary"]["deleted"] == 3

    def test_regex_labeled_groups_parsing(self):
        output = (
            "┌─ Triage/Jira ──── Important · 3 emails\n"
            "│ Updates from the team\n"
            "│ · ACM-123 fix\n"
            "│ · ACM-456 review\n"
            "└─────────────────────────────────────────\n"
        )
        result = self.parse(output)
        assert len(result["labeled_groups"]) == 1
        group = result["labeled_groups"][0]
        assert group["name"] == "Triage/Jira"
        assert group["priority"] == "Important"
        assert group["count"] == 3
        assert "ACM-123 fix" in group["items"]

    def test_regex_auto_cleaned_archived_and_deleted(self):
        output = (
            "AUTO-CLEANED\n"
            "Archived (2)\n"
            "· Meeting invite from Alice\n"
            "· Calendar event: Team sync\n"
            "Deleted (1)\n"
            "· ACM-100 Status changed\n"
            "QUICK LINKS\n"
        )
        result = self.parse(output)
        assert "Meeting invite from Alice" in result["auto_cleaned"]["archived"]
        assert "Calendar event: Team sync" in result["auto_cleaned"]["archived"]
        assert "ACM-100 Status changed" in result["auto_cleaned"]["deleted"]

    def test_regex_quick_links_fills_missing_groups(self):
        output = "QUICK LINKS\nTriage/Newsletter  (4) https://mail.google.com/\n"
        result = self.parse(output)
        names = [g["name"] for g in result["labeled_groups"]]
        assert "Triage/Newsletter" in names

    def test_priority_assignment_by_name_keywords(self):
        output = (
            "┌─ Triage/Security ──── Info · 1 emails\n"
            "└────────────────────────────────────────\n"
            "┌─ Triage/Jira ──── Info · 2 emails\n"
            "└────────────────────────────────────────\n"
        )
        result = self.parse(output)
        groups = {g["name"]: g for g in result["labeled_groups"]}
        assert groups["Triage/Security"]["priority"] == "Critical"
        assert groups["Triage/Jira"]["priority"] == "Important"

    def test_ansi_codes_stripped(self):
        output = "\x1b[32mProcessed 2 emails — 1 labeled, 0 archived, 1 deleted\x1b[0m"
        result = self.parse(output)
        assert result["summary"]["total"] == 2

    def test_empty_output_returns_none(self):
        assert self.parse("") is None or self.parse("")["summary"]["total"] == 0

    def test_exception_returns_none(self):
        # Passing a non-string should not crash — returns None
        result = self.parse(None)
        assert result is None

    def test_json_block_with_empty_groups(self):
        data = {
            "summary": {"total": 0, "labeled": 0, "archived": 0, "deleted": 0},
            "groups": [],
            "archived": [],
            "deleted": [],
        }
        result = self.parse(self._json_block(data))
        assert result is not None
        assert result["labeled_groups"] == []


# ---------------------------------------------------------------------------
# PIN helpers
# ---------------------------------------------------------------------------


class TestPinHelpers:
    """Tests for _verify_pin, _pin_configured, and require_pin."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        # Point Path(".pincode") at a temp dir
        monkeypatch.chdir(tmp_path)
        import app as app_module

        self.app_module = app_module
        self._verify_pin = app_module._verify_pin
        self._pin_configured = app_module._pin_configured

    def _write_pincode(self, pin, path=None):
        salt = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 260000).hex()
        Path(".pincode").write_text(f"{salt}:{h}")

    def test_no_pincode_file_not_configured(self):
        assert not self._pin_configured()

    def test_no_pincode_verify_returns_true(self):
        assert self._verify_pin("anything")

    def test_valid_pin_returns_true(self):
        self._write_pincode("secret123")
        assert self._pin_configured()
        assert self._verify_pin("secret123")

    def test_wrong_pin_returns_false(self):
        self._write_pincode("correct")
        assert not self._verify_pin("wrong")

    def test_require_pin_blocks_without_session(self):
        app = self.app_module.app
        app.config["TESTING"] = True
        self._write_pincode("1234")
        with app.test_client() as client:
            resp = client.get("/api/triage")
            assert resp.status_code == 401
            assert resp.get_json()["error"] == "pin_required"

    def test_require_pin_passes_with_valid_session(self):
        app = self.app_module.app
        app.config["TESTING"] = True
        self._write_pincode("1234")
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["pin_ok"] = True
            resp = client.get("/api/triage")
            # Should not be 401 (may be 200 with no data, or redirect — just not auth error)
            assert resp.status_code != 401


# ---------------------------------------------------------------------------
# SSL retry helpers
# ---------------------------------------------------------------------------


class TestSSLRetryHelpers:
    """Tests for _is_ssl_error, _is_retryable_gmail_error, _with_ssl_retry."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from app import AuthError, _is_retryable_gmail_error, _is_ssl_error, _with_ssl_retry

        self._is_ssl_error = _is_ssl_error
        self._is_retryable = _is_retryable_gmail_error
        self._with_retry = _with_ssl_retry
        self.AuthError = AuthError

    def test_ssl_error_detected_by_type(self):
        exc = ssl.SSLError("certificate verify failed")
        assert self._is_ssl_error(exc)

    def test_non_ssl_error_not_detected(self):
        exc = ValueError("not an ssl error")
        assert not self._is_ssl_error(exc)

    def test_ssl_in_message_detected(self):
        exc = OSError("[ssl: certificate_verify_failed]")
        assert self._is_ssl_error(exc)

    def test_retryable_includes_ssl(self):
        exc = ssl.SSLError("handshake failure")
        assert self._is_retryable(exc)

    def test_retryable_http_429(self):
        resp = MagicMock()
        resp.status = 429
        exc = HttpError(resp=resp, content=b"rate limited")
        assert self._is_retryable(exc)

    def test_retryable_http_500(self):
        resp = MagicMock()
        resp.status = 500
        exc = HttpError(resp=resp, content=b"server error")
        assert self._is_retryable(exc)

    def test_non_retryable_http_400(self):
        resp = MagicMock()
        resp.status = 400
        exc = HttpError(resp=resp, content=b"bad request")
        assert not self._is_retryable(exc)

    def test_with_retry_succeeds_first_try(self):
        fn = MagicMock(return_value="ok")
        result = self._with_retry(fn, retries=3, delay=0)
        assert result == "ok"
        assert fn.call_count == 1

    def test_with_retry_retries_on_ssl(self):
        call_count = {"n": 0}

        def fn():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ssl.SSLError("transient")
            return "recovered"

        result = self._with_retry(fn, retries=3, delay=0)
        assert result == "recovered"
        assert call_count["n"] == 3

    def test_with_retry_raises_non_retryable(self):
        fn = MagicMock(side_effect=ValueError("permanent"))
        with pytest.raises(ValueError, match="permanent"):
            self._with_retry(fn, retries=3, delay=0)
        assert fn.call_count == 1


# ---------------------------------------------------------------------------
# Flask routes (key routes via test client)
# ---------------------------------------------------------------------------


class TestFlaskRoutes:
    """Tests for key Flask routes using the Flask test client."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import app as app_module

        app_module.app.config["TESTING"] = True
        # Reset triage cache for each test
        app_module.triage_cache.update(
            {
                "data": None,
                "timestamp": None,
                "next_sync": None,
                "model": None,
                "last_unread_count": None,
                "error": None,
            }
        )
        self.client = app_module.app.test_client()
        self.app_module = app_module

    def test_index_returns_html(self):
        resp = self.client.get("/")
        assert resp.status_code == 200
        assert b"html" in resp.data.lower()

    def test_pin_status_no_pin_file(self):
        resp = self.client.get("/api/pin/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["configured"] is False
        assert data["authenticated"] is False

    def test_pin_verify_no_pin_configured_succeeds(self):
        resp = self.client.post("/api/pin/verify", json={"pin": "anything"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_pin_verify_correct_pin(self, tmp_path):
        pin = "mypin"
        salt = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 260000).hex()
        Path(".pincode").write_text(f"{salt}:{h}")
        resp = self.client.post("/api/pin/verify", json={"pin": pin})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_pin_verify_wrong_pin(self, tmp_path):
        salt = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", b"correct", salt.encode(), 260000).hex()
        Path(".pincode").write_text(f"{salt}:{h}")
        resp = self.client.post("/api/pin/verify", json={"pin": "wrong"})
        assert resp.status_code == 401
        assert resp.get_json()["ok"] is False

    def test_get_triage_returns_empty_data_without_pin(self):
        # No PIN file → require_pin passes through
        resp = self.client.get("/api/triage")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "data" in data
        assert data["data"] is None

    def test_get_triage_returns_cached_data(self):
        import app as app_module

        app_module.triage_cache["data"] = {"labeled_groups": [], "summary": {"total": 5}}
        app_module.triage_cache["timestamp"] = "2026-06-01T12:00:00"
        resp = self.client.get("/api/triage")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["summary"]["total"] == 5

    def test_logout_redirects(self):
        resp = self.client.get("/logout")
        assert resp.status_code in (301, 302)

    def test_triage_running_endpoint(self):
        resp = self.client.get("/api/triage/running")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "running" in data
        assert data["running"] is False
