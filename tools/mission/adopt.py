#!/usr/bin/env python3
"""Adopt a foreign Git repository as a mission: clone, branch, install the circuit (Python 3.14)."""

import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
NAME_PATTERN = re.compile(r'[a-z0-9][a-z0-9-]{0,62}')
IDENTITY = ['-c', 'user.name=Workspace IA', '-c', 'user.email=workspace-ia@example.invalid', '-c', 'core.hooksPath=/dev/null']
MAKEFILES = ('GNUmakefile', 'makefile', 'Makefile')
TEST_RULE = re.compile(r'^test[ \t]*:(?![=:])', re.MULTILINE)
KIT_EOL_RULE = re.compile(rb'^(\*\.\w+ text eol=lf)$', re.MULTILINE)
PYTEST_MARKERS = (('pyproject.toml', '[tool.pytest'), ('setup.cfg', '[tool:pytest]'), ('tox.ini', '[pytest]'))


def load_workspace():
    spec = importlib.util.spec_from_file_location('kit_workspace', KIT / 'scripts/workspace.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo, *args, check=True):
    return subprocess.run(['git', '-C', str(repo)] + list(args), check=check, capture_output=True)


def git_text(repo, *args):
    return git(repo, *args).stdout.decode('utf-8', 'replace').strip()


def derive_name(source):
    segment = re.split(r'[/:]', source.rstrip('/'))[-1]
    if segment in ('', '.', '..') and Path(source).exists():
        segment = Path(source).resolve().name
    if segment.endswith('.git'):
        segment = segment[:-4]
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9-]', '-', segment.lower())).strip('-')


def read_text(path):
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None


def detect_gate(repo):  # noqa: C901
    """Return the test command of a repository, or None."""
    repo = Path(repo)
    for name in MAKEFILES:
        if (repo / name).is_file():
            text = read_text(repo / name)
            if text is not None and TEST_RULE.search(text):
                return 'make test'
            break
    package = read_text(repo / 'package.json')
    if package is not None:
        try:
            script = json.loads(package)['scripts']['test']
        except (ValueError, KeyError, TypeError):
            script = None
        if isinstance(script, str) and script.strip() and 'no test specified' not in script:
            return 'npm test'
    if (repo / 'pytest.ini').is_file():
        return 'python3 -m pytest'
    for name, marker in PYTEST_MARKERS:
        text = read_text(repo / name)
        if text is not None and marker in text:
            return 'python3 -m pytest'
    for path in sorted(repo.glob('requirements*.txt')):
        text = read_text(path)
        if text is not None and 'pytest' in text:
            return 'python3 -m pytest'
    for folder in ('tests', 'test'):
        if any(path.is_file() for path in (repo / folder).glob('test_*.py')):
            return 'python3 -m pytest'
    return None


def tracked(repo, relative):
    return bool(git_text(repo, 'ls-files', '--', relative))


def git_path(repo, relative):
    path = Path(git_text(repo, 'rev-parse', '--git-path', relative))
    return path if path.is_absolute() else Path(repo) / path


def move_additions(repo, relative, original, exclude_name):
    """Restore a tracked file and keep what the installation appended in .git/info instead."""
    target = Path(repo) / relative
    installed = target.read_bytes()
    if installed == original:
        return
    if not installed.startswith(original):
        raise ValueError(f'Modification inattendue de {relative} par l’installation du circuit')
    addition = installed[len(original) :]
    if original and not original.endswith(b'\n'):
        addition = addition.lstrip(b'\n')
    target.write_bytes(original)
    exclude = git_path(repo, exclude_name)
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = exclude.read_bytes() if exclude.exists() else b''
    if current and not current.endswith(b'\n'):
        current += b'\n'
    exclude.write_bytes(current + addition)


def scope_line_endings(repo, original):
    """Limit the kit's line-ending rules to the circuit's scripts: the adopted repository keeps its own endings.

    A rule such as `*.py text eol=lf` makes Git rewrite every CRLF file of the repository at the first `git add`."""
    target = Path(repo) / '.gitattributes'
    installed = target.read_bytes() if target.exists() else b''
    if not installed.startswith(original):
        return
    addition = KIT_EOL_RULE.sub(rb'scripts/**/\1', installed[len(original) :])
    target.write_bytes(original + addition)


