#!/bin/sh
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
umask 077
# Usage: scope-baseline.sh <fichier-de-tâche>
# Écrit .workspace/run/delegate/<id>/baseline.json depuis la racine Git.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${WORKSPACE_PYTHON:-python3}" "$SCRIPT_DIR/lib/scope.py" baseline "$@"
