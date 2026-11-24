#!/bin/sh
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
umask 077
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ "${1-}" = answer ]; then
  shift
  exec "${WORKSPACE_PYTHON:-python3}" "$SCRIPT_DIR/lib/questions.py" answer "$root" "$@"
fi

has_root=0
for arg do
  case "$arg" in
    -*) ;;
    *) has_root=1; break ;;
  esac
done

if [ "$has_root" -eq 0 ]; then
  exec "${WORKSPACE_PYTHON:-python3}" "$SCRIPT_DIR/lib/questions.py" "$root" "$@"
else
  exec "${WORKSPACE_PYTHON:-python3}" "$SCRIPT_DIR/lib/questions.py" "$@"
fi
