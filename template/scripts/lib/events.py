"""Journal d'événements du circuit, en ajout seul (Python 3.14, stdlib seule).

Un fichier par projet, `.workspace/events.jsonl`, une ligne JSON par transition.
Le journal s'ajoute aux registres existants sans les remplacer et n'est jamais
une condition du circuit : une écriture qui échoue écrit `events: ...` sur
stderr et rend la main.

Lecture : `read` ignore les lignes vides ou illisibles ; `read_errors(root)`
renvoie le nombre de lignes non vides ignorées dans tout le fichier, filtres
`since` et `task_id` non appliqués.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

import safety

sys.dont_write_bytecode = True

EVENTS_PATH = '.workspace/events.jsonl'
LOCK_PATH = '.workspace/orchestrator.json'
KINDS = (
    'prepared',
    'delegate_started',
    'attempt_started',
    'attempt_finished',
    'verify',
    'acceptance',
    'outcome',
    'merged',
    'closed',
    'quota',
    'orchestrator_claimed',
    'orchestrator_released',
    'selfcheck',
    'subagent_started',
    'subagent_finished',
    'question_answered',
    'note',
    'state',
    'reading_measured',
    'human_intervention',
    'tool_refused',
    'breaker_instrument',
    'cascade_step',
    'correction_reverted',
)


def _warn(error):
    print('events: ' + str(error).replace('\n', ' '), file=sys.stderr)


def _orchestrator(root):
    """Session de l'orchestrateur lue dans le verrou, `None` si absente ou illisible (jamais d'exception)."""
    path = root / LOCK_PATH
    try:
        safety.check_no_symlinks(path, root)
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, RecursionError):
        return None
    session = data.get('session') if isinstance(data, dict) else None
    return session if isinstance(session, str) and session else None


def record(root, task_id, event, detail=None):
    """Ajoute un événement, `True` s'il est écrit, `False` sinon. `ValueError` si `event` est inconnu ou `task_id` vide."""
    if event not in KINDS:
        raise ValueError('événement inconnu : %r' % (event,))
    if not isinstance(task_id, str) or not task_id:
        raise ValueError('task_id vide')
    root = Path(root)
    line = (
        json.dumps(
            {
                'at': datetime.datetime.now(datetime.UTC).isoformat(),
                'task_id': task_id,
                'event': event,
                'detail': detail,
                'orchestrator': _orchestrator(root),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        + '\n'
    )
    path = root / EVENTS_PATH
    try:
        safety.check_no_symlinks(path, root)
        if not path.parent.exists():
            path.parent.mkdir(mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            os.write(fd, line.encode('utf-8'))
        finally:
            os.close(fd)
    except (OSError, ValueError) as error:
        _warn(error)
        return False
    return True


def _instant(value):
    """Instant UTC d'une date, d'un datetime ou d'une chaîne ISO 8601 ; naïf = UTC."""
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
    elif not isinstance(value, datetime.datetime):
        value = datetime.datetime(value.year, value.month, value.day)
    return value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value


def _scan(root):
    root = Path(root)
    path = root / EVENTS_PATH
    found, errors = [], 0
    try:
        if not path.exists() and not path.is_symlink():
            return found, errors
        safety.check_no_symlinks(path, root)
        with path.open(encoding='utf-8', errors='replace') as stream:
            for raw in stream:
                if not raw.strip():
                    continue
                try:
                    entry = json.loads(raw)
                    if not (isinstance(entry, dict) and isinstance(entry.get('task_id'), str) and isinstance(entry.get('event'), str)):
                        raise ValueError('ligne incomplète')
                    entry['_instant'] = _instant(entry['at'])
                except (ValueError, TypeError, KeyError):
                    errors += 1
                    continue
                found.append(entry)
    except (OSError, ValueError) as error:
        _warn(error)
    return found, errors


def read(root, since=None, task_id=None):
    """Événements dans l'ordre du fichier ; `since` (date ou datetime) compare sur l'instant UTC."""
    limit = _instant(since) if since is not None else None
    selected = []
    for entry in _scan(root)[0]:
        instant = entry.pop('_instant')
        if (limit is None or instant >= limit) and (task_id is None or entry['task_id'] == task_id):
            selected.append(entry)
    return selected


def read_errors(root):
    """Nombre de lignes non vides ignorées parce qu'illisibles."""
    return _scan(root)[1]
