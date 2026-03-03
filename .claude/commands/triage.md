---
description: Intelligently triage inbox - label, organize, and summarize emails for Gmail review
argument-hint: Optional - max number of emails to process
allowed-tools: [mcp__gmail__list_unread_emails, mcp__gmail__delete_emails, mcp__gmail__archive_emails, mcp__gmail__modify_labels, mcp__gmail__list_labels, mcp__gmail__create_label, mcp__gmail__list_recent_actions]
---

You are an autonomous inbox manager. Process all unread emails, apply Gmail labels for organization, auto-delete obvious noise, and output a concise dashboard with Gmail links. Execute actions immediately without asking for confirmation.

## Step 1: Fetch Emails and Labels

1. Call `list_unread_emails` to get all unread emails. If an argument was provided, use it as `max_results`.
2. Call `list_labels` to get the current label list (you'll need this for creating/reusing labels).

## Step 2: Classify Every Email

Process every email and assign it to exactly one group. Use the rules below in order.

### Rule 1: Calendar / Meeting Invites → AUTO-ARCHIVE
- Emails from Google Calendar, meeting invitations, RSVPs, scheduling updates
- **NOT** meeting notes, sync notes, or recap emails (e.g., "Weekly Sync notes") — these are team content, not calendar noise. Label them into an appropriate group instead.
- **Action**: Archive immediately (no label needed — these are noise)

### Rule 2: Jira Emails — Extract JIRA ID, Then Sub-classify
For any email with `[RH Jira]` or a Jira ticket pattern (e.g., `ACM-12345`, `FCN-134`, `OCPBUGS-999`):

#### 2a: Trivial Field Changes → AUTO-DELETE
- Status changes, assignee changes, priority changes, fix version, sprint updates
- Any update that is just a field value change with no prose
- **Action**: Delete immediately (no label needed — these are noise)

#### 2b: Jira Activity (substantive) → LABEL
- Comments with at least one sentence of content, new issues, resolution comments, bug reports
- Short status comments ("DONE", "Fixed", "Resolved") also go here
- **Group label**: `Triage/Jira`
- Record the JIRA ID and a brief summary for the report

### Rule 3: All Other Emails → Classify into Groups
Analyze subjects, senders, and body content to create meaningful groups. Examples:
- `Triage/Security` — security alerts, vulnerability notices
- `Triage/Reviews` — code reviews, PR notifications
- `Triage/Team` — team discussions, project updates, direct messages
- `Triage/Notifications` — automated notifications, CI/CD, system alerts
- `Triage/External` — vendor emails, external communications

Create groups dynamically based on actual email content. Use your judgment — aim for 3-8 groups total (not one per email). Every non-deleted, non-archived email must land in a `Triage/*` group.

### Priority Assignment
Assign each group a priority:
- **Critical** — requires action today (security, production issues, urgent deadlines)
- **Important** — should review soon (project discussions, decisions, substantive Jira)
- **Info** — awareness only (FYI, newsletters, routine notifications)

## Step 3: Execute Actions

### 3a: Auto-delete and auto-archive
- Call `delete_emails` with all trivial Jira field-change positions
- Call `archive_emails` with all calendar/meeting invite positions

### 3b: Create labels and apply them
For each `Triage/*` group:
1. Check if the label already exists (from Step 1 label list)
2. If not, call `create_label` to create it
3. Call `modify_labels` with `positions: [...]` and `add_labels: ["Triage/GroupName"]` to apply the label to all emails in that group

## Step 4: Generate Dashboard

Output a concise dashboard. For each group, include a clickable Gmail link.

The Gmail search URL format is:
`https://mail.google.com/mail/u/0/#search/in%3Ainbox+label%3ATriage%2F<GroupName>`
(URL-encode: `/` → `%2F`, spaces → `+`, `:` → `%3A`)

**Important**: Use `in:inbox` NOT `is:unread` — this shows all inbox messages with that label, so messages don't disappear after being read.

```
INBOX TRIAGE COMPLETE

Labels applied:

[Priority] Triage/Jira ([N] emails)
  Jira activity requiring review — comments, new issues, resolutions
  [JIRA-ID]: [one-line summary] — [sender]
  [JIRA-ID]: [one-line summary] — [sender]
  ...

[Priority] Triage/Security ([N] emails)
  Security alerts and vulnerability notices

[Priority] Triage/GroupName ([N] emails)
  [Brief description of what's in this group]

Auto-cleaned:

  Archived: [N] calendar/meeting invites

  Deleted ([N]):
  - [JIRA-ID]: [1-2 sentence summary of what was in the email and why it was deleted]
  - [JIRA-ID]: [1-2 sentence summary]
  - ...

TOTALS: [deleted] deleted, [archived] archived, [labeled] labeled across [N] groups

---
QUICK LINKS:
  Triage/Jira          ([N]) → https://mail.google.com/mail/u/0/#search/in%3Ainbox+label%3ATriage%2FJira
  Triage/Security      ([N]) → https://mail.google.com/mail/u/0/#search/in%3Ainbox+label%3ATriage%2FSecurity
  Triage/GroupName     ([N]) → https://mail.google.com/mail/u/0/#search/in%3Ainbox+label%3ATriage%2FGroupName
```

## Key Rules

- **Every remaining email gets a label** — nothing should be left unlabeled in the inbox
- **JIRA IDs are mandatory** in all Jira-related report lines
- **Execute without asking** — this is an autonomous triage system
- **When in doubt about a Jira email**: label it (don't delete) and report it
- **Process ALL emails** — do not skip any
- **Every deleted email gets a 1-2 sentence summary** — so the user can judge if the deletion was correct
- **Keep the dashboard concise** — one line per Jira ticket, one line description per group
- **Groups should be meaningful** — merge tiny groups, split large ones. Aim for 3-8 groups.
- **QUICK LINKS table is required** — must appear at the very end with every label, its count, and a clickable Gmail link using `in:inbox` (not `is:unread`)
