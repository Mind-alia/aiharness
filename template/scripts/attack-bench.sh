#!/bin/sh
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
umask 077
# Usage: attack-bench.sh <task-id> [--markdown]
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
exec "${WORKSPACE_PYTHON:-python3}" -u "$SCRIPT_DIR/lib/attack_bench.py" "$root" "$@"
