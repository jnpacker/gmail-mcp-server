#!/bin/sh
set -e

# Fix the hardcoded host path in .mcp.json → container WORKDIR
if [ -f /app/.mcp.json ]; then
    jq '.mcpServers.gmail.cwd = "/app"' /app/.mcp.json > /tmp/mcp.tmp \
        && mv /tmp/mcp.tmp /app/.mcp.json
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

exec python3 app.py
