"""Taux d'erreur du vérificateur : rejoue des diffs étalons dont on connaît le bon verdict (Python 3.14).

Un étalon est un diff accepté ou rejeté à raison. `replay` le passe dans `evaluation.evaluate_diff` et dans le
contrôle de périmètre ; un bon étalon rejeté est un faux rejet, un mauvais étalon accepté une fausse acceptation.
En ligne de commande : `python3 tools/campaign/campaign.py etalons [<manifeste>] [--write <fichier.md>]`.
Chaque étalon peut porter sa propre `config` et sa propre `task` ; `--harvest` fabrique le jeu depuis les preuves
du circuit d'un projet (voir etalons_harvest.py)."""

import datetime
import fnmatch
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluation import Refused, evaluate_diff, load_task, run

KIT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = KIT / 'tools/campaign/etalons/etalons.json'
USAGE = 'usage: python3 tools/campaign/campaign.py etalons [<manifeste>] [--write <fichier.md>]'
MANIFEST_KEYS = {'config', 'task', 'etalons'}
ETALON_KEYS = {'id', 'diff', 'base', 'expected', 'kind', 'source'}
OPTIONAL_ETALON_KEYS = {'config', 'task'}


def text(value):
    return isinstance(value, str) and bool(value.strip())


def resolve(base, name):
    path = Path(name).expanduser()
    return str((path if path.is_absolute() else base / path).resolve())


def load_manifest(path):  # noqa: C901
    """Manifeste validé, `config` et chaque `diff` résolus ; ValueError en français s'il est invalide.

    Chaque étalon reçoit `config` (résolue), `task` et `written_config` (telle qu'écrite dans le manifeste)."""
    path = Path(path).resolve()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError('Manifeste illisible (%s) : %s' % (path, error))
    if not isinstance(data, dict) or 'etalons' not in data or not set(data) <= MANIFEST_KEYS:
        raise ValueError('Le manifeste doit être un objet avec etalons et, facultatifs, config et task')
    if any(key in data and not text(data[key]) for key in ('config', 'task')):
        raise ValueError('config et task doivent être des chaînes non vides')
    etalons = data['etalons']
    if not isinstance(etalons, list) or not etalons:
        raise ValueError('etalons doit être une liste non vide')
    seen, result = set(), []
    for item in etalons:
        if not isinstance(item, dict) or not ETALON_KEYS <= set(item) <= ETALON_KEYS | OPTIONAL_ETALON_KEYS:
            raise ValueError('Chaque étalon doit être un objet avec ' + ', '.join(sorted(ETALON_KEYS)) + ' et, facultatifs, config et task')
        if not isinstance(item['id'], str) or not re.fullmatch('[a-z0-9-]+', item['id']):
            raise ValueError('Identifiant d’étalon invalide : ' + json.dumps(item['id']))
        if item['id'] in seen:
            raise ValueError('Identifiant d’étalon dupliqué : ' + item['id'])
        seen.add(item['id'])
        if item['expected'] not in ('accept', 'reject'):
            raise ValueError('%s : expected doit être accept ou reject' % item['id'])
        if not text(item['diff']) or not text(item['base']) or item['base'].startswith('-'):
            raise ValueError('%s : diff et base doivent être des chaînes valides' % item['id'])
        if not isinstance(item['kind'], str) or not isinstance(item['source'], str):
            raise ValueError('%s : kind et source doivent être des chaînes' % item['id'])
        written, task = item.get('config', data.get('config')), item.get('task', data.get('task'))
        if not text(written) or not text(task):
            raise ValueError('%s : config et task manquent (ni sur l’étalon ni sur le manifeste)' % item['id'])
        result.append(
            dict(item, diff=resolve(path.parent, item['diff']), config=resolve(path.parent, written), task=task, written_config=written)
        )
    if 'config' in data:
        data = dict(data, config=resolve(path.parent, data['config']))
    return dict(data, etalons=result)


