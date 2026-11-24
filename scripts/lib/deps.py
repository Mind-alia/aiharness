"""Préparation de dépendances Linux ; aucune installation dans le worktree hôte."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path, PurePosixPath
from secrets import known_values, mask_secrets

EXCLUDED = {'.git', '.workspace', 'node_modules', '.venv', 'secrets', '__pycache__'}
# Writable tool caches overlaid with per-task tmpfs; dependencies stay read-only.
NODE_CACHE_DIRS = ('.vite-temp', '.vite', '.cache', '.vitest', '.vitest-cache')


def dependency_plan(root, worktree, image, profile):  # noqa: C901
    files = []
    for directory, dirs, names in os.walk(worktree, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not d.startswith('.env'))
        for name in sorted(names):
            path = Path(directory) / name
            rel = path.relative_to(worktree).as_posix()
            selected = (
                name in ('package-lock.json', 'npm-shrinkwrap.json', 'package.json')
                if profile == 'node'
                else name in ('uv.lock', 'poetry.lock', 'pyproject.toml') or (name.startswith('requirements') and name.endswith('.txt'))
            )
            if selected:
                if path.is_symlink():
                    raise ValueError('Fichier de dépendances en lien symbolique interdit : ' + rel)
                files.append((rel, path.read_bytes()))
    locks = [name for name, _ in files if Path(name).name != 'package.json']
    if not locks:
        return None
    if profile == 'node' and not any((worktree / n).is_file() for n in ('npm-shrinkwrap.json', 'package-lock.json')):
        raise ValueError('npm ci exige un verrouillage à la racine du projet.')
    digest = hashlib.sha256()
    for value in ['workspace-deps-v2', image, profile] + [v for pair in sorted(files) for v in pair]:
        data = value.encode() if isinstance(value, str) else value
        digest.update(len(data).to_bytes(8, 'big'))
        digest.update(data)
    fingerprint = digest.hexdigest()
    project = re.sub('[^a-z0-9-]+', '-', root.name.lower()).strip('-')[:32] or 'project'
    project += '-' + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:8]
    mounts = ['node_modules'] if profile == 'node' else []
    if profile == 'node':
        lock = next(worktree / n for n in ('npm-shrinkwrap.json', 'package-lock.json') if (worktree / n).is_file())
        data = json.loads(lock.read_text())
        if json.loads((worktree / 'package.json').read_text()).get('workspaces') and not data.get('packages'):
            raise ValueError('Espaces de travail npm : package-lock v2/v3 requis.')
        for path in data.get('packages', {}):
            if not path or 'node_modules' in PurePosixPath(path).parts:
                continue
            parts = PurePosixPath(path).parts
            if PurePosixPath(path).is_absolute() or '..' in parts or not re.fullmatch(r'[a-zA-Z0-9_./@ -]+', path):
                raise ValueError('Chemin de workspace npm invalide : ' + path)
            target = worktree / path
            if not target.is_dir() or target.is_symlink() or not target.resolve().is_relative_to(worktree.resolve()):
                raise ValueError('Workspace npm absent ou sortant : ' + path)
            mounts.append(path + '/node_modules')
    return {
        'volume': 'workspace-deps-' + project + '-' + fingerprint[:16],
        'fingerprint': fingerprint,
        'profile': profile,
        'mounts': sorted(set(mounts)),
    }


def volume_mounts(plan):
    if not plan:
        return []
    volume = plan['volume']
    if plan['profile'] == 'node':
        return [
            arg
            for path in plan['mounts']
            for arg in ('--mount', f'type=volume,src={volume},dst=/work/{path},volume-subpath=deps/work/{path},readonly,volume-nocopy')
        ]
    return [
        '--mount',
        f'type=volume,src={volume},dst=/deps,volume-subpath=deps,readonly,volume-nocopy',
        '--mount',
        f'type=volume,src={volume},dst=/work/.venv,volume-subpath=deps/.venv,readonly,volume-nocopy',
        '-e',
        'VIRTUAL_ENV=/deps/.venv',
        '-e',
        'PATH=/deps/.venv/bin:/usr/local/bin:/usr/bin:/bin',
    ]


def prepare_mount_points(worktree, plan):
    """Create on the host the empty `.venv` Docker mounts the Python volume on.

    `/work` is read-only for a task clone and `.venv` is ignored by Git, hence absent from the clone:
    Docker cannot create the mount point itself. Git does not see an empty folder."""
    if not plan or plan['profile'] == 'node':
        return
    path = Path(worktree) / '.venv'
    if path.is_symlink():
        raise ValueError('Lien symbolique interdit dans le worktree : .venv')
    if path.exists() and not path.is_dir():
        raise ValueError('Dossier attendu dans le worktree, fichier trouvé : .venv')
    path.mkdir(exist_ok=True)


def installation_script(worktree, plan):
    # Copy source to Linux storage for lifecycle/build scripts; never copy host dependencies or Git metadata.
    excludes = sorted(EXCLUDED | {'.env', '.env.*'})
    copy = 'tar ' + ' '.join('--exclude=' + shlex.quote(p) for p in excludes)
    script = (
        'set -eu\n' + copy + ' -C /source -cf /tmp/source.tar .\n'
        'mkdir -p /deps/work\ntar -C /deps/work -xf /tmp/source.tar\ncd /deps/work\n'
    )
    if plan['profile'] == 'node':
        script += 'npm ci\n'
        for path in plan['mounts']:
            script += 'mkdir -p ' + shlex.quote('/deps/work/' + path) + '\n'
            for cache in NODE_CACHE_DIRS:
                target = shlex.quote('/deps/work/' + path + '/' + cache)
                # Lifecycle scripts may already have populated these disposable caches.
                script += 'rm -rf ' + target + '\nmkdir -p ' + target + '\n'
    else:
        script += 'python -m venv /deps/.venv\n'
        pip = '/deps/.venv/bin/python -m pip'
        if (worktree / 'uv.lock').is_file():
            script += pip + ' install uv\nUV_PROJECT_ENVIRONMENT=/deps/.venv /deps/.venv/bin/uv sync --frozen --no-editable\n'
        elif (worktree / 'poetry.lock').is_file():
            script += (
                pip + ' install poetry poetry-plugin-export\n'
                '/deps/.venv/bin/poetry export --format requirements.txt --output /tmp/requirements.txt\n'
                + pip
                + ' install -r /tmp/requirements.txt\n'
            )
        else:
            requirements = sorted(p.name for p in worktree.glob('requirements*.txt'))
            if requirements:
                script += pip + ' install ' + ' '.join('-r ' + shlex.quote(n) for n in requirements) + '\n'
            elif (worktree / 'pyproject.toml').is_file():
                script += pip + ' install .\n'
            else:
                raise ValueError('Dépendances Python : fichier installable absent à la racine.')
    return script


def prepare_dependencies(root, task_dir, worktree, image, profile, docker, rebuild=False):  # noqa: C901, PLR0913, PLR0915
    plan = dependency_plan(root, worktree, image, profile)
    if plan is None:
        print('Aucun fichier de verrouillage/dépendances : aucun volume préparé.')
        return None
    # Serialize cache publication/rebuilds across tasks of this project. Refuse symlinked orchestration paths.
    from sandbox import check_no_symlinks

    cache = root / '.workspace/deps'
    check_no_symlinks(cache, root)
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = cache / '.lock'
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        receipt = cache / (plan['fingerprint'] + '.json')
        check_no_symlinks(receipt, root)
        volume = plan['volume']

        def call(args):
            return subprocess.run([docker] + args, capture_output=True, text=True)

        exists = call(['volume', 'inspect', volume]).returncode == 0
        if exists and receipt.is_file() and not rebuild:
            print('Dépendances réutilisées : ' + volume)
            return plan
        if exists:
            removed = call(['volume', 'rm', volume])
            if removed.returncode:
                raise RuntimeError('Volume de dépendances utilisé ou non supprimable ; arrêter les tâches avant --rebuild.')
        if receipt.exists():
            receipt.unlink()
        created = call(
            [
                'volume',
                'create',
                '--label',
                'workspace.managed_by=workspace-ia',
                '--label',
                'workspace.role=deps',
                '--label',
                'workspace.project=' + str(root.resolve()),
                '--label',
                'workspace.fingerprint=' + plan['fingerprint'],
                '--label',
                'workspace.profile=' + profile,
                '--label',
                'workspace.image=' + image,
                volume,
            ]
        )
        if created.returncode:
            raise RuntimeError('Création du volume de dépendances impossible.')
        sandbox_dir = task_dir / 'sandbox'
        check_no_symlinks(sandbox_dir, root)
        sandbox_dir.mkdir(mode=0o700, exist_ok=True)
        log = sandbox_dir / ('deps-' + plan['fingerprint'][:16] + '.log')
        check_no_symlinks(log, root)
        name = 'workspace-deps-install-' + hashlib.sha256(str(task_dir).encode()).hexdigest()[:16]
        common = [
            'run',
            '--rm',
            '--name',
            name,
            '--user',
            '1000:1000',
            '--cap-drop',
            'ALL',
            '--security-opt',
            'no-new-privileges',
            '--read-only',
            '--cpus',
            '2',
            '--memory',
            '2g',
            '-e',
            'HOME=/tmp',
        ]
        success = False
        output = ''
        try:
            # Docker copies /tmp's 1777 permissions into an empty volume. No root init container.
            init = call(common + ['--network', 'none', '-v', volume + ':/tmp', image, 'sh', '-ec', 'mkdir -p /tmp/deps'])
            output += init.stdout + init.stderr
            if init.returncode:
                raise RuntimeError('Initialisation du volume de dépendances impossible.')
            proc = call(
                common
                + [
                    '--label',
                    'workspace.role=deps',
                    '--network',
                    'bridge',
                    '--tmpfs',
                    '/tmp',
                    '-v',
                    str(worktree.resolve()) + ':/source:ro',
                    '--mount',
                    f'type=volume,src={volume},dst=/deps,volume-subpath=deps,volume-nocopy',
                    image,
                    'sh',
                    '-ec',
                    installation_script(worktree, plan),
                ]
            )
            output += proc.stdout + proc.stderr
            if proc.returncode:
                raise RuntimeError('Échec d’installation des dépendances ; consulter ' + str(log))
            receipt.write_text(json.dumps(plan) + '\n')
            receipt.chmod(0o600)
            success = True
        finally:
            masked = mask_secrets(output, known_values(root))
            log.write_text(masked)
            log.chmod(0o600)
            if masked:
                print(masked, end='' if masked.endswith('\n') else '\n')
            call(['rm', '-f', name])
            if not success:
                call(['volume', 'rm', volume])
        print('Dépendances préparées : ' + volume)
        return plan
