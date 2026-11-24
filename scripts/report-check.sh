#!/bin/sh
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
umask 077
# Usage: report-check.sh <rapport.md> [--verifier antigravity|codex] [--repo <chemin>]... [--max <n>]
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "${WORKSPACE_PYTHON:-python3}" "$root/scripts/lib/report_check.py" "$root" "$@"
