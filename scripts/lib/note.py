"""Déclare un sous-agent lancé hors du circuit, par deux événements liés (Python 3.14, stdlib seule).

`note.sh start <libellé>` écrit `subagent_started` et imprime un identifiant `note-…` ; `note.sh end <id>`
écrit `subagent_finished` pour ce même identifiant, avec la durée. Le hub des usines lit ces événements dans
`.workspace/events.jsonl` : sans eux, un sous-agent lancé à la main reste invisible.

`note.sh defect <libellé> --task <id>` écrit un événement `note` (`kind: defect`) pour une tâche déjà fusionnée :
le compteur des défauts trouvés après fusion (`report.sh --defauts`) le lit.

`note.sh human <libellé> --task <id> --minutes <n>` écrit un événement `human_intervention` : le temps humain passé sur
une tâche, que l'étude E3 additionne (`report.sh --task` et la grille de mission le lisent).

Règle : tout sous-agent lancé hors du circuit est encadré par `note.sh start` / `end`.
"""

import argparse
import datetime
import os
import re
import sys
from pathlib import Path

import events

sys.dont_write_bytecode = True

KINDS = ('subagent', 'check', 'step')
DEFAULT_KIND = 'subagent'
DEFAULT_OUTCOME = 'done'
DEFECT_KIND = 'defect'
HUMAN_EVENT = 'human_intervention'
MINUTES_MIN = 1
MINUTES_MAX = 1440
TASK_ID_PATTERN = r'[a-z0-9-]+'
LABEL_MAX = 200
VALUE_MAX = 64
USAGE_ERROR = 64
STATE_ERROR = 1
RANDOM_BYTES = 3
ID_PREFIX = 'note-'
CONTROL_LIMIT = 32
DELETE_CHAR = 127


def fail(code, message):
    """Message d'une ligne sur stderr, puis sortie avec le code donné."""
    print('note: ' + message.replace('\n', ' '), file=sys.stderr)
    sys.exit(code)


class _Parser(argparse.ArgumentParser):
    """Erreurs d'usage refusées avec le code 64 et un message d'une ligne."""

    def error(self, message):
        fail(USAGE_ERROR, message)


def _check_control(name, value):
    if any(ord(char) < CONTROL_LIMIT or ord(char) == DELETE_CHAR for char in value):
        fail(USAGE_ERROR, name + ' contient un caractère de contrôle')


def _check_value(name, value, limit):
    if not value:
        fail(USAGE_ERROR, name + ' vide')
    if len(value) > limit:
        fail(USAGE_ERROR, '%s de plus de %d caractères' % (name, limit))
    _check_control(name, value)


def new_id(now):
    """Identifiant `note-<AAAAMMJJTHHMMSSZ>-<6 hexadécimaux>` ; `os.urandom` car `secrets` est masqué par le kit."""
    return ID_PREFIX + now.strftime('%Y%m%dT%H%M%SZ') + '-' + os.urandom(RANDOM_BYTES).hex()


def start(root, label, model, kind):
    _check_value('libellé', label, LABEL_MAX)
    if model is not None:
        _check_value('--model', model, VALUE_MAX)
    task_id = new_id(datetime.datetime.now(datetime.UTC))
    detail = {'label': label, 'model': model, 'kind': kind}
    events.record(root, task_id, 'subagent_started', detail)
    print(task_id)
    return 0


def _find(rows, event):
    for row in rows:
        if row['event'] == event:
            return row
    return None


def end(root, task_id, outcome):
    _check_control('identifiant', task_id)
    _check_value('--outcome', outcome, VALUE_MAX)
    rows = events.read(root, task_id=task_id)
    started = _find(rows, 'subagent_started')
    if started is None:
        fail(STATE_ERROR, 'identifiant inconnu : ' + task_id)
    if _find(rows, 'subagent_finished') is not None:
        fail(STATE_ERROR, 'déjà terminé : ' + task_id)
    started_at = datetime.datetime.fromisoformat(started['at'])
    now = datetime.datetime.now(datetime.UTC)
    origin = started['detail']
    detail = {
        'label': origin['label'],
        'model': origin['model'],
        'kind': origin['kind'],
        'outcome': outcome,
        'seconds': max(0, int((now - started_at).total_seconds())),
    }
    events.record(root, task_id, 'subagent_finished', detail)
    return 0


