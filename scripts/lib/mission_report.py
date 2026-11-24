"""Grille de jugement d'une mission : mesures tirées des journaux, jugement humain laissé vide.

Python 3.14, bibliothèque standard seule. Appelé par report.py (option --mission).
"""

import datetime
import json
import math
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import consumption
import note
import safety

HUMAN = 'À remplir (jugement humain)'
NAME = re.compile(r'[a-z0-9-]+')
TASK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
CRITERIA = (
    'Règles · périmètre',
    'Règles · secrets',
    'Règles · tests présents',
    'Règles · seuils',
    'Qualité du cadrage',
    'Qualité du livrable',
    'Interventions humaines · nombre',
    'Interventions humaines · nature',
    'Coût · tokens par rôle',
    'Coût · euros (tarif API)',
    'Délai',
)


def run(root, name):
    """Imprime la grille de la mission `name`, puis l'écrit dans son dossier ; code de sortie."""
    root = Path(root).absolute()
    try:
        mission_dir, repo, adopted = _mission(root, name)
    except ValueError as error:
        print('mission : ' + str(error), file=sys.stderr)
        return 2
    text = _grid(root, name, repo, adopted)
    sys.stdout.write(text)
    return _write(mission_dir, text)


def _mission(root, name):
    """Dossier de la mission, dépôt adopté (sous le dossier) et instant d'adoption ; `ValueError` sinon."""
    if not NAME.fullmatch(name):
        raise ValueError('nom de mission invalide : %s' % name)
    mission_dir = root / '.workspace/missions' / name
    mission = _read_json(mission_dir / 'mission.json', root)
    adopted = _instant(mission.get('adopted_at')) if isinstance(mission, dict) else None
    if adopted is None:
        raise ValueError('mission.json absent, illisible ou sans adopted_at : %s' % name)
    repo_name = mission.get('repo', 'repo')
    if not isinstance(repo_name, str) or not repo_name:
        raise ValueError('repo invalide dans mission.json : %r' % (repo_name,))
    repo = (mission_dir / repo_name).resolve()
    try:
        repo.relative_to(mission_dir.resolve())
    except ValueError:
        raise ValueError('dépôt hors du dossier de mission : %s' % repo_name)
    return mission_dir, repo, adopted


def _grid(root, name, repo, adopted):
    """Texte Markdown exact de la grille ; les lignes de jugement restent à remplir."""
    delegations = _delegations(repo)
    statuses = [status for _, status in delegations]
    total = len(delegations)
    consumption_rows = _jsonl(repo / '.workspace/metrics/consumption.jsonl', repo)
    journal = _jsonl(repo / '.workspace/events.jsonl', repo)
    measures = {
        'Règles · périmètre': '%d hors périmètre sur %d délégations'
        % (sum(1 for status in statuses if status.get('outcome') == 'out_of_scope'), total),
        'Règles · secrets': '%d refus pour secret sur %d délégations' % (sum(1 for status in statuses if _secret_refusal(status)), total),
        'Règles · tests présents': '%d/%d tâches avec tests cachés'
        % (sum(1 for task_id, _ in delegations if _has_tests(repo, task_id)), total),
        'Interventions humaines · nombre': "%d questions d'agent ; %s"
        % (sum(_questions(s) for s in statuses), note.intervention_summary(journal)),
        'Coût · tokens par rôle': _tokens_by_role(consumption_rows),
        'Coût · euros (tarif API)': _euros(root, consumption_rows),
        'Délai': _delay(journal, adopted),
    }
    lines = ['# Grille de mission — %s' % name, '', '| Critère | Mesure | Jugement |', '| --- | --- | --- |']
    for criterion in CRITERIA:
        if criterion in measures:
            measure, judgment = _cell(measures[criterion]), '—'
        else:
            measure, judgment = '—', HUMAN
        lines.append('| %s | %s | %s |' % (criterion, measure, judgment))
    return '\n'.join(lines) + '\n'


def _write(mission_dir, text):
    """Écrit grille.md une seule fois ; une grille existante n'est jamais réécrite. Code de sortie."""
    path = mission_dir / 'grille.md'
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except FileExistsError:
        print('grille.md existe déjà : non réécrit', file=sys.stderr)
        return 0
    except OSError as error:
        print('grille.md non écrit : %s' % error, file=sys.stderr)
        return 1
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)
    return 0


