"""Lecture du registre et vérification en lecture seule d'une réservation pour mission.py.

Python 3.14 standard library uniquement.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from mission_rules import check_path_security, patterns_overlap, validate_allowed_patterns
from task import read_task

KNOWN_STATUSES = {
    'prepared',
    'running',
    'integrated',
    'rejected',
    'failed',
    'abandoned',
    'merged',
    'merge_conflict',
    'integration_failed',
    'done',
}
ACTIVE_STATUSES = ('prepared', 'running', 'merge_conflict', 'integration_failed')
CHECK_CONFLICT_EXIT = 3


def validate_state_data(state: Any, expected_task_id: str | None = None) -> None:
    if not isinstance(state, dict):
        raise ValueError("L'état de tâche doit être un dictionnaire JSON.")
    if state.get('schema_version') != 1:
        raise ValueError("schema_version différent de 1.")

    task_id = state.get('task_id')
    if not isinstance(task_id, str) or not re.fullmatch(r'[a-z0-9-]+', task_id):
        raise ValueError(f"Identifiant task_id invalide ou manquant : {task_id}")
    if expected_task_id is not None and task_id != expected_task_id:
        raise ValueError(f"Incohérence d'identifiant : task_id '{task_id}' ne correspond pas au dossier '{expected_task_id}'")

    status = state.get('status')
    if not isinstance(status, str) or status not in KNOWN_STATUSES:
        raise ValueError(f"Statut inconnu ou incohérent : '{status}'")

    allowed = state.get('allowed')
    if not isinstance(allowed, list) or not allowed:
        raise ValueError("Champ 'allowed' manquant, vide ou non liste.")
    for p in allowed:
        if not isinstance(p, str) or not p.strip():
            raise ValueError(f"Motif invalide dans 'allowed' : {p}")


def iter_registry_states(root: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    tasks_dir = root / '.workspace/orchestration/tasks'
    if not tasks_dir.exists():
        return
    if tasks_dir.is_symlink():
        raise ValueError("Lien symbolique (symlink) interdit sur le dossier des tâches.")

    for entry in tasks_dir.iterdir():
        if entry.is_symlink():
            raise ValueError(f"Lien symbolique (symlink) interdit dans le registre : {entry.name}")
        if not entry.is_dir():
            continue

        state_file = entry / 'state.json'
        if not state_file.is_file():
            raise ValueError(f"Dossier sans état final dans le registre : {entry.name}. Refus prudent de réservation.")
        if state_file.is_symlink():
            raise ValueError(f"Lien symbolique (symlink) interdit sur l'état de tâche : {state_file}")

        try:
            content = state_file.read_text(encoding='utf-8')
            state = json.loads(content)
        except Exception as exc:
            raise ValueError(f"Fichier d'état corrompu dans le registre ({entry.name}). Refus prudent de réservation.") from exc

        try:
            validate_state_data(state, expected_task_id=entry.name)
        except Exception as exc:
            raise ValueError(
                f"Fichier d'état corrompu ou invalide dans le registre ({entry.name}) : {exc}. Refus prudent de réservation."
            ) from exc

        yield entry.name, state


def resolve_task_file(root: Path, task_file_arg: str, caller_cwd: Path) -> Path:
    raw_task_path = Path(task_file_arg)
    unresolved_task_path = raw_task_path if raw_task_path.is_absolute() else (caller_cwd / raw_task_path)

    if not unresolved_task_path.exists() and not unresolved_task_path.is_symlink():
        raise ValueError(f"Fichier de tâche introuvable : {task_file_arg}")

    # Vérification des composants du chemin AVANT toute résolution (.resolve())
    curr = unresolved_task_path
    while True:
        if curr.is_symlink():
            raise ValueError(f"Lien symbolique (symlink) interdit pour le fichier de tâche : {curr}")
        if curr in (root, caller_cwd) or curr == curr.parent:
            break
        try:
            if curr.resolve() in (root.resolve(), caller_cwd.resolve()):
                break
        except Exception:
            pass
        curr = curr.parent

    task_path = unresolved_task_path.resolve()
    check_path_security(task_path, root, label="fichier de tâche")
    return task_path


def protected_pattern_lines(root: Path, allowed: list[str]) -> list[str]:
    lines = []
    for pattern in allowed:
        try:
            validate_allowed_patterns(root, [pattern])
        except ValueError:
            lines.append(f'protégé : {pattern}')
    return lines


def registry_lines(root: Path, task_id: str, allowed: list[str]) -> list[str]:
    states = [state for _, state in iter_registry_states(root)]
    lines = []
    for state in states:
        if state['task_id'] == task_id:
            lines.append(f"identifiant déjà enregistré : {task_id} ({state['status']})")
    for state in states:
        if state['status'] not in ACTIVE_STATUSES:
            continue
        for pattern in allowed:
            for registered_pattern in state['allowed']:
                if patterns_overlap(pattern, registered_pattern):
                    lines.append(f"conflit : {pattern} chevauche la tâche {state['task_id']} ({registered_pattern})")
    return lines


def cmd_check(root: Path, task_file_arg: str, caller_cwd: Path) -> int:
    task_path = resolve_task_file(root, task_file_arg, caller_cwd)
    task_data = read_task(task_path)
    task_id = task_data['id']
    allowed = task_data['allowed']

    lines = protected_pattern_lines(root, allowed) + registry_lines(root, task_id, allowed)
    if not lines:
        print(f'libre : {task_id}')
        return 0
    for line in lines:
        print(line)
    return CHECK_CONFLICT_EXIT
