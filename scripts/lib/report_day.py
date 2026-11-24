"""Partie factuelle du rapport de jour, tirée des journaux du circuit (Python 3.14, standard library uniquement)."""

import datetime
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote

import defects
import events
import factory_cost
import note
import task_cost

sys.dont_write_bytecode = True

BLOCK_BEGIN_PREFIX = '<!-- généré: début'
BLOCK_BEGIN = BLOCK_BEGIN_PREFIX + ' -->'
BLOCK_END = '<!-- généré: fin -->'
HEADER_END = ' -->'
HEADER_FIELDS = ('day', 'usine', 'until', 'claude-dir')
UNTIL_FORMAT = '%Y-%m-%dT%H:%M:%SZ'
BLOCK_BEGIN_LINE = re.compile('^' + re.escape(BLOCK_BEGIN_PREFIX), re.MULTILINE)
TAIL = '\n## Décisions\n\n## Constats\n'
DAY_FORMAT = re.compile(r'\d{4}-\d{2}-\d{2}')
TASK_FORMAT = re.compile(r'[a-z0-9-]+')
ORCHESTRATOR_TASK = factory_cost.ORCHESTRATOR_TASK
FICHE_KINDS = ('prepared', 'delegate_started', 'merged', 'closed')
ACCEPTANCE_DIR = ('.workspace', 'acceptance')
TEST_PREFIX = '    def test_'
SECURITY_DIR = ('docs', 'security')
JOURNAL_DIR = ('docs', 'journal')
COMMIT_LENGTH = 7
NONE_CELL = '-'
UNKNOWN_CELL = '?'
ABANDONED = 'abandoned'
FICHE_HEADER = [
    'Fiche',
    'Agent',
    'Tentatives',
    'Tests cachés',
    'Fusion',
    'Durée (s)',
    'Hors cache',
    'Cache lu',
    'Cache créé',
    'Sortie',
    'Issue',
    'Cause',
]
ROLE_HEADER = ['Rôle', 'Fournisseur', 'Appels', 'Hors cache', 'Cache lu', 'Cache créé', 'Sortie']
SIDE_ROLE = 'sous-agents et appels directs'
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 64
USAGE = 'usage : report_day.py <racine> --day AAAA-MM-JJ [--usine <nom>] [--out <fichier>] [--claude-dir <dossier>]'
OPTIONS = ('--day', '--usine', '--out', '--claude-dir')


def _detail(entry):
    detail = entry.get('detail')
    return detail if isinstance(detail, dict) else {}


def _moment(value):
    if not isinstance(value, str):
        return None
    try:
        return events._instant(value)
    except (ValueError, TypeError):
        return None


def _cell(value):
    text = ' '.join(str(value).split()).replace('|', '/')
    return text or NONE_CELL


def _row(values):
    return '| ' + ' | '.join(str(value) for value in values) + ' |'


def _day_of(value):
    moment = _moment(value)
    return moment.astimezone(datetime.UTC).date().isoformat() if moment is not None else None


def _after(value, until):
    moment = _moment(value)
    return moment is not None and moment > until


def _rows_until(root, until, task_id=None):
    return [row for row in task_cost._consumption_rows(Path(root), task_id) if not _after(row.get('at'), until)]


def _now():
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0)


def block_header(day, usine=None, until=None, claude_dir=None) -> str:
    """Ligne d'en-tête du bloc : les arguments de sa régénération."""
    until = until or _now()
    fields = ['day=' + day]
    if usine is not None:
        fields.append('usine=' + usine)
    fields.append('until=' + until.astimezone(datetime.UTC).strftime(UNTIL_FORMAT))
    if claude_dir is not None:
        fields.append('claude-dir=' + quote(str(claude_dir), safe='/'))
    return ' '.join([BLOCK_BEGIN_PREFIX] + fields) + HEADER_END


