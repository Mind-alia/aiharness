#!/usr/bin/env bash
# Run Antigravity CLI (agy, Google) non-interactively on this project (relay used by the gemini-* agents).
# Usage: scripts/gemini.sh review|write <prompt-file> <answer-file>
#   review: plan mode, Gemini can only read and report.
#   write:  accept-edits mode in a terminal sandbox; Gemini edits files but cannot run
#           commands (headless denies them), so the caller runs the verification itself.
# Model: GEMINI_MODEL (default gemini-3.8-flash-medium; see `agy models`). Time limit: GEMINI_TIMEOUT (default 600s).
# Events are kept in .workspace/run/gemini/<timestamp>.jsonl (ignored by git).
# Exit codes: 0 success, 2 Gemini failed, 3 Gemini unavailable or a missing prerequisite, 64 bad usage.
set -u
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
mode=${1:-}; prompt=${2:-}; answer=${3:-}
# Resolve paths from the caller's directory before moving to the project root (C:\ or C:/ is absolute on Windows).
case "$prompt" in ""|/*|[A-Za-z]:/*|[A-Za-z]:'\'*) ;; *) prompt="$PWD/$prompt" ;; esac
case "$answer" in ""|/*|[A-Za-z]:/*|[A-Za-z]:'\'*) ;; *) answer="$PWD/$answer" ;; esac
cd "$(dirname "$0")/.." || exit 64
metrics_root="$PWD"
case "$mode" in
  review) agy_mode=plan ;;
  write) agy_mode=accept-edits ;;
  *) echo "usage: scripts/gemini.sh review|write <prompt-file> <answer-file>" >&2; exit 64 ;;
esac
[ -f "$prompt" ] || { echo "prompt file not found: $prompt" >&2; exit 64; }
[ -n "$answer" ] || { echo "answer file missing" >&2; exit 64; }

# Prerequisites are checked before anything is created or run.
missing() { echo "gemini.sh: prérequis manquant : $1" >&2; exit 3; }
command -v python3 > /dev/null || missing python3
[ -r "$prompt" ] || missing "fichier de consigne illisible : $prompt"
agy="${AGY_BIN:-$HOME/.local/bin/agy}"
[ -x "$agy" ] || missing "binaire agy introuvable : $agy"

mkdir -p .workspace/run/gemini "$(dirname "$answer")"
events=$(mktemp ".workspace/run/gemini/$(date +%Y%m%d-%H%M%S)-$mode-XXXXXX") || exit 2
mv "$events" "$events.jsonl" || exit 2
events="$events.jsonl"
model="${GEMINI_MODEL:-gemini-3.8-flash-medium}"
if [ -n "${WORKSPACE_AGENT_ROOT:-}" ]; then
  events="$(pwd)/$events"
  cd "$WORKSPACE_AGENT_ROOT" || exit 64
fi

started=$("${WORKSPACE_PYTHON:-python3}" -c 'import time; print(time.monotonic())')
"$agy" -p "$(cat "$prompt")" --mode "$agy_mode" --sandbox --model "$model" \
  --output-format json --print-timeout "${GEMINI_TIMEOUT:-600s}" < /dev/null > "$events" 2>&1
status=$?
duration=$("${WORKSPACE_PYTHON:-python3}" -c 'import sys,time; print(max(0, time.monotonic()-float(sys.argv[1])))' "$started")
role="${WORKSPACE_ROLE:-}"
if [ -z "$role" ]; then
  if [ "$mode" = review ]; then role=relecture; else role=execution; fi
fi
metrics_args=(--role "$role" --model "$model" --duration "$duration" --exit "$status")
[ -z "${WORKSPACE_TASK_ID:-}" ] || metrics_args+=(--task-id "$WORKSPACE_TASK_ID")
[ -z "${WORKSPACE_ATTEMPT:-}" ] || metrics_args+=(--attempt "$WORKSPACE_ATTEMPT")
"${WORKSPACE_PYTHON:-python3}" "$metrics_root/scripts/lib/consumption.py" "$metrics_root" from-antigravity-events "$events" "${metrics_args[@]}" >&2 ||
  echo "Consumption: recording failed" >&2
# agy may print lines around the JSON envelope, or pretty-print it: scripts/lib/agy_envelope.py finds the last one and
# says on stderr why an answer is refused (3/11: two report checks lost on "Gemini failed (exit 0)" with no cause).
"${WORKSPACE_PYTHON:-python3}" "$metrics_root/scripts/lib/agy_envelope.py" "$events" "$mode" > "$answer"
envelope_status=$?
if [ $status -ne 0 ] || [ $envelope_status -ne 0 ] || [ ! -s "$answer" ]; then
  echo "Gemini failed (exit $status); events in $events" >&2
  exit 2
fi
echo "Gemini done ($mode); answer in $answer; events in $events"
