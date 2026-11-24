#!/bin/sh
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
umask 077
root=$(CDPATH= cd -- "${0%/*}/.." && pwd) || exit 2
exec "${WORKSPACE_PYTHON:-python3}" - "$root" "$@" <<'PY'
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys

root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'scripts/lib'))
sys.dont_write_bytecode = True
from output import run_masked
raw_args = sys.argv[2:]
sandbox_id = os.environ.get('WORKSPACE_TASK_ID') or os.environ.get('TASK_ID')
is_exchange = os.environ.get('WORKSPACE_ISOLATION') == 'exchange' or os.environ.get('ISOLATION') == 'exchange'

if raw_args and raw_args[0] == '--sandbox':
    if len(raw_args) < 3:
        print('Usage : verify-task.sh [--sandbox <id>] <fichier-de-tâche>', file=sys.stderr)
        sys.exit(2)
    sandbox_id = raw_args[1]
    is_exchange = True
    task_arg = raw_args[2]
elif raw_args and raw_args[0] == '--exchange':
    if len(raw_args) < 2:
        print('Usage : verify-task.sh [--exchange] <fichier-de-tâche>', file=sys.stderr)
        sys.exit(2)
    is_exchange = True
    task_arg = raw_args[1]
elif len(raw_args) == 1:
    task_arg = raw_args[0]
else:
    print('Usage : verify-task.sh [--sandbox <id>] <fichier-de-tâche>', file=sys.stderr)
    sys.exit(2)

task = Path(task_arg).resolve()
task_data = {}
try:
    parser = root / 'scripts/lib/task.py'
    if parser.is_file():
        import importlib.util
        spec = importlib.util.spec_from_file_location('workspace_task', parser)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        task_data = module.read_task(task)
        command = task_data.get('verify') or ''
        if not isinstance(command, str):
            raise ValueError('verify doit être une chaîne')
        if task_data.get('isolation') == 'exchange':
            is_exchange = True
        if not sandbox_id and isinstance(task_data, dict):
            sandbox_id = task_data.get('id')
    else:
        lines = task.read_text().splitlines()
        if not lines or lines[0].strip() != '---':
            raise ValueError('En-tête de tâche absent')
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == '---')
        command = ''
        # Minimal scalar reader, until the shared task parser is available.
        for line in lines[1:end]:
            if line.startswith('verify:'):
                tokens = shlex.shlex(line[len('verify:'):].strip(), posix=True)
                tokens.whitespace_split = True
                tokens.commenters = '#'
                # Keep shell syntax and quoting intact; shlex only locates comments.
                raw = line[len('verify:'):].strip()
                if raw.startswith(('"', "'")):
                    values = list(tokens)
                    if len(values) != 1:
                        raise ValueError('verify doit être une chaîne simple')
                    command = values[0]
                else:
                    # YAML inline comments start after whitespace, outside shell quotes.
                    quote = None
                    for i, char in enumerate(raw):
                        if char in ('"', "'"):
                            quote = None if quote == char else (char if quote is None else quote)
                        if char == '#' and quote is None and (i == 0 or raw[i-1].isspace()):
                            raw = raw[:i]
                            break
                    command = raw.strip()
            elif line.startswith(('verify_profile:', 'verify_lint:')):
                key, value = line.split(':', 1)
                values = shlex.split(value, comments=True)
                if len(values) != 1 or values[0] not in ('on', 'off'):
                    raise ValueError(key + ' doit être on ou off')
                task_data[key] = values[0]
            elif line.startswith('id:'):
                if not sandbox_id:
                    sandbox_id = line[len('id:'):].strip()
            elif line.startswith('isolation:'):
                if line[len('isolation:'):].strip() == 'exchange':
                    is_exchange = True
except (OSError, ValueError, StopIteration) as error:
    print(f'Tâche invalide : {error}', file=sys.stderr)
    sys.exit(2)
os.chdir(root)
checks = []
if command:
    checks.append(('Vérification de la tâche.', ['/bin/sh', '-c', command]))
if task_data.get('verify_profile', 'on') != 'off':
    try:
        profile = (root / 'project.profile').read_text().strip()
    except OSError:
        profile = ''
    if profile == 'node':
        # The config must be checked in the task worktree, inside the sandbox.
        checks.append(('Vérification rapide du profil node.', ['/bin/sh', '-c',
                       'if [ -f tsconfig.json ]; then '
                       'echo "Contrôle : npx tsc -b" >&2; npx tsc -b; fi']))
        if task_data.get('verify_lint', 'on') != 'off':
            # Inspect the root package inside the same worktree/sandbox as npm.
            lint_check = """
const fs = require('fs');
const pkg = fs.existsSync('package.json')
    ? JSON.parse(fs.readFileSync('package.json', 'utf8')) : {};
if (!pkg.scripts || typeof pkg.scripts.lint !== 'string' || !pkg.scripts.lint.trim()) {
    console.error('Lint sauté : script lint absent du package.json racine.');
} else {
    console.error('Contrôle : npm run lint');
    const result = require('child_process').spawnSync('npm', ['run', 'lint'], {stdio: 'inherit'});
    process.exit(result.status === null ? 127 : result.status);
}
"""
            checks.append(('Vérification du lint du profil node.', ['node', '-e', lint_check]))
    elif profile or not command:
        checks.append(('Vérification rapide du profil.',
                       # Relative path: inside the sandbox the worktree is mounted at /work, not at the host root.
                       ['/bin/sh', 'scripts/gate.sh', '--quick']))

if is_exchange and not sandbox_id:
    print("Erreur : identifiant de tâche manquant pour l'exécution en sandbox.", file=sys.stderr)
    sys.exit(2)

PROFILE_GATE_CHECK = ['/bin/sh', 'scripts/gate.sh', '--quick']


def file_digest(path):
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def worktree_gate_is_sealed(root, sandbox_id):
    # The task clone's gate runs inside the sandbox: refuse it unless it matches the project's own gate.
    import workdir
    try:
        worktree_gate = workdir.task_worktree(root, sandbox_id) / 'scripts/gate.sh'
    except ValueError:
        return False
    if worktree_gate.is_symlink() or not worktree_gate.is_file():
        return False
    return file_digest(worktree_gate) == file_digest(root / 'scripts/gate.sh')


if is_exchange and any(exec_cmd == PROFILE_GATE_CHECK for _, exec_cmd in checks) \
        and not worktree_gate_is_sealed(root, sandbox_id):
    print('control_tampered : scripts/gate.sh du worktree diffère de celui du projet', file=sys.stderr)
    print('Vérification : échec (code 3).', file=sys.stderr)
    sys.exit(3)

code = 0
for label, exec_cmd in checks:
    print(label, file=sys.stderr)
    if is_exchange:
        verify_cmd = ['bash', str(root / 'scripts/sandbox.sh'), 'exec', sandbox_id, '--'] + exec_cmd
    else:
        verify_cmd = exec_cmd
    code, out, err = run_masked(verify_cmd, root)
    print(out, end='')
    print(err, end='', file=sys.stderr)
    if code:
        break
print(f'Vérification : {"OK" if code == 0 else "échec"} (code {code}).', file=sys.stderr)
sys.exit(code)
PY
