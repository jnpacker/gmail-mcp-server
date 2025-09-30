.PHONY: prompt prompt-plus

prompt:
	@echo "Generating GEMINI.md and CLAUDE.md from gmail-assistant-prompt.md..."
	@cp gmail-assistant-prompt.md GEMINI.md
	@cp gmail-assistant-prompt.md CLAUDE.md
	@echo "✅ Generated GEMINI.md"
	@echo "✅ Generated CLAUDE.md"

prompt-plus:
	@echo "Generating GEMINI.md and CLAUDE.md from gmail-assistant-prompt.md + gcal-assistant-prompt.md..."
	@cat gmail-assistant-prompt.md /home/jpacker/workspace_git/gcal-mcp-server/gcal-assistant-prompt.md > GEMINI.md
	@cat gmail-assistant-prompt.md /home/jpacker/workspace_git/gcal-mcp-server/gcal-assistant-prompt.md > CLAUDE.md
	@echo "✅ Generated GEMINI.md"
	@echo "✅ Generated CLAUDE.md"
