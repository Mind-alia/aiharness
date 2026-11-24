#!/usr/bin/env bash
# Run Codex CLI non-interactively on this project (relay used by the codex-* agents).
# Orchestration policy: AGENTS.md assigns plans, task briefs and acceptance tests
# to the orchestrator or Opus. Prefer Gemini for suitable bounded work; switch
# to Codex after two verification failures. No recursive worker delegation.
# Usage: scripts/codex.sh review|write <prompt-file> <answer-file>
#   review: read-only sandbox, Codex can only read and report.
#   write:  workspace-write sandbox, Codex can edit files inside this project only.
# Events are kept in .workspace/run/codex/<timestamp>.jsonl (ignored by git).
# Exit codes: 0 success, 2 Codex failed, 3 Codex unavailable or a missing prerequisite, 64 bad usage.
set -u
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
mode=${1:-}; prompt=${2:-}; answer=${3:-}
# Resolve paths from the caller's directory before moving to the project root (C:\ or C:/ is absolute on Windows).
case "$prompt" in ""|/*|[A-Za-z]:/*|[A-Za-z]:'\'*) ;; *) prompt="$PWD/$prompt" ;; esac
case "$answer" in ""|/*|[A-Za-z]:/*|[A-Za-z]:'\'*) ;; *) answer="$PWD/$answer" ;; esac
script_dir=$(cd "$(dirname "$0")" && pwd) || exit 64
cd "$script_dir/.." || exit 64
metrics_root="$PWD"
case "$mode" in
  review|write) ;;
  *) echo "usage: scripts/codex.sh review|write <prompt-file> <answer-file>" >&2; exit 64 ;;
esac
[ -f "$prompt" ] || { echo "prompt file not found: $prompt" >&2; exit 64; }
[ -n "$answer" ] || { echo "answer file missing" >&2; exit 64; }

# Prerequisites are checked before anything is created or run.
missing() { echo "codex.sh: prérequis manquant : $1" >&2; exit 3; }
command -v python3 > /dev/null || missing python3
[ -r "$prompt" ] || missing "fichier de consigne illisible : $prompt"
command -v npx > /dev/null || missing npx

# Permission profile: Codex reads the project and the system minimum only, not the rest of the host disk.
# If it cannot be built, Codex is not started.
profile_out=$("${WORKSPACE_PYTHON:-python3}" "$script_dir/lib/codex_profile.py" "$mode") || {
  echo "codex.sh: profil de permissions de Codex indisponible (lib/codex_profile.py)" >&2; exit 3; }
profile_args=()
while IFS= read -r profile_line; do profile_args+=("$profile_line"); done <<< "$profile_out"

codex=(npx -y @openai/codex@0.162.1)
"${codex[@]}" --version > /dev/null 2>&1 || { echo "Codex CLI unavailable" >&2; exit 3; }

mkdir -p .workspace/run/codex "$(dirname "$answer")"
# Unique suffix: two relays started in the same second must not share an events file.
events=$(mktemp ".workspace/run/codex/$(date +%Y%m%d-%H%M%S)-$mode-XXXXXX") || exit 2
mv "$events" "$events.jsonl" || exit 2
events="$events.jsonl"
target_root="${WORKSPACE_AGENT_ROOT:-$PWD}"
if [ -n "${WORKSPACE_AGENT_ROOT:-}" ]; then
  events="$(pwd)/$events"
  cd "$target_root" || exit 64
fi
model_args=(-m "${CODEX_MODEL:-gpt-6.1-sol}")

# Codex sees a reduced home: only the credentials, no config, plugin or skill of the host user.
# Only tools of the reduced PATH of tests/test_relay_prereqs.py are used (cat, mkdir, mktemp, python3).
auth_source="${CODEX_HOME:-${HOME:-/nonexistent}/.codex}/auth.json"
mkdir -p "$metrics_root/.workspace/run/codex-home" || exit 2
codex_home=$(mktemp -d "$metrics_root/.workspace/run/codex-home/XXXXXX") || exit 2
remove_auth() { "${WORKSPACE_PYTHON:-python3}" -c 'import os, sys
try:
    os.unlink(sys.argv[1])
except OSError:
    pass' "$codex_home/auth.json"; }
trap remove_auth EXIT
if [ -f "$auth_source" ]; then
  (umask 077; cat "$auth_source" > "$codex_home/auth.json") || exit 2
fi
export CODEX_HOME="$codex_home"

started=$("${WORKSPACE_PYTHON:-python3}" -c 'import time; print(time.monotonic())')
# No --sandbox here: it would make Codex ignore the permission profile.
"${codex[@]}" exec --json --ignore-user-config -c sandbox_workspace_write.network_access=false \
  ${profile_args[@]+"${profile_args[@]}"} -C "$target_root" ${model_args[@]+"${model_args[@]}"} -o "$answer" "$(cat "$prompt")" < /dev/null > "$events" 2>&1
status=$?
remove_auth
duration=$("${WORKSPACE_PYTHON:-python3}" -c 'import sys,time; print(max(0, time.monotonic()-float(sys.argv[1])))' "$started")
role="${WORKSPACE_ROLE:-}"
if [ -z "$role" ]; then
  if [ "$mode" = review ]; then role=relecture; else role=execution; fi
fi
metrics_args=(--role "$role" --model "${CODEX_MODEL:-gpt-6.1-sol}" --duration "$duration" --exit "$status")
[ -z "${WORKSPACE_TASK_ID:-}" ] || metrics_args+=(--task-id "$WORKSPACE_TASK_ID")
[ -z "${WORKSPACE_ATTEMPT:-}" ] || metrics_args+=(--attempt "$WORKSPACE_ATTEMPT")
"${WORKSPACE_PYTHON:-python3}" "$metrics_root/scripts/lib/consumption.py" "$metrics_root" from-codex-events "$events" "${metrics_args[@]}" >&2 ||
  echo "Consumption: recording failed" >&2
if [ $status -ne 0 ] || [ ! -s "$answer" ]; then
  echo "Codex failed (exit $status); events in $events" >&2
  exit 2
fi
echo "Codex done ($mode); answer in $answer; events in $events"
