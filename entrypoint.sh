#!/bin/sh
set -e

PYTHON_BIN="$(which python3)"

# Fix hardcoded host paths → container WORKDIR (/app)
if [ -f /app/.mcp.json ]; then
    jq --arg py "$PYTHON_BIN" \
        '.mcpServers.gmail.cwd = "/app" | .mcpServers.gmail.command = $py' \
        /app/.mcp.json > /tmp/mcp.tmp \
        && mv /tmp/mcp.tmp /app/.mcp.json
fi

# Fix python path and cwd in project-level Gemini settings (overrides global ~/.gemini/settings.json)
if [ -f /app/.gemini/settings.json ]; then
    jq --arg py "$PYTHON_BIN" \
        '.mcpServers.gmail.command = $py | .mcpServers.gmail.cwd = "/app"' \
        /app/.gemini/settings.json > /tmp/gemini.tmp \
        && mv /tmp/gemini.tmp /app/.gemini/settings.json
fi

# Stage token.json from read-only secret mount to writable app dir.
# GmailClient writes refreshed access tokens back to this path.
# On container restart, a fresh copy from the mount (which retains the
# long-lived refresh token) is staged again.
if [ -f /secrets/token.json ]; then
    cp /secrets/token.json /app/token.json
fi

# Stage .pincode from read-only secret mount to writable app dir.
# app.py reads this file at runtime for PIN verification.
if [ -f /secrets/.pincode ]; then
    cp /secrets/.pincode /app/.pincode
fi

# credentials.json is mounted directly at /app/credentials.json (read-only is fine)

# All Gemini CLI config lives in /root/.gemini (global user dir — where Gemini always looks)
# Generate Gemini .toml commands from Claude .md sources (single source of truth)
mkdir -p /root/.gemini/commands
python3 - << 'PYEOF'
import re, os

for cmd in ['triage', 'emails']:
    src = f'/app/commands/{cmd}.md'
    dst = f'/root/.gemini/commands/{cmd}.toml'
    with open(src) as f:
        content = f.read()
    m = re.match(r'^---\n(.*?)\n---\n(.*)', content, re.DOTALL)
    frontmatter, body = m.group(1), m.group(2).strip()
    desc = re.search(r'^description:\s*(.+)$', frontmatter, re.MULTILINE).group(1).strip()
    with open(dst, 'w') as f:
        f.write(f'description="{desc}"\nprompt="""\n{body}\n"""\n')
PYEOF

mkdir -p /root/.gemini/policies
cp /app/.gemini/policies/*.toml /root/.gemini/policies/

cat > /root/.gemini/settings.json << GEMINI_EOF
{
  "selectedAuthType": "gemini-api-key",
  "model": {
    "disableLoopDetection": true
  },
  "security": {
    "auth": {
      "selectedType": "gemini-api-key"
    }
  },
  "mcpServers": {
    "gmail": {
      "command": "${PYTHON_BIN}",
      "args": ["-m", "gmail_mcp_server.server"],
      "cwd": "/app"
    }
  }
}
GEMINI_EOF

cat > /root/.gemini/trustedFolders.json << 'TRUSTED_EOF'
{
  "/root": "TRUST_FOLDER",
  "/app": "TRUST_FOLDER"
}
TRUSTED_EOF

exec python3 -u app.py
