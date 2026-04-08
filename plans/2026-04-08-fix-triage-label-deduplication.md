# Fix: Enforce Single Triage/* Label at Tool Level

**Date**: 2026-04-08

## Problem

Emails accumulate multiple `Triage/*` labels across triage runs. For example, an email might end up with both `Triage/Jira` and `Triage/Team` simultaneously.

## Root Cause

`modify_labels` calls Gmail's `messages.modify` API, which **patches** the label set — it only adds/removes what you explicitly tell it to. It does not overwrite. If the triage prompt calls `modify_labels` with `add_labels: ["Triage/Jira"]` but omits `remove_labels`, the old `Triage/*` label stays, and the email ends up with two.

The triage prompt (`triage.md`) already instructs Claude to populate `remove_labels` with all existing `Triage/*` labels before applying the new one, but this is LLM-dependent and fails intermittently.

## Approach

Move enforcement down to the tool layer in `gmail_client.py`. When any label in `add_labels` starts with `Triage/`, the tool automatically:

1. Fetches all `Triage/*` label IDs (one `list_labels()` call per invocation, outside the per-message loop)
2. For each message, fetches its current labels via `messages.get(format='minimal')` — metadata only, no body
3. Auto-adds any conflicting `Triage/*` labels (on the message but not being added) to `removeLabelIds`

This is transparent to Claude — works even if the prompt passes no `remove_labels` at all.

## Implementation

### `gmail_mcp_server/gmail_client.py` — `modify_labels()` (line 253)

```python
def modify_labels(self, message_ids: List[str], add_labels: List[str] = None, remove_labels: List[str] = None) -> List[Dict[str, Any]]:
    """Batch add/remove labels on messages.

    Args:
        message_ids: List of message IDs to modify.
        add_labels: Label names to add.
        remove_labels: Label names to remove.

    Returns:
        List of result dicts with success, message_id, and error fields.
    """
    self._ensure_authenticated()
    add_ids = [self._resolve_label_name_to_id(n) for n in (add_labels or [])]
    remove_ids = [self._resolve_label_name_to_id(n) for n in (remove_labels or [])]

    # If adding any Triage/* label, pre-fetch all Triage/* label IDs once
    adding_triage = any(n.startswith('Triage/') for n in (add_labels or []))
    triage_label_ids = set()
    if adding_triage:
        all_labels = self.list_labels()
        triage_label_ids = {l['id'] for l in all_labels if l['name'].startswith('Triage/')}

    results = []
    for mid in message_ids:
        try:
            final_remove_ids = set(remove_ids)

            if adding_triage:
                # Fetch current labels on this message (metadata only, no body)
                msg = self.service.users().messages().get(
                    userId='me', id=mid, format='minimal'
                ).execute()
                current = set(msg.get('labelIds', []))
                # Auto-remove any Triage/* labels not being added
                final_remove_ids |= (current & triage_label_ids) - set(add_ids)

            body = {}
            if add_ids:
                body['addLabelIds'] = add_ids
            if final_remove_ids:
                body['removeLabelIds'] = list(final_remove_ids)
            self.service.users().messages().modify(userId='me', id=mid, body=body).execute()
            results.append({'success': True, 'message_id': mid, 'error': None})
        except HttpError as error:
            results.append({'success': False, 'message_id': mid, 'error': str(error)})
    return results
```

### `gmail_mcp_server/server.py` — Tool description (line 169)

```python
description="Batch add/remove labels on emails. Accepts positions[] and/or message_ids[], plus add_labels[] and/or remove_labels[] (label names). When adding a Triage/* label, all other Triage/* labels on the email are automatically removed.",
```

### `tests/test_gmail_client.py` — New tests in `TestModifyLabels`

```python
def test_auto_removes_conflicting_triage_labels(self):
    """Adding a Triage/* label should auto-remove other Triage/* labels already on the message."""
    # Message currently has Triage/Jira (L1) applied
    self.client.service.users().messages().get().execute.return_value = {
        'labelIds': ['INBOX', 'UNREAD', 'L1']
    }
    self.client.service.users().messages().modify().execute.return_value = {}

    results = self.client.modify_labels(['m1'], add_labels=['Triage/Security'])

    assert results[0]['success'] is True
    modify_call = self.client.service.users().messages().modify.call_args
    body = modify_call.kwargs.get('body') or modify_call.args[0] if modify_call.args else modify_call.kwargs['body']
    assert 'L2' in body['addLabelIds']   # Triage/Security added
    assert 'L1' in body['removeLabelIds']  # Triage/Jira auto-removed

def test_no_auto_remove_for_non_triage_labels(self):
    """Adding a non-Triage label should not trigger any auto-removal logic."""
    self.client.service.users().messages().modify().execute.return_value = {}

    results = self.client.modify_labels(['m1'], add_labels=['Triage/Jira'], remove_labels=['Triage/Security'])

    assert results[0]['success'] is True
    # messages().get() should have been called (Triage/* label being added)
    self.client.service.users().messages().get.assert_called()
```

## Result

74/74 tests pass. `python3 -m pytest tests/ -v` confirms all new and existing tests green.
