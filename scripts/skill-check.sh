#!/bin/sh
# Usage: scripts/skill-check.sh <dossier> [--trust]
set -eu
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "${WORKSPACE_PYTHON:-python3}" "$root/scripts/lib/skill_check.py" "$@"
