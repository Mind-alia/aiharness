"""Rapport de la semaine : défauts après fusion, coût par fournisseur, bancs, travaux sortis de la table (Python 3.14).

`weekly-report.sh [--date AAAA-MM-JJ] [--no-check]` écrit `docs/RAPPORTS/semaine-<date>.md` pour la semaine ISO qui
contient la date (lundi 00:00 UTC inclus, lundi suivant exclu), puis le fait vérifier par `report-check.sh`.
Codes : 0 rapport écrit et vérifié ; 1 rapport écrit mais vérification en échec ; 64 usage ou date invalide (rien écrit).
Défauts et coûts viennent de `defects` et `consumption` ; ce module ne recopie pas leur logique.
"""

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import consumption
import defects
import safety

UTC = datetime.UTC
ONE_WEEK = datetime.timedelta(days=7)
USAGE_ERROR = 64
CHECK_ERROR = 1
REPORT_DIR = 'docs/RAPPORTS'
CONSUMPTION_PATH = '.workspace/metrics/consumption.jsonl'
BENCH_DIR = 'docs/security'
BENCH_GLOB = 'attack-bench-*.md'
BENCH_DATE = re.compile(r'^attack-bench-(\d{4}-\d{2}-\d{2})')
VERIFIER_PATH = 'docs/VERIFICATEUR-taux.md'
VERIFIER_DATE = re.compile(r'^- Date : (\d{4}-\d{2}-\d{2})\s*$', re.MULTILINE)
VERIFIER_PREFIXES = ('faux rejets', 'fausses acceptations')
TASKS_PATH = 'docs/TRAVAUX.md'
TASK_ROW = re.compile(r'^\|\s*(T\d+)\s*\|\s*([^|]*?)\s*\|')
CHECK_SCRIPT = 'scripts/report-check.sh'
COLUMNS = ('Fournisseur', 'Appels', 'Entrée', 'Cache lu', 'Cache créé', 'Sortie', 'Total')
TOKEN_KEYS = ('input', 'cached_input', 'cache_creation', 'output')


def _read(root, relative):
    """Texte d'un fichier du projet ; absent, lien symbolique ou illisible : chaîne vide."""
    path = Path(root) / relative
    try:
        safety.check_no_symlinks(path, Path(root))
        return path.read_text(encoding='utf-8') if path.is_file() else ''
    except (OSError, ValueError):
        return ''


def _instant(value):
    """Instant UTC d'une date ISO 8601 (`Z` accepté, naïf = UTC) ; `ValueError` si la valeur n'en est pas une."""
    if not isinstance(value, str):
        raise ValueError('date absente')
    text = value[:-1] + '+00:00' if value.endswith('Z') else value
    instant = datetime.datetime.fromisoformat(text)
    return instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant


def _day(text):
    """Date `AAAA-MM-JJ`, `None` si elle n'est pas valide."""
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def _in_week(day, monday):
    return day is not None and monday <= day < monday + ONE_WEEK


def _count(value):
    """Champ de jetons entier ; absent ou non entier : 0."""
    return value if type(value) is int else 0


def consumption_totals(root, start, end):
    """Appels et jetons par fournisseur (ordre alphabétique) des événements dont `at` est dans [start, end[."""
    totals = {}
    for line in _read(root, CONSUMPTION_PATH).splitlines():
        try:
            event = json.loads(line)
            provider = event['provider']
            instant = _instant(event['at'])
        except (ValueError, TypeError, KeyError, RecursionError):
            continue
        if not isinstance(provider, str) or not provider or not start <= instant < end:
            continue
        tokens = event.get('tokens') if isinstance(event.get('tokens'), dict) else {}
        entry = totals.setdefault(provider, dict.fromkeys(('calls', 'total') + TOKEN_KEYS, 0))
        entry['calls'] += 1
        for key in TOKEN_KEYS:
            entry[key] += _count(tokens.get(key))
        entry['total'] += consumption.token_total(tokens, provider) or 0
    return sorted(totals.items())


def consumption_table(totals):
    lines = ['| ' + ' | '.join(COLUMNS) + ' |', '|' + '---|' * len(COLUMNS)]
    for provider, entry in totals:
        cells = [entry['calls']] + [entry[key] for key in TOKEN_KEYS] + [entry['total']]
        lines.append('| ' + ' | '.join([provider] + [str(cell) for cell in cells]) + ' |')
    return lines


