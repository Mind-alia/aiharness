#!/usr/bin/env python3
"""Banc de rejeu de l'étude de reprise de session, Python 3.14, bibliothèque standard.

Chaque tâche est rejouée deux fois sur un clone propre du kit, avec un faux échec forcé à la première tentative :
`reprise` (session reprise) puis `sans-reprise` (WORKSPACE_DELEGATE_RESUME=0). Chaque passage est fermé en `--abandoned`
(jamais de fusion). Une ligne par passage dans `<out>/results.jsonl`, le tableau dans `<out>/resultats.md`.

Codes : 0 fini ; 64 usage invalide, projet non propre ou tâche inconnue (rien n'est lancé ni écrit)."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

MODES = ('reprise', 'sans-reprise')
TOKEN_KEYS = (('input', 'input'), ('cache_read', 'cached_input'), ('cache_write', 'cache_creation'), ('output', 'output'))
HEADER = (
    '| Tâche | Mode | Issue | Tentatives | Durée (s) | 2e tentative (s) | Cache lu | Cache écrit | Entrée | Sortie |\n'
    '|---|---|---|---|---|---|---|---|---|---|\n'
)
DELEGATE_TIMEOUT = 3600


class Usage(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print('resume_study.py: ' + message, file=sys.stderr)
        sys.exit(64)


def validate(args):
    """Return [(task id, source fiche text, hidden tests dir, base commit)]; raise Usage on any gap."""
    project, source = Path(args.project), Path(args.source)
    ids = [name.strip() for name in args.tasks.split(',')]
    if not ids or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name) for name in ids) or len(set(ids)) != len(ids):
        raise Usage('liste de tâches invalide : ' + args.tasks)
    if not project.is_dir() or not source.is_dir():
        raise Usage('projet ou kit source introuvable')
    status = subprocess.run(['git', '-C', str(project), 'status', '--porcelain'], text=True, capture_output=True)
    if status.returncode or status.stdout.strip():
        raise Usage('projet absent de Git ou non propre : ' + str(project))
    tasks = []
    for name in ids:
        fiche = source / '.workspace/tasks' / (name + '.md')
        tests = source / '.workspace/acceptance' / name
        state = source / '.workspace/orchestration/tasks' / name / 'state.json'
        try:
            base = json.loads(state.read_text(encoding='utf-8'))['base_commit']
            text = fiche.read_text(encoding='utf-8')
        except (OSError, ValueError, KeyError, TypeError):
            raise Usage('tâche inconnue ou incomplète : ' + name)
        if not tests.is_dir() or not isinstance(base, str) or not base:
            raise Usage('tâche inconnue ou incomplète : ' + name)
        tasks.append((name, text, tests, base))
    return tasks


def study_fiche(text, study_id, agent):
    """Rewrite id, agent and attempts in the header only; the body is untouched."""
    lines = text.split('\n')
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == '---'), None) if lines[0].rstrip() == '---' else None
    if end is None:
        raise Usage('fiche sans en-tête')
    wanted = {'id': study_id, 'agent': agent, 'attempts': '2'}
    header = []
    for line in lines[1:end]:
        key = line.split(':', 1)[0] if ':' in line else None
        header.append('%s: %s' % (key, wanted.pop(key)) if key in wanted and not line.startswith((' ', '\t')) else line)
    header += ['%s: %s' % item for item in wanted.items()]
    return '\n'.join([lines[0]] + header + lines[end:])


def passes_done(results):
    done = set()
    for row in read_rows(results):
        if 'error' not in row:
            done.add((row.get('task'), row.get('mode')))
    return done


def read_rows(results):
    rows = []
    if results.is_file():
        for line in results.read_text(encoding='utf-8').splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def run(project, command, env, timeout=None):
    try:
        done = subprocess.run(command, cwd=project, env=env, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, '%s : %s' % (type(exc).__name__, exc)
    return done.returncode, (done.stdout + done.stderr)[-400:]


def one_pass(project, agent, task, mode):
    name, text, tests, base = task
    study_id = '%s-etude-%s' % (name, mode)
    row = dict(
        task=name,
        mode=mode,
        study_id=study_id,
        outcome=None,
        attempts=None,
        duration_seconds=None,
        second_attempt_seconds=None,
        tokens=dict.fromkeys(('input', 'cache_read', 'cache_write', 'output')),
    )
    fiche = project / '.workspace/tasks' / (study_id + '.md')
    fiche.parent.mkdir(parents=True, exist_ok=True)
    fiche.write_text(study_fiche(text, study_id, agent), encoding='utf-8')
    target = project / '.workspace/acceptance' / study_id
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(tests, target)
    env = {k: v for k, v in os.environ.items() if k not in ('WORKSPACE_AGENT_ROOT', 'WORKSPACE_DELEGATE_RESUME')}
    env['WORKSPACE_STUDY_FORCE_FAIL'] = '1'
    if mode == 'sans-reprise':
        env['WORKSPACE_DELEGATE_RESUME'] = '0'
    relative = '.workspace/tasks/%s.md' % study_id
    code, detail = run(project, ['bash', 'scripts/task-workspace.sh', 'prepare', relative, '--base', base], env)
    if code:
        row['error'] = 'prepare en échec : ' + detail
        return row
    run(project, ['bash', 'scripts/delegate.sh', relative], env, DELEGATE_TIMEOUT)
    try:
        status = json.loads((project / '.workspace/run/delegate' / study_id / 'status.json').read_text(encoding='utf-8'))
        metrics = status.get('metrics') or {}
        durations = metrics.get('attempt_durations_seconds') or []
        tokens = metrics.get('tokens') or {}
        row.update(
            outcome=status.get('outcome'),
            attempts=status.get('attempts'),
            duration_seconds=metrics.get('duration_seconds'),
            second_attempt_seconds=durations[1] if len(durations) > 1 else None,
            tokens={key: tokens.get(source) for key, source in TOKEN_KEYS},
        )
    except (OSError, ValueError, AttributeError):
        row['error'] = 'status.json illisible'
    code, detail = run(project, ['bash', 'scripts/task-workspace.sh', 'close', study_id, '--abandoned', '--force'], env)
    if code and 'error' not in row:
        row['error'] = 'close en échec : ' + detail
    return row


def cell(value):
    if isinstance(value, float):
        value = round(value, 1)
    return '-' if value is None else str(value)


def write_table(out):
    latest = {}
    for row in read_rows(out / 'results.jsonl'):
        latest[(row.get('task'), row.get('mode'))] = row
    rows = list(latest.values())
    lines = []
    for row in rows:
        tokens = row.get('tokens') or {}
        lines.append(
            [
                row.get('task'),
                row.get('mode'),
                row.get('outcome') if 'error' not in row else 'erreur',
                row.get('attempts'),
                row.get('duration_seconds'),
                row.get('second_attempt_seconds'),
                tokens.get('cache_read'),
                tokens.get('cache_write'),
                tokens.get('input'),
                tokens.get('output'),
            ]
        )
    for mode in MODES:
        part = [row for row in rows if row.get('mode') == mode]

        def total(get, part=part):
            return sum(v for v in map(get, part) if isinstance(v, (int, float)))

        lines.append(
            ['Total ' + mode, '', '', '', total(lambda r: r.get('duration_seconds')), total(lambda r: r.get('second_attempt_seconds'))]
            + [total(lambda r, k=k: (r.get('tokens') or {}).get(k)) for k in ('cache_read', 'cache_write', 'input', 'output')]
        )
    body = ''.join('| %s |\n' % ' | '.join(cell(v) if v != '' else '' for v in line) for line in lines)
    (out / 'resultats.md').write_text(HEADER + body, encoding='utf-8')


def main(argv=None):
    parser = Parser(description=__doc__.split('\n')[0])
    parser.add_argument('--project', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--tasks', required=True)
    parser.add_argument('--agent', default='claude-haiku')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        tasks = validate(args)
    except Usage as exc:
        print('resume_study.py: ' + str(exc), file=sys.stderr)
        return 64
    project, out = Path(args.project), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = out / 'results.jsonl'
    for task in tasks:
        for mode in MODES:
            if (task[0], mode) in passes_done(results):
                continue
            try:
                row = one_pass(project, args.agent, task, mode)
            except (OSError, Usage) as exc:
                row = dict(
                    task=task[0],
                    mode=mode,
                    study_id='%s-etude-%s' % (task[0], mode),
                    outcome=None,
                    attempts=None,
                    duration_seconds=None,
                    second_attempt_seconds=None,
                    tokens={},
                    error=str(exc),
                )
            with results.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
            write_table(out)
    write_table(out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