def install_circuit(repo, profile):
    originals = {}
    for relative in ('.gitignore', '.gitattributes'):
        if tracked(repo, relative):
            originals[relative] = (repo / relative).read_bytes()
    module = load_workspace()
    with redirect_stdout(io.StringIO()):
        module.install_tooling(repo, profile=profile)
    scope_line_endings(repo, originals.get('.gitattributes', b''))
    names = {'.gitignore': 'info/exclude', '.gitattributes': 'info/attributes'}
    for relative, original in originals.items():
        move_additions(repo, relative, original, names[relative])


def commit_circuit(repo, name, base_commit):
    git(repo, 'add', '-A')
    git(repo, *IDENTITY, 'commit', '-q', '--no-verify', '-m', f'chore(workspace): circuit installé pour la mission {name}')
    commit = git_text(repo, 'rev-parse', 'HEAD')
    if git_text(repo, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Dépôt non propre après l’installation du circuit')
    if git(repo, 'check-ignore', '-q', '.workspace/', check=False).returncode:
        raise ValueError('.workspace/ n’est pas ignoré dans le dépôt adopté')
    if git_text(repo, 'diff', '--name-only', '--diff-filter=MDRT', base_commit, 'HEAD'):
        raise ValueError('L’installation du circuit a modifié des fichiers du dépôt adopté')
    return commit


def prepare_mission(source, repo, name):
    result = subprocess.run(['git', 'clone', '-q', '--end-of-options', source, str(repo)], capture_output=True)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    default_branch = (
        subprocess.run(['git', '-C', str(repo), 'symbolic-ref', '--short', '-q', 'HEAD'], capture_output=True).stdout.decode().strip()
    )
    head = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--verify', '-q', 'HEAD'], capture_output=True)
    if not default_branch or head.returncode:
        raise ValueError(f'Dépôt sans branche courante ou sans commit : {source}')
    base_commit = head.stdout.decode().strip()
    git(repo, 'checkout', '-q', '-b', f'mission/{name}', base_commit)
    module = load_workspace()
    profile = module.detect_profile(repo)
    verify = detect_gate(repo)
    install_circuit(repo, profile)
    tooling_commit = commit_circuit(repo, name, base_commit)
    return {
        'name': name,
        'source': source,
        'repo': 'repo',
        'profile': profile,
        'verify': verify,
        'default_branch': default_branch,
        'base_commit': base_commit,
        'branch': f'mission/{name}',
        'tooling_commit': tooling_commit,
        'adopted_at': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }


def adopt(source, directory=None, name=None):
    """Clone `source` into a mission folder, install the circuit on `mission/<name>`, write mission.json."""
    source = str(source)
    name = name if name is not None else derive_name(source)
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError(f'Nom de mission invalide : {name!r} (attendu [a-z0-9][a-z0-9-]{{0,62}})')
    if source.startswith('-'):
        raise ValueError(f'Source refusée (commence par un tiret) : {source}')
    mission = Path(directory).expanduser().resolve() if directory is not None else KIT / '.workspace/missions' / name
    created = not mission.exists()
    if not created and (not mission.is_dir() or any(mission.iterdir())):
        raise ValueError(f'Le dossier de mission existe et n’est pas vide : {mission}')
    mission.mkdir(parents=True, exist_ok=True)
    try:
        record = prepare_mission(source, mission / 'repo', name)
        (mission / 'mission.json').write_text(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    except BaseException:
        if created:
            shutil.rmtree(mission, ignore_errors=True)
        else:
            for child in mission.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        raise
    print(f'Mission : {name}')
    print(f'Profil : {record["profile"]}')
    print(f'Porte : {record["verify"] or "aucune"}')
    print(f'Branche : mission/{name}')
    return record


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--dir')
    parser.add_argument('--name')
    arguments = parser.parse_args()
    try:
        adopt(arguments.source, arguments.dir, arguments.name)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'Erreur : {error}', file=sys.stderr)
        sys.exit(1)
