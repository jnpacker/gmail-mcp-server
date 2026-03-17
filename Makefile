.PHONY: help auth triage watch dashboard kill-dashboard set-pin

.DEFAULT_GOAL := help

MODEL ?= haiku

help: ## List available commands
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-15s %s\n", $$1, $$2}'

auth: ## Initialize Gmail OAuth authentication (requires credentials.json)
	@if [ ! -f credentials.json ]; then \
		echo "Error: credentials.json not found."; \
		echo "Download it from Google Cloud Console and place it in this directory."; \
		exit 1; \
	fi
	python3 -m gmail_mcp_server.auth

triage: ## Run inbox triage (MODEL=haiku|sonnet|opus)
	./scripts/inbox-manager.sh --model $(MODEL)

watch: ## Watch inbox every 10 min (MODEL=haiku|sonnet|opus)
	./scripts/inbox-manager.sh --watch 10 --model $(MODEL)

dashboard: ## Start the web dashboard (auto-restarts on crash)
	@while true; do \
		python3 app.py; \
		echo "[dashboard] Server exited (code $$?), restarting in 2s..."; \
		sleep 2; \
	done

kill-dashboard: ## Kill the running dashboard server
	@pkill -f 'python3 app.py' && echo "Dashboard stopped." || echo "No dashboard running."

set-pin: ## Set or change the dashboard PIN (prompts for PIN twice)
	@python3 -c "\
import getpass, secrets, hashlib, sys; \
from pathlib import Path; \
p1 = getpass.getpass('Enter new PIN: '); \
p2 = getpass.getpass('Confirm PIN: '); \
sys.exit('Error: PINs do not match.') if p1 != p2 else None; \
salt = secrets.token_hex(16); \
h = hashlib.pbkdf2_hmac('sha256', p1.encode(), salt.encode(), 260000).hex(); \
Path('.pincode').write_text(f'{salt}:{h}'); \
print('PIN saved.')"
