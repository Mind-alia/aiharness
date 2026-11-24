"""Récolte d'étalons dans les preuves que le circuit garde pour chaque délégation d'un projet (Python 3.14).

Une tâche fusionnée donne un bon étalon et jusqu'à deux mauvais (tests seuls, fichier hors périmètre ajouté) ; une
tâche en échec connu (`acceptance_failed`, `out_of_scope`) donne un mauvais étalon réel. Les tests cachés restent où
ils sont : la configuration privée de campagne les désigne par leur chemin absolu.
En ligne de commande : `python3 tools/campaign/campaign.py etalons --harvest <projet> --out <dossier> [options]`."""

import fnmatch
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'template/scripts/lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import etalons
from delegate_run import git_mode
from evaluation import evaluation_target
from task import read_task

USAGE = (
    'usage: python3 tools/campaign/campaign.py etalons --harvest <projet> --out <dossier> [--configs <dossier>] '
    '[--label <nom>] [--since AAAA-MM-JJ] [--only id1,id2] [--setup <commande>]'
)
OPTIONS = ('--harvest', '--out', '--configs', '--label', '--since', '--only', '--setup')
FAILED = ('acceptance_failed', 'out_of_scope')
TEST_DIRECTORIES = {'tests', 'test', '__tests__'}
TEST_NAMES = ('test_*.py', '*_test.py', '*.test.*', '*.spec.*')
OUTSIDE = 'scripts/etalon-hors-perimetre.sh'
OUTSIDE_SECTION = 'diff --git a/%(p)s b/%(p)s\nnew file mode 100644\n--- /dev/null\n+++ b/%(p)s\n@@ -0,0 +1 @@\n+exit 0\n' % dict(p=OUTSIDE)
HEADER_MODE = re.compile(rb'(new file|deleted file) mode ([0-7]+)\n')
OLD_MODE = re.compile(rb'old mode ([0-7]+)\n')
NEW_MODE = re.compile(rb'new mode ([0-7]+)\n')


def normalize(data):
    """Diff du circuit ramené aux en-têtes que `git apply` accepte ; les autres octets ne changent pas."""
    lines = re.findall(rb'[^\n]*\n|[^\n]+\Z', data)
    out, index = [], 0
    while index < len(lines):
        line = lines[index]
        old = OLD_MODE.fullmatch(line)
        new = NEW_MODE.fullmatch(lines[index + 1]) if old and index + 1 < len(lines) else None
        if new:
            old_mode, new_mode = git_mode(int(old.group(1), 8)), git_mode(int(new.group(1), 8))
            if old_mode != new_mode:
                out.extend([b'old mode ' + old_mode.encode() + b'\n', b'new mode ' + new_mode.encode() + b'\n'])
            index += 2
            continue
        if line.startswith(b'diff --git/'):
            line = b'diff --git a/' + line[len(b'diff --git/') :]
        header = HEADER_MODE.fullmatch(line)
        if header:
            line = header.group(1) + b' mode ' + git_mode(int(header.group(2), 8)).encode() + b'\n'
        out.append(line)
        index += 1
    return b''.join(out)


def parse(args):
    options, index = {}, 0
    while index < len(args):
        if args[index] not in OPTIONS or args[index] in options or index + 1 >= len(args) or args[index + 1].startswith('--'):
            return None
        options[args[index]], index = args[index + 1], index + 2
    if '--harvest' not in options or '--out' not in options:
        return None
    if '--since' in options and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', options['--since']):
        return None
    if '--label' in options and not re.fullmatch('[a-z0-9-]+', options['--label']):
        return None
    if '--only' in options:
        options['--only'] = {name for name in options['--only'].split(',') if name}
        if not options['--only']:
            return None
    return options


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def is_test_path(path):
    parts = PurePosixPath(path).parts
    return bool(parts) and (
        bool(TEST_DIRECTORIES & set(parts[:-1])) or any(fnmatch.fnmatchcase(parts[-1], pattern) for pattern in TEST_NAMES)
    )