def unquote(name):
    """Nom de chemin d'un en-tête Git, avec les échappements des noms entre guillemets."""
    if not (len(name) >= 2 and name.startswith('"') and name.endswith('"')):
        return name
    simple = {'n': 10, 't': 9, '"': 34, '\\': 92, 'r': 13, 'a': 7, 'b': 8, 'f': 12, 'v': 11}
    raw, body, index = bytearray(), name[1:-1], 0
    while index < len(body):
        char = body[index]
        octal = re.match('[0-7]{3}', body[index + 1 : index + 4]) if char == '\\' else None
        if octal:
            raw.append(int(octal.group(0), 8))
            index += 4
        elif char == '\\' and body[index + 1 : index + 2] in simple:
            raw.append(simple[body[index + 1]])
            index += 2
        else:
            raw.extend(char.encode('utf-8'))
            index += 1
    return raw.decode('utf-8', 'replace')


def strip_prefix(name, prefix):
    name = unquote(name.split('\t')[0])
    return name[len(prefix) :] if name.startswith(prefix) else name


def git_header(rest):
    """(a, b) depuis « a/X b/Y » ; en cas d'ambiguïté, la coupe qui donne deux chemins égaux."""
    if rest.startswith('"'):
        match = re.match(r'("(?:[^"\\]|\\.)*") (.*)$', rest)
        return (strip_prefix(match.group(1), 'a/'), strip_prefix(match.group(2), 'b/')) if match else ()
    if not rest.startswith('a/'):
        return ()
    rest, cuts = rest[2:], []
    for found in re.finditer(' b/', rest):
        cuts.append((rest[: found.start()], rest[found.end() :]))
    for left, right in cuts:
        if left == right:
            return left, right
    return cuts[0] if cuts else ()


def diff_paths(data):
    """Chemins touchés par un diff, triés et sans doublon (en-têtes `diff --git`, `---` et `+++`)."""
    paths = set()
    for line in data.decode('utf-8', 'replace').splitlines():
        if line.startswith('diff --git '):
            paths.update(git_header(line[len('diff --git ') :]))
        elif line.startswith('--- ') or line.startswith('+++ '):
            name = line[4:].split('\t')[0]
            if unquote(name) != '/dev/null':
                paths.add(strip_prefix(name, 'a/' if line[0] == '-' else 'b/'))
    paths.discard('')
    return sorted(paths)


def in_scope(path, allowed):
    if not allowed:
        return True
    for pattern in allowed:
        if pattern.endswith('/**'):
            if path.startswith(pattern[:-3] + '/'):
                return True
        elif fnmatch.fnmatchcase(path, pattern):
            return True
    return False


def replay(manifest):
    """Une ligne par étalon, dans l'ordre ; Refused 64 si une configuration ou un commit est inutilisable."""
    scopes = {}
    for item in manifest['etalons']:
        pair = (item['config'], item['task'])
        if pair not in scopes:
            allowed = scopes[pair] = load_task(*pair)[1].get('allowed')
            if allowed is not None and not (isinstance(allowed, list) and all(isinstance(a, str) for a in allowed)):
                raise Refused('Configuration invalide : allowed doit être une liste de motifs', 64)
    rows = []
    for item in manifest['etalons']:
        allowed = scopes[(item['config'], item['task'])]
        row = dict(
            id=item['id'],
            kind=item['kind'],
            expected=item['expected'],
            task=item['task'],
            passed=None,
            total=None,
            code=None,
            out_of_scope=[],
            reason='',
        )
        try:
            data = Path(item['diff']).read_bytes()
        except OSError as error:
            raise Refused('%s : diff illisible : %s' % (item['id'], error), 64)
        row['out_of_scope'] = [p for p in diff_paths(data) if not in_scope(p, allowed)]
        try:
            row['passed'], row['total'], row['code'], output = evaluate_diff(item['config'], item['task'], item['diff'], item['base'])
            sys.stderr.write('== %s ==\n%s' % (item['id'], output))
        except Refused as error:
            if error.code == 64:
                raise Refused('%s : %s' % (item['id'], error), 64)
            row['reason'] = str(error)
        known = row['passed'] is not None and row['total'] is not None
        accepted = not row['reason'] and known and row['passed'] == row['total'] > 0 and row['code'] == 0 and not row['out_of_scope']
        row['observed'] = 'accept' if accepted else 'reject'
        row['verdict'] = (
            'ok' if row['observed'] == row['expected'] else 'faux_rejet' if row['expected'] == 'accept' else 'fausse_acceptation'
        )
        rows.append(row)
    return rows


