#!/bin/sh
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
umask 077
# Usage: advise.sh [--task <id>] [--watch <secondes>] | --ask "<question>" | --bench <questions.json>
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
if [ "${1:-}" = "--bench" ]; then
    shift
    exec "${WORKSPACE_PYTHON:-python3}" -u "$SCRIPT_DIR/lib/advisor_bench.py" "$root" "$@"
fi
exec "${WORKSPACE_PYTHON:-python3}" -u "$SCRIPT_DIR/lib/advisor.py" "$root" "$@"
