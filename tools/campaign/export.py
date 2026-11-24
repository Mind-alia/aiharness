"""Export campaign course results as CSV and JSON (Python 3.14, stdlib only)."""

import csv
import json
from pathlib import Path

NM = 'non mesuré'
COLUMNS = (
    'mission',
    'bras',
    'repetition',
    'livre',
    'faux_succes',
    'tentatives',
    'duree_s',
    'tokens',
    'interventions_humaines',
    'minutes_humaines',
    'violations',
    'verdict_juge',
)


def _read_jsonl(path):
    rows = []
    with path.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def journal_summary(worktree):
    path = Path(worktree) / '.workspace/events.jsonl'
    if not path.is_file():
        return None
    human_interventions = human_minutes = scope_violations = secret_violations = 0
    for event in _read_jsonl(path):
        kind = event.get('event', event.get('type'))
        detail = event.get('detail')
        detail = detail if isinstance(detail, dict) else {}
        if kind == 'human_intervention':
            human_interventions += 1
            minutes = detail.get('minutes')
            if type(minutes) is int:
                human_minutes += minutes
        if kind == 'outcome':
            outcome = detail.get('outcome')
            if outcome == 'out_of_scope':
                scope_violations += 1
            if isinstance(outcome, str) and 'secret' in outcome:
                secret_violations += 1
    return dict(
        human_interventions=human_interventions,
        human_minutes=human_minutes,
        scope_violations=scope_violations,
        secret_violations=secret_violations,
    )


def _course(row):
    evaluation = row.get('evaluation')
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    passed, total = evaluation.get('passed'), evaluation.get('total')
    if type(passed) is int and type(total) is int and total > 0:
        livre = 'oui' if passed == total else 'non'
    else:
        livre = NM
    status = row.get('status')
    faux_succes = NM if livre == NM else ('oui' if status == 'completed' and livre == 'non' else 'non')
    attempts = row.get('attempts')
    attempts = str(attempts) if type(attempts) is int else NM
    duration = row.get('duration_seconds')
    duration = '%.1f' % duration if type(duration) in (int, float) else NM
    consumption = row.get('consumption')
    tokens = []
    if isinstance(consumption, dict):
        for provider, roles in sorted(consumption.items()):
            if not isinstance(roles, dict):
                continue
            for role, counts in sorted(roles.items()):
                counts = counts if isinstance(counts, dict) else {}
                value = counts.get('total_tokens')
                tokens.append('%s/%s=%s' % (provider, role, value if type(value) is int else NM))
    journal = row.get('journal')
    if not isinstance(journal, dict):
        humans = minutes = violations = NM
    else:
        humans = str(journal.get('human_interventions', NM))
        minutes = str(journal.get('human_minutes', NM))
        violations = 'périmètre=%s; secret=%s' % (journal.get('scope_violations', NM), journal.get('secret_violations', NM))
    judge = row.get('judge')
    verdict = judge.get('verdict') if isinstance(judge, dict) else None
    if not isinstance(verdict, str) or not verdict:
        verdict = NM
    values = (
        row.get('task', NM),
        row.get('arm', NM),
        str(row.get('repetition', NM)),
        livre,
        faux_succes,
        attempts,
        duration,
        '; '.join(tokens) if tokens else NM,
        humans,
        minutes,
        violations,
        verdict,
    )
    return dict(zip(COLUMNS, (str(value) for value in values), strict=False))


def export(results_dir):
    directory = Path(results_dir)
    results = directory / 'results.jsonl'
    if not directory.is_dir() or not results.is_file():
        raise ValueError('dossier de résultats invalide ou results.jsonl absent : %s' % results_dir)
    rows = [_course(row) for row in _read_jsonl(results)]
    csv_path, json_path = directory / 'courses.csv', directory / 'courses.json'
    with csv_path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(COLUMNS)
        writer.writerows([[row[column] for column in COLUMNS] for row in rows])
    with json_path.open('w', encoding='utf-8', newline='') as stream:
        json.dump(rows, stream, ensure_ascii=False)
        stream.write('\n')
    return str(csv_path.resolve()), str(json_path.resolve())
