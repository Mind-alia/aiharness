"""Primitives de sécurité partagées par les modules de template/scripts/lib (Python 3.14, stdlib seule)."""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def check_no_symlinks(path: Path, root: Path) -> None:
    """Vérifie qu'aucun composant entre root et path n'est un lien symbolique."""
    curr = path
    while True:
        if curr.is_symlink():
            raise ValueError(f"Lien symbolique (symlink) interdit : {curr}")
        if curr == root or curr.parent == curr:
            break
        curr = curr.parent


def write_file_secure(file_path: Path, content: str) -> None:
    """Écrit atomiquement ou directement un fichier avec permissions 0600 sans suivre les liens."""
    if file_path.is_symlink():
        raise ValueError(f"Lien symbolique (symlink) interdit : {file_path}")

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(file_path, flags, 0o600)
    except OSError as exc:
        if exc.errno == getattr(errno, 'ELOOP', None) or file_path.is_symlink():
            raise ValueError(f"Lien symbolique (symlink) interdit : {file_path}") from exc
        raise

    with open(fd, 'w', encoding='utf-8') as f:
        f.write(content)
        if not content.endswith('\n'):
            f.write('\n')

    file_path.chmod(0o600)
