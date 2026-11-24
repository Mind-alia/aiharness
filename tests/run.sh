#!/bin/sh
# Usage : sh tests/run.sh fast|full
# fast : saute les modules lents (WORKSPACE_TESTS=fast), full : toute la suite.
# Les clones de tâche vont dans un dossier temporaire hors du dépôt (WORKSPACE_WORK_DIR), retiré à la fin :
# aucun test n'écrit dans le vrai ~/.workspace-ia.
set -eu

mode=${1:-}
[ $# -eq 1 ] || mode=

case "$mode" in
  fast|full) ;;
  *)
    echo "usage : sh tests/run.sh fast|full" >&2
    exit 64
    ;;
esac

work=$(mktemp -d "${TMPDIR:-/tmp}/workspace-work.XXXXXX")
work=$(cd "$work" && pwd -P)
trap 'rm -rf "$work"' EXIT
trap 'exit 130' HUP INT TERM

export WORKSPACE_WORK_DIR="$work"
# Interpreter declared in .python-version; PYTHON overrides it.
python=${PYTHON:-$(command -v python3.14 || echo python3)}
if [ "$mode" = fast ]; then
  WORKSPACE_TESTS=fast "$python" -m unittest discover -s tests
else
  "$python" -m unittest discover -s tests
fi
