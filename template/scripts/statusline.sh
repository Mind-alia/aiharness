#!/bin/sh
# Barre d'état Claude Code : exécute lib/statusline.py à côté de ce script (voir ce module).
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
exec "${WORKSPACE_PYTHON:-python3}" "$(dirname -- "$0")/lib/statusline.py"
