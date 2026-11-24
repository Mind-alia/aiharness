#!/bin/sh
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
umask 077
# Usage: scripts/delegate.sh [--cascade <haiku,sonnet,opus>] <task.md> [--resume-dirty]. Python 3.14, Git, no model dependencies.
# --resume-dirty (before or after the sheet, never with --cascade) accepts the clone an interrupted delegation left dirty.
# Resolve the task relative to the caller; the project root comes from this script.
set -eu
usage() {
    echo 'usage: scripts/delegate.sh [--cascade <liste>] <fichier-de-tâche> [--resume-dirty]' >&2
    exit 64
}
resume=0
for arg in "$@"; do
    if [ "$arg" = --resume-dirty ]; then resume=$((resume + 1)); fi
done
if [ "$resume" -gt 1 ]; then usage; fi
if [ "$resume" -eq 1 ]; then
    [ "$#" -eq 2 ] || usage
    if [ "$1" = --resume-dirty ]; then set -- "$2"; else set -- "$1"; fi
fi
use_cascade=0
if [ "$#" -eq 3 ] && [ "$1" = --cascade ]; then
    use_cascade=1
    cascade=$2
    shift 2
fi
if [ "$#" -ne 1 ] || [ "$1" = --cascade ]; then
    usage
fi
case "$1" in /*) task=$1 ;; *) task=$PWD/$1 ;; esac
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$use_cascade" -eq 1 ]; then
    # A cascade restarts a stage on the base (git reset --hard, git clean): it never starts on a clone an interruption
    # left dirty, and refuses like delegate.py does (outcome worktree_dirty, no model call).
    "${WORKSPACE_PYTHON:-python3}" "$script_dir/lib/delegate_interrupt.py" "$root" "$task" || exit $?
    exec "${WORKSPACE_PYTHON:-python3}" "$script_dir/lib/cascade.py" "$root" "$cascade" "$task"
fi
if [ "$resume" -eq 1 ]; then
    exec "${WORKSPACE_PYTHON:-python3}" "$script_dir/lib/delegate.py" "$root" "$task" --resume-dirty
fi
exec "${WORKSPACE_PYTHON:-python3}" "$script_dir/lib/delegate.py" "$root" "$task"
