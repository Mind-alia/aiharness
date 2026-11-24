"""Plan de l'orchestrateur programme (fiche T14, fiche 2, règle 1) : lecture et validation, rien n'est écrit.

Une ligne par fiche : `- fiche: <chemin>`, ou `- demande: <chemin> | id: <id>` pour une demande que le planificateur
transforme en proposition (T14 B4). Après un `|`, une ligne peut dire comment la fiche est répartie : `vague: N`
(les fiches d'une même vague sont déléguées en parallèle, trois au plus, périmètres disjoints), `exécutant: claude-haiku`
(remplace celui de la fiche), `relecture: opus` (relecture ciblée avant le « prêt à fusionner », T14 B5).
Les décisions du propriétaire s'écrivent dans le plan pendant ou entre deux passages : `- fusion validée: <id>` (T14 B0) et
`- proposition acceptée: <id>` (T14 B4) ; elles n'entrent pas dans l'empreinte du plan, qui reste donc le même.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import journal

FIELDS = (
    'session',
    'by',
    'model',
    'tasks',
    'verify',
    'resync',
    'image',
    'report',
    'report_check',
    'commit_report',
    'diagnostic',
    'correcteur',
)
# Décisions du propriétaire (plan JSON : listes d'identifiants), hors empreinte.
DECISIONS = {'merge_validated': 'fusion validée', 'accepted': 'proposition acceptée'}
SCALARS = tuple(name for name in FIELDS if name != 'tasks')
BOOLEANS = ('resync', 'report_check', 'commit_report', 'diagnostic')
SESSION = re.compile(r'[A-Za-z0-9._-]{1,64}')
IDENTIFIER = re.compile(r'[a-z0-9-]+')
FICHE_LINE = re.compile(r'^\s*[-*]\s+(fiche|demande):\s*(.*?)\s*$')
DECISION_LINE = re.compile(r'^\s*[-*]\s+(fusion valid[ée]e|proposition accept[ée]e):\s*`?([a-z0-9-]+)`?\s*$', re.IGNORECASE)
ATTRIBUTES = {'vague': 'wave', 'exécutant': 'executor', 'executant': 'executor', 'relecture': 'review', 'id': 'id'}
EXECUTORS = ('claude-haiku', 'claude-sonnet', 'claude-opus')
REVIEWERS = ('opus',)
WAVE_SIZE = 3
FIELD_LINE = re.compile(r'^(' + '|'.join(SCALARS) + r'):[ \t]*(.*?)\s*$')
TRUE_WORDS = ('oui', 'true')
FALSE_WORDS = ('non', 'false')
DEFAULT_IMAGE = 'workspace-python-git:3.14'
DEFAULT_REPORT = '.workspace/orchestrator-run/RAPPORT-{}.md'
DEFAULT_CORRECTOR = 'claude-haiku'


class PlanError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print('run.py : ' + message, file=sys.stderr)
        raise SystemExit(64)


def parse_args(argv):
    """Arguments de `run.py` ; usage invalide : code 64."""
    parser = Parser(prog='run.py', description="Exécute un plan de fiches ; relancé, reprend à l'étape courante.")
    parser.add_argument('plan')
    parser.add_argument('--root', default=None)
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--status', action='store_true')
    for name in ('session', 'by', 'model'):
        parser.add_argument('--' + name)
    args = parser.parse_args(argv)
    if args.status and args.detach:
        parser.error('--status et --detach ne vont pas ensemble')
    return args


def load(root, path, overrides=None):
    """Plan validé : dict décrit par la règle 1. PlanError au moindre doute."""
    target = _safe(root, path, 'plan')
    if not target.is_file():
        raise PlanError(f'plan absent : {path}')
    text = target.read_bytes().decode('utf-8', 'replace')
    values, fiches = _json(text) if target.suffix == '.json' else _markdown(text)
    values = dict(values)
    for key, value in (overrides or {}).items():
        if value is not None:
            values[key] = value
    plan = _build(root, target, hashlib.sha256(_without_decisions(text, target).encode('utf-8')).hexdigest(), values, fiches)
    plan.update(_decisions(text, target))
    return plan


def decisions(root, path):
    """Décisions du propriétaire lues à l'instant dans le plan : {'merge_validated': set, 'accepted': set}."""
    target = _safe(root, path, 'plan')
    try:
        return _decisions(target.read_bytes().decode('utf-8', 'replace'), target)
    except OSError:
        return {key: set() for key in DECISIONS}