def bench_lines(root, monday):
    """Bancs d'attaque et étalons datés dans la semaine ; `Aucun.` si rien n'est trouvé."""
    lines = []
    for path in sorted((Path(root) / BENCH_DIR).glob(BENCH_GLOB)):
        match = BENCH_DATE.match(path.name)
        if match and not path.is_symlink() and _in_week(_day(match.group(1)), monday):
            lines.append('- banc d’attaque : ' + path.name)
    text = _read(root, VERIFIER_PATH)
    if any(_in_week(_day(found), monday) for found in VERIFIER_DATE.findall(text)):
        lines += ['- étalons : ' + line for line in text.splitlines() if line.startswith(VERIFIER_PREFIXES)]
    return lines or ['Aucun.']


def _git(root, args):
    """Sortie de `git -C <racine> <args>` ; `None` si la commande échoue ou si git manque."""
    try:
        done = subprocess.run(['git', '-C', str(root)] + args, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.decode('utf-8', errors='replace') if done.returncode == 0 else None


def task_titles(text):
    """Titres `{identifiant: titre}` des lignes `| T<n> | <titre> |` d'un TRAVAUX.md."""
    titles = {}
    for line in text.splitlines():
        match = TASK_ROW.match(line)
        if match:
            titles[match.group(1)] = match.group(2)
    return titles


def _task_number(ident):
    return int(ident[1:])


def travaux_lines(root, monday):
    """Travaux présents au dernier commit d'avant la semaine et absents du fichier actuel."""
    before = datetime.datetime(monday.year, monday.month, monday.day, tzinfo=UTC).isoformat()
    commit = (_git(root, ['rev-list', '-1', '--before=' + before, 'HEAD']) or '').strip()
    old = _git(root, ['show', commit + ':' + TASKS_PATH]) if commit else None
    if old is None:
        return ['Historique insuffisant.']
    current = task_titles(_read(root, TASKS_PATH))
    gone = task_titles(old)
    lines = ['- %s · %s' % (ident, gone[ident]) for ident in sorted(gone, key=_task_number) if ident not in current]
    return lines or ['Aucun.']


def render(root, day):
    monday = day - datetime.timedelta(days=day.weekday())
    sunday = monday + datetime.timedelta(days=6)
    start = datetime.datetime(monday.year, monday.month, monday.day, tzinfo=UTC)
    year, week, _ = day.isocalendar()
    sections = (
        ('Défauts après fusion', ['Défauts trouvés après fusion cette semaine : %d' % defects.weekly(root, today=day)['this_week']]),
        ('Coût par fournisseur', consumption_table(consumption_totals(root, start, start + ONE_WEEK))),
        ('Bancs et étalons', bench_lines(root, monday)),
        ('Travaux sortis de la table', travaux_lines(root, monday)),
    )
    lines = ['# Rapport de la semaine %04d-W%02d' % (year, week), '', 'Du %s au %s (UTC).' % (monday.isoformat(), sunday.isoformat())]
    for title, body in sections:
        lines += ['', '## ' + title, ''] + body
    return '\n'.join(lines) + '\n'


def write_report(root, day, text):
    """Écrit ou remplace le rapport de la date, dossier créé au besoin ; renvoie son chemin."""
    path = Path(root) / REPORT_DIR / ('semaine-%s.md' % day.isoformat())
    safety.check_no_symlinks(path, Path(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    safety.write_file_secure(path, text)
    return path


def check(root, path):
    """Lance `report-check.sh` sur le rapport ; `True` si la vérification passe."""
    done = subprocess.run(['bash', str(Path(root) / CHECK_SCRIPT), str(path)], capture_output=True, encoding='utf-8', errors='replace')
    if done.returncode == 0:
        return True
    sys.stderr.write(done.stdout + done.stderr)
    print('weekly-report: report-check.sh en échec (code %d), rapport conservé' % done.returncode, file=sys.stderr)
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--date')
    parser.add_argument('--no-check', action='store_true')
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return USAGE_ERROR if stop.code else 0
    day = _day(args.date) if args.date is not None else datetime.datetime.now(UTC).date()
    if day is None:
        print('weekly-report: date invalide : %s (attendu AAAA-MM-JJ)' % args.date, file=sys.stderr)
        return USAGE_ERROR
    root = Path(args.root).absolute()
    try:
        report = write_report(root, day, render(root, day))
    except (OSError, ValueError) as error:
        print('weekly-report: écriture impossible : %s' % error, file=sys.stderr)
        return CHECK_ERROR
    print('Rapport écrit : %s/semaine-%s.md' % (REPORT_DIR, day.isoformat()), flush=True)
    if args.no_check or check(root, report):
        return 0
    return CHECK_ERROR


if __name__ == '__main__':
    sys.exit(main())
