.PHONY: help triage watch dashboard kill-dashboard

.DEFAULT_GOAL := help

MODEL ?= haiku

help: ## List available commands
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-15s %s\n", $$1, $$2}'

triage: ## Run inbox triage (MODEL=haiku|sonnet|opus)
	./scripts/inbox-manager.sh --model $(MODEL)

watch: ## Watch inbox every 10 min (MODEL=haiku|sonnet|opus)
	./scripts/inbox-manager.sh --watch 10 --model $(MODEL)

dashboard: ## Start the web dashboard
	python3 app.py

kill-dashboard: ## Kill the running dashboard server
	@pkill -f 'python3 app.py' && echo "Dashboard stopped." || echo "No dashboard running."
