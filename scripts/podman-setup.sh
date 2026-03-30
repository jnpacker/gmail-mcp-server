#!/usr/bin/env bash
#
# podman-setup.sh - Podman setup for Gmail MCP Server
#
# Run this from within the cloned repo (no root required):
#   bash scripts/podman-setup.sh
#
# What it does:
#   1. Check prerequisites (podman, repo Dockerfile)
#   2. Prepare the secrets directory (~/.gmail-mcp-secrets)
#   3. Configure the env file (Gemini API key)
#   4. Handle Gmail credentials.json
#   5. Handle Gmail OAuth token.json
#   6. Optional PIN protection
#   7. Build the container image
#   8. Run (or restart) the container
#   9. Optional systemd autostart
#

set -uo pipefail   # no -e: missing auth never aborts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
CYAN="\033[36m"
RED="\033[31m"
RESET="\033[0m"

info()    { echo -e "${CYAN}[setup]${RESET} $*"; }
success() { echo -e "${GREEN}[done]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}[warn]${RESET}  $*"; }
pending() { echo -e "${YELLOW}[pend]${RESET}  $*"; }
fatal()   { echo -e "${RED}[error]${RESET} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}==> $*${RESET}"; }

confirm() {
    local prompt="$1"
    local default="${2:-y}"
    local yn
    if [[ "$default" == "y" ]]; then
        read -rp "$prompt [Y/n] " yn
        [[ "${yn:-y}" =~ ^[Yy]$ ]]
    else
        read -rp "$prompt [y/N] " yn
        [[ "${yn:-n}" =~ ^[Yy]$ ]]
    fi
}

PENDING_ITEMS=()
need() { PENDING_ITEMS+=("$*"); }

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="${HOME}/.gmail-mcp-secrets"
ENV_FILE="${SECRETS_DIR}/env"
CREDS_FILE="${SECRETS_DIR}/credentials.json"
TOKEN_FILE="${SECRETS_DIR}/token.json"
PIN_FILE="${SECRETS_DIR}/.pincode"
CLAUDE_CREDS_FILE="${HOME}/.claude/.credentials.json"
IMAGE_REPO="${IMAGE_REPO:-your-registry/gmail-mcp-server}"
_SAVED_TAG=$(cat "$REPO_DIR/.image-tag" 2>/dev/null)
IMAGE_TAG="${IMAGE_TAG:-${_SAVED_TAG:-0.1}}"
IMAGE_NAME="${IMAGE_REPO}:${IMAGE_TAG}"
CONTAINER_NAME="gmail-dashboard"

info "Repo       : $REPO_DIR"
info "Secrets    : $SECRETS_DIR"
info "Image      : $IMAGE_NAME"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Prerequisites
# ---------------------------------------------------------------------------
step "1. Checking prerequisites"

command -v podman &>/dev/null || fatal "podman not found. Install it first: https://podman.io/getting-started/installation"
success "podman $(podman --version)"

[[ -f "$REPO_DIR/Dockerfile" ]] || fatal "Dockerfile not found in $REPO_DIR — run this script from the repo root."
success "Dockerfile found"

# ---------------------------------------------------------------------------
# Step 2: Secrets directory
# ---------------------------------------------------------------------------
step "2. Preparing secrets directory"

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"
success "$SECRETS_DIR ready"

# ---------------------------------------------------------------------------
# Step 3: Gemini API key
# ---------------------------------------------------------------------------
step "3. Gemini API key"

if grep -q "GEMINI_API_KEY" "$ENV_FILE" 2>/dev/null; then
    success "GEMINI_API_KEY already in env file"
else
    echo "  Get a key at: https://aistudio.google.com/app/apikey"
    read -rp "  Gemini API key (AIza..., or Enter to skip): " gemini_key || gemini_key=""
    if [[ -n "$gemini_key" ]]; then
        echo "GEMINI_API_KEY=${gemini_key}" >> "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        success "GEMINI_API_KEY added to env file"
    else
        warn "Gemini API key skipped — Gemini CLI will not be authenticated."
        need "Gemini CLI: add GEMINI_API_KEY=AIza... to $ENV_FILE and re-run"
    fi
fi

# ---------------------------------------------------------------------------
# Step 3b: Gmail account email (for direct account linking in URLs)
# ---------------------------------------------------------------------------
step "3b. Gmail account email"

