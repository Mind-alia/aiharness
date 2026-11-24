"""Correction round: a new delegation after the breaker must not break a state which passed.

Python 3.14, standard library only. The state before the round is saved under
.workspace/orchestration/tasks/<id>/before-correction; when the round makes the original
hidden tests fail, the worktree goes back to it and the breaker findings stay unresolved.
"""

import json
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import breaker
import events
import workdir
from task import safe_path

SNAPSHOT_DIR = 'before-correction'


def _status(root, task_id):
    try:
        data = json.loads(safe_path(Path(root) / '.workspace/run/delegate' / task_id / 'status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_correction_round(root, task):
    if _status(root, task['id']).get('outcome') not in ('done', 'done_with_warning'):
        return False
    return bool(breaker.extra_names(task, root) or breaker.instrument_names(task, root))


def _git(worktree, *args):
    result = subprocess.run(workdir.git_command(*args, cwd=worktree), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError('git %s : %s' % (args[0], result.stderr.decode(errors='replace').strip()))
    return result.stdout.decode(errors='replace')


def _names(output):
    return [name for name in output.split('\0') if name]


def _relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Chemin de manifeste invalide : ' + name)
    return path


def snapshot(worktree, base, dest):
    worktree, dest = Path(worktree), Path(dest)
    names = set(_names(_git(worktree, 'diff', '--name-only', '-z', '--no-renames', base, '--')))
    names.update(_names(_git(worktree, 'ls-files', '--others', '--exclude-standard', '-z')))
    files, deleted = [], []
    for name in sorted(names):
        path = worktree / _relative(name)
        if path.is_symlink():
            raise ValueError('Lien symbolique dans le worktree : ' + name)
        (files if path.is_file() else deleted).append(name)
    safe_path(dest)
    if dest.is_dir():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, mode=0o700)
    dest.chmod(0o700)
    for name in files:
        target = dest / 'files' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(worktree / name, target)
    (dest / 'manifest.json').write_text(
        json.dumps({'base': base, 'files': files, 'deleted': deleted}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )


def restore(worktree, base, dest):  # noqa: C901
    worktree, dest = Path(worktree), Path(dest)
    try:
        manifest = json.loads((dest / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ValueError('Manifeste de rétablissement absent ou illisible.')
    if not isinstance(manifest, dict) or manifest.get('base') != base:
        raise ValueError('Le manifeste est d\'une autre base.')
    files, deleted = manifest.get('files'), manifest.get('deleted')
    if not isinstance(files, list) or not isinstance(deleted, list):
        raise ValueError('Manifeste de rétablissement invalide.')
    for name in files + deleted:
        if not isinstance(name, str):
            raise ValueError('Manifeste de rétablissement invalide.')
        _relative(name)
    for name in files:
        if not (dest / 'files' / name).is_file():
            raise ValueError('Fichier enregistré absent : ' + name)
    _git(worktree, 'reset', '-q', '--hard', base)
    _git(worktree, 'clean', '-fdq')
    for name in files:
        target = worktree / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        shutil.copy2(dest / 'files' / name, target)
    for name in deleted:
        target = worktree / name
        if target.is_symlink() or target.is_file():
            target.unlink()


def run_round(root, task, worktree, base, run_agent, run_original):  # noqa: PLR0913
    outcome = run_agent()
    code, _ = run_original()
    if code == 0:
        return {'reverted': False, 'outcome': outcome, 'unresolved': []}
    restore(worktree, base, snapshot_path(root, task['id']))
    names = list(breaker.extra_names(task, root)) + list(breaker.instrument_names(task, root))
    events.record(root, task['id'], 'correction_reverted', {'unresolved': names, 'outcome': outcome, 'cause': 'implementation'})
    return {'reverted': True, 'outcome': 'done_with_warning', 'unresolved': names}


def snapshot_path(root, task_id):
    return Path(root) / '.workspace/orchestration/tasks' / task_id / SNAPSHOT_DIR


def unresolved(root, task_id) -> list[str]:
    found = _status(root, task_id).get('correction_reverted')
    names = found.get('unresolved') if isinstance(found, dict) else None
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []
