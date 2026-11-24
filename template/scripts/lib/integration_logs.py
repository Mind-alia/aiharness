"""Journaux de la porte d'intégration, copiés avant la suppression de son worktree.

Les valeurs secrètes sont masquées avant écriture ; les fichiers sont créés en 0600.
Python 3.14 standard library uniquement.
"""

from __future__ import annotations

import fnmatch
import os
import stat
from pathlib import Path
from secrets import mask_secrets

GATE_LOG = '.gate-*.log'
GATE_OUTPUT = 'gate-output.log'
KEEP_BYTES = 5 * 1024 * 1024


def folder(root, task_id) -> Path:
    return Path(root) / '.workspace/orchestration/tasks' / task_id / 'integration'


def _make_folder(root, target: Path) -> None:
    # Every directory from root down is created 0700, parents included.
    current = Path(root)
    for part in target.relative_to(root).parts:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ValueError("journaux d'intégration : chemin non sûr " + str(current))
        if not current.exists():
            os.mkdir(current, 0o700)


def _write(directory: Path, name: str, text: str) -> None:
    path = directory / name
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o600), 'w', encoding='utf-8') as stream:
        stream.write(text)


def _tail(path: Path, values) -> str:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        stream.seek(max(0, size - KEEP_BYTES))
        text = stream.read().decode('utf-8', errors='replace')
    return mask_secrets(text, values)


def _gate_logs(integration: Path) -> list[Path]:
    # First level only; symbolic links and directories are never copied.
    names = sorted(
        entry.name
        for entry in os.scandir(integration)
        if fnmatch.fnmatchcase(entry.name, GATE_LOG) and entry.is_file(follow_symlinks=False)
    )
    return [integration / name for name in names]


def keep(root, task_id, integration, output: str, values) -> None:
    directory = folder(root, task_id)
    _make_folder(root, directory)
    for path in _gate_logs(Path(integration)):
        _write(directory, path.name, _tail(path, values))
    _write(directory, GATE_OUTPUT, mask_secrets(output, values))


def listing(root, task_id) -> list[str]:
    directory = folder(root, task_id)
    if directory.is_symlink() or not directory.is_dir():
        return []
    paths = sorted(directory.iterdir(), key=lambda path: path.name)
    return ["journal d'intégration : " + str(path.relative_to(root)) for path in paths if stat.S_ISREG(path.lstat().st_mode)]
