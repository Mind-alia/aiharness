"""Note du casseur sur les étalons du vérificateur (Python 3.14).

Sur un diff « mauvais » le casseur doit retenir au moins un test, sur un « bon » aucun. Chaque couple (étalon, exécutant)
rejoue le circuit dans le clone d'un projet : prepare --base, diff appliqué, breaker.sh, report.json, fermeture."""

import datetime
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import etalons
import evaluation

USAGE = (
    'usage: python3 tools/campaign/breaker_score.py <manifeste> --project <clone> --agent <exécutant> '
    '[--agent <exécutant>…] [--only id1,id2] --out <dossier> [--write <fichier.md>]'
)
SHORT = {'claude-haiku': 'haiku', 'claude-sonnet': 'sonnet', 'claude-opus': 'opus'}
SKIPPED_KIND = 'hors périmètre'
SECTION = '## Casseur'
STEP_TIMEOUT, BREAKER_TIMEOUT = 600, 3600


class Usage(Exception):
    pass


def parse(args):  # noqa: C901
    """(manifeste, options) ; Usage si la ligne de commande est invalide."""
    options, manifest, index = dict(agent=[], only=None, project=None, out=None, write=None), None, 0
    while index < len(args):
        name = args[index]
        if name in ('--project', '--agent', '--only', '--out', '--write'):
            if index + 1 >= len(args) or args[index + 1].startswith('--'):
                raise Usage(name + ' demande une valeur')
            value, index = args[index + 1], index + 2
            key = name[2:]
            if key == 'agent':
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', value):
                    raise Usage('exécutant invalide : ' + value)
                options['agent'] += [value] if value not in options['agent'] else []
            elif options[key] is not None:
                raise Usage(name + ' donné deux fois')
            elif key == 'only':
                options[key] = [part.strip() for part in value.split(',') if part.strip()]
                if not options[key]:
                    raise Usage('--only vide')
            else:
                options[key] = value
        elif name.startswith('--') or manifest is not None:
            raise Usage('argument inattendu : ' + name)
        else:
            manifest, index = name, index + 1
    if manifest is None or not options['agent'] or options['project'] is None or options['out'] is None:
        raise Usage('manifeste, --project, --agent et --out sont obligatoires')
    return manifest, options


def sh(args, cwd, timeout=STEP_TIMEOUT):
    """(code, sortie) sans WORKSPACE_AGENT_ROOT ; 124 si le délai tombe (groupe de processus tué)."""
    env = {k: v for k, v in os.environ.items() if k != 'WORKSPACE_AGENT_ROOT' and k not in evaluation.GIT_VARIABLES}
    process = subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
        text=True,
        errors='replace',
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        out, _ = process.communicate(timeout=timeout)
        return process.returncode, out
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.communicate()
        return 124, 'délai dépassé'


def tail(output):
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    return (' : ' + lines[-1][:200]) if lines else ''


def close_task(identifier, clone):
    """'' si la tâche est fermée (au second essai au plus), sinon le motif de l'échec."""
    args = ['bash', 'scripts/task-workspace.sh', 'close', identifier, '--abandoned', '--force']
    code, output = sh(args, clone)
    if code == 0:
        return ''
    sh(['bash', 'scripts/sandbox.sh', 'stop', identifier], clone)
    code, output = sh(args, clone)
    return '' if code == 0 else 'close (code %d)%s' % (code, tail(output))


def original_task(item, cache):
    """(chemin de la fiche d'origine, texte) ; ValueError dit pourquoi elle est introuvable."""
    key = (item['config'], item['task'])
    if key not in cache:
        try:
            project, _, _ = evaluation.load_task(*key)
            state_file = project / '.workspace/orchestration/tasks' / item['task'] / 'state.json'
            source = (project / json.loads(state_file.read_text(encoding='utf-8'))['source_task']).resolve()
            if project.resolve() not in source.parents or not source.is_file():
                raise ValueError('fiche hors du projet ou absente : ' + str(source))
            cache[key] = source.read_text(encoding='utf-8')
        except (evaluation.Refused, OSError, ValueError, KeyError, TypeError) as error:
            cache[key] = ValueError('fiche d’origine introuvable (%s) : %s' % (item['task'], error))
    if isinstance(cache[key], ValueError):
        raise cache[key]
    return cache[key]


