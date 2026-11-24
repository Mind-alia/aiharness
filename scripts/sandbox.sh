#!/bin/sh
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
umask 077
# Usage: sandbox.sh deps|start|exec|stop|status <id> [args...]
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
exec "${WORKSPACE_PYTHON:-python3}" "$SCRIPT_DIR/lib/sandbox.py" "$root" "$@"