def _decisions(text, target):
    found = {key: set() for key in DECISIONS}
    if target.suffix == '.json':
        try:
            data = json.loads(text)
        except ValueError:
            return found
        for key in DECISIONS:
            items = data.get(key) if isinstance(data, dict) else None
            found[key] = {item for item in items if isinstance(item, str)} if isinstance(items, list) else set()
        return found
    for line in text.splitlines():
        match = DECISION_LINE.match(line)
        if match:
            kind = 'merge_validated' if match.group(1).lower().startswith('fusion') else 'accepted'
            found[kind].add(match.group(2).lower())
    return found


def _without_decisions(text, target):
    """Texte du plan sans les décisions du propriétaire : les ajouter ne change pas l'empreinte."""
    if target.suffix == '.json':
        try:
            data = json.loads(text)
        except ValueError:
            return text
        if isinstance(data, dict):
            return json.dumps({key: value for key, value in data.items() if key not in DECISIONS}, sort_keys=True, ensure_ascii=False)
        return text
    return '\n'.join(line for line in text.splitlines() if not DECISION_LINE.match(line))


def _safe(root, path, what):
    try:
        return journal.inside(root, path)
    except ValueError as error:
        raise PlanError(f'{what} refusé : {error}')


def _json(text):
    try:
        data = json.loads(text)
    except ValueError as error:
        raise PlanError(f'plan JSON illisible : {error}')
    if not isinstance(data, dict):
        raise PlanError('plan JSON : objet attendu')
    unknown = sorted(set(data) - set(FIELDS) - set(DECISIONS))
    if unknown:
        raise PlanError('clé inconnue dans le plan : {}'.format(', '.join(unknown)))
    fiches = data.get('tasks')
    if not isinstance(fiches, list) or not all(isinstance(item, str) for item in fiches):
        raise PlanError('plan JSON : « tasks » doit être une liste de chemins')
    entries = [('fiche', item) for item in fiches]
    return {key: value for key, value in data.items() if key not in ('tasks', *DECISIONS)}, entries


def _markdown(text):
    values, fiches = {}, []
    for line in text.splitlines():
        found = FICHE_LINE.match(line)
        if found:
            fiches.append((found.group(1), found.group(2)))
            continue
        found = FIELD_LINE.match(line)
        if found:
            values[found.group(1)] = _unquote(found.group(2))
    return values, fiches


def _unquote(value):
    if len(value) > 1 and value.startswith('`') and value.endswith('`') and value.count('`') == 2:
        return value[1:-1].strip()
    return value


def _flag(key, value):
    if isinstance(value, bool):
        return value
    word = str(value).strip().lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    raise PlanError(f'« {key} » attend oui/non/true/false, pas {value!r}')