def parse_header(line) -> dict:
    """Inverse de `block_header` ; ValueError si la ligne n'est pas un en-tête exact."""
    start = BLOCK_BEGIN_PREFIX + ' '
    if not line.startswith(start) or not line.endswith(HEADER_END) or len(line) < len(start) + len(HEADER_END):
        raise ValueError('en-tête invalide : ' + repr(line))
    found, last = {}, -1
    for token in line[len(start) : -len(HEADER_END)].split(' '):
        name, separator, value = token.partition('=')
        if not separator or name not in HEADER_FIELDS or HEADER_FIELDS.index(name) <= last:
            raise ValueError("champ invalide ou hors d'ordre : " + repr(token))
        last = HEADER_FIELDS.index(name)
        found[name] = value
    if 'day' not in found or 'until' not in found or not _valid_day(found['day']):
        raise ValueError('jour ou instant manquant ou invalide')
    usine = found.get('usine')
    if usine is not None and not factory_cost.FACTORY_NAME.fullmatch(usine):
        raise ValueError("nom d'usine invalide : " + repr(usine))
    until = datetime.datetime.strptime(found['until'], UNTIL_FORMAT).replace(tzinfo=datetime.UTC)
    claude_dir = found.get('claude-dir')
    if claude_dir is not None:
        claude_dir = unquote(claude_dir)
        if quote(claude_dir, safe='/') != found['claude-dir']:
            raise ValueError('chemin mal encodé')
    return {'day': found['day'], 'usine': usine, 'until': until, 'claude_dir': claude_dir}


def select(root, day, usine=None, until=None) -> list[dict]:
    """Événements du rapport : ceux de l'usine, ou ceux dont la date UTC est `day` ; rien après `until`."""
    if not _valid_day(day):
        raise ValueError('date invalide : ' + repr(day))
    rows = [entry for entry in events.read(root) if until is None or not _after(entry.get('at'), until)]
    if usine is not None:
        return [entry for entry in rows if entry.get('orchestrator') == usine]
    return [entry for entry in rows if _day_of(entry.get('at')) == day]


def _fiche_ids(selection):
    found = []
    for entry in selection:
        task = entry.get('task_id')
        if task != ORCHESTRATOR_TASK and isinstance(task, str) and entry.get('event') in FICHE_KINDS and task not in found:
            found.append(task)
    return found


def _hidden_tests(root, task_id):
    if not TASK_FORMAT.fullmatch(task_id):
        return NONE_CELL
    folder = Path(root).joinpath(*ACCEPTANCE_DIR, task_id, 'tests')
    files = (
        [path for path in sorted(folder.glob('*.py')) if path.is_file() and not path.is_symlink()]
        if folder.is_dir() and not folder.is_symlink()
        else []
    )
    if not files:
        return NONE_CELL
    count = 0
    for path in files:
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except (OSError, ValueError):
            continue
        count += sum(1 for line in lines if line.startswith(TEST_PREFIX))
    return str(count)


def _duration(rows):
    total = 0.0
    found = False
    for index, entry in enumerate(rows):
        if entry.get('event') != 'delegate_started':
            continue
        start = _moment(entry.get('at'))
        for later in rows[index + 1 :]:
            if later.get('event') == 'outcome':
                end = _moment(later.get('at'))
                if start is not None and end is not None:
                    total += (end - start).total_seconds()
                    found = True
                break
    return str(round(total)) if found else NONE_CELL


def _sum(values):
    total = 0
    for value in values:
        if value is None:
            return UNKNOWN_CELL
        total += value
    return str(total)


def _task_costs(root, task_id, until):
    totals = task_cost.role_totals(_rows_until(root, until, task_id)).values()
    if not totals:
        return [NONE_CELL] * 4
    created = [value['cache_creation'] for value in totals if value['cache_creation'] != NONE_CELL]
    return [
        _sum(value['uncached'] for value in totals),
        _sum(value['cache_read'] for value in totals),
        _sum(created) if created else NONE_CELL,
        _sum(value['output'] for value in totals),
    ]


