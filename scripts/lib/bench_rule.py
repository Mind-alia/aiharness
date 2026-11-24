"""Règle de la porte : le banc d'attaque doit être vert après tout changement du bac à sable.

Le banc (attack_bench.py) enregistre son dernier passage dans RECORD. La porte refuse si le
bac à sable a changé depuis ce passage, si ce passage était rouge, ou s'il n'y en a aucun.
Python 3.14, bibliothèque standard seule.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

LIB_DIR = Path(__file__).resolve().parent
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from safety import check_no_symlinks  # noqa: E402

RECORD = '.workspace/security/last-attack-bench.json'
WATCHED = ('template/scripts/lib/sandbox.py', 'template/scripts/run-in-sandbox.sh', 'template/docker')
DOCKER_DIR = 'template/docker'
SHORT_COMMIT_LENGTH = 7


def applies(root: Path) -> bool:
    """Règle 1 : la porte ne s'applique que si un chemin surveillé existe (template/docker en dossier)."""
    return (root / WATCHED[0]).exists() or (root / WATCHED[1]).exists() or (root / DOCKER_DIR).is_dir()


def check(root: Path) -> tuple[bool, str]:
    """Retourne (ok, message) ; le message est vide si ok, sinon une seule ligne."""
    record, reason = _read_record(root)
    if record is None:
        return False, _refusal(reason)
    if record.get('green') is not True:
        return False, _refusal('dernier banc rouge')
    commit = record['commit']
    if _git(root, 'cat-file', '-e', commit + '^{commit}') is None:
        return False, _refusal(f'commit du banc introuvable : {commit}')
    changed = _changed_since(root, commit)
    if changed is None:
        return False, _refusal('git indisponible')
    if changed:
        short = commit[:SHORT_COMMIT_LENGTH]
        return False, _refusal(f"modifié depuis le banc {short} : {', '.join(changed)}")
    return True, ''


def _refusal(reason: str) -> str:
    return f"banc d'attaque requis : {reason} ; lancer bash scripts/attack-bench.sh <tâche préparée>"


def _read_record(root: Path) -> tuple[dict | None, str]:
    """Lit l'enregistrement ; retourne (contenu, '') ou (None, raison)."""
    record = root / RECORD
    if not os.path.lexists(record):
        return None, 'aucun banc enregistré'
    try:
        check_no_symlinks(record, root)
        if not record.is_file():
            return None, 'enregistrement illisible'
        data = json.loads(record.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, 'enregistrement illisible'
    if not isinstance(data, dict) or not _valid_commit(data.get('commit')):
        return None, 'enregistrement illisible'
    return data, ''


def _valid_commit(commit: object) -> bool:
    # Un identifiant qui commence par '-' serait lu comme une option de git.
    return isinstance(commit, str) and bool(commit.strip()) and not commit.startswith('-')


def _changed_since(root: Path, commit: str) -> list[str] | None:
    """Fichiers surveillés changés depuis le commit, suivis ou non ; None si git échoue."""
    tracked = _git(root, 'diff', '--name-only', commit, '--', *WATCHED)
    untracked = _git(root, 'ls-files', '--others', '--exclude-standard', '--', *WATCHED)
    if tracked is None or untracked is None:
        return None
    return sorted(set(tracked.splitlines()) | set(untracked.splitlines()))


def _git(root: Path, *args: str) -> str | None:
    """Sortie standard de git, ou None si la commande échoue."""
    try:
        res = subprocess.run(['git', *args], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError:
        return None
    return res.stdout if res.returncode == 0 else None
