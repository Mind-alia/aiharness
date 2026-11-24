"""Reprise d'un identifiant de tâche abandonnée (`prepare --retry`).

Ne supprime rien : la branche est renommée, les dossiers d'état et d'exécution
sont déplacés sous `.workspace/orchestration/archive/<id>-tentative<N>/`.
Python 3.14 standard library uniquement.
"""

import datetime
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import state_view
import workdir
from mission_git import run_git
from mission_rules import check_path_security

ARCHIVE_REL = '.workspace/orchestration/archive'


def _branch_exists(root: Path, branch: str) -> bool:
    res = run_git(root, ['rev-parse', '--verify', '--quiet', '--end-of-options', 'refs/heads/' + branch], check=False)
    return res.returncode == 0


def _count_attempts(archive_dir: Path, task_id: str) -> int:
    if not archive_dir.is_dir():
        return 0
    pattern = re.compile(re.escape(task_id) + r'-tentative\d+')
    return sum(1 for entry in archive_dir.iterdir() if pattern.fullmatch(entry.name) and entry.is_dir())


def _check_retry_preconditions(root: Path, task_id: str, load_state: Callable[[], dict[str, Any]]) -> tuple[int, Path, Path]:
    state_file = root / '.workspace/orchestration/tasks' / task_id / 'state.json'
    check_path_security(state_file.parent, root, "dossier d'état de la tâche")
    if not state_file.exists() and not state_file.is_symlink():
        raise ValueError(f"Refus de --retry : aucune tâche enregistrée pour {task_id}.")
    state = load_state()
    status = state.get('status')
    if status != 'abandoned':
        raise ValueError(f"Refus de --retry : la tâche {task_id} est au statut '{status}' ; seule une tâche 'abandoned' se reprend.")

    # L'ancien dossier de travail (chemin enregistré) comme celui que préparerait un nouveau prepare.
    for worktree in (workdir.state_worktree(root, state), workdir.task_worktree(root, task_id)):
        if worktree.exists() or worktree.is_symlink():
            raise ValueError(f"Refus de --retry : le worktree {worktree} existe encore.")

    archive_dir = root / ARCHIVE_REL
    check_path_security(archive_dir, root, "dossier d'archive")
    check_path_security(root / '.workspace/run/delegate' / task_id, root, "dossier d'exécution de la tâche")
    attempt = _count_attempts(archive_dir, task_id) + 1
    target = archive_dir / f"{task_id}-tentative{attempt}"
    archived_branch = f"archive/workspace-task/{task_id}-tentative{attempt}"
    if target.exists() or target.is_symlink():
        raise ValueError(f"Refus de --retry : l'archive {target} existe déjà.")
    if _branch_exists(root, archived_branch):
        raise ValueError(f"Refus de --retry : la branche {archived_branch} existe déjà.")
    return attempt, target, state_file


def prepare_retry(root: Path, task_id: str, load_state: Callable[[], dict[str, Any]], check_reservation: Callable[[], None]) -> int:
    """Archive la tentative abandonnée de `task_id` et renvoie son numéro N (à appeler sous le verrou)."""
    attempt, target, state_file = _check_retry_preconditions(root, task_id, load_state)
    check_reservation()

    branch = f"workspace-task/{task_id}"
    archived_branch = f"archive/workspace-task/{task_id}-tentative{attempt}"
    run_dir = root / '.workspace/run/delegate' / task_id
    undo: list[Callable[[], None]] = []
    try:
        renamed = _branch_exists(root, branch)
        if renamed:
            run_git(root, ['branch', '-m', '--', branch, archived_branch])
            undo.append(lambda: run_git(root, ['branch', '-m', '--', archived_branch, branch], check=False))
        state = json.loads(state_file.read_text(encoding='utf-8'))
        state['archived_at'] = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        state['archived_branch'] = archived_branch if renamed else None
        state_view.write(root, task_id, state, 'retry')

        target.parent.mkdir(mode=0o700, exist_ok=True)
        target.parent.chmod(0o700)
        os.replace(state_file.parent, target)
        undo.append(lambda: os.replace(target, state_file.parent))
        if run_dir.exists():
            os.replace(run_dir, target / 'run')
        state_view.removed(root, task_id, 'archive')
    except Exception:
        for step in reversed(undo):
            try:
                step()
            except Exception:
                pass
        raise
    return attempt
