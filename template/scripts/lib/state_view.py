"""Le journal est la source de l'état des tâches ; `state.json` en est une vue (Python 3.14, stdlib seule).

Chaque écriture d'état ajoute d'abord un événement `state` qui porte l'état complet,
puis régénère la vue `.workspace/orchestration/tasks/<id>/state.json` depuis cet état.
`rebuild` reconstruit les vues depuis le seul journal et signale tout écart.
Le journal n'est jamais une condition du circuit : si l'événement ne peut pas être
écrit, la vue l'est quand même et `state_view: ...` part sur stderr.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import events
import safety

sys.dont_write_bytecode = True

TASKS_REL = '.workspace/orchestration/tasks'
TASK_ID = re.compile(r'[a-z0-9-]+')


def _warn(message):
    print('state_view: ' + str(message).replace('\n', ' '), file=sys.stderr)


def serialize(state):
    """Forme canonique de la vue : clés triées, comme les lignes du journal."""
    return json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + '\n'


def _view_path(root, task_id):
    return Path(root) / TASKS_REL / task_id / 'state.json'


def _make_dirs(directory, root):
    """Crée les dossiers manquants entre `root` et `directory`, chacun en 0700."""
    missing = []
    current = directory
    while current != root and not current.exists() and not current.is_symlink():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir(mode=0o700)
        path.chmod(0o700)


def _write_view(root, task_id, state):
    """Écrit la vue : temporaire voisin exclusif en 0600, puis `os.replace`."""
    root = Path(root)
    view = _view_path(root, task_id)
    safety.check_no_symlinks(view, root)
    _make_dirs(view.parent, root)
    tmp = view.with_name('state.json.view.%d' % os.getpid())
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(serialize(state))
        os.replace(tmp, view)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _check_id(task_id):
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        raise ValueError('identifiant de tâche invalide : %r' % (task_id,))


def write(root, task_id, state, reason):
    """Événement `state` d'abord, vue `state.json` ensuite. `ValueError` si l'identifiant est invalide."""
    _check_id(task_id)
    if not events.record(root, task_id, 'state', {'reason': reason, 'state': state}):
        _warn("événement d'état non écrit pour " + task_id)
    _write_view(root, task_id, state)


def removed(root, task_id, reason):
    """Note dans le journal que l'état de la tâche n'existe plus ; aucune vue n'est touchée."""
    events.record(root, task_id, 'state', {'reason': reason, 'state': None})


def latest(root):
    """Identifiant -> dernier état du journal (`None` si retiré), dans l'ordre du fichier."""
    states = {}
    for entry in events.read(root):
        detail = entry.get('detail')
        if entry['event'] == 'state' and isinstance(detail, dict) and 'state' in detail:
            states[entry['task_id']] = detail['state']
    return states


def _read_view(root, task_id):
    """Octets de la vue, `None` si absente ; `ValueError` ou `OSError` si elle est illisible."""
    view = _view_path(root, task_id)
    if not view.exists() and not view.is_symlink():
        return None
    safety.check_no_symlinks(view, Path(root))
    return view.read_bytes()


def _view_ids(root):
    """Noms des dossiers de `tasks/` qui contiennent un `state.json` (lien compris)."""
    tasks = Path(root) / TASKS_REL
    try:
        names = sorted(path.name for path in tasks.iterdir() if (path / 'state.json').exists() or (path / 'state.json').is_symlink())
    except OSError:
        return []
    return names


def _adopt(root, task_id, raw):
    """Verse une vue ancienne dans le journal puis la réécrit en forme canonique ; `None` si elle n'est pas un objet."""
    try:
        state = json.loads(raw.decode('utf-8'))
    except (ValueError, RecursionError):
        return None
    if not isinstance(state, dict):
        return None
    write(root, task_id, state, 'adopt')
    return state


def rebuild(root, adopt=False):  # noqa: C901
    """Reconstruit les vues depuis le journal, affiche un compte rendu et renvoie 0 (aucun écart) ou 1."""
    root = Path(root)
    states = latest(root)
    counts = {'recreated': 0, 'same': 0, 'gaps': 0}

    def gap(task_id, why):
        counts['gaps'] += 1
        print('écart : %s : %s' % (task_id, why))

    for task_id in sorted(states):
        if not TASK_ID.fullmatch(task_id):
            gap(task_id, 'identifiant invalide, ignoré')
            continue
        state = states[task_id]
        try:
            raw = _read_view(root, task_id)
            if state is None:
                if raw is not None:
                    gap(task_id, 'vue présente, retirée dans le journal')
                continue
            if raw is None:
                _write_view(root, task_id, state)
                counts['recreated'] += 1
                print('recréée : ' + task_id)
            elif raw == serialize(state).encode('utf-8'):
                counts['same'] += 1
            else:
                gap(task_id, 'vue différente du journal')
        except (OSError, ValueError) as error:
            gap(task_id, 'vue illisible : ' + str(error).replace('\n', ' '))

    for task_id in _view_ids(root):
        if task_id in states:
            continue
        if not TASK_ID.fullmatch(task_id):
            gap(task_id, 'identifiant invalide, ignoré')
            continue
        try:
            raw = _read_view(root, task_id)
            if adopt and raw is not None and _adopt(root, task_id, raw) is not None:
                print('adoptée : ' + task_id)
            else:
                gap(task_id, 'vue sans événement d\'état')
        except (OSError, ValueError) as error:
            gap(task_id, 'vue illisible : ' + str(error).replace('\n', ' '))

    print('bilan : %d recréée(s), %d identique(s), %d écart(s)' % (counts['recreated'], counts['same'], counts['gaps']))
    return 1 if counts['gaps'] else 0
