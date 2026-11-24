#!/bin/sh
# Usage: scripts/orchestrator.sh claim [--session NOM] [--model NOM] [--repos CHEMIN[,CHEMIN...]] [--pid N] [--force]
#        scripts/orchestrator.sh release | status [--repair]
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
umask 077
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "${WORKSPACE_PYTHON:-python3}" "$root/scripts/lib/orchestrator.py" "$root" "$@"