def is_test_section(section):
    paths = etalons.diff_paths(section.encode('utf-8', 'surrogateescape'))
    return bool(paths) and all(is_test_path(path) for path in paths)


def examine(project, task_id, state):  # noqa: C901
    """(raison de l'écart, preuves) ; la raison est vide quand la tâche est retenue."""
    run = project / '.workspace/run/delegate' / task_id
    outcome = (read_json(run / 'status.json') or {}).get('outcome')
    merged = state.get('status') == 'merged'
    if not merged and outcome not in FAILED:
        return 'ni fusionnée ni en échec connu', None
    source = state.get('source_task')
    try:
        if not isinstance(source, str) or Path(source).is_absolute() or '..' in Path(source).parts:
            raise ValueError(source)
        task = read_task(project / source, project)
    except Exception:  # fiche absente, mal formée ou exécutant inconnu : la tâche est écartée, pas la récolte
        return 'fiche illisible', None
    if not task['acceptance'] or not task.get('acceptance_verify'):
        return 'sans acceptance ni acceptance_verify', None
    parents = {PurePosixPath(name).parent.as_posix() for name in task['acceptance']}
    if len(parents) > 1:
        return 'tests cachés dans plusieurs dossiers', None
    try:
        evaluation_target(project, next(iter(parents)))
    except ValueError:
        return 'dossier des tests cachés invalide', None
    files = [project / '.workspace/acceptance' / task_id / name for name in task['acceptance']]
    missing = [name for name, path in zip(task['acceptance'], files, strict=False) if not path.is_file()]
    if missing:
        return 'test caché absent : ' + missing[0], None
    try:
        data = normalize((run / 'diff.txt').read_bytes())
    except OSError:
        return 'diff absent', None
    lines = data.decode('utf-8', 'replace').splitlines()
    if not any(line.startswith('diff --git ') for line in lines):
        return 'diff sans ligne diff --git', None
    if any('***' in line for line in lines if not line.startswith(('diff ', '--- ', '+++ ', '@@'))):
        return 'diff masqué par ***', None
    base = state.get('base_commit')
    if not isinstance(base, str) or not re.fullmatch('[0-9a-f]{40,64}', base):
        return 'base_commit absent ou incomplet', None
    return '', dict(
        task=task, files=files, target=next(iter(parents)), data=data, merged=merged, base=base, status='merged' if merged else outcome
    )


def variants(proof):
    """[(suffixe, attendu, nature, diff en octets, transformation)] d'une tâche retenue."""
    data = proof['data']
    if not proof['merged']:
        return [('echec', 'reject', 'échec réel', data, 'tel quel')]
    found = [('bon', 'accept', 'bon', data, 'tel quel')]
    sections = [s for s in re.split(r'(?m)^(?=diff --git )', data.decode('utf-8', 'surrogateescape')) if s.startswith('diff --git ')]
    tests = [s for s in sections if is_test_section(s)]
    if tests and len(tests) < len(sections):
        found.append(
            (
                'sans-code',
                'reject',
                'sans implémentation',
                ''.join(tests).encode('utf-8', 'surrogateescape'),
                'seules les sections de test gardées',
            )
        )
    if not etalons.in_scope(OUTSIDE, proof['task']['allowed']) and OUTSIDE not in etalons.diff_paths(data):
        found.append(
            (
                'hors-perimetre',
                'reject',
                'hors périmètre',
                data + (b'' if data.endswith(b'\n') else b'\n') + OUTSIDE_SECTION.encode('utf-8'),
                'section hors périmètre ajoutée',
            )
        )
    return found


def load_existing(path, key):
    """Objet JSON déjà écrit (ou un objet vide) dont `key` est une liste ; ValueError s'il est inutilisable."""
    if not path.exists():
        return {key: []}
    data = read_json(path)
    if data is None or not isinstance(data.get(key), list):
        raise ValueError('fichier existant illisible : ' + str(path))
    return data


