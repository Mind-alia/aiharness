"""Rapport de fin de plan de l'orchestrateur programme (fiche T14, module de rapport).

Chaque chiffre vient d'un fichier ou d'une commande ; ce qui n'est pas mesuré s'écrit « non mesuré ».
"""

import json
import os
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import journal

DEFECTS_PREFIX = 'Défauts trouvés après fusion cette semaine : '
DEFECTS_TIMEOUT = 60
CHECK_TIMEOUT_ENV = 'ORCHESTRATOR_REPORT_CHECK_TIMEOUT'
CHECK_TIMEOUT = 3600
MISSING = 'non mesuré'
NO_DECISION = "Aucune décision prise à la place du propriétaire par le programme."
token_keys = ('input', 'cached_input', 'cache_creation', 'output')
TRAVAUX_HEADER = ['Fiche', 'Exécutant', 'Fusion', 'Preuve']
COSTS_HEADER = ['Fiche', 'Appels', 'Rôles', 'Claude entrée / cache lu / cache créé / sortie', 'Dollars']
DELEGATIONS_HEADER = ['Tâche', 'Exécutant', 'Issue', 'Tentatives', 'Durée (s)', 'Claude entrée / cache lu / cache créé / sortie']
VERIFY_LABELS = {'done': 'verte', 'skipped': 'sautée'}


def build(root, run):
    """Texte Markdown du rapport, règles 1 à 9 de la fiche."""
    order = _order(run)
    tasks = run.get('tasks') or {}
    rows = [(task_id, tasks.get(task_id) or {}, _delegation(root, task_id)) for task_id in order]
    measured = [m for m in (_measure(status) for _, _, status in rows) if m is not None]
    blocks = [
        f"# Rapport de l'orchestrateur — {_text(run.get('session'))} ({_day(run)})\n\n{_defects(root)}",
        _identity(run),
        _section('Travaux', _table(TRAVAUX_HEADER, [_travail(run, *row) for row in rows])),
        _section('Délégations', _table(DELEGATIONS_HEADER, _delegation_rows(rows) + [_total(measured)])),
        _section('Défauts rencontrés et décisions prises à la place du propriétaire', _decisions(run)),
        _section('Tokens par fournisseur et par rôle', _tokens(run, measured) + '\n\n' + _costs(root, run, order)),
        _section('État des dépôts', _repos(root)),
        _section('Ce que l\'orchestrateur ferait ensuite', _next(run, tasks)),
    ]
    return '\n\n'.join(blocks) + '\n'


def write(root, run, path):
    """Écrit le rapport dans path (relatif à root ou absolu sous root) et rend son chemin absolu."""
    target = _inside(root, path)
    content = build(root, run)
    os.makedirs(str(target.parent), exist_ok=True)
    target.write_text(content, encoding='utf-8')
    return target


def check(root, path, log=None):
    """Passe le rapport par scripts/report-check.sh et rend son code (127 absent, 124 délai dépassé)."""
    base = os.path.abspath(str(root))
    target = _inside(root, path)
    if not (Path(base) / 'scripts' / 'report-check.sh').is_file():
        return 127
    log = Path(base) / '.workspace' / 'orchestrator-run' / 'report-check.log' if log is None else Path(base) / log
    os.makedirs(str(log.parent), mode=0o700, exist_ok=True)
    rel = os.path.relpath(str(target), base)
    with open(str(log), 'ab') as stream:
        proc = subprocess.Popen(
            ['bash', 'scripts/report-check.sh', Path(rel).as_posix()],
            cwd=base,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return proc.wait(timeout=_check_timeout())
        except subprocess.TimeoutExpired:
            _kill(proc)
            return 124


def _inside(root, path):
    """Chemin absolu sous root ; ValueError hors de root ou à travers un lien symbolique."""
    base = os.path.abspath(str(root))
    target = os.path.abspath(os.path.join(base, str(path)))
    if not target.startswith(base + os.sep):
        raise ValueError(f'chemin hors du dépôt : {path}')
    current = Path(base)
    for part in Path(target).relative_to(base).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f'lien symbolique refusé : {current}')
    return Path(target)


def _check_timeout():
    try:
        return float(os.environ.get(CHECK_TIMEOUT_ENV, CHECK_TIMEOUT))
    except ValueError:
        return float(CHECK_TIMEOUT)


