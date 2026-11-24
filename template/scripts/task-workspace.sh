#!/bin/sh
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
umask 077
# Usage: task-workspace.sh prepare <task.md> [--base REF] [--retry] [--selfcheck] | check <task.md> | status [<task-id>] | merge <id> [--no-verify-again] [--no-integration] [--message "<texte>"] | close <id> --merged|--abandoned [--keep-branch] [--force]
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
exec "${WORKSPACE_PYTHON:-python3}" "$SCRIPT_DIR/lib/mission.py" "$root" "$@"
