# Gmail MCP Server — LXC Setup

An LXC container is a lightweight alternative to Kubernetes for self-hosted deployments. This guide covers container creation, dependency installation, secret management, and running the dashboard as a systemd service.

## Prerequisites

- Proxmox host (or any system with `lxc-*` tools)
- A Google Cloud project with the Gmail API enabled
- `credentials.json` downloaded from Google Cloud Console (OAuth 2.0 Desktop client)
- An Anthropic API key **or** access to Claude via Vertex AI (see [Vertex AI](#vertex-ai-instead-of-anthropic-api-key))

---

## 1. Create the LXC Container

### Proxmox (via UI or CLI)

```sh
# Example: Debian 12, 2 CPU, 2 GB RAM, 8 GB disk
pct create 200 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname gmail-mcp \
  --cores 2 \
  --memory 2048 \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 \
  --start 1
```

Then attach a shell:

```sh
pct enter 200
```

---

## 2. Install Dependencies

```sh
apt-get update && apt-get install -y \
    python3 python3-pip python3-venv \
    curl ca-certificates git jq

# Node.js 22
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs

# Claude Code CLI
npm install -g @anthropic-ai/claude-code
```

---

## 3. Create an App User and Clone the Repo

Running as root inside an LXC is common but an unprivileged user is safer:

```sh
useradd -m -s /bin/bash gmailmcp
su - gmailmcp

git clone https://github.com/YOUR_ORG/gmail-mcp-server.git
cd gmail-mcp-server

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Note the install path — you will need it in later steps (e.g. `/home/gmailmcp/gmail-mcp-server`).

---

## 4. Configure Secrets

Unlike Kubernetes, secrets are plain files in the app directory. Lock down permissions so only the app user can read them.

### 4a. Copy `credentials.json` from the host

From your **host machine** (outside the LXC):

```sh
# Proxmox: copy via pct push
pct push 200 ./credentials.json /home/gmailmcp/gmail-mcp-server/credentials.json \
    --user gmailmcp --group gmailmcp

# Or via scp if the container has SSH
scp credentials.json gmailmcp@<container-ip>:~/gmail-mcp-server/
```

Then inside the container:

```sh
chmod 600 /home/gmailmcp/gmail-mcp-server/credentials.json
```

### 4b. Set the API credentials

**Anthropic API key:**

```sh
cat > /home/gmailmcp/gmail-mcp-server/.env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
EOF
chmod 600 /home/gmailmcp/gmail-mcp-server/.env
```

**Using Vertex AI instead?** See the [Vertex AI section](#vertex-ai-instead-of-anthropic-api-key) for what to put in `.env` and how to supply GCP credentials.

---

## 5. Run OAuth Authentication

Gmail OAuth requires a browser redirect. Because the LXC has no browser, do this on your **host machine** first, then copy the resulting `token.json` into the container.

### Option A — Auth on the host, copy token

```sh
# On your host (with credentials.json already present)
make auth
# A browser window opens → sign in → token.json is written

# Copy into the container
pct push 200 ./token.json /home/gmailmcp/gmail-mcp-server/token.json \
    --user gmailmcp --group gmailmcp
```

Inside the container:

```sh
chmod 600 /home/gmailmcp/gmail-mcp-server/token.json
```

### Option B — Auth inside the container (port-forward)

If you can forward a port from the container to your desktop:

```sh
# Inside container — run auth, it will print a localhost URL
source .venv/bin/activate
make auth
# Open the printed URL in a browser on your desktop
```

---

## 6. Fix `.mcp.json`

The repo's `.mcp.json` contains a hardcoded `cwd` pointing to the original developer's path. Update it to match the container install path:

```sh
cd /home/gmailmcp/gmail-mcp-server
jq '.mcpServers.gmail.cwd = "/home/gmailmcp/gmail-mcp-server"' .mcp.json > .mcp.tmp \
    && mv .mcp.tmp .mcp.json
```

---

## 7. Create a systemd Service

This runs the dashboard automatically on boot and restarts it on crash.

```sh
# Exit back to root to write the unit file
exit   # back to root shell

cat > /etc/systemd/system/gmail-mcp.service <<'EOF'
[Unit]
Description=Gmail MCP Dashboard
After=network.target

[Service]
Type=simple
User=gmailmcp
WorkingDirectory=/home/gmailmcp/gmail-mcp-server
EnvironmentFile=/home/gmailmcp/gmail-mcp-server/.env
ExecStart=/home/gmailmcp/gmail-mcp-server/.venv/bin/python3 app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now gmail-mcp
```

Check status:

```sh
systemctl status gmail-mcp
journalctl -u gmail-mcp -f
```

The dashboard will be available at `http://<container-ip>:5000`.

---

## 8. Optional: Set a Dashboard PIN

```sh
su - gmailmcp
cd gmail-mcp-server
source .venv/bin/activate
make set-pin
```

Restart the service after setting the PIN:

```sh
systemctl restart gmail-mcp
```

---

## Re-auth (if the refresh token is ever revoked)

```sh
# On your host
make auth   # regenerates token.json

pct push 200 ./token.json /home/gmailmcp/gmail-mcp-server/token.json \
    --user gmailmcp --group gmailmcp

# Inside container
chmod 600 /home/gmailmcp/gmail-mcp-server/token.json
systemctl restart gmail-mcp
```

---

---

## Vertex AI (instead of Anthropic API key)

Use these steps if Claude is accessed through Google Cloud Vertex AI (`CLAUDE_CODE_USE_VERTEX=1`) rather than a direct Anthropic API key.

### 1. Obtain GCP credentials

No `gcloud` CLI is required inside the container — only the credentials file.

**Option A — User credentials (good for development):**

On your **host machine**:

```sh
gcloud auth application-default login
# Creates: ~/.config/gcloud/application_default_credentials.json
```

Copy into the container:

```sh
pct push 200 ~/.config/gcloud/application_default_credentials.json \
    /home/gmailmcp/.config/gcloud/application_default_credentials.json \
    --user gmailmcp --group gmailmcp

# Inside container
mkdir -p /home/gmailmcp/.config/gcloud
chmod 700 /home/gmailmcp/.config/gcloud
chmod 600 /home/gmailmcp/.config/gcloud/application_default_credentials.json
```

User ADC tokens expire after ~1 hour. For always-on services, use Option B.

**Option B — Service account key (recommended):**

On your **host machine**:

```sh
gcloud iam service-accounts create gmail-mcp \
  --display-name="Gmail MCP Dashboard"

gcloud projects add-iam-policy-binding MY_PROJECT \
  --member="serviceAccount:gmail-mcp@MY_PROJECT.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

gcloud iam service-accounts keys create ~/gmail-mcp-sa-key.json \
  --iam-account=gmail-mcp@MY_PROJECT.iam.gserviceaccount.com
```

Copy into the container:

```sh
pct push 200 ~/gmail-mcp-sa-key.json \
    /home/gmailmcp/.config/gcloud/application_default_credentials.json \
    --user gmailmcp --group gmailmcp

# Inside container
chmod 600 /home/gmailmcp/.config/gcloud/application_default_credentials.json
```

Service account keys do not expire.

### 2. Write the `.env` file for Vertex AI

Replace the `ANTHROPIC_API_KEY` line from step 4b with:

```sh
cat > /home/gmailmcp/gmail-mcp-server/.env <<'EOF'
CLAUDE_CODE_USE_VERTEX=1
ANTHROPIC_VERTEX_PROJECT_ID=my-gcp-project
GOOGLE_APPLICATION_CREDENTIALS=/home/gmailmcp/.config/gcloud/application_default_credentials.json
EOF
chmod 600 /home/gmailmcp/gmail-mcp-server/.env
```

`CLOUD_ML_REGION` defaults to `us-east5`; add it to `.env` if you use a different region.

The systemd service defined in step 7 picks up `.env` via `EnvironmentFile=` automatically — no other changes needed.

---

## Quick Reference

| What | Where |
|---|---|
| App directory | `/home/gmailmcp/gmail-mcp-server/` |
| OAuth credentials | `credentials.json` (600, gmailmcp) |
| OAuth token | `token.json` (600, gmailmcp) |
| API key (Anthropic) | `.env` → `ANTHROPIC_API_KEY` (600, gmailmcp) |
| API key (Vertex AI) | `.env` → `CLAUDE_CODE_USE_VERTEX`, `ANTHROPIC_VERTEX_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS` |
| GCP ADC / SA key | `/home/gmailmcp/.config/gcloud/application_default_credentials.json` (600, gmailmcp) |
| PIN hash | `.pincode` (written by `make set-pin`) |
| Flask secret | `.flask_secret` (auto-generated on first run) |
| Logs | `journalctl -u gmail-mcp` |
| Dashboard | `http://<container-ip>:5000` |