_current_gmail_user=$(grep "^GMAIL_USER=" "$ENV_FILE" 2>/dev/null | cut -d= -f2-)
if [[ -n "$_current_gmail_user" ]]; then
    info "Current GMAIL_USER: $_current_gmail_user"
    read -rp "  Gmail address (Enter to keep '$_current_gmail_user'): " gmail_user_input || gmail_user_input=""
    gmail_user_input="${gmail_user_input:-$_current_gmail_user}"
else
    read -rp "  Gmail address (e.g. you@gmail.com, or Enter to skip): " gmail_user_input || gmail_user_input=""
fi

if [[ -n "$gmail_user_input" ]]; then
    # Remove existing entry if present, then append updated value
    sed -i '/^GMAIL_USER=/d' "$ENV_FILE" 2>/dev/null || true
    echo "GMAIL_USER=${gmail_user_input}" >> "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    success "GMAIL_USER set to '${gmail_user_input}'"
else
    warn "GMAIL_USER skipped — Gmail links will fall back to /u/0/"
fi

# ---------------------------------------------------------------------------
# Step 4: credentials.json
# ---------------------------------------------------------------------------
step "4. Gmail API credentials (credentials.json)"

if [[ -f "$CREDS_FILE" ]]; then
    success "credentials.json already present"
elif [[ -f "$REPO_DIR/credentials.json" ]]; then
    cp "$REPO_DIR/credentials.json" "$CREDS_FILE"
    chmod 600 "$CREDS_FILE"
    success "credentials.json copied from repo"
else
    warn "credentials.json not found — dashboard cannot access Gmail without this."
    echo "  1. Go to Google Cloud Console → APIs & Services → Credentials"
    echo "  2. Create an OAuth 2.0 Client ID (Desktop app) and download the JSON"
    echo "  3. Copy it to: $CREDS_FILE"
    echo "  4. Re-run this script to continue."
    need "Gmail API: copy credentials.json to $CREDS_FILE and re-run"
fi

# ---------------------------------------------------------------------------
# Step 5: Gmail OAuth token.json
# ---------------------------------------------------------------------------
step "5. Gmail OAuth token (token.json)"

if [[ -f "$TOKEN_FILE" ]]; then
    success "token.json already present"
elif [[ -f "$REPO_DIR/token.json" ]]; then
    cp "$REPO_DIR/token.json" "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
    success "token.json copied from repo"
elif [[ ! -f "$CREDS_FILE" ]]; then
    warn "Gmail OAuth token: skipped (credentials.json is also missing — fix that first)"
    need "Gmail OAuth: add credentials.json first, then re-run to get token.json"
else
    warn "Gmail OAuth token not found — the dashboard cannot access Gmail without it."
    echo ""
    echo "  Run auth on this machine (requires a browser):"
    echo "    cd $REPO_DIR && make auth"
    echo "    cp token.json $TOKEN_FILE"
    echo ""
    if confirm "Run 'make auth' now?" "n"; then
        (cd "$REPO_DIR" && make auth) || true
        if [[ -f "$REPO_DIR/token.json" ]]; then
            cp "$REPO_DIR/token.json" "$TOKEN_FILE"
            chmod 600 "$TOKEN_FILE"
            success "Gmail OAuth complete"
        else
            warn "Auth did not produce a token.json — complete it manually."
            need "Gmail OAuth: run 'make auth' then cp token.json $TOKEN_FILE"
        fi
    else
        need "Gmail OAuth: run 'make auth' then cp token.json $TOKEN_FILE"
    fi
fi

# ---------------------------------------------------------------------------
# Step 6: Optional PIN
# ---------------------------------------------------------------------------
step "6. Dashboard PIN (optional)"

if [[ -f "$PIN_FILE" ]]; then
    success "PIN already configured — skipping (delete $PIN_FILE to reset)"
elif confirm "Set a dashboard PIN now?" "n"; then
    (cd "$REPO_DIR" && make set-pin && cp .pincode "$PIN_FILE" && chmod 600 "$PIN_FILE") || true
    [[ -f "$PIN_FILE" ]] && success "PIN saved to $PIN_FILE"
fi

# ---------------------------------------------------------------------------
# Step 7: Build the image
# ---------------------------------------------------------------------------
step "7. Building container image"

info "Image: $IMAGE_NAME"

if confirm "Build (or rebuild) the image now?" "y"; then
    (cd "$REPO_DIR" && IMAGE_REPO="$IMAGE_REPO" IMAGE_TAG="$IMAGE_TAG" make podman-build) \
        || fatal "podman-build failed"
    # Re-read tag in case user changed it during the build prompt
    _SAVED_TAG=$(cat "$REPO_DIR/.image-tag" 2>/dev/null)
    IMAGE_TAG="${_SAVED_TAG:-$IMAGE_TAG}"
    IMAGE_NAME="${IMAGE_REPO}:${IMAGE_TAG}"
    success "Image '$IMAGE_NAME' built"
