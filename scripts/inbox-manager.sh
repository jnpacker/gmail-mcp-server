#!/usr/bin/env bash
#
# inbox-manager.sh - Autonomous inbox management via Claude Code
#
# Usage:
#   ./scripts/inbox-manager.sh              # Run triage once
#   ./scripts/inbox-manager.sh --watch      # Auto-reconcile every 10 minutes
#   ./scripts/inbox-manager.sh --watch 15   # Auto-reconcile every 15 minutes
#   ./scripts/inbox-manager.sh --help       # Show usage
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DEFAULT_INTERVAL=10
DEFAULT_MODEL="haiku"
LOG_DIR="$PROJECT_DIR/logs"

# Dark orange (ANSI 256-color 208)
C_ORANGE='\033[38;5;208m'
C_DIM='\033[2m'
C_RESET='\033[0m'
C_GREEN='\033[32m'
C_RED='\033[31m'

SPINNER_CHARS='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'

usage() {
    cat <<'USAGE'
inbox-manager.sh - Autonomous Gmail inbox management

Usage:
  inbox-manager.sh                  Run triage once (manual mode)
  inbox-manager.sh --watch [MIN]    Auto-reconcile every MIN minutes (default: 10)
  inbox-manager.sh --help           Show this help

Options:
  --watch [MIN]    Enable auto-reconcile mode. Runs triage on a loop.
                   MIN = interval in minutes (default: 10, range: 5-60)
  --max N          Max emails to process per run (default: 50)
  --model MODEL    Claude model to use (default: haiku)
                   Examples: haiku, sonnet, opus
  --quiet          Suppress stdout, log to file only
  --help           Show this help message

Examples:
  ./scripts/inbox-manager.sh                        # One-shot triage (haiku)
  ./scripts/inbox-manager.sh --model opus           # Use Opus instead
  ./scripts/inbox-manager.sh --watch                # Every 10 minutes
  ./scripts/inbox-manager.sh --watch 15             # Every 15 minutes
  ./scripts/inbox-manager.sh --watch --max 30       # Every 10 min, 30 emails max
USAGE
}

format_elapsed() {
    local secs=$1
    local mins=$((secs / 60))
    local s=$((secs % 60))
    if [[ $mins -gt 0 ]]; then
        printf "%dm%02ds" "$mins" "$s"
    else
        printf "%ds" "$s"
    fi
}

spinner() {
    # Usage: spinner <pid> <output_file>
    # Shows a dark orange spinner with elapsed timer.
    # Every 15s, verifies the background process is alive.
    local pid=$1
    local outfile=$2
    local start_time=$SECONDS
    local i=0
    local last_check=0
    local check_interval=15
    local last_size=0

    # Hide cursor
    tput civis 2>/dev/null || true

    while kill -0 "$pid" 2>/dev/null; do
        local elapsed=$((SECONDS - start_time))
        local elapsed_fmt
        elapsed_fmt=$(format_elapsed $elapsed)

        # Spinner character
        local char="${SPINNER_CHARS:i%${#SPINNER_CHARS}:1}"
        i=$((i + 1))

        # Every 15s, check process health + output file growth
        local status_msg=""
        local since_check=$((elapsed - last_check))
        if [[ $since_check -ge $check_interval ]]; then
            last_check=$elapsed
            if kill -0 "$pid" 2>/dev/null; then
                # Check if output file is growing (claude is producing output)
                local cur_size
                cur_size=$(stat -c%s "$outfile" 2>/dev/null || echo 0)
                if [[ $cur_size -gt $last_size ]]; then
                    status_msg=" — processing"
                    last_size=$cur_size
                else
                    # Process alive but no new output — still thinking
                    status_msg=" — thinking"
                fi
            fi
        fi

        printf "\r${C_ORANGE}%s${C_RESET} Triaging inbox... ${C_DIM}%s${C_RESET}  ${C_DIM}pid:%s${C_RESET}%s  " \
            "$char" "$elapsed_fmt" "$pid" "$status_msg"

        sleep 0.1
    done

    # Clear spinner line
    printf "\r\033[K"

    # Restore cursor
    tput cnorm 2>/dev/null || true
}