def main(args):  # noqa: C901, PLR0915
    options = parse(list(args))
    if options is None:
        print(USAGE, file=sys.stderr)
        return 64
    project = Path(options['--harvest']).expanduser().resolve()
    tasks_dir = project / '.workspace/orchestration/tasks'
    if not tasks_dir.is_dir():
        print('etalons: aucune tâche du circuit dans ' + str(project), file=sys.stderr)
        return 64
    out = Path(options['--out']).expanduser().resolve()
    configs = Path(options['--configs']).expanduser().resolve() if '--configs' in options else out
    label = options.get('--label') or re.sub('[^a-z0-9-]', '-', project.name.lower())
    config_path = configs / (label + '.config.json')
    try:
        manifest = load_existing(out / 'etalons.json', 'etalons')
        config = load_existing(config_path, 'tasks')
    except ValueError as error:
        print('etalons: ' + str(error), file=sys.stderr)
        return 64
    added, diffs, tasks = [], {}, []
    for directory in sorted(tasks_dir.iterdir()):
        task_id = directory.name
        if not (directory / 'state.json').is_file():
            continue
        state = read_json(directory / 'state.json')
        if state is None:
            print('%s : écarté : state.json illisible' % task_id)
            continue
        if '--only' in options and task_id not in options['--only']:
            continue
        if '--since' in options and str(state.get('created_at'))[:10] < options['--since']:
            continue
        reason, proof = ('identifiant invalide', None) if not re.fullmatch('[a-z0-9-]+', task_id) else examine(project, task_id, state)
        if reason:
            print('%s : écarté : %s' % (task_id, reason))
            continue
        print(task_id + ' : retenu')
        tasks.append(
            dict(
                id=task_id,
                allowed=proof['task']['allowed'],
                evaluation_files=[str(p) for p in proof['files']],
                evaluation_target=proof['target'],
                evaluation_command=proof['task']['acceptance_verify'],
            )
        )
        for suffix, expected, kind, data, how in variants(proof):
            name = '%s-%s-%s' % (label, task_id, suffix)
            source = 'tâche %s (exécutant %s, statut %s) : diff du circuit, %s' % (
                task_id,
                proof['task']['agent_name'] or proof['task']['agent'],
                proof['status'],
                how,
            )
            diffs[name] = data
            added.append(
                dict(
                    id=name,
                    diff='diffs/%s.diff' % name,
                    base=proof['base'],
                    expected=expected,
                    kind=kind,
                    source=source,
                    config=os.path.relpath(str(config_path), str(out)),
                    task=task_id,
                )
            )
    known = {item.get('id') for item in manifest['etalons'] if isinstance(item, dict)}
    clash = sorted(item['id'] for item in added if item['id'] in known)
    if clash:
        print('etalons: identifiants déjà présents dans le manifeste : ' + ', '.join(clash), file=sys.stderr)
        return 64
    if added:
        config.update(project=str(project), tasks=[t for t in config['tasks'] if t.get('id') not in {new['id'] for new in tasks}] + tasks)
        if '--setup' in options:
            config['setup'] = options['--setup']
        try:
            (out / 'diffs').mkdir(parents=True, exist_ok=True)
            configs.mkdir(parents=True, exist_ok=True)
            for name, data in diffs.items():
                (out / 'diffs' / (name + '.diff')).write_bytes(data)
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            manifest['etalons'] = manifest['etalons'] + added
            (out / 'etalons.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        except OSError as error:
            print('etalons: écriture impossible : ' + str(error), file=sys.stderr)
            return 64
    good = sum(item['expected'] == 'accept' for item in added)
    print('étalons ajoutés : %d (%d bons, %d mauvais)' % (len(added), good, len(added) - good))
    return 0 if added else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
