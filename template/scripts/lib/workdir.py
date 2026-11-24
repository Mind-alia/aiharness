"""Dossier de travail d'une tâche : un clone sans historique, hors de l'arbre du projet (Python 3.14, stdlib seule).

`$WORKSPACE_WORK_DIR` (chemin absolu) ou `~/.workspace-ia/work` contient un sous-dossier par projet
(`<nom>-<12 hexadécimaux du chemin réel>`), lui-même un dossier par tâche. L'état d'une tâche
enregistre le chemin absolu dans `state['worktree']` ; un chemin relatif (tâche ancienne, worktree
d'intégration) se lit depuis la racine du projet.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

WORK_DIR_VARIABLE = 'WORKSPACE_WORK_DIR'
PROJECT_POINTER = 'workspace-root'
# Un clone n'exécute jamais de crochet, de moniteur de fichiers ni de fichier d'attributs global.
GIT_HARDENING = ['-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=/dev/null']
# Clés de configuration (en minuscules, comme `git config --list`) par lesquelles git lance un programme.
SAFE_CONFIG_KEYS = (
    'core.repositoryformatversion',
    'core.filemode',
    'core.bare',
    'core.logallrefupdates',
    'core.ignorecase',
    'core.precomposeunicode',
    'core.symlinks',
    'core.hookspath',
    'core.fsmonitor',
    'user.name',
    'user.email',
)
# Admises à cette valeur exacte seulement.
SAFE_CONFIG_VALUES = {'core.fsmonitor': 'false', 'core.hookspath': '/dev/null', 'core.bare': 'false', 'core.repositoryformatversion': '0'}
# Autres sources de configuration sous `.git/` : toute présence est refusée.
EXTRA_CONFIG_SOURCES = ('config.worktree', 'commondir', 'worktrees')
GIT_DIR_NOT_DIRECTORY = '.git (pas un dossier)'
CONFIG_SYMLINK = '.git/config (lien symbolique)'
CONFIG_ABSENT = '.git/config (absent)'
EXTRA_SOURCE_REFUSED = '.git/%s (source de configuration refusée)'
CONFIG_UNREADABLE = '.git/config (illisible)'
CONFIG_READ_TIMEOUT = 30


def git_command(*args: str, cwd: Path | None = None) -> list[str]:
    """Ligne de commande git du circuit, durcie, à exécuter dans `cwd` s'il est donné."""
    command = ['git', *GIT_HARDENING]
    if cwd is not None:
        command += ['-C', str(cwd)]
    return command + list(args)


