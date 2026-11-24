#!/bin/sh
# Python 3.14 is supplied by the kit; stdout is exclusively JSON lines.
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
root=$(CDPATH= cd -- "${0%/*}/.." && pwd) || exit 2
exec "${WORKSPACE_PYTHON:-python3}" - "$root" "$@" <<'PY'
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(sys.argv[1])
os.chdir(root)
sys.path.insert(0, str(root / 'scripts/lib'))
sys.dont_write_bytecode = True
from output import run_masked
quick = sys.argv[2:] == ['--quick']
if sys.argv[2:] and not quick:
    print('Usage : gate.sh [--quick]', file=sys.stderr)
    sys.exit(2)
try:
    profile = (root / 'project.profile').read_text().strip()
except OSError:
    profile = ''
if profile not in ('node', 'docker', 'python', 'docs', 'c'):
    print(json.dumps({'profile': 'non_configure'}))
    print('Profil absent ou inconnu dans project.profile.', file=sys.stderr)
    sys.exit(2)


def configured(command):
    if not shutil.which(command[0]):
        return False
    if command[0] == 'npm':
        try:
            script = json.loads(Path('package.json').read_text())['scripts'][command[-1]]
            return isinstance(script, str) and bool(script.strip())
        except (OSError, ValueError, KeyError, TypeError):
            return False
    if command[0] == 'make':
        # Inspect the selected makefile as data. Never invoke make to probe:
        # even -n evaluates $(shell ...). Dynamic declarations fail closed.
        makefile = next((Path(name) for name in ('GNUmakefile', 'makefile', 'Makefile')
                         if Path(name).is_file()), None)
        if makefile is None:
            return False
        if command == ['make']:
            return True
        try:
            text = makefile.read_text()
        except (OSError, UnicodeError):
            return False
        targets, phony = set(), set()
        text = text.replace('\\\n', ' ')
        for line in text.splitlines():
            if line.startswith('\t'):
                continue
            line = line.split('#', 1)[0].strip()
            # Conditionals, includes and macros require make evaluation; do not
            # pretend to resolve them. Use explicit unconditional local rules.
            if re.match(r'^(?:-?include|sinclude|ifdef|ifndef|ifeq|ifneq|else|endif|define|endef)\b', line):
                return False
            match = re.fullmatch(r'([A-Za-z0-9_. /-]+)\s*:(?![=])(:?)(.*)', line)
            if not match:
                continue
            names = match[1].split()
            if names == ['.PHONY']:
                dependencies = match[3].split(';', 1)[0]
                phony.update(dependencies.split())
            else:
                targets.update(names)
        return command[-1] in targets and command[-1] in phony
    if command[0] == '/bin/sh':
        return Path(command[1]).is_file()
    return True


def c_sources():
    # Sources C et en-têtes sous la racine ; les dossiers et fichiers cachés (.workspace, .git…) sont exclus.
    found = []
    for directory, dirs, files in os.walk('.'):
        dirs[:] = [name for name in dirs if not name.startswith('.')]
        for name in files:
            path = Path(directory, name)
            if name.endswith(('.c', '.h')) and not name.startswith('.') and not path.is_symlink():
                found.append(path.as_posix())
    return sorted(found)


if profile == 'node':
    names = ['test', 'typecheck'] if quick else ['typecheck', 'lint', 'test', 'e2e']
    checks = [(name, ['npm', 'run', name]) for name in names]
elif profile == 'docker':
    checks = [(name, ['make', name]) for name in (['check'] if quick else ['check', 'test'])]
elif profile == 'python':
    checks = [] if quick else [('ruff', ['ruff', 'check', '.'])]
    if Path('tests/run.sh').is_file():
        checks.append(('unittest', ['sh', 'tests/run.sh', 'fast' if quick else 'full']))
    else:
        checks.append(('pytest', ['pytest']))
elif profile == 'c':
    checks = [('make', ['make'])]
    if not quick and configured(['make', 'test']):
        checks.append(('test', ['make', 'test']))
    files = c_sources()
    if not shutil.which('norminette'):
        print('norminette absent : norme non vérifiée.', file=sys.stderr)
    elif files:
        checks.append(('norminette', ['norminette', *files]))
else:
    checks = []
if not quick or profile == 'docs':
    checks.append(('docs', ['/bin/sh', 'scripts/docs-check.sh']))
checks.insert(0, ('secrets', ['/bin/sh', 'scripts/check-secret-tracing.sh']))
if (root / 'scripts/lib/bench_rule.py').is_file():
    # Import à l'intérieur de ce test : une copie sans bench_rule ne doit rien importer.
    import bench_rule
    if bench_rule.applies(root):
        checks.insert(1, ('attack_bench', None))
failed = 0
for name, command in checks:
    if command is None:
        ok, message = bench_rule.check(root)
        if message:
            print(message, file=sys.stderr)
        value, code = ok, int(not ok)
    elif not configured(command):
        value = 'non_configure'
        code = 2
    else:
        code, out, err = run_masked(command, root)
        print(out, end='', file=sys.stderr)
        print(err, end='', file=sys.stderr)
        value = code == 0
    print(json.dumps({name: value}), flush=True)
    if code and not failed:
        failed = code
    if quick and code:
        break
sys.exit(failed if quick else int(bool(failed)))
PY
