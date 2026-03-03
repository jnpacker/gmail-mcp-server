.PHONY: triage watch

MODEL ?= haiku

triage:
	./scripts/inbox-manager.sh --model $(MODEL)

watch:
	./scripts/inbox-manager.sh --watch 10 --model $(MODEL)