def unsafe_clone_config(clone: Any) -> list[str]:  # noqa: C901
    """Clés refusées (minuscules, triées, sans doublon) de `<clone>/.git/config` ; rien n'est exécuté ni inclus."""
    git_dir = Path(clone) / '.git'
    config = git_dir / 'config'
    if git_dir.is_symlink() or not git_dir.is_dir():
        return [GIT_DIR_NOT_DIRECTORY]
    if config.is_symlink():
        return [CONFIG_SYMLINK]
    if not config.exists():
        return [CONFIG_ABSENT]
    try:
        done = subprocess.run(
            git_command('config', '--file', str(config), '--no-includes', '--list', '-z'),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=CONFIG_READ_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return [CONFIG_UNREADABLE]
    if done.returncode != 0:
        return [CONFIG_UNREADABLE]
    refused = set()
    for name in EXTRA_CONFIG_SOURCES:
        source = git_dir / name
        if source.exists() or source.is_symlink():
            refused.add(EXTRA_SOURCE_REFUSED % name)
    for entry in done.stdout.decode('utf-8', errors='replace').split('\0'):
        if not entry:
            continue
        key, _, value = entry.partition('\n')
        key = key.lower()
        if key in SAFE_CONFIG_KEYS and value == SAFE_CONFIG_VALUES.get(key, value):
            continue
        refused.add(key)
    return sorted(refused)


def check_clone_config(clone: Any) -> None:
    """Refuse un clone dont la configuration git ferait exécuter un programme par les `git` de l'hôte."""
    refused = unsafe_clone_config(clone)
    if refused:
        raise ValueError('configuration git du clone refusée : ' + ', '.join(refused))


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def work_base(root: Any) -> Path:
    """Dossier des tâches du projet `root`, distinct pour deux projets de même nom."""
    real = Path(root).resolve()
    configured = os.environ.get(WORK_DIR_VARIABLE)
    if configured is None:
        parent = Path.home() / '.workspace-ia' / 'work'
    elif Path(configured).is_absolute():
        parent = Path(configured)
    else:
        raise ValueError(f"{WORK_DIR_VARIABLE} doit être un chemin absolu : '{configured}'")
    # Chemin réel : /var/folders ou /tmp sont des liens symboliques sur macOS, et le circuit les refuse.
    suffix = hashlib.sha256(str(real).encode('utf-8')).hexdigest()[:12]
    return Path(os.path.realpath(str(parent))) / f'{real.name}-{suffix}'


def task_worktree(root: Any, task_id: str) -> Path:
    """Clone de la tâche `task_id` ; refuse un dossier de travail situé dans le projet."""
    if not isinstance(task_id, str) or not re.fullmatch(r'[a-z0-9-]+', task_id):
        raise ValueError(f"Identifiant de tâche invalide : '{task_id}'")
    path = work_base(root) / task_id
    if _inside(path, Path(root).resolve()):
        raise ValueError(f"Le dossier de travail doit être hors du projet ({path}) : changer {WORK_DIR_VARIABLE}.")
    return path


def state_worktree(root: Any, state: dict[str, Any]) -> Path:
    """Chemin enregistré dans `state['worktree']` : absolu, ou relatif à la racine du projet."""
    recorded = state.get('worktree') if isinstance(state, dict) else None
    if not isinstance(recorded, str) or not recorded:
        raise ValueError("Champ 'worktree' manquant ou invalide dans l'état de la tâche.")
    path = Path(recorded)
    return path if path.is_absolute() else Path(root) / path


def is_clone_state(state: dict[str, Any]) -> bool:
    """Vrai pour une tâche à clone (chemin absolu) ; faux pour un git worktree ancien (chemin relatif)."""
    recorded = state.get('worktree') if isinstance(state, dict) else None
    return isinstance(recorded, str) and Path(recorded).is_absolute()


def record_project(clone: Path, root: Any) -> None:
    """Note, dans le `.git` du clone, le projet qui l'a préparé : une copie des scripts y retrouve son registre."""
    pointer = Path(clone) / '.git' / PROJECT_POINTER
    pointer.write_text(str(Path(root).resolve()) + '\n', encoding='utf-8')
    pointer.chmod(0o600)


def project_of(clone: Any) -> Path | None:
    """Projet noté par `record_project`, ou None ; le pointeur n'est jamais cru sur parole.

    Le registre du projet doit enregistrer ce clone pour cette tâche."""
    clone = Path(clone)
    try:
        pointer = clone / '.git' / PROJECT_POINTER
        if pointer.is_symlink() or not pointer.is_file():
            return None
        project = Path(pointer.read_text(encoding='utf-8').strip())
        if not project.is_absolute():
            return None
        state = json.loads((project / '.workspace/orchestration/tasks' / clone.name / 'state.json').read_text(encoding='utf-8'))
        recorded = state_worktree(project, state)
    except (OSError, ValueError):
        return None
    return project if os.path.realpath(str(recorded)) == os.path.realpath(str(clone)) else None


def containing_task(root: Any, path: Any) -> Path | None:
    """Clone de tâche de `root` qui contient `path`, ou None."""
    try:
        relative = Path(os.path.realpath(str(path))).relative_to(work_base(root))
    except ValueError:
        return None
    return work_base(root) / relative.parts[0] if relative.parts else None


def check_no_symlinks_below(base: Path, path: Path) -> None:
    """Aucun composant de `base` (inclus) à `path` n'est un lien symbolique ; ce qui est au-dessus de `base` n'est pas lu."""
    if not _inside(path, base):
        raise ValueError(f"Chemin hors du dossier de travail {base} : {path}")
    current = path
    while True:
        if current.is_symlink():
            raise ValueError(f"Lien symbolique (symlink) interdit : {current}")
        if current == base or current == current.parent:
            return
        current = current.parent


def check_private_owner(directory: Path) -> None:
    """Un dossier existant appartient à l'utilisateur courant et n'est inscriptible ni par le groupe ni par les autres."""
    info = directory.lstat()
    if info.st_uid != os.getuid():
        raise ValueError(
            f"Le dossier {directory} n'appartient pas à l'utilisateur courant : corriger le propriétaire, puis chmod 700 {directory}"
        )
    if info.st_mode & 0o022:
        raise ValueError(f"Le dossier {directory} est inscriptible par le groupe ou les autres : chmod 700 {directory}")


def ensure_private_dir(path: Path, base: Path) -> None:
    """Crée `path` (et ses parents jusqu'à `base`) en 0700, sans suivre de lien symbolique.

    Le dossier de base (le parent de `base`) et `base` eux-mêmes, s'ils existent déjà, doivent être privés :
    le contrôle précède toute création."""
    for existing in (base.parent, base):
        if existing.is_dir() and not existing.is_symlink():
            check_private_owner(existing)
    missing = []
    current = path
    while not current.exists() and not current.is_symlink():
        missing.append(current)
        if current == current.parent:
            break
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
    check_no_symlinks_below(base, path)
