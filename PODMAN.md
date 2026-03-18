# Gmail MCP Server — Podman Setup

Run the dashboard as a Podman container on any Linux host (including a Proxmox VM or privileged LXC). Podman is a drop-in Docker replacement — all commands below work with `docker` as well.

## Prerequisites

- Podman installed on the host
- `credentials.json` from Google Cloud Console (OAuth 2.0 Desktop client)
- `token.json` obtained by running `make auth` on a machine with a browser (see step 3)
- An Anthropic API key **or** access to Claude via Vertex AI (see [Vertex AI](#vertex-ai-instead-of-anthropic-api-key))

---

## 1. Build the Image

```sh
cd gmail-mcp-server
podman build -t gmail-dashboard .
```

---

## 2. Prepare Secret Files

Create a directory to hold secrets outside the repo:

```sh
mkdir -p ~/.gmail-mcp-secrets
cp credentials.json ~/.gmail-mcp-secrets/
chmod 600 ~/.gmail-mcp-secrets/credentials.json
```

Create an env file for the API key:

```sh
cat > ~/.gmail-mcp-secrets/env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
EOF
chmod 600 ~/.gmail-mcp-secrets/env
```

> **Using Vertex AI instead?** Skip the API key above and see the [Vertex AI section](#vertex-ai-instead-of-anthropic-api-key) before continuing to step 3.

---

## 3. Run OAuth Authentication

Gmail OAuth requires a browser redirect. Do this on a machine with a browser before running the container.

```sh
# In the repo directory with credentials.json present
make auth
# A browser window opens → sign in → token.json is written to the repo root

cp token.json ~/.gmail-mcp-secrets/
chmod 600 ~/.gmail-mcp-secrets/token.json
```

If authenticating on a headless host, forward the port or copy `credentials.json` to a desktop machine, run `make auth` there, then copy `token.json` back.

---

## 4. Run the Container

```sh
podman run -d \
  --name gmail-dashboard \
  --restart unless-stopped \
  --env-file ~/.gmail-mcp-secrets/env \
  -v ~/.gmail-mcp-secrets/credentials.json:/app/credentials.json:ro \
  -v ~/.gmail-mcp-secrets/token.json:/secrets/token.json:ro \
  -p 5000:5000 \
  gmail-dashboard
```

The entrypoint copies `token.json` from the read-only `/secrets/` mount to the writable `/app/` directory on startup, so the app can write refreshed access tokens back.

The dashboard will be available at `http://<host-ip>:5000`.

---

## 5. Optional: PIN Protection

Generate the `.pincode` file on the host:

```sh
cd gmail-mcp-server
make set-pin
cp .pincode ~/.gmail-mcp-secrets/
chmod 600 ~/.gmail-mcp-secrets/.pincode
```

Add the mount to your run command:

```sh
podman run -d \
  --name gmail-dashboard \
  --restart unless-stopped \
  --env-file ~/.gmail-mcp-secrets/env \
  -v ~/.gmail-mcp-secrets/credentials.json:/app/credentials.json:ro \
  -v ~/.gmail-mcp-secrets/token.json:/secrets/token.json:ro \
  -v ~/.gmail-mcp-secrets/.pincode:/secrets/.pincode:ro \
  -p 5000:5000 \
  gmail-dashboard
```

---

## 6. Autostart with systemd (Optional)

Generate a systemd unit from the running container:

```sh
podman generate systemd --name gmail-dashboard --files --new
mv container-gmail-dashboard.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now container-gmail-dashboard
```

For system-wide autostart (runs as root):

```sh
podman generate systemd --name gmail-dashboard --files --new
mv container-gmail-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now container-gmail-dashboard
```

---

## Re-auth (if refresh token is revoked)

```sh
# On a machine with a browser
make auth
cp token.json ~/.gmail-mcp-secrets/token.json

podman restart gmail-dashboard
```

The entrypoint re-copies the updated `token.json` on restart.

---

---

## Vertex AI (instead of Anthropic API key)

Use these steps if Claude is accessed through Google Cloud Vertex AI (`CLAUDE_CODE_USE_VERTEX=1`) rather than a direct Anthropic API key.

### 1. Obtain GCP credentials

**Option A — User credentials (good for development):**

```sh
gcloud auth application-default login
cp ~/.config/gcloud/application_default_credentials.json ~/.gmail-mcp-secrets/adc.json
chmod 600 ~/.gmail-mcp-secrets/adc.json
```

User ADC tokens expire after ~1 hour and auto-refresh only while `gcloud` is present. For long-running containers, use Option B.

**Option B — Service account key (recommended for always-on deployments):**

```sh
# Create a service account and grant it the Vertex AI User role
gcloud iam service-accounts create gmail-mcp \
  --display-name="Gmail MCP Dashboard"

gcloud projects add-iam-policy-binding MY_PROJECT \
  --member="serviceAccount:gmail-mcp@MY_PROJECT.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# Download the key
gcloud iam service-accounts keys create ~/.gmail-mcp-secrets/adc.json \
  --iam-account=gmail-mcp@MY_PROJECT.iam.gserviceaccount.com
chmod 600 ~/.gmail-mcp-secrets/adc.json
```

Service account keys do not expire.

### 2. Create the env file for Vertex AI

```sh
cat > ~/.gmail-mcp-secrets/env <<'EOF'
CLAUDE_CODE_USE_VERTEX=1
ANTHROPIC_VERTEX_PROJECT_ID=my-gcp-project
GOOGLE_APPLICATION_CREDENTIALS=/secrets/adc.json
EOF
chmod 600 ~/.gmail-mcp-secrets/env
```

`CLOUD_ML_REGION` defaults to `us-east5`; set it explicitly if you use a different region.

### 3. Run the container with the ADC mounted

```sh
podman run -d \
  --name gmail-dashboard \
  --restart unless-stopped \
  --env-file ~/.gmail-mcp-secrets/env \
  -v ~/.gmail-mcp-secrets/credentials.json:/app/credentials.json:ro \
  -v ~/.gmail-mcp-secrets/token.json:/secrets/token.json:ro \
  -v ~/.gmail-mcp-secrets/adc.json:/secrets/adc.json:ro \
  -p 5000:5000 \
  gmail-dashboard
```

The `GOOGLE_APPLICATION_CREDENTIALS` env var points the Claude subprocess to the ADC file inside the container.

---

## Quick Reference

| What | Where |
|---|---|
| Image name | `gmail-dashboard` |
| OAuth credentials | `~/.gmail-mcp-secrets/credentials.json` (mounted `:ro` at `/app/`) |
| OAuth token | `~/.gmail-mcp-secrets/token.json` (mounted `:ro` at `/secrets/`, copied to `/app/` on start) |
| API key (Anthropic) | `~/.gmail-mcp-secrets/env` → `ANTHROPIC_API_KEY` |
| API key (Vertex AI) | `~/.gmail-mcp-secrets/env` → `CLAUDE_CODE_USE_VERTEX`, `ANTHROPIC_VERTEX_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS` |
| GCP ADC / SA key | `~/.gmail-mcp-secrets/adc.json` (mounted `:ro` at `/secrets/adc.json`) |
| PIN hash | `~/.gmail-mcp-secrets/.pincode` (mounted `:ro` at `/secrets/`, copied to `/app/` on start) |
| Dashboard | `http://<host-ip>:5000` |
| Logs | `podman logs -f gmail-dashboard` |
| Stop | `podman stop gmail-dashboard` |
| Rebuild | `podman build -t gmail-dashboard . && podman restart gmail-dashboard` |
