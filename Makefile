.PHONY: help auth triage watch dashboard kill-dashboard set-pin podman-setup podman-build podman-push link-commands lint format test test-cov

.DEFAULT_GOAL := help

MODEL      ?= haiku
IMAGE_REPO ?= quay.io/gmail-dashboard
_SAVED_TAG := $(shell cat .image-tag 2>/dev/null)
IMAGE_TAG  ?= $(if $(_SAVED_TAG),$(_SAVED_TAG),0.1)

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

watch: ## Watch inbox every 5 min (MODEL=haiku|sonnet|opus)
	./scripts/inbox-manager.sh --watch 5 --model $(MODEL)

dashboard: ## Start the web dashboard (auto-restarts on crash)
	@PYTHON=$$(if [ -f .venv/bin/python3 ]; then echo .venv/bin/python3; else echo python3; fi); \
	while true; do \
		$$PYTHON app.py; \
		echo "[dashboard] Server exited (code $$?), restarting in 2s..."; \
		sleep 2; \
	done

kill-dashboard: ## Kill the running dashboard server
	@pkill -f 'python3 app.py' && echo "Dashboard stopped." || echo "No dashboard running."

link-commands: ## Link commands for Claude Code and generate TOML commands for Gemini CLI
	@mkdir -p .claude/commands .gemini/commands
	@for cmd in triage emails; do \
		ln -sf ../../commands/$$cmd.md .claude/commands/$$cmd.md; \
		python3 -c "import re; text=open('commands/$$cmd.md').read(); parts=text.split('---',2) if text.startswith('---') else ['','',text]; fm=parts[1] if len(parts)>=3 else ''; body=parts[2].strip() if len(parts)>=3 else text.strip(); m=re.search(r'description:\s*(.+)',fm,re.M); desc=m.group(1).strip() if m else ''; tq=chr(34)*3; lines=((['description='+chr(34)+desc+chr(34)]) if desc else [])+['prompt='+tq+chr(10)+body+chr(10)+tq]; open('.gemini/commands/$$cmd.toml','w').write(chr(10).join(lines)+chr(10)); print('Generated .gemini/commands/$$cmd.toml')"; \
	done
	@ln -sf GEMINI.md CLAUDE.md
	@echo "Linked commands for Claude (.claude/commands/)"
	@echo "Generated TOML commands for Gemini (.gemini/commands/)"
	@echo "Linked CLAUDE.md -> GEMINI.md"

podman-build: ## Build Podman image (IMAGE_REPO=...; prompts for tag)
	@current="$(IMAGE_TAG)"; \
	echo "Current IMAGE_TAG: $$current"; \
	printf "Enter new tag (or press Enter to keep '$$current'): "; \
	read new_tag; \
	tag=$${new_tag:-$$current}; \
	sed -i "s|image: [^ ]*|image: $(IMAGE_REPO):$$tag|" k8s/deployment.yaml; \
	if [ "$$tag" != "$$current" ]; then \
		echo "$$tag" > .image-tag; \
		echo "Updated IMAGE_TAG -> $$tag (saved to .image-tag, updated k8s/deployment.yaml)"; \
	fi; \
	$(MAKE) link-commands; \
	podman build -t $(IMAGE_REPO):$$tag .

podman-push: ## Push image to registry (IMAGE_REPO=...; uses saved tag)
	podman push $(IMAGE_REPO):$(IMAGE_TAG)

podman-setup: ## Run full Podman container setup
	@bash scripts/podman-setup.sh

lint: ## Run ruff linter
	ruff check .

format: ## Auto-format and fix code with ruff
	ruff format .
	ruff check --fix .

test: ## Run tests with pytest
	python3 -m pytest tests/ -v

test-cov: ## Run tests with coverage report
	python3 -m pytest tests/ --cov=gmail_mcp_server --cov=app --cov-report=term-missing

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