def cells(row):
    count = '%s/%s' % tuple('?' if row[k] is None else row[k] for k in ('passed', 'total'))
    scope = 'hors : ' + ','.join(row['out_of_scope']) if row['out_of_scope'] else 'ok'
    return [row['id'], row['kind'], 'attendu ' + row['expected'], 'obtenu ' + row['observed'], count, 'périmètre ' + scope, row['verdict']]


def summary(rows):
    wrong = [sum(r['verdict'] == name for r in rows) for name in ('faux_rejet', 'fausse_acceptation')]
    return [
        'faux rejets : %d/%d' % (wrong[0], sum(r['expected'] == 'accept' for r in rows)),
        'fausses acceptations : %d/%d' % (wrong[1], sum(r['expected'] == 'reject' for r in rows)),
    ]


def kit_commit():
    try:
        done = run(['git', 'rev-parse', '--short', 'HEAD'], KIT)
    except OSError:
        return 'inconnu'
    return done.stdout.strip() if not done.returncode and done.stdout.strip() else 'inconnu'


def report(manifest, rows):
    def escape(value):
        return str(value).replace('|', '\\|')

    tasks = sorted({row['task'] for row in rows})
    good = sum(row['expected'] == 'accept' for row in rows)
    configs = sorted({item['written_config'] for item in manifest['etalons']})
    lines = [
        "# Taux d'erreur du vérificateur",
        '',
        '- Date : ' + datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d'),
        '- Commit du kit : ' + kit_commit(),
        '- Tâche : ' + ', '.join(tasks),
        '- Configuration : ' + ', '.join(configs),
        '- Jeu : %d étalons (%d bons, %d mauvais), %d tâches' % (len(rows), good, len(rows) - good, len(tasks)),
        '',
        '| Étalon | Nature | Attendu | Obtenu | Évaluation | Périmètre | Verdict |',
        '|---|---|---|---|---|---|---|',
    ]
    lines += ['| ' + ' | '.join(escape(c) for c in cells(row)) + ' |' for row in rows]
    size = 'taille du jeu : %d étalons, %d tâches' % (len(rows), len(tasks))
    return '\n'.join(lines + ['', size] + summary(rows)) + '\n'


def parse(args):
    manifest, write, index = None, None, 0
    while index < len(args):
        if args[index] == '--write':
            if write is not None or index + 1 >= len(args) or args[index + 1].startswith('--'):
                return None
            write, index = args[index + 1], index + 2
        elif args[index].startswith('--') or manifest is not None:
            return None
        else:
            manifest, index = args[index], index + 1
    return manifest, write


def main(args):
    if '--harvest' in args:
        from etalons_harvest import main as harvest

        return harvest(list(args))
    parsed = parse(list(args))
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return 64
    path = Path(parsed[0]) if parsed[0] else DEFAULT_MANIFEST
    try:
        manifest = load_manifest(path)
        rows = replay(manifest)
    except ValueError as error:
        print('etalons: ' + str(error), file=sys.stderr)
        return 64
    except Refused as error:
        print('etalons: ' + str(error), file=sys.stderr)
        return error.code
    for row in rows:
        print(' | '.join(cells(row)))
    for line in summary(rows):
        print(line)
    if parsed[1]:
        try:
            Path(parsed[1]).write_text(report(manifest, rows), encoding='utf-8')
        except OSError as error:
            print('etalons: écriture impossible : ' + str(error), file=sys.stderr)
            return 64
    return 0 if all(row['verdict'] == 'ok' for row in rows) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
