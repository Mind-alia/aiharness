# Resolves the kit's interpreter once for every circuit script and hook (.python-version of the kit: Python 3.14).
# Sourced, never executed: `. "<scripts dir>/python.sh"`, then `"$WORKSPACE_PYTHON" ...`.
# WORKSPACE_PYTHON overrides the choice; an older interpreter is refused with exit code 2 (a hook then blocks
# instead of letting the tool call through) and a clear message instead of a cryptic import error.
if [ -z "${WORKSPACE_PYTHON:-}" ]; then
  WORKSPACE_PYTHON=$(command -v python3.14 || command -v python3 || echo python3)
fi
if ! "$WORKSPACE_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)' 2>/dev/null; then
  echo "Python 3.14 requis (interpréteur trouvé : $WORKSPACE_PYTHON) ; installer python3.14 ou définir WORKSPACE_PYTHON." >&2
  exit 2
fi
export WORKSPACE_PYTHON