def rename_id(text, identifier):
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith('id:'):
            lines[index] = 'id: %s%s' % (identifier, line[len(line.rstrip('\r\n')) :])
            return ''.join(lines)
    raise ValueError('fiche d’origine sans ligne id:')


def verdict_of(expected, report):
    """Verdict d'un couple (attendu, rapport du casseur) : `no_implementation` compte comme un rejet."""
    no_implementation = report.get('verdict') == 'no_implementation'
    if expected == 'reject':
        return 'attrapé' if report['retained'] or no_implementation else 'manqué'
    return 'faux test' if report['retained'] else 'faux rejet' if no_implementation else 'propre'


def score_one(item, agent, clone, cache):
    """Ligne de résultat d'un couple (étalon, exécutant)."""
    identifier = '%s-cas-%s' % (item['id'], SHORT.get(agent, agent))
    row = dict(
        etalon=item['id'],
        task=item['task'],
        expected=item['expected'],
        kind=item['kind'],
        agent=agent,
        retained=None,
        discarded=None,
        error='',
        verdict='erreur',
    )
    try:
        fiche = rename_id(original_task(item, cache), identifier)
        target = clone / '.workspace/tasks' / (identifier + '.md')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(fiche, encoding='utf-8')
    except (OSError, ValueError) as error:
        row['error'] = 'fiche : ' + str(error)
        return row
    state_file = clone / '.workspace/orchestration/tasks' / identifier / 'state.json'
    code, output = sh(
        ['bash', 'scripts/task-workspace.sh', 'prepare', '.workspace/tasks/%s.md' % identifier, '--base', item['base']]
        + ['--retry'] * state_file.exists(),
        clone,
    )
    if code:
        row['error'] = 'prepare (code %d)%s' % (code, tail(output))
        return row
    try:
        step = 'worktree'
        worktree = clone / json.loads(state_file.read_text(encoding='utf-8'))['worktree']
        step = 'diff'
        code, output = sh(['git', 'apply', '--index', item['diff']], worktree)
        if code:
            row['error'] = 'diff (code %d)%s' % (code, tail(output))
            return row
        step = 'casseur'
        code, output = sh(['bash', 'scripts/breaker.sh', identifier, '--agent', agent], clone, BREAKER_TIMEOUT)
        if code:
            row['error'] = 'casseur (code %d)%s' % (code, tail(output))
            return row
        step = 'rapport'
        report = json.loads((clone / '.workspace/breaker' / identifier / 'report.json').read_text(encoding='utf-8'))
        retained, discarded = report['retained'], report['discarded']
        if not isinstance(retained, list) or not isinstance(discarded, list):
            raise ValueError('listes attendues')
    except (OSError, ValueError, KeyError, TypeError) as error:
        row['error'] = '%s illisible : %s' % (step, error) if step != 'diff' else 'diff : ' + str(error)
        return row
    finally:
        closing = close_task(identifier, clone)
        if closing:
            row['error'] = (row['error'] + ' ; ' if row['error'] else '') + closing
    if row['error']:
        return row
    row.update(retained=len(retained), discarded=len(discarded))
    row['verdict'] = verdict_of(item['expected'], report)
    return row


def read_rows(path):
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines() if path.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get('etalon'), str) and isinstance(row.get('agent'), str):
            rows.append(row)
    return rows