else
    warn "Skipped image build."
    if ! podman image exists "$IMAGE_NAME" 2>/dev/null; then
        need "Container image: run 'make podman-build' to build"
    fi
fi

# ---------------------------------------------------------------------------
# Step 8: Run the container
# ---------------------------------------------------------------------------
step "8. Running the container"

RUN_ARGS=(
    "-d"
    "--name" "$CONTAINER_NAME"
    "--restart" "unless-stopped"
    "-p" "5000:5000"
)

[[ -f "$ENV_FILE"   ]] && RUN_ARGS+=("--env-file" "$ENV_FILE")
[[ -f "$CREDS_FILE" ]] && RUN_ARGS+=("-v" "$CREDS_FILE:/app/credentials.json:ro")
[[ -f "$TOKEN_FILE" ]] && RUN_ARGS+=("-v" "$TOKEN_FILE:/secrets/token.json:ro")
[[ -f "$PIN_FILE"   ]] && RUN_ARGS+=("-v" "$PIN_FILE:/secrets/.pincode:ro")

if podman container exists "$CONTAINER_NAME" 2>/dev/null; then
    info "Container '$CONTAINER_NAME' already exists — recreating with updated config..."
    podman rm -f "$CONTAINER_NAME" >/dev/null
fi

if podman image exists "$IMAGE_NAME" 2>/dev/null; then
    podman run "${RUN_ARGS[@]}" "$IMAGE_NAME"
    sleep 2
    podman ps --filter "name=$CONTAINER_NAME" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    success "Dashboard: http://${HOST_IP}:5000"
else
    warn "Image not built — skipping container start."
    need "Container: build the image first (step 7), then re-run"
fi

# ---------------------------------------------------------------------------
# Step 9: systemd autostart (optional)
# ---------------------------------------------------------------------------
step "9. systemd autostart (optional)"

if confirm "Generate a systemd unit for autostart?" "n"; then
    UNIT_DIR="${HOME}/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    podman generate systemd --name "$CONTAINER_NAME" --files --new 2>/dev/null \
        && mv "container-${CONTAINER_NAME}.service" "$UNIT_DIR/" \
        && systemctl --user daemon-reload \
        && systemctl --user enable --now "container-${CONTAINER_NAME}" \
        && success "systemd user unit enabled" \
        || warn "systemd unit generation failed — set up autostart manually (see PODMAN.md)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
step "Setup summary"

GEMINI_OK=false
grep -q "GEMINI_API_KEY" "$ENV_FILE" 2>/dev/null && GEMINI_OK=true
GMAIL_USER_OK=false
grep -q "^GMAIL_USER=" "$ENV_FILE" 2>/dev/null && GMAIL_USER_OK=true

_status() { [[ "$1" == "true" ]] && echo -e "${GREEN}ready${RESET}" || echo -e "${YELLOW}pending${RESET}"; }

echo ""
printf "  %-40s %s\n" "Component" "Status"
printf "  %-40s %s\n" "---------" "------"
printf "  %-40s %b\n" "Gmail account (GMAIL_USER)"      "$(_status $GMAIL_USER_OK)"
printf "  %-40s %b\n" "Gemini CLI (GEMINI_API_KEY)"     "$(_status $GEMINI_OK)"
printf "  %-40s %b\n" "Gmail credentials.json"          "$(_status $([[ -f $CREDS_FILE ]] && echo true || echo false))"
printf "  %-40s %b\n" "Gmail token.json (OAuth)"        "$(_status $([[ -f $TOKEN_FILE  ]] && echo true || echo false))"
printf "  %-40s %b\n" "Container image"                 "$(_status $(podman image exists $IMAGE_NAME 2>/dev/null && echo true || echo false))"
printf "  %-40s %b\n" "Container running"               "$(_status $(podman ps -q --filter name=$CONTAINER_NAME 2>/dev/null | grep -q . && echo true || echo false))"
echo ""

if [[ ${#PENDING_ITEMS[@]} -gt 0 ]]; then
    echo -e "${BOLD}Pending actions:${RESET}"
    for item in "${PENDING_ITEMS[@]}"; do
        pending "$item"
    done
    echo ""
fi

info "Logs   : podman logs -f $CONTAINER_NAME"
info "Stop   : podman stop $CONTAINER_NAME"
info "Rebuild: make podman-setup"