def _text(key, value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise PlanError(f'« {key} » doit être un texte')
    return value.strip() or None


def _build(root, target, digest, values, paths):
    session, by = _text('session', values.get('session')), _text('by', values.get('by'))
    if not session or not by:
        raise PlanError('« session » et « by » sont obligatoires')
    if not SESSION.fullmatch(session):
        raise PlanError(f'session invalide (A-Z a-z 0-9 . _ - ; 64 caractères au plus) : {session!r}')
    if not paths:
        raise PlanError('aucune fiche dans le plan')
    tasks = _tasks(root, paths)
    mirror = (Path(root) / 'template' / 'scripts').is_dir() and (Path(root) / 'scripts').is_dir()
    report = _text('report', values.get('report')) or DEFAULT_REPORT.format(session)
    report_rel = _rel(root, _safe(root, report, 'rapport'))
    default_commit = not report_rel.startswith('.workspace/')
    flags = {
        key: _flag(key, values[key]) if values.get(key) is not None else default
        for key, default in (('report_check', True), ('resync', mirror), ('commit_report', default_commit), ('diagnostic', True))
    }
    return {
        'path': _rel(root, target),
        'sha256': digest,
        'session': session,
        'by': by,
        'model': _text('model', values.get('model')) or 'opus',
        'tasks': tasks,
        'verify': _verify(values.get('verify')),
        'image': _text('image', values.get('image')) or DEFAULT_IMAGE,
        'report': report_rel,
        'correcteur': _executor('correcteur', _text('correcteur', values.get('correcteur')) or DEFAULT_CORRECTOR),
        **flags,
    }


def _tasks(root, entries):
    """Fiches du plan, dans l'ordre, avec leur vague ; PlanError sur un doublon ou une vague invalide."""
    tasks, seen = [], set()
    for kind, line in entries:
        path, attributes = _attributes(line)
        task = _demande(root, path, attributes) if kind == 'demande' else _fiche(root, path, attributes)
        if task['id'] in seen:
            raise PlanError('identifiant en double : {}'.format(task['id']))
        seen.add(task['id'])
        tasks.append(task)
    _waves(tasks)
    return tasks


def _attributes(line):
    """(chemin, attributs) d'une ligne `chemin | clé: valeur | …` ; PlanError sur une clé inconnue."""
    parts = [part.strip() for part in line.split('|')]
    attributes = {}
    for part in parts[1:]:
        key, separator, value = part.partition(':')
        name = ATTRIBUTES.get(key.strip().lower())
        if not separator or name is None:
            raise PlanError(f'attribut inconnu dans la ligne « {line} » : {part}')
        attributes[name] = value.strip().strip('`').strip()
    return parts[0].strip('`').strip(), attributes


def _common(attributes):
    wave = attributes.get('wave')
    if wave is not None and not re.fullmatch(r'[1-9][0-9]{0,2}', wave):
        raise PlanError(f'vague invalide (entier de 1 à 999) : {wave!r}')
    review = attributes.get('review')
    if review is not None and review.lower() not in REVIEWERS + ('non',):
        raise PlanError(f'relecture inconnue : {review!r} (opus ou non)')
    executor = attributes.get('executor')
    return {
        'wave': int(wave) if wave else None,
        'executor': _executor('exécutant', executor) if executor else None,
        'review': review.lower() if review and review.lower() != 'non' else None,
    }


def _executor(key, value):
    if value not in EXECUTORS:
        raise PlanError('« {} » attend {}, pas {!r}'.format(key, ', '.join(EXECUTORS), value))
    return value


def _fiche(root, path, attributes):
    if 'id' in attributes:
        raise PlanError(f'« id » ne vaut que pour une demande : {path}')
    fiche = _safe(root, path, 'fiche')
    return dict({'id': _fiche_id(fiche, path), 'fiche': _rel(root, fiche), 'demande': None}, **_common(attributes))


def _demande(root, path, attributes):
    """Demande du propriétaire : sa fiche naîtra dans `.workspace/tasks/<id>.md` une fois la proposition acceptée."""
    task_id = attributes.get('id')
    if not task_id or not IDENTIFIER.fullmatch(task_id):
        raise PlanError(f'demande sans « id: » valide : {path}')
    demande = _safe(root, path, 'demande')
    if not demande.is_file():
        raise PlanError(f'demande absente : {path}')
    fiche = _rel(root, _safe(root, f'.workspace/tasks/{task_id}.md', 'fiche'))
    return dict({'id': task_id, 'fiche': fiche, 'demande': _rel(root, demande)}, **_common(attributes))


def _waves(tasks):
    """Sans « vague », chaque fiche a la sienne, dans l'ordre ; avec, toutes en ont une et une vague tient trois fiches."""
    given = [task['wave'] is not None for task in tasks]
    if not any(given):
        for index, task in enumerate(tasks, 1):
            task['wave'] = index
        return
    if not all(given):
        raise PlanError('« vague » est donnée pour certaines fiches seulement : la donner à toutes')
    for wave in {task['wave'] for task in tasks}:
        members = [task['id'] for task in tasks if task['wave'] == wave]
        if len(members) > WAVE_SIZE:
            raise PlanError('vague {} : {} fiches, {} au plus ({})'.format(wave, len(members), WAVE_SIZE, ', '.join(members)))


def waves(plan):
    """Fiches du plan groupées par vague, vagues dans l'ordre croissant, fiches dans l'ordre du plan."""
    numbers = sorted({task['wave'] for task in plan['tasks']})
    return [[task for task in plan['tasks'] if task['wave'] == number] for number in numbers]


def _verify(value):
    text = _text('verify', value)
    return None if text is None or text.lower() in ('null', 'none') else text


def _rel(root, path):
    return Path(os.path.relpath(str(path), os.path.abspath(str(root)))).as_posix()


def _fiche_id(fiche, entry):
    if not fiche.is_file():
        raise PlanError(f'fiche absente : {entry}')
    lines = fiche.read_text(encoding='utf-8', errors='replace').splitlines()
    if not lines or lines[0].strip() != '---':
        raise PlanError(f'fiche sans en-tête --- : {entry}')
    for line in lines[1:]:
        if line.strip() == '---':
            break
        found = re.match(r'^id:\s*(\S+)\s*$', line)
        if found and IDENTIFIER.fullmatch(found.group(1)):
            return found.group(1)
    raise PlanError(f'fiche sans ligne « id: » valide dans son en-tête : {entry}')