run_triage() {
    local max_emails="${1:-50}"
    local model="${2:-$DEFAULT_MODEL}"
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"

    echo -e "${C_ORANGE}[$timestamp]${C_RESET} Running inbox triage (max: $max_emails emails, model: $model)..."

    cd "$PROJECT_DIR"

    # MCP tools are prefixed with mcp__gmail__ in Claude's tool registry
    local allowed_tools="mcp__gmail__list_unread_emails"
    allowed_tools+=",mcp__gmail__delete_emails"
    allowed_tools+=",mcp__gmail__archive_emails"
    allowed_tools+=",mcp__gmail__modify_labels"
    allowed_tools+=",mcp__gmail__list_labels"
    allowed_tools+=",mcp__gmail__create_label"
    allowed_tools+=",mcp__gmail__list_recent_actions"

    # Run claude in background, capture output to temp file
    local tmpfile
    tmpfile=$(mktemp /tmp/triage-output.XXXXXX)

    local start_time=$SECONDS
    local claude_pid=""

    # Cleanup handler: kill claude, restore cursor, remove tmpfile
    cleanup() {
        if [[ -n "$claude_pid" ]] && kill -0 "$claude_pid" 2>/dev/null; then
            kill -TERM "$claude_pid" 2>/dev/null
            wait "$claude_pid" 2>/dev/null || true
        fi
        tput cnorm 2>/dev/null || true
        printf "\r\033[K"
        rm -f "$tmpfile"
        echo ""
        echo -e "${C_RED}Triage aborted.${C_RESET}"
        exit 130
    }
    trap cleanup INT TERM

    CLAUDECODE= claude -p "/triage $max_emails" \
        --model "$model" \
        --allowedTools "$allowed_tools" \
        --output-format json \
        > "$tmpfile" 2>/dev/null &
    claude_pid=$!

    # Show spinner while claude runs
    spinner "$claude_pid" "$tmpfile"

    # Wait for claude to finish and get exit code
    local exit_code=0
    wait "$claude_pid" || exit_code=$?
    claude_pid=""  # Clear so cleanup doesn't try to kill again

    # Restore default signal handling and clean tmpfile on return
    trap - INT TERM
    trap "rm -f '$tmpfile'" RETURN

    local elapsed=$((SECONDS - start_time))
    local elapsed_fmt
    elapsed_fmt=$(format_elapsed $elapsed)

    local raw_output
    raw_output=$(cat "$tmpfile")

    # Extract the result text and cost from JSON output
    # Try multiple field names for cost (API may vary)
    local result cost_usd
    result=$(echo "$raw_output" | jq -r '.result // empty' 2>/dev/null)
    cost_usd=$(echo "$raw_output" | jq -r '.total_cost_usd | select(. != null) | tostring' 2>/dev/null)

    if [[ -n "$result" ]]; then
        echo "$result"
    else
        # Fallback: JSON parsing failed, show raw output
        echo "$raw_output"
    fi

    echo ""
    local cost_str=""
    if [[ -n "$cost_usd" ]]; then
        cost_str=" | Cost: \$${cost_usd}"
    fi

    if [[ $exit_code -eq 0 ]]; then
        echo -e "${C_GREEN}[$timestamp]${C_RESET} Triage complete in ${elapsed_fmt}${cost_str}"
    else
        echo -e "${C_RED}[$timestamp]${C_RESET} Triage failed (exit $exit_code) after ${elapsed_fmt}${cost_str}"
    fi
}

main() {
    local watch=false
    local interval=$DEFAULT_INTERVAL
    local max_emails=100
    local model=$DEFAULT_MODEL
    local quiet=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --watch)
                watch=true
                # Check if next arg is a number (interval)
                if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
                    interval="$2"
                    shift
                fi
                shift
                ;;
            --max)
                max_emails="${2:?--max requires a number}"
                shift 2
                ;;
            --model)
                model="${2:?--model requires a value (e.g. haiku, sonnet, opus)}"
                shift 2
                ;;
            --quiet)
                quiet=true
                shift
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done

    # Validate interval
    if [[ "$interval" -lt 5 || "$interval" -gt 60 ]]; then
        echo "Error: interval must be between 5 and 60 minutes (got: $interval)"
        exit 1
    fi

    if $watch; then
        echo "Inbox Manager: auto-reconcile mode (every ${interval}m, max ${max_emails} emails, model: ${model})"
        echo "Press Ctrl+C to stop."
        echo ""

        # Set up log directory for watch mode
        mkdir -p "$LOG_DIR"
        local logfile="$LOG_DIR/triage-$(date '+%Y%m%d').log"

        trap 'echo ""; echo "Inbox Manager stopped."; exit 0' INT TERM

        while true; do
            if $quiet; then
                run_triage "$max_emails" "$model" >> "$logfile" 2>&1
            else
                run_triage "$max_emails" "$model" 2>&1 | tee -a "$logfile"
            fi

            echo "Next run in ${interval} minutes... (Ctrl+C to stop)"
            sleep "$((interval * 60))"
        done
    else
        # One-shot mode
        run_triage "$max_emails" "$model"
    fi
}

main "$@"
