#!/bin/sh
# Usage: scripts/report.sh [--since AAAA-MM-JJ] [--markdown] [--events] [--rebuild [--adopt]]
#        scripts/report.sh --mission <nom>   grille de jugement (docs/GRILLE-MISSION.md) ; écrit grille.md une fois
#        scripts/report.sh --day AAAA-MM-JJ [--usine <nom>] [--out <fichier>]   partie factuelle du rapport de jour
#        scripts/report.sh --usine <nom> [--claude-dir <dossier>]   tokens de l'orchestrateur d'une usine
set -eu
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
for arg in "$@"; do
    if [ "$arg" = "--day" ]; then
        exec "${WORKSPACE_PYTHON:-python3}" "$root/scripts/lib/report_day.py" "$root" "$@"
    fi
done
for arg in "$@"; do
    if [ "$arg" = "--usine" ]; then
        exec "${WORKSPACE_PYTHON:-python3}" "$root/scripts/lib/factory_cost.py" "$root" "$@"
    fi
done
exec "${WORKSPACE_PYTHON:-python3}" "$root/scripts/lib/report.py" "$root" "$@"