def _kill(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    proc.wait()


def _capture(args, root, timeout):
    """Sortie standard de la commande, ou None si elle échoue, manque ou dépasse le délai."""
    try:
        done = subprocess.run(
            args, cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.decode('utf-8', 'replace') if done.returncode == 0 else None


def _git(root, *args):
    return _capture(['git', *args], root, DEFECTS_TIMEOUT)


def _defects(root):
    if not (Path(root) / 'scripts' / 'report.sh').is_file():
        return DEFECTS_PREFIX + MISSING
    output = _capture(['bash', 'scripts/report.sh', '--defauts'], root, DEFECTS_TIMEOUT) or ''
    lines = [line for line in output.splitlines() if line.startswith(DEFECTS_PREFIX)]
    return lines[-1] if lines else DEFECTS_PREFIX + MISSING


def _order(run):
    order = run.get('order')
    return list(run.get('tasks') or {}) if order is None else list(order)


def _day(run):
    for key in ('finished_at', 'started_at'):
        if run.get(key):
            return str(run[key])[:10]
    return datetime.now(UTC).date().isoformat()


def _text(value):
    return MISSING if value in (None, '') else str(value)


def _word(output):
    return MISSING if output is None or not output.strip() else output.strip()


def _count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value))


def _fmt(number):
    if number is None:
        return MISSING
    return f'{number:,}'.replace(',', ' ')


def _cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def _table(header, rows):
    lines = [header, ['---'] * len(header)] + rows
    return '\n'.join('| ' + ' | '.join(_cell(cell) for cell in line) + ' |' for line in lines)


def _section(title, body):
    return '## {}\n\n{}'.format(title, body if isinstance(body, str) else '\n'.join(body))


def _delegation(root, task_id):
    """status.json de la délégation, ou None s'il est absent, illisible ou à travers un lien symbolique."""
    path = Path(root)
    for part in ('.workspace', 'run', 'delegate', task_id, 'status.json'):
        path = path / part
        if path.is_symlink():
            return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _agent(status, task):
    for source in (status or {}, task):
        if source.get('agent'):
            return str(source['agent'])
    return MISSING


def _identity(run):
    stop = run.get('stop_reason')
    issue = _text(run.get('status')) + (f' ({stop})' if stop else '')
    return (
        "Programme `tools/orchestrator/run.py`, session `{}` lancée par `{}`, modèle `{}`, plan `{}`. "
        "Début {}, fin {}, reprises après interruption : {}. Issue : {}."
    ).format(
        _text(run.get('session')),
        _text(run.get('by')),
        _text(run.get('model')),
        _text(run.get('plan')),
        _text(run.get('started_at')),
        run.get('finished_at') or 'non terminé',
        run.get('resumes') or 0,
        issue,
    )


def _step(run, task_id):
    current = run.get('current') or {}
    if current.get('task') == task_id:
        return current.get('step') or MISSING
    return 'non commencée'


def _travail(run, task_id, task, status):
    sha = task.get('merge_commit')
    fusion = f'`{str(sha)[:7]}`' if sha else 'aucune'
    if task_id in (run.get('done') or []):
        steps = task.get('steps') or {}
        verify = VERIFY_LABELS.get((steps.get('verify') or {}).get('status'), 'non mesurée')
        outcome = _text((status or {}).get('outcome'))
        proof = f'délégation `{outcome}`, vérification hôte {verify}, fusion {fusion}'
    elif task_id in (run.get('failed') or []):
        proof = 'échec : {}'.format(task.get('reason') or 'non précisé')
        if task.get('patch'):
            proof += " ; diff complet `{}`, clone fermé (T120)".format(task['patch'])
    elif task.get('notice'):
        proof = 'en attente du propriétaire : avis `{}`'.format(task['notice'])
    else:
        proof = f'non terminée (étape {_step(run, task_id)})'
    return [task_id, _agent(status, task), fusion, proof]


def _measure(status):
    """(tentatives, durée, jetons) d'une délégation mesurée, None sinon."""
    if status is None:
        return None
    metrics = status.get('metrics') if isinstance(status.get('metrics'), dict) else {}
    tokens = metrics.get('tokens') if isinstance(metrics.get('tokens'), dict) else {}
    values = tuple(_count(tokens.get(key)) or 0 for key in token_keys)
    return (_count(metrics.get('attempts', status.get('attempts'))), _count(metrics.get('duration_seconds')), values)


def _delegation_rows(rows):
    lines = []
    for task_id, task, status in rows:
        measure = _measure(status)
        if measure is None:
            lines.append([task_id, _agent({}, task), MISSING, MISSING, MISSING, MISSING])
            continue
        attempts, duration, values = measure
        lines.append(
            [
                task_id,
                _agent(status, task),
                _text(status.get('outcome')),
                _fmt(attempts),
                _fmt(duration),
                ' / '.join(_fmt(value) for value in values),
            ]
        )
    return lines


def _total(measured):
    attempts = sum(m[0] or 0 for m in measured)
    duration = sum(m[1] or 0 for m in measured)
    totals = [sum(m[2][i] for m in measured) for i in range(len(token_keys))]
    return ['**Total**', '', f'{len(measured)} mesurées', _fmt(attempts), _fmt(duration), ' / '.join(_fmt(value) for value in totals)]


