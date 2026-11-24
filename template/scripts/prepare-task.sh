#!/usr/bin/env bash
# Usage: bash scripts/prepare-task.sh <demande.md> <task-id> --executor <agent> [--planner claude-opus|codex]
#        bash scripts/prepare-task.sh --from-demande <fichier> <task-id> --executor <agent> [--planner claude-opus|codex]
#        bash scripts/prepare-task.sh --from-brief <brief.md> <nom> [--executor <agent>] [--planner claude-opus|codex]
#        bash scripts/prepare-task.sh --from-brief <brief.md> <nom> --with-answers --executor <agent> [--planner claude-opus|codex]
# --from-brief : cadrage sous .workspace/missions/<nom>/ (rien n'est construit), puis propositions après reponses.md.
# --from-demande : écrit des propositions sous .workspace/proposals/<id>/, jamais délégables avant relecture.
# Codes : 0 propositions rouges sur la base, 1 au moins un test vert sur la base, 2 invalide, 3 planificateur absent.
set -eu
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
script_dir="${BASH_SOURCE[0]%/*}"
exec "${WORKSPACE_PYTHON:-python3}" "$script_dir/lib/prepare_task.py" "$@"