def _read_json(path, root):
    """Contenu JSON d'un fichier sous root ; `None` s'il manque, est illisible ou passe par un lien symbolique."""
    try:
        safety.check_no_symlinks(path, root)
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _jsonl(path, root):
    """Objets d'un journal JSONL ; fichier absent = vide, ligne illisible ignorée."""
    try:
        safety.check_no_symlinks(path, root)
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except (OSError, ValueError):
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _instant(value):
    """Instant UTC d'une chaîne ISO 8601 (`Z` admis) ; `None` si ce n'en est pas une. Sans fuseau = UTC."""
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
    except ValueError:
        return None
    return instant.replace(tzinfo=datetime.UTC) if instant.tzinfo is None else instant


def _delegations(repo):
    """(identifiant, statut) de chaque délégation ; l'identifiant est celui du statut, sinon le nom du dossier."""
    found = []
    for path in sorted((repo / '.workspace/run/delegate').glob('*/status.json')):
        status = _read_json(path, repo)
        if isinstance(status, dict):
            found.append((status.get('id') or path.parent.name, status))
    return found


def _has_tests(repo, task_id):
    """Vrai si acceptance/<id>/ contient au moins un fichier, à n'importe quelle profondeur."""
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        return False
    directory = repo / '.workspace/acceptance' / task_id
    try:
        safety.check_no_symlinks(directory, repo)
    except ValueError:
        return False
    return any(files for _, _, files in os.walk(str(directory)))


def _secret_refusal(status):
    outcome = status.get('outcome')
    return isinstance(outcome, str) and 'secret' in outcome


def _questions(status):
    metrics = status.get('metrics')
    value = metrics.get('questions') if isinstance(metrics, dict) else None
    return value if type(value) is int else 0


def _tokens_by_role(rows):
    """Volume de tokens par rôle, ordre alphabétique ; un rôle dont une ligne est incomplète est signalé."""
    totals = {}
    for row in rows:
        role = row.get('role')
        if not isinstance(role, str):
            continue
        value = consumption.token_total(row.get('tokens'), row.get('provider'))
        total = totals.setdefault(role, 0)
        totals[role] = None if value is None or total is None else total + value
    if not totals:
        return 'aucune consommation relevée'
    return ' ; '.join('%s %d' % (role, total) if total is not None else '%s incomplet' % role for role, total in sorted(totals.items()))


def _euros(root, rows):
    """Coût au tarif API du kit ; `inconnu` plutôt qu'un prix inventé."""
    path = root / 'docs/TARIFS-API.json'
    if not path.exists() and not path.is_symlink():
        return 'inconnu (docs/TARIFS-API.json absent)'
    tariffs = _read_json(path, root)
    prices = tariffs.get('eur_per_million') if isinstance(tariffs, dict) else None
    date = tariffs.get('date') if isinstance(tariffs, dict) else None
    if not isinstance(prices, dict) or not isinstance(date, str):
        return 'inconnu (docs/TARIFS-API.json illisible)'
    keys = ['%s/%s' % (row.get('provider'), row.get('model')) for row in rows]
    missing = sorted(set(key for key in keys if not isinstance(prices.get(key), dict)))
    if missing:
        return 'inconnu (tarif absent : %s)' % ', '.join(missing)
    total = 0.0
    for row, key in zip(rows, keys, strict=False):
        tokens = row.get('tokens') if isinstance(row.get('tokens'), dict) else {}
        for token_key, price in prices[key].items():
            value = tokens.get(token_key)
            value = 0 if value is None else value
            if not _number(value) or not _number(price):
                return 'inconnu (valeur illisible : %s)' % key
            total += value * price / 1000000
    return '%.2f € (tarifs du %s)' % (total, date)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _delay(journal, adopted):
    """Temps écoulé entre l'adoption et l'événement le plus tardif du journal de la mission."""
    instants = [_instant(row.get('at')) for row in journal]
    instants = [instant for instant in instants if instant is not None]
    if not instants:
        return 'inconnu (aucun événement)'
    last = max(instants)
    if last < adopted:
        return "inconnu (événement antérieur à l'adoption)"
    minutes = int((last - adopted).total_seconds() // 60)
    return '%d h %02d min' % (minutes // 60, minutes % 60)


def _cell(text):
    return text.replace('|', '\\|')
