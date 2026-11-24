"""Lecture du score d'évaluation et contrôle du dossier cible, partagés par les outils de campagne (Python 3.14).

En ligne de commande : évalue un diff d'agent dans un clone jetable du projet (voir README.md)."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def json_output(output):
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
    return {}


TAP_LEAF = re.compile(r'^[ \t]*(not ok|ok)[ \t]+\d+.*$', re.M)


def score(output, returncode):
    """(passed, total) depuis un JSON {"passed", "total"}, TAP ou unittest ; (None, None) si inconnu, jamais inventé."""
    data = json_output(output)
    passed, total = data.get('passed'), data.get('total')
    if passed is None or total is None:
        # Vitest imbrique une ligne par fichier et par groupe, ouverte par « { » : seules les feuilles comptent.
        leaves = [m.group(1) for m in TAP_LEAF.finditer(output) if not m.group(0).rstrip().endswith('{')]
        run = re.search(r'Ran (\d+) tests?', output)
        if leaves:
            passed, total = leaves.count('ok'), len(leaves)
        elif run:
            total = int(run.group(1))
            counts = dict(re.findall(r'(failures|errors|skipped)=(\d+)', output))
            passed = total - sum(int(v) for v in counts.values()) if returncode == 0 or counts else None
    return passed, total


def evaluation_target(root, name):
    """Dossier d'évaluation relatif au projet, hors infrastructure du circuit."""
    path = root / name
    if Path(name).is_absolute() or '..' in Path(name).parts or not Path(name).parts:
        raise ValueError('Dossier cible invalide')
    if Path(name).parts[0] in ('.git', '.workspace', 'scripts', 'tasks'):
        raise ValueError('Dossier cible réservé')
    if root not in path.resolve().parents:
        raise ValueError('Dossier cible hors projet')
    return path


USAGE = 'usage: python3 tools/campaign/evaluation.py <config.json> --task <id> --diff <fichier> --base <commit>'
GIT_VARIABLES = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY', 'GIT_COMMON_DIR')


class Refused(Exception):
    """Évaluation impossible ; `code` est le code de retour du programme."""

    def __init__(self, message, code):
        Exception.__init__(self, message)
        self.code = code


def run(args, cwd):
    env = {k: v for k, v in os.environ.items() if k != 'WORKSPACE_AGENT_ROOT' and k not in GIT_VARIABLES}
    return subprocess.run(args, cwd=str(cwd), env=env, text=True, errors='replace', stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def argv(command):
    return command if isinstance(command, list) else ['bash', '-c', command]


def load_task(config_path, task_id):
    """(projet, tâche avec fichiers d'évaluation résolus, setup) ; Refused 64 si la configuration est inutilisable."""
    try:
        config_path = Path(config_path).resolve()
        config = json.loads(config_path.read_text())
        root = Path(config['project']).expanduser()
        root = (root if root.is_absolute() else config_path.parent / root).resolve()
        tasks = [t for t in config['tasks'] if t['id'] == task_id]
        if not tasks:
            raise Refused('Tâche inconnue : ' + task_id, 64)
        task, files = tasks[0], []
        for name in task['evaluation_files']:
            path = Path(name).expanduser()
            path = path if path.is_absolute() else (config_path.parent / path).resolve()
            if not path.is_file():
                raise ValueError('Test absent : ' + name)
            files.append(path)
        evaluation_target(root, task['evaluation_target'])
        setup = config.get('setup')
        for command in (task['evaluation_command'], setup):
            if command is not None and not (
                isinstance(command, str) or (isinstance(command, list) and command and all(isinstance(a, str) for a in command))
            ):
                raise ValueError('Commande invalide')
        if not root.is_dir():
            raise ValueError('Projet introuvable : ' + str(root))
        return root, dict(task, evaluation_files=files), setup
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise Refused('Configuration invalide : ' + str(error), 64)


def resolve_base(root, base):
    found = run(['git', 'rev-parse', '--verify', '--quiet', base + '^{commit}'], root)
    if found.returncode or not found.stdout.strip():
        raise Refused('Commit introuvable ou projet non Git : ' + base, 64)
    return found.stdout.strip().splitlines()[-1]


def check_diff(data):
    lines = data.decode('utf-8', 'replace').splitlines()
    if not any(line.startswith('diff --git ') or line.startswith('--- ') for line in lines):
        raise Refused('Diff vide ou sans fichier', 2)
    if any('***' in line for line in lines if not line.startswith(('diff ', '--- ', '+++ ', '@@'))):
        raise Refused('Diff masqué par ***', 2)


def checkout(clone, sha):
    if run(['git', 'checkout', '--quiet', '--detach', sha], clone).returncode:
        run(['git', 'fetch', '--quiet', 'origin', sha], clone)
        done = run(['git', 'checkout', '--quiet', '--detach', sha], clone)
        if done.returncode:
            raise Refused('Extraction impossible : ' + done.stdout.strip(), 64)


def evaluate_diff(config_path, task_id, diff_path, base):
    """(passed, total, code de l'évaluation, sortie brute) ; le projet d'origine n'est jamais modifié."""
    root, task, setup = load_task(config_path, task_id)
    sha = resolve_base(root, base)
    try:
        data = Path(diff_path).read_bytes()
    except OSError as error:
        raise Refused('Diff illisible : ' + str(error), 64)
    check_diff(data)
    tmp = Path(tempfile.mkdtemp(prefix='evaluation-diff-')).resolve()
    try:
        clone = tmp / 'clone'
        done = run(['git', 'clone', '--quiet', '--no-checkout', '--no-hardlinks', str(root), str(clone)], tmp)
        if done.returncode:
            raise Refused('Clonage impossible : ' + done.stdout.strip(), 64)
        checkout(clone, sha)
        patch = tmp / 'diff.patch'
        patch.write_bytes(data)
        done = run(['git', 'apply', '--whitespace=nowarn', str(patch)], clone)
        if done.returncode:
            raise Refused('Le diff ne s’applique pas : ' + done.stdout.strip(), 2)
        if setup is not None:
            done = run(argv(setup), clone)
            if done.returncode:
                sys.stderr.write(done.stdout)
                raise Refused('setup en échec (code %d)' % done.returncode, 1)
        target = evaluation_target(clone, task['evaluation_target'])
        target.mkdir(parents=True, exist_ok=True)
        for source in task['evaluation_files']:
            dest = target / source.name
            if dest.exists() or dest.is_symlink():
                raise Refused('Refus d’écraser un fichier d’évaluation : ' + source.name, 1)
            shutil.copyfile(str(source), str(dest))
        proc = run(argv(task['evaluation_command']), clone)
        passed, total = score(proc.stdout, proc.returncode)
        return passed, total, proc.returncode, proc.stdout
    except ValueError as error:
        raise Refused(str(error), 1)
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def main(args):
    options, index = {}, 1
    while index < len(args) - 1 and args[index] in ('--task', '--diff', '--base') and args[index] not in options:
        options[args[index]], index = args[index + 1], index + 2
    if not args or args[0].startswith('--') or index != len(args) or len(options) != 3:
        print(USAGE, file=sys.stderr)
        return 64
    try:
        passed, total, code, output = evaluate_diff(args[0], options['--task'], options['--diff'], options['--base'])
    except Refused as error:
        print('evaluation: ' + str(error), file=sys.stderr)
        if error.code != 64:
            print('?/?')
        return error.code
    sys.stderr.write(output)
    known = passed is not None and total is not None
    print('%s/%s' % (passed, total) if known else '?/?')
    return 0 if known and passed == total and total > 0 and code == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
