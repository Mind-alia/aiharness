"""Read delegation metrics without requiring an agent or model dependency."""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

import defects
import events
import integration_logs
import mission_report
import note
import reading
import state_view
from consumption import token_total
from task_cost import NO_ROLE_CALL, cost_line, role_lines


def final_task_status(root, task_id):
    # Same identifier rule as the task registry: never a path.
    if not isinstance(task_id, str) or not re.fullmatch(r'[a-z0-9-]+', task_id):
        return None
    try:
        task_dir = root / '.workspace/orchestration/tasks' / task_id
        if task_dir.is_symlink() or not task_dir.is_dir():
            return None
        state_file = task_dir / 'state.json'
        if state_file.is_symlink() or not state_file.is_file():
            return None
        data = json.loads(state_file.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            status = data.get('status')
            return str(status) if status is not None else None
    except (OSError, ValueError):
        return None
    return None


def hidden_tests_line(root, task_id):
    """`tests cachés : <summary>` from the task's status.json, or `non joués` when there is none."""
    path = root / '.workspace/run/delegate' / task_id / 'status.json'
    summary = None
    try:
        if not path.is_symlink() and path.is_file():
            data = json.loads(path.read_text(encoding='utf-8'))
            summary = data.get('acceptance_summary') if isinstance(data, dict) else None
    except (OSError, ValueError):
        summary = None
    return 'tests cachés : ' + (summary if isinstance(summary, str) and summary else 'non joués')


def event_detail(entry):
    # A state event keeps its full state in the file; the listing shows the reason only.
    detail = entry.get('detail')
    if detail is None:
        return '-'
    if entry['event'] == 'state' and isinstance(detail, dict):
        detail = {'reason': detail.get('reason')}
    return json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def print_events(root, since, task_id, markdown):
    # Date, task, event, compact detail: one line each, or a table with --markdown.
    rows = [
        [entry['at'], entry['task_id'], entry['event'], event_detail(entry)] for entry in events.read(root, since=since, task_id=task_id)
    ]
    if markdown:
        print('| Date | Tâche | Événement | Détail |\n| --- | --- | --- | --- |')
    for row in rows:
        print('| ' + ' | '.join(cell.replace('|', '\\|') for cell in row) + ' |' if markdown else '  '.join(row))


def print_defects(root, since, markdown):
    # One row per ISO week with a defect, then the count for the current week as the last line.
    report = defects.weekly(root, since=since)
    rows = [[week['week'], week['events'], week['log'], week['total']] for week in report['weeks']]
    if markdown:
        print('| Semaine | Journal | LOG | Total |\n| --- | --- | --- | --- |')
    for row in rows:
        print('| ' + ' | '.join(str(cell) for cell in row) + ' |' if markdown else '  '.join(str(cell) for cell in row))
    print('Défauts trouvés après fusion cette semaine : %d' % report['this_week'])


def main():  # noqa: C901, PLR0915
    parser = argparse.ArgumentParser(description='Rapport des délégations')
    parser.add_argument('root', type=Path)
    parser.add_argument('--since', type=datetime.date.fromisoformat)
    parser.add_argument('--markdown', action='store_true')
    parser.add_argument('--events', action='store_true', help='liste le journal .workspace/events.jsonl')
    parser.add_argument('--defauts', action='store_true', help='défauts trouvés après fusion, par semaine')
    parser.add_argument('--rebuild', action='store_true', help='reconstruit les state.json depuis le journal ; code 1 au moindre écart')
    parser.add_argument('--adopt', action='store_true', help='avec --rebuild : verse au journal les vues sans événement')
    parser.add_argument('--task', help='seul : coût de la tâche ; avec --events : limite à une tâche')
    parser.add_argument('--mission', help='grille de jugement d\'une mission (.workspace/missions/<nom>/)')
    args = parser.parse_args()
    if args.mission is not None:
        return mission_report.run(args.root, args.mission)
    if args.adopt and not args.rebuild:
        parser.error('--adopt demande --rebuild')
    if args.rebuild:
        return state_view.rebuild(args.root, adopt=args.adopt)
    if args.task and not args.events:
        try:
            print(cost_line(args.root, args.task))
            # The placeholder for a task without any recorded call stays out of the report.
            for line in role_lines(args.root, args.task):
                if line != NO_ROLE_CALL:
                    print(line)
        except ValueError as error:
            parser.error(str(error))
        print(hidden_tests_line(args.root, args.task))
        print(reading.task_line(args.root, args.task))
        print('Interventions humaines : ' + note.intervention_summary(events.read(args.root, task_id=args.task)))
        for line in integration_logs.listing(args.root, args.task):
            print(line)
        return None
    if args.defauts:
        return print_defects(args.root, args.since, args.markdown)
    if args.events:
        return print_events(args.root, args.since, args.task, args.markdown)
    rows = []
    totals = {}
    first = 0
    for path in sorted((args.root / '.workspace/run/delegate').glob('*/status.json')):
        status = json.loads(path.read_text(encoding='utf-8'))
        if args.since and status.get('started_at', '')[:10] < args.since.isoformat():
            continue
        metrics = status.get('metrics') or {}
        agent = status.get('agent', 'null')
        attempts = metrics.get('attempts', status.get('attempts'))
        duration = metrics.get('duration_seconds')
        tokens = None if metrics.get('tokens_complete') is False else token_total(metrics.get('tokens'), agent)
        task_id = status.get('id') or path.parent.name
        final_state = final_task_status(args.root, task_id)
        rows.append([task_id, agent, status.get('isolation'), status.get('outcome'), final_state, attempts, duration, tokens])
        total = totals.setdefault(agent, [0, 0, 0.0, 0])
        total[0] += 1
        for index, value in enumerate((attempts, duration, tokens), 1):
            total[index] = total[index] + value if total[index] is not None and value is not None else None
        first += attempts == 1 and status.get('outcome') in ('done', 'done_with_warning')

    merged = sum(1 for row in rows if row[4] == 'merged')

    def table(headers, data, duration_column):
        def cell(value, index):
            if value is None:
                return 'null'
            text = '%.3f' % value if index == duration_column else str(value)
            return text.replace('|', '\\|').replace('\n', ' ')

        print(' | '.join(headers) if not args.markdown else '| ' + ' | '.join(headers) + ' |')
        if args.markdown:
            print('| ' + ' | '.join('---' for _ in headers) + ' |')
        for row in data:
            line = ' | '.join(cell(v, i) for i, v in enumerate(row))
            print('| ' + line + ' |' if args.markdown else line)

    table(['Tâche', 'Agent', 'Isolation', 'Résultat', 'État final', 'Tentatives', 'Durée (s)', 'Tokens'], rows, 6)
    print('\nTotaux par agent')
    table(['Agent', 'Tâches', 'Tentatives', 'Durée (s)', 'Tokens'], [[agent] + values for agent, values in sorted(totals.items())], 3)
    print('\nRéussite au premier essai : %.2f%% (%d/%d)' % (100 * first / len(rows) if rows else 0, first, len(rows)))
    print('Tâches fusionnées : %d/%d' % (merged, len(rows)))


if __name__ == '__main__':
    sys.exit(main())