def write_rows(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def section(rows, agents, items):
    def escape(value):
        return str(value).replace('|', '\\|')

    by_key = {(row['etalon'], row['agent']): row for row in rows}
    lines = [
        SECTION,
        '',
        '- Date : ' + datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d'),
        '',
        '| Exécutant du casseur | Mauvais attrapés | Bons sans faux test | Erreurs |',
        '|---|---|---|---|',
    ]
    for agent in agents:
        mine = [row for row in rows if row['agent'] == agent]
        bad, good = [[r for r in mine if r['expected'] == name] for name in ('reject', 'accept')]
        lines.append(
            '| %s | %d/%d | %d/%d | %d |'
            % (
                escape(agent),
                sum(r['verdict'] == 'attrapé' for r in bad),
                len(bad),
                sum(r['verdict'] == 'propre' for r in good),
                len(good),
                sum(r['verdict'] == 'erreur' for r in mine),
            )
        )
    lines += ['', '| Étalon | Nature | Attendu | ' + ' | '.join(escape(a) for a in agents) + ' |', '|---|---|---|' + '---|' * len(agents)]
    for item in items:
        cells = []
        for agent in agents:
            row = by_key.get((item['id'], agent))
            cells.append(
                '?' if row is None else row['verdict'] if row['retained'] is None else '%s (%d)' % (row['verdict'], row['retained'])
            )
        lines.append('| ' + ' | '.join(escape(c) for c in [item['id'], item['kind'], item['expected']] + cells) + ' |')
    return '\n'.join(lines) + '\n'


def merge_section(text, new):
    """`text` avec la section « ## Casseur » remplacée par `new`, ou `new` ajoutée à la fin après une ligne vide."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.rstrip() == SECTION), None)
    if start is None:
        return text + '\n' * max(0, 2 - (len(text) - len(text.rstrip('\n')))) * bool(text) + new
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('## ')), len(lines))
    return ''.join(lines[:start]) + new + ('\n' + ''.join(lines[end:]) if end < len(lines) else '')


def main(args):
    try:
        manifest_path, options = parse(list(args))
        manifest = etalons.load_manifest(manifest_path)
        clone, out = Path(options['project']).expanduser().resolve(), Path(options['out']).expanduser()
        code, output = sh(['git', 'status', '--porcelain'], clone)
        if code or output.strip():
            raise Usage('le clone n’est pas un dépôt Git propre')
        write = Path(options['write']).expanduser() if options['write'] else None
        if write is not None and (write.is_dir() or not write.parent.is_dir()):
            raise Usage('--write inutilisable : ' + str(write))
        out.mkdir(parents=True, exist_ok=True)
    except (Usage, ValueError, OSError) as error:
        print('breaker_score: %s\n%s' % (error, USAGE), file=sys.stderr)
        return 64
    items = [
        item
        for item in manifest['etalons']
        if item['kind'].strip() != SKIPPED_KIND and (options['only'] is None or item['id'] in options['only'])
    ]
    agents = options['agent']
    plan = [(item, agent) for item in items for agent in agents]
    results = out / 'results.jsonl'
    rows = [row for row in read_rows(results) if row.get('verdict') != 'erreur']
    cache = {}
    for item, agent in plan:
        if any(row['etalon'] == item['id'] and row['agent'] == agent for row in rows):
            continue
        rows.append(score_one(item, agent, clone, cache))
        write_rows(results, rows)
        print('%s %s : %s' % (item['id'], agent, rows[-1]['verdict']), file=sys.stderr)
    write_rows(results, rows)
    text = section([r for r in rows if any(r['etalon'] == i['id'] and r['agent'] == a for i, a in plan)], agents, items)
    try:
        (out / 'casseur.md').write_text(text, encoding='utf-8')
        if write is not None:
            write.write_text(merge_section(write.read_text(encoding='utf-8') if write.exists() else '', text), encoding='utf-8')
    except OSError as error:
        print('breaker_score: écriture impossible : ' + str(error), file=sys.stderr)
        return 64
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
