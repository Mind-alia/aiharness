"""Read the team registry team.json: named members mapped to executors.

Public API: find_registry(start) -> Path | None, load(path) -> list of members,
resolve(name, start) -> executor name (case-sensitive member lookup).
"""

import json
from pathlib import Path

EXECUTORS = ('codex', 'antigravity', 'claude-haiku', 'claude-sonnet', 'claude-opus')
REGISTRY_NAME = 'team.json'


def find_registry(start):
    """First regular team.json in start or its parents; symbolic links are refused."""
    directory = Path(start).absolute()
    for folder in (directory, *directory.parents):
        candidate = folder / REGISTRY_NAME
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def load(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('team.json invalide : ' + str(exc)) from exc
    if not isinstance(data, dict) or not isinstance(data.get('membres'), list):
        raise ValueError('team.json invalide : objet avec une liste membres attendu')
    names = set()
    for member in data['membres']:
        if not isinstance(member, dict):
            raise ValueError('team.json invalide : membre non objet')
        name = member.get('nom')
        if not isinstance(name, str) or not name:
            raise ValueError('team.json invalide : nom de membre non vide attendu')
        if name in names:
            raise ValueError('team.json invalide : nom en double : ' + name)
        names.add(name)
        if member.get('executant') not in EXECUTORS:
            raise ValueError('team.json invalide : executant inconnu pour ' + name)
    return data['membres']


def resolve(name, start):
    registry = find_registry(start)
    members = load(registry) if registry else []
    for member in members:
        if member['nom'] == name:
            return member['executant']
    raise ValueError(f'agent inconnu : {name} (ni exécutant, ni membre de team.json)')
