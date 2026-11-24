"""Mesure de ce que l'exécutant lit avant d'agir (Python 3.14, stdlib seule).

Trois parts du message de l'exécutant : consigne commune, fiche, pièges. Les
tailles sont en caractères Python ; les tokens sont une estimation (caractères / 4).
"""

import json
import sys
from pathlib import Path

import events
import safety

sys.dont_write_bytecode = True

PARTS = ('common', 'task', 'pitfalls')
ESTIMATE = 'caractères / 4'


def measure(prompt, task_body, pitfalls_text):
    """Tailles des trois parts du message ; la consigne commune est le reste du message."""
    sizes = {'task': len(task_body), 'pitfalls': len(pitfalls_text)}
    sizes['common'] = len(prompt) - sizes['task'] - sizes['pitfalls']
    result = {}
    for part in PARTS:
        result[part + '_chars'] = sizes[part]
        result[part + '_tokens_estimated'] = sizes[part] // 4
    result['estimate'] = ESTIMATE
    return result


def _valid(entry):
    """Détail d'un événement `reading_measured` s'il est sain, `None` sinon (jamais d'exception)."""
    detail = entry.get('detail')
    if not isinstance(detail, dict) or not isinstance(detail.get('role'), str) or not detail['role']:
        return None
    for part in PARTS:
        chars = detail.get(part + '_chars')
        tokens = detail.get(part + '_tokens_estimated')
        for value in (chars, tokens):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return None
        if tokens != chars // 4:
            return None
    return detail


def _read(root, task_id, since):
    """Événements du journal ; une ligne qui fait échouer `events.read` (date non texte, JSON trop imbriqué) est écartée seule."""
    try:
        return events.read(root, since=since, task_id=task_id)
    except Exception:
        pass
    selected = []
    try:
        path = Path(root) / events.EVENTS_PATH
        safety.check_no_symlinks(path, Path(root))
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        limit = events._instant(since) if since is not None else None
    except Exception:
        return selected
    for raw in lines:
        try:
            entry = json.loads(raw)
            instant = events._instant(entry['at'])
            if (
                entry['event'] == 'reading_measured'
                and (limit is None or instant >= limit)
                and (task_id is None or entry['task_id'] == task_id)
            ):
                selected.append(entry)
        except Exception:
            continue
    return selected


def _measured(root, task_id=None, since=None):
    found = []
    for entry in _read(root, task_id, since):
        if entry.get('event') == 'reading_measured':
            detail = _valid(entry)
            if detail is not None:
                found.append(detail)
    return found


def task_line(root, task_id):
    """Ligne de `report.sh --task` : dernière mesure valide de la tâche."""
    found = _measured(root, task_id=task_id)
    if not found:
        return "Lecture avant d'agir : non mesurée."
    detail = found[-1]
    parts = [
        '%s %d caractères (~%d tokens estimés)' % (label, detail[part + '_chars'], detail[part + '_tokens_estimated'])
        for label, part in (('consigne commune', 'common'), ('fiche', 'task'), ('pièges', 'pitfalls'))
    ]
    return "Lecture avant d'agir : " + ', '.join(parts) + '.'


def collect(root, since=None):
    """Moyennes entières par rôle sur les mesures valides depuis `since` (date ou `None`)."""
    groups = {}
    for detail in _measured(root, since=since):
        groups.setdefault(detail['role'], []).append(detail)
    result = {}
    for role, group in groups.items():
        result[role] = {'delegations': len(group)}
        for part in PARTS:
            result[role][part + '_chars'] = sum(d[part + '_chars'] for d in group) // len(group)
    return result