def _issue(rows):
    outcomes = [entry for entry in rows if entry.get('event') == 'outcome']
    abandoned = [entry for entry in rows if entry.get('event') == 'closed' and _detail(entry).get('outcome') == ABANDONED]
    if any(entry.get('event') == 'merged' for entry in rows):
        issue = 'merged'
    elif abandoned:
        issue = ABANDONED
    else:
        issue = _detail(outcomes[-1]).get('outcome') if outcomes else None
    source = abandoned[-1] if abandoned else outcomes[-1] if outcomes else None
    cause = _detail(source).get('cause') if source is not None else None
    return _cell(issue) if issue else NONE_CELL, _cell(cause) if cause else NONE_CELL


def _fiche_row(root, task_id, selection, until):
    rows = [entry for entry in selection if entry.get('task_id') == task_id]
    starts = [entry for entry in rows if entry.get('event') == 'delegate_started']
    merges = [entry for entry in rows if entry.get('event') == 'merged']
    agent = _detail(starts[-1]).get('agent') if starts else None
    commit = _detail(merges[-1]).get('commit') if merges else None
    attempts = sum(1 for entry in rows if entry.get('event') == 'attempt_started')
    issue, cause = _issue(rows)
    return _row(
        [
            _cell(task_id),
            _cell(agent) if agent else NONE_CELL,
            attempts,
            _hidden_tests(root, task_id),
            _cell(str(commit)[:COMMIT_LENGTH]) if commit else NONE_CELL,
            _duration(rows),
        ]
        + _task_costs(root, task_id, until)
        + [issue, cause]
    )


def _fiches_section(root, selection, until):
    ids = _fiche_ids(selection)
    if not ids:
        return ['## Fiches', '', 'Aucune fiche.']
    lines = ['## Fiches', '', _row(FICHE_HEADER), _row(['---'] * len(FICHE_HEADER))]
    return lines + [_fiche_row(root, task_id, selection, until) for task_id in ids]


def _day_rows(root, day, usine, until):
    rows = _rows_until(root, until)
    if usine is None:
        return [row for row in rows if _day_of(row.get('at')) == day]
    spans = []
    for window in factory_cost.windows(root, usine):
        start, end = _moment(window['start']), _moment(window['end']) if window['end'] else until
        if start is not None and end is not None:
            spans.append((start, min(end, until)))
    kept = []
    for row in rows:
        moment = _moment(row.get('at'))
        if moment is not None and any(start <= moment <= end for start, end in spans):
            kept.append(row)
    return kept


def _cost_section(root, day, usine, claude_dir, until):
    totals = task_cost.role_totals(_day_rows(root, day, usine, until))
    lines = ['## Coût par rôle', '', _row(ROLE_HEADER), _row(['---'] * len(ROLE_HEADER))]
    calls = 0
    columns = [[], [], [], []]
    for (role, provider), value in sorted(totals.items()):
        sums = [value['uncached'], value['cache_read'], value['cache_creation'], value['output']]
        lines.append(_row([_cell(role), _cell(provider), value['calls']] + [UNKNOWN_CELL if item is None else item for item in sums]))
        calls += value['calls']
        for column, item in zip(columns, sums, strict=False):
            if item != NONE_CELL:
                column.append(item)
    if usine is None:
        lines.append(_row(['orchestrateur', 'claude', 'non mesuré'] + [NONE_CELL] * 4))
        lines.append(_row([SIDE_ROLE, 'claude', 'non mesuré'] + [NONE_CELL] * 4))
    else:
        cost = factory_cost.collect(root, usine, claude_dir, now=until)
        for role, key in (('orchestrateur', 'total'), (SIDE_ROLE, 'side_total')):
            total = cost[key]
            mine = [total[name] for name in factory_cost.COUNTERS]
            lines.append(_row([role, 'claude', total['messages']] + mine))
            for column, item in zip(columns, mine, strict=False):
                column.append(item)
    lines.append('| Total | | %s |' % ' | '.join([str(calls)] + [_sum(column) for column in columns]))
    return lines


def _questions_section(selection):
    answered = [entry for entry in selection if entry.get('event') == 'question_answered']
    lines = ['## Questions et interventions', '', '- Questions répondues : %d' % len(answered)]
    for entry in answered:
        detail = _detail(entry)
        lines.append('  - %s/%s par %s' % (_cell(entry.get('task_id')), _cell(detail.get('question')), _cell(detail.get('by'))))
    lines.append('- Interventions humaines : ' + note.intervention_summary(selection))
    lines.append('- Sous-agents hors circuit : %d' % sum(1 for entry in selection if entry.get('event') == 'subagent_started'))
    return lines


