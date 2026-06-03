# Gmail Triage Dashboard

A clean, modern web interface for intelligent Gmail inbox management. The dashboard automatically triages emails every 15 minutes, organizing them into meaningful groups and auto-cleaning obvious noise.

## Features

- **Auto-Triage Every 15 Minutes**: Automatically runs the triage process with configurable intervals
- **Auto-Clean Section**: Shows deleted trivial emails and archived calendar invites with summaries
- **Labeled Groups**: Displays organized email groups sorted by priority (Critical → Important → Info)
- **Quick Links**: Navigate to Gmail search results for each group, formatted to show summaries
- **Preview Pane**: View Gmail search results in an embedded iframe without leaving the dashboard
- **Real-time Stats**: Header shows total emails, last sync time, and next sync countdown
- **Responsive Design**: Works on desktop, tablet, and mobile devices

## Getting Started

### Installation

1. Install Flask dependency:
```bash
pip3 install flask
```

Or use the requirements file:
```bash
pip3 install -r requirements.txt
```

### Running the Dashboard

Start the web server:
```bash
python3 app.py
```

Or use the Makefile:
```bash
make dashboard
```

The dashboard will be available at `http://localhost:5000`

### Using with Make

```bash
# Run the triage dashboard
make dashboard

# Run traditional CLI triage
make triage

# Watch triage (runs every 10 seconds in CLI mode)
make watch

# Kill the triage dashboard
make kill-dashboard
```

## How It Works

### Dashboard Flow

1. **Initial Load**: On startup, the app runs an immediate triage via the Claude CLI
2. **Display**: Results are parsed and displayed in the web UI with organized sections
3. **Auto-Refresh**: Every 15 minutes, the dashboard automatically refreshes
4. **Manual Refresh**: Click "Refresh Now" to manually trigger triage immediately

### Triage Process

The dashboard uses the `/triage` Claude command which:
- Lists all unread emails
- Classifies each email (Calendar → Jira → General categories)
- Auto-deletes trivial field changes and noise
- Auto-archives calendar invitations
- Applies meaningful labels to all remaining emails

See `commands/triage.md` for complete classification rules.

### Data Format

The backend parses triage output and returns JSON with:
- **summary**: Total, labeled, archived, deleted counts
- **auto_cleaned**: Items that were automatically processed
- **labeled_groups**: Array of groups with name, priority, count, items, and description
- **raw_output**: Full triage dashboard text for debugging

## UI Components

### Header Bar
- Title and logo
- Last sync timestamp
- Total email count
- Next sync countdown
- Manual refresh button

### Top Cards (Side by Side)
- **Auto-Cleaned**: Shows deletion and archival counts with item list
- **Archived**: Summary of auto-archived items (calendar invites, etc.)

### Quick Links Section
- Grid of clickable links for each group
- Shows group name and email count
- Click to preview in iframe below

### Split View (Bottom)
- **Left**: Labeled Groups
  - Groups sorted by priority (Critical → Important → Info)
  - Click group header to expand/collapse
  - Shows description and item summaries

- **Right**: Preview Pane
  - Embedded iframe showing Gmail search results
  - Sandboxed to prevent full Gmail navigation
  - Displays the search query for selected group

## API Endpoints

All endpoints except the PIN/auth group require a valid session (set by `POST /api/pin/verify`). Unauthenticated requests return `401 {"error": "pin_required"}` when a PIN is configured.

### PIN Authentication

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/pin/status` | Returns `{"configured": bool, "authenticated": bool}` |
| `POST` | `/api/pin/verify` | Body: `{"pin": "1234"}` → `{"ok": true}` or 401 |
| `GET` | `/logout` | Clears session, redirects to `/` |

### Triage

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/triage` | Returns cached triage data + timestamps |
| `POST` | `/api/triage/refresh` | Manually triggers a new triage run. Returns 409 if already running. |
| `GET` | `/api/triage/running` | Returns `{"running": bool, "pid": int, "elapsed_seconds": float, "timeout_seconds": int}` |

#### GET /api/triage response shape

