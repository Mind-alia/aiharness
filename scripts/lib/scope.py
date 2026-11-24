"""Read-only scope checks, Python 3.14, no third-party dependencies.

Patterns are root-relative POSIX paths: fnmatchcase on individual segments
(*, ? and [] never cross '/'); a whole ** segment matches zero or more
segments, including hidden names. Paths and patterns are case-sensitive.
Baseline JSON stores [file type/permissions, SHA-256], or null for deleted paths. Paths absent
from the baseline are compared with HEAD. The baseline file itself is excluded
because creating it is bookkeeping, not task output. No other path is excluded.
"""

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

# Reading the shared parser must not create __pycache__ in the project.
sys.dont_write_bytecode = True


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def minimal_task(path):  # noqa: C901
    """Fallback for the documented scalar id + block list allowed YAML subset."""
    lines = Path(path).read_text().splitlines()
    if not lines or lines[0].strip() != '---':
        raise ValueError('en-tête de tâche manquant')
    result = {}
    in_allowed = False
    closed = False
    for line in lines[1:]:
        if line.strip() == '---':
            closed = True
            break
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        item = re.match(r'^\s+-\s+(.+)$', line)
        if in_allowed and item:
            result['allowed'].append(scalar(item.group(1)))
            continue
        if line[:1].isspace():
            raise ValueError('en-tête de tâche invalide')
        key, sep, value = line.partition(':')
        if not sep:
            raise ValueError('en-tête de tâche invalide')
        in_allowed = key == 'allowed'
        if in_allowed:
            if value.strip() and not value.strip().startswith('#'):
                raise ValueError('allowed doit être une liste en bloc')
            result['allowed'] = []
        elif key == 'id':
            result['id'] = scalar(value)
    if not closed:
        raise ValueError('en-tête de tâche non terminé')
    return result


def scalar(value):
    value = re.split(r'\s+#', value.strip(), maxsplit=1)[0].strip()
    if value.startswith(('"', "'")):
        value = ast.literal_eval(value)
    return value


def read_task(path):
    # Prefer the common parser when installed alongside this module.
    if Path(__file__).with_name('task.py').exists():
        import task

        loader = getattr(task, 'read_task', None) or getattr(task, 'load_task', None) or getattr(task, 'parse_task', None)
        if loader is None:
            raise ValueError('task.py doit exposer read_task')
        data = loader(path)
    else:
        data = minimal_task(path)
    if not isinstance(data, dict):
        raise ValueError('format de tâche invalide')
    task_id = data.get('id', '')
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', task_id):
        raise ValueError('id de tâche invalide')
    allowed = data.get('allowed')
    if not isinstance(allowed, list) or not all(isinstance(p, str) and p for p in allowed):
        raise ValueError('allowed doit être une liste de motifs')
    for pattern in allowed:
        if pattern.startswith('/') or '..' in pattern.split('/'):
            raise ValueError('motif allowed hors de la racine')
    return data


def changed_paths(root):
    records = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all').split(b'\0')
    paths = set()
    index = 0
    while index < len(records) and records[index]:
        record = records[index]
        paths.add(os.fsdecode(record[3:]))
        index += 1
        if b'R' in record[:2] or b'C' in record[:2]:
            paths.add(os.fsdecode(records[index]))
            index += 1
    from secrets import secret_files

    paths.update(str(p.relative_to(root)) for p in secret_files(root))
    # Snapshot all tracked paths, so mode-only changes are compared too.
    paths.update(os.fsdecode(p) for p in git(root, 'ls-files', '-z').split(b'\0') if p)
    return {p for p in paths if not p.startswith('.workspace/run/')}


def fingerprint(path):
    if path.is_symlink():
        return [path.lstat().st_mode, hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()]
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError('type de fichier non pris en charge : ' + str(path))
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return [path.lstat().st_mode, digest.hexdigest()]


def head_fingerprint(root, path):
    result = subprocess.run(['git', '-C', str(root), 'show', 'HEAD:' + path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        return None
    return hashlib.sha256(result.stdout).hexdigest()


def matches(path, pattern):
    parts, patterns = path.split('/'), pattern.removeprefix('./').split('/')

    @lru_cache(None)
    def match(i, j):
        if j == len(patterns):
            return i == len(parts)
        if patterns[j] == '**':
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], patterns[j]) and match(i + 1, j + 1)

    return match(0, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['baseline', 'check'])
    parser.add_argument('task')
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        data = read_task(args.task)
        root = Path(os.fsdecode(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'])).strip())
        baseline = args.baseline or root / '.workspace/run/delegate' / data['id'] / 'baseline.json'
        from task import safe_path

        baseline = safe_path(baseline)
        paths = changed_paths(root)
        if args.operation == 'baseline':
            files = {p: fingerprint(root / p) for p in sorted(paths) if root / p != baseline}
            baseline.parent.mkdir(parents=True, exist_ok=True)
            baseline.write_text(json.dumps({'version': 1, 'files': files}, indent=2) + '\n')
            print(baseline)
            return 0
        saved = json.loads(baseline.read_text())
        if saved.get('version') != 1 or not isinstance(saved.get('files'), dict):
            raise ValueError('ligne de base invalide')
        files = saved['files']
        violations = []
        for path in sorted(paths | files.keys()):
            if root / path == baseline:
                continue
            before = files[path] if path in files else head_fingerprint(root, path)
            if fingerprint(root / path) != before and not any(matches(path, p) for p in data['allowed']):
                violations.append(path)
        for path in violations:
            print('hors périmètre : ' + path)
        if violations:
            print('Fichiers modifiés pendant la tâche ; attribution à un agent non garantie.')
        return 1 if violations else 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('contrôle du périmètre : ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