def defect(root, label, task_id):
    _check_value('libellé', label, LABEL_MAX)
    if not re.fullmatch(TASK_ID_PATTERN, task_id):
        fail(USAGE_ERROR, 'identifiant de tâche invalide : ' + task_id)
    if _find(events.read(root, task_id=task_id), 'merged') is None:
        fail(STATE_ERROR, 'aucune fusion connue pour ' + task_id)
    events.record(root, task_id, 'note', {'kind': DEFECT_KIND, 'label': label})
    print(task_id)
    return 0


def _minutes(text):
    """Entier de 1 à 1440 écrit en chiffres décimaux seulement ; refus sinon."""
    digits = text.lstrip('0') or '0'
    if not re.fullmatch(r'[0-9]+', text) or len(digits) > len(str(MINUTES_MAX)) or not MINUTES_MIN <= int(digits) <= MINUTES_MAX:
        fail(USAGE_ERROR, '--minutes attend un entier de %d à %d : %s' % (MINUTES_MIN, MINUTES_MAX, text[:20]))
    return int(digits)


def human(root, label, task_id, minutes):
    _check_value('libellé', label, LABEL_MAX)
    if not re.fullmatch(TASK_ID_PATTERN, task_id):
        fail(USAGE_ERROR, 'identifiant de tâche invalide : ' + task_id)
    value = _minutes(minutes)
    events.record(root, task_id, HUMAN_EVENT, {'label': label, 'minutes': value})
    print(task_id)
    return 0


def intervention_summary(rows):
    """`<k> <mot>, <m> min` pour les événements `human_intervention` de rows.

    Une minute absente ou non entière compte 0.
    """
    found = [row for row in rows if row.get('event') == HUMAN_EVENT]
    minutes = sum(_counted_minutes(row) for row in found)
    word = 'intervention' if len(found) <= 1 else 'interventions'
    return '%d %s, %d min' % (len(found), word, minutes)


def _counted_minutes(row):
    detail = row.get('detail')
    value = detail.get('minutes') if isinstance(detail, dict) else None
    return value if type(value) is int else 0


def build_parser():
    parser = _Parser(prog='note.sh')
    commands = parser.add_subparsers(dest='command', metavar='start|end|defect|human')
    commands.required = True
    begin = commands.add_parser('start')
    begin.add_argument('label')
    begin.add_argument('--model')
    begin.add_argument('--kind', choices=KINDS, default=DEFAULT_KIND)
    finish = commands.add_parser('end')
    finish.add_argument('task_id')
    finish.add_argument('--outcome', default=DEFAULT_OUTCOME)
    flaw = commands.add_parser('defect')
    flaw.add_argument('label')
    flaw.add_argument('--task', required=True)
    spent = commands.add_parser('human')
    spent.add_argument('label')
    spent.add_argument('--task', required=True)
    spent.add_argument('--minutes', required=True)
    return parser


def main(argv):
    if len(argv) < 2:
        fail(
            USAGE_ERROR,
            'usage : note.sh start <libellé> | end <id> | defect <libellé> --task <id> | human <libellé> --task <id> --minutes <n>',
        )
    root = Path(argv[0])
    args = build_parser().parse_args(argv[1:])
    if args.command == 'start':
        return start(root, args.label, args.model, args.kind)
    if args.command == 'defect':
        return defect(root, args.label, args.task)
    if args.command == 'human':
        return human(root, args.label, args.task, args.minutes)
    return end(root, args.task_id, args.outcome)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