```json
{
  "data": {
    "summary": {"total": 10, "labeled": 7, "archived": 2, "deleted": 1},
    "auto_cleaned": {"archived": ["Meeting invite"], "deleted": ["Status change"]},
    "labeled_groups": [{"name": "Triage/Jira", "priority": "Important", "count": 3, "items": [...], "description": "..."}]
  },
  "timestamp": "2026-03-06T10:30:00",
  "next_sync": "2026-03-06T10:45:00",
  "model": "gemini-3.5-flash",
  "error": null
}
```

### Email Operations

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/emails` | Query: `?label=Triage/Foo` — returns emails matching label |
| `GET` | `/api/emails/counts` | Query: `?label=Triage/Foo&label=Triage/Bar` — batch unread/total counts |
| `POST` | `/api/emails/readstate` | Body: `{"message_id": "...", "unread": true}` — mark read or unread |
| `POST` | `/api/emails/archive` | Body: `{"message_id": "..."}` — archive email |
| `POST` | `/api/emails/delete` | Body: `{"message_id": "..."}` — move to trash |
| `POST` | `/api/emails/unarchive` | Body: `{"message_id": "..."}` — move archived back to inbox |
| `POST` | `/api/emails/undelete` | Body: `{"message_id": "..."}` — move trashed back to inbox |

### Labels & Config

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/labels/triage` | Returns all `Triage/*` label names |
| `GET` | `/api/model` | Returns current triage model name |
| `GET` | `/api/config` | Returns `{"gmail_user": "..."}` |

## Configuration

### Refresh Interval
Edit `static/dashboard.js` and change `REFRESH_INTERVAL`:
```javascript
const REFRESH_INTERVAL = 15 * 60 * 1000; // 15 minutes in milliseconds
```

### Flask Settings
Edit `app.py` to configure:
- Port: Change `app.run(debug=True, port=5000)`
- Debug mode: Set `debug=False` for production

## Customization

### Styling
All CSS is in `static/style.css`. The design uses CSS custom properties:
- `--primary`, `--secondary`, `--accent` for colors
- `--light`, `--lighter` for backgrounds
- Easily change colors by modifying `:root` variables

### Group Sorting
By default, groups are sorted by priority. To change sorting, modify `updateGroupsSection()` in `static/dashboard.js`.

### Preview Pane Restrictions
The iframe uses sandbox attributes to prevent dangerous navigation:
```html
sandbox="allow-same-origin allow-scripts allow-popups allow-popups-to-escape-sandbox"
```

To restrict further, remove `allow-scripts` or `allow-popups`. See `static/dashboard.js` in `openPreview()`.

## Troubleshooting

### Dashboard shows "Loading..." indefinitely
- Check browser console for errors (F12)
- Ensure Claude CLI is installed and configured
- Verify credentials.json and token.json exist
- Try manual refresh button

### Triage not running
- Ensure `commands/triage.md` exists
- Run `make triage` directly to test the CLI command
- Check that Gmail API is enabled and credentials are valid

### Preview iframe is blank
- This is normal - Gmail may block embedding in iframes
- The iframe is sandboxed by design to prevent accidental actions
- Click the quick link to open in Gmail directly instead

### Styling looks broken
- Clear browser cache (Ctrl+Shift+Delete)
- Check that `static/style.css` is being loaded (Network tab)
- Ensure Flask is serving static files correctly

## Production Deployment

For production use:

1. **Disable debug mode** in `app.py`:
   ```python
   app.run(debug=False, port=5000)
   ```

2. **Use a production server** like Gunicorn:
   ```bash
   pip install gunicorn
   gunicorn -w 4 -b 0.0.0.0:5000 app:app
   ```

3. **Set up reverse proxy** (nginx/Apache) for SSL and caching

4. **Secure credentials**:
   - Keep `credentials.json` and `token.json` in secure location
   - Use environment variables for sensitive config
   - Never commit credentials to git (checked in .gitignore)

## Architecture

```
Gmail Triage Dashboard
├── app.py                          # Flask server & triage runner
├── templates/dashboard.html        # Single-page web UI
├── static/
│   ├── style.css                  # Responsive styling
│   └── dashboard.js               # Frontend logic & API calls
└── commands/triage.md      # Triage classification rules
```

The architecture is intentionally simple:
- No database needed - triage results are ephemeral
- Single Flask app handles both serving UI and API
- Triage runs via Claude CLI subprocess
- All rendering happens client-side for better responsiveness