def _bench_section(root, day, until):
    folder = Path(root).joinpath(*SECURITY_DIR)
    names = (
        sorted(
            path.name
            for path in folder.glob('attack-bench-%s*.md' % day)
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime <= until.timestamp()
        )
        if folder.is_dir()
        else []
    )
    lines = ['## Banc d\'attaque', '']
    if not names:
        return lines + ['- aucun banc d\'attaque daté du ' + day]
    return lines + ['- docs/security/' + name for name in names]


def render(root, day, usine=None, claude_dir=None, until=None) -> str:
    """Bloc généré complet, en-tête et marqueur de fin compris, arrêté à `until`."""
    # L'instant `until` est passé explicitement à chaque lecture : rien n'est remplacé dans `events`.
    until = until or _now()
    selection = select(root, day, usine, until)
    title = '# Rapport — %s — usine `%s`' % (day, usine) if usine is not None else '# Rapport — ' + day
    weekly = defects.weekly(root, today=datetime.date.fromisoformat(day), until=until)['this_week']
    sections = [
        _fiches_section(root, selection, until),
        _cost_section(root, day, usine, claude_dir, until),
        _questions_section(selection),
        _bench_section(root, day, until),
    ]
    lines = [block_header(day, usine, until, claude_dir), 'Défauts trouvés après fusion cette semaine : %d' % weekly, '', title, '']
    for section in sections:
        lines += section + ['']
    return '\n'.join(lines + [BLOCK_END]) + '\n'


def write(path, block) -> None:
    """Écrit le bloc : fichier neuf, ou remplacement du seul passage entre marqueurs."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('lien symbolique refusé : %s' % path)
    if not path.exists():
        path.write_bytes((block + TAIL).encode('utf-8'))
        return
    text = path.read_bytes().decode('utf-8')
    begins = list(BLOCK_BEGIN_LINE.finditer(text))
    last = text.find(BLOCK_END)
    if len(begins) != 1 or text.count(BLOCK_END) != 1 or last < begins[0].start():
        raise ValueError('marqueurs absents ou dépareillés : %s' % path)
    end = last + len(BLOCK_END)
    path.write_bytes((text[: begins[0].start()] + block.rstrip('\n') + text[end:]).encode('utf-8'))


def _parse(argv):
    values = dict.fromkeys(OPTIONS)
    positional = []
    index = 0
    while index < len(argv):
        if argv[index] in values and index + 1 < len(argv):
            values[argv[index]] = argv[index + 1]
            index += 2
        elif argv[index].startswith('--'):
            return None
        else:
            positional.append(argv[index])
            index += 1
    if len(positional) != 1 or values['--day'] is None:
        return None
    return Path(positional[0]), values


def _valid_day(day):
    if not DAY_FORMAT.fullmatch(day):
        return False
    try:
        datetime.date.fromisoformat(day)
    except ValueError:
        return False
    return True


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parsed = _parse(argv)
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return EXIT_USAGE
    root, values = parsed
    day, usine = values['--day'], values['--usine']
    if not _valid_day(day):
        print('report_day: date invalide : ' + day, file=sys.stderr)
        return EXIT_USAGE
    if usine is not None and not factory_cost.FACTORY_NAME.fullmatch(usine):
        print("report_day: nom d'usine invalide (caractères admis : A-Z a-z 0-9 . _ -, 1 à 64)", file=sys.stderr)
        return EXIT_USAGE
    if values['--out'] is not None:
        out = Path(values['--out'])
    else:
        out = root.joinpath(*JOURNAL_DIR, day, 'RAPPORT-JOUR-%s%s.md' % (day, '-' + usine if usine else ''))
    block = render(root, day, usine, values['--claude-dir'])
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        write(out, block)
    except ValueError as error:
        print('report_day: %s' % error, file=sys.stderr)
        return EXIT_ERROR
    print(out)
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