def _decisions(run):
    decisions = run.get('decisions') or []
    if not decisions:
        return NO_DECISION
    return [f'{number}. {decision}' for number, decision in enumerate(decisions, 1)]


def _tokens(run, measured):
    output = sum(m[2][-1] for m in measured)
    lines = [f'Claude, exécution : sortie {_fmt(output)} tokens sur {len(measured)} délégations mesurées.']
    if run.get('report_check_enabled') is not False:
        lines.append('Codex, relecture : coût dans le bloc ajouté en tête par `report-check.sh`.')
    else:
        lines.append('Codex, relecture : vérification du rapport non lancée.')
    return '\n\n'.join(lines)


def _costs(root, run, order):
    """T102 : tous les appels de modèle de chaque fiche depuis le début du passage (délégations, diagnostic,
    relectures), lus dans le journal de consommation du circuit, en tokens et en dollars donnés par le fournisseur."""
    task_cost = journal.circuit_module(root, 'task_cost')
    if task_cost is None or not hasattr(task_cost, 'consumption_rows'):
        return f'Coût complet par fiche : {MISSING} (scripts/lib/task_cost.py absent ou ancien).'
    every = []
    lines = []
    for task_id in order:
        rows = task_cost.consumption_rows(root, task_id, since=run.get('started_at'))
        every += rows
        lines.append(_cost_row(task_cost, task_id, rows))
    lines.append(_cost_row(task_cost, '**Total**', every))
    intro = (
        'Coût complet par fiche, tous les appels depuis le début du passage (journal de consommation : délégations et '
        'leurs tentatives, diagnostic, relectures) ; dollars donnés par le fournisseur, `+?` quand des appels n\'en ont pas.'
    )
    return intro + '\n\n' + _table(COSTS_HEADER, lines)


def _cost_row(task_cost, task_id, rows):
    roles = {}
    for row in rows:
        roles[str(row.get('role'))] = roles.get(str(row.get('role')), 0) + 1
    sums = []
    for key in token_keys:
        values = [_count((row.get('tokens') or {}).get(key)) for row in rows if row.get('provider') == 'claude']
        known = [value for value in values if value is not None]
        sums.append(sum(known) if len(known) == len(values) else None)
    spent, unpriced = task_cost.dollars(rows)
    return [
        task_id,
        str(len(rows)),
        ', '.join(f'{role} ×{count}' for role, count in sorted(roles.items())) or 'aucun',
        ' / '.join(_fmt(value) for value in sums),
        task_cost.dollars_text(spent, unpriced, len(rows)) if rows else '0',
    ]


def _repos(root):
    status = _git(root, 'status', '--short')
    changed = MISSING if status is None else str(len([line for line in status.splitlines() if line.strip()]))
    head = '`{}`, tête `{}`'.format(
        _word(_git(root, 'rev-parse', '--abbrev-ref', 'HEAD')), _word(_git(root, 'rev-parse', '--short', 'HEAD'))
    )
    first = f'Branche {head}, {changed} fichiers modifiés non commités (`git status --short`).'
    return [first, '', f'Écart avec la branche suivie : {_gap(root)}']


def _gap(root):
    output = _git(root, 'rev-list', '--left-right', '--count', '@{u}...HEAD')
    if output is not None and len(output.split()) == 2:
        behind, ahead = output.split()
        return f'{behind} en retard, {ahead} en avance.'
    if output is None and _git(root, 'rev-parse', '--abbrev-ref', '@{u}') is None:
        return 'pas de branche suivie.'
    return MISSING + '.'


def _next(run, tasks):
    bullets = []
    for task_id in run.get('failed') or []:
        reason = (tasks.get(task_id) or {}).get('reason') or 'non précisé'
        patch = (tasks.get(task_id) or {}).get('patch')
        again = f' ; `prepare --retry`, puis `git apply {patch}` dans le nouveau clone' if patch else ''
        bullets.append(f'- reprendre `{task_id}` : {reason}{again}')
    for task_id, task in tasks.items():
        if task.get('notice') and task_id not in (run.get('done') or []) and task_id not in (run.get('failed') or []):
            bullets.append('- Le propriétaire : lire `{}` et écrire la ligne demandée dans le plan'.format(task['notice']))
    if run.get('status') != 'finished':
        current = run.get('current') or {}
        where = ' de `{}`'.format(current['task']) if current.get('task') else ''
        bullets.append(
            "- relancer `python3 tools/orchestrator/run.py {}` : il reprendra à l'étape {}{}".format(
                _text(run.get('plan')), current.get('step') or 'non commencée', where
            )
        )
    return bullets or ['- Plan terminé ; rien à reprendre.']
