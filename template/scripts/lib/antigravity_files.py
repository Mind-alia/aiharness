"""Mode « fichiers » d'Antigravity : aucune commande, les fichiers rendus sont appliqués par le circuit (Python 3.14, stdlib)."""

import os
import shutil
import stat
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from safety import check_no_symlinks
from scope import matches

OUTBOX = '.workspace/antigravity/out'
LISTING_LIMIT = 200
SKIPPED_DIRS = ('.git', '.workspace', 'node_modules', '__pycache__')


def active(task):
    return task.get('agent') == 'antigravity' and task.get('mode') == 'write'


def reset_outbox(work_dir):
    outbox = Path(work_dir) / OUTBOX
    if outbox.is_symlink():
        outbox.unlink()
    elif outbox.exists():
        shutil.rmtree(outbox)
    outbox.mkdir(parents=True)
    return outbox


def scope_files(task, work_dir):
    """Existing files of the scope, relative POSIX paths, links not followed."""
    found = []
    for directory, dirs, files in os.walk(work_dir, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS)
        for name in sorted(files):
            path = Path(directory) / name
            rel = path.relative_to(work_dir).as_posix()
            if not path.is_symlink() and any(matches(rel, pattern) for pattern in task['allowed']):
                found.append(rel)
                if len(found) >= LISTING_LIMIT:
                    return found
    return found


def prompt_section(task, work_dir):
    existing = scope_files(task, work_dir)
    listing = '\n'.join('- ' + rel for rel in existing) or '- (aucun fichier existant dans le périmètre)'
    return (
        "Mode fichiers (aucune commande) :\n"
        "- Ne lance aucune commande : elles sont refusées.\n"
        "- Fichiers existants du périmètre, à lire en place :\n" + listing + "\n"
        "- Écris chaque fichier modifié ou créé, en entier, sous `" + OUTBOX + "/<chemin relatif>`.\n"
        "- Le circuit applique ces fichiers dans le dossier de travail (ceux du périmètre seulement), "
        "puis lance lui-même la vérification."
    )


def apply_outbox(work_dir, allowed):  # noqa: C901
    work_dir = Path(work_dir)
    applied, refused = [], []
    outbox = work_dir / OUTBOX
    if outbox.is_symlink():
        return [], [{'path': OUTBOX, 'reason': 'unsafe'}]
    if not outbox.is_dir():
        return [], []

    def walk(directory, prefix):
        for entry in sorted(os.scandir(directory), key=lambda e: e.name):
            rel = prefix + entry.name
            mode = os.lstat(entry.path).st_mode
            if stat.S_ISDIR(mode):
                walk(entry.path, rel + '/')
            elif not stat.S_ISREG(mode):
                refused.append({'path': rel, 'reason': 'unsafe'})
            elif not any(matches(rel, pattern) for pattern in allowed):
                refused.append({'path': rel, 'reason': 'out_of_scope'})
            else:
                copy(Path(entry.path), rel)

    def copy(source, rel):
        target = work_dir / rel
        try:
            check_no_symlinks(target, work_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            check_no_symlinks(target.parent, work_dir)
            target.write_bytes(source.read_bytes())
        except (ValueError, OSError):
            refused.append({'path': rel, 'reason': 'unsafe'})
        else:
            applied.append(rel)

    walk(outbox, '')
    return sorted(applied), sorted(refused, key=lambda item: item['path'])
