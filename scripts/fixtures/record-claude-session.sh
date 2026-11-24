#!/usr/bin/env bash
# Records a REAL Claude Code session's hook payloads into a fixture (Slice 01, T02, AD-11).
# Must be run by a human or an approved agent: it launches `claude -p` (uses Claude quota, model haiku).
# Output: $1 (default: apps/service/fixtures/claude-code/hooks-session-basique.jsonl)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-$ROOT/apps/service/fixtures/claude-code/hooks-session-basique.jsonl}"
RECORDER="$ROOT/scripts/fixtures/record-claude-hooks.mjs"
NODE_BIN="$(command -v node)"
PROJECT="$(mktemp -d)"
RAW="$(mktemp)"
trap 'rm -rf "$PROJECT" "$RAW"' EXIT

mkdir -p "$PROJECT/src" "$PROJECT/.claude"
printf 'export function add(a: number, b: number): number {\n  return a + b;\n}\n' > "$PROJECT/src/app.ts"
printf '# Throwaway project\n\nUsed to record Claude Code hooks.\n' > "$PROJECT/README.md"

hook() { # event, matcher?
  local ev=$1
  printf '"%s":[{%s"hooks":[{"type":"command","command":"\\"%s\\" \\"%s\\" %s"}]}]' \
    "$ev" "${2:+\"matcher\":\"$2\",}" "$NODE_BIN" "$RECORDER" "$ev"
}
{
  printf '{"hooks":{'
  hook SessionStart; printf ','
  hook SessionEnd; printf ','
  hook UserPromptSubmit; printf ','
  hook PreToolUse '*'; printf ','
  hook PostToolUse '*'; printf ','
  hook PostToolUseFailure '*'; printf ','
  hook SubagentStart; printf ','
  hook SubagentStop; printf ','
  hook Stop; printf ','
  hook Notification
  printf '}}\n'
} > "$PROJECT/.claude/settings.local.json"

PROMPT='Follow these steps in order, then stop: 1) read src/app.ts with the Read tool; 2) run `ls src` with Bash; 3) run `cat absent.txt` with Bash (it is expected to fail); 4) create notes.md containing one line with the Write tool; 5) delegate to a general-purpose sub-agent (Task or Agent tool) the reading of notes.md with a one-sentence summary; 6) stop without doing anything else.'

cd "$PROJECT"
WSC_FIXTURE_OUT="$RAW" claude -p "$PROMPT" --model haiku --setting-sources project,local \
  --permission-mode acceptEdits --allowedTools "Read,Bash(ls:*),Bash(cat:*),Write,Edit,Task,Agent" --max-turns 20 >/dev/null

mkdir -p "$(dirname "$OUT")"
sed "s#$HOME#/Users/fixture#g" "$RAW" > "$OUT"
echo "Recorded $(wc -l < "$OUT") hook lines into $OUT"
