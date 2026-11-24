#!/bin/sh
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
umask 077
# Usage: ask.sh [<task-id>] "<question>"
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
exec "${WORKSPACE_PYTHON:-python3}" "$SCRIPT_DIR/lib/context.py" "$root" ask "$@"
