.PHONY: help auth triage watch dashboard kill-dashboard

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
