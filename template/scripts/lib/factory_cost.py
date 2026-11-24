"""Tokens de l'orchestrateur d'une usine : messages des transcriptions Claude tombés dans ses fenêtres
(Python 3.14, standard library uniquement)."""

import datetime
import os
import re
import sys
from pathlib import Path

import events
import metrics_claude

ORCHESTRATOR_TASK = 'orchestrator'
EVENT_CLAIMED = 'orchestrator_claimed'
EVENT_RELEASED = 'orchestrator_released'
DEFAULT_CLAUDE_DIR = '~/.claude/projects'
WORKSPACE_DIR = '.workspace'
FACTORY_NAME = re.compile(r'[A-Za-z0-9._-]{1,64}')
EXIT_USAGE = 64
EXIT_NO_WINDOW = 1
SOURCE_EVENT = 'événement'
SOURCE_ROOT = 'racine'
SOURCE_LABELS = {SOURCE_EVENT: "cwd de l'événement", SOURCE_ROOT: 'racine du projet : événement sans cwd'}
SESSION_ENTRYPOINTS = ('claude-desktop', 'cli')
SIDE_LABEL = 'Sous-agents et appels directs'
COUNTERS = ('input', 'cache_read', 'cache_creation', 'output')
USAGE = 'usage : factory_cost.py <racine> --usine <nom> [--claude-dir <dossier>]'


def _text(value):
    return value if isinstance(value, str) and value else None


def windows(root, session):
    """Fenêtres de l'usine `session`, dans l'ordre du journal."""
    found = []
    open_window = None
    for entry in events.read(root, task_id=ORCHESTRATOR_TASK):
        kind = entry['event']
        if kind not in (EVENT_CLAIMED, EVENT_RELEASED):
            continue
        if open_window is not None:
            open_window['end'] = entry.get('at')
            open_window = None
        detail = entry.get('detail')
        detail = detail if isinstance(detail, dict) else {}
        if kind == EVENT_CLAIMED and detail.get('session') == session:
            open_window = {
                'start': entry.get('at'),
                'end': None,
                'cwd': _text(detail.get('cwd')),
                'model': _text(detail.get('model')),
                'claude_session_id': _text(detail.get('claude_session_id')),
            }
            found.append(open_window)
    return found


def _instant(value):
    if not isinstance(value, str):
        return None
    try:
        return events._instant(value)
    except (ValueError, TypeError):
        return None


def _inside(path, folder):
    return path == folder or path.startswith(folder + os.sep)


def _belongs(cwd, folder):
    if not cwd:
        return False
    path = os.path.realpath(cwd)
    return _inside(path, folder) and not _inside(path, os.path.join(folder, WORKSPACE_DIR))


def _empty():
    return dict.fromkeys(('messages',) + COUNTERS + ('total',), 0)


def _add(target, message):
    target['messages'] += 1
    for key in COUNTERS:
        target[key] += message[key]
        target['total'] += message[key]


def _in_session(message):
    return message.get('entrypoint') in SESSION_ENTRYPOINTS and not message.get('sidechain')


def _dominant_session(messages):
    """`session_id` le plus fourni parmi les messages que `_in_session` compte (égalité : le plus ancien)."""
    counts, first = {}, {}
    for message in messages:
        sid = message.get('session_id')
        if sid is None or not _in_session(message):
            continue
        counts[sid] = counts.get(sid, 0) + 1
        moment = _instant(message['timestamp'])
        if sid not in first or moment < first[sid]:
            first[sid] = moment
    if not counts:
        return None
    return min(counts, key=lambda sid: (-counts[sid], first[sid]))


def _counts_in_session(message, retained, explicit):
    """Vrai si le message compte dans la session pour une fenêtre dont la session retenue est `retained`."""
    sid = message.get('session_id')
    if sid is None:
        return _in_session(message)
    if explicit is not None:
        return sid == explicit
    if retained is not None:
        return sid == retained and _in_session(message)
    return _in_session(message)


def collect(root, session, claude_dir=None, now=None):
    capped = now is not None
    now = now or datetime.datetime.now(datetime.UTC)
    result = {'session': session, 'windows': [], 'by_model': {}, 'total': _empty(), 'side_by_model': {}, 'side_total': _empty()}
    spans = []
    for window in windows(root, session):
        folder_source = SOURCE_EVENT if window['cwd'] else SOURCE_ROOT
        folder = os.path.realpath(window['cwd'] or str(root))
        start = _instant(window['start'])
        end = _instant(window['end']) if window['end'] is not None else now
        if capped and end is not None:
            end = min(end, now)
        result['windows'].append(dict(window, folder=folder, cwd_source=folder_source, open=window['end'] is None))
        spans.append((start, end, folder))
    if not spans:
        return result
    messages = metrics_claude.assistant_messages(claude_dir or DEFAULT_CLAUDE_DIR)
    by_model, side_by_model = {}, {}
    hit_messages = []
    inside = [[] for _ in spans]
    for message in messages.values():
        moment = _instant(message['timestamp'])
        if moment is None:
            continue
        hits = [
            index
            for index, (start, end, folder) in enumerate(spans)
            if start is not None and end is not None and start <= moment <= end and _belongs(message['cwd'], folder)
        ]
        if hits:
            for index in hits:
                inside[index].append(message)
            hit_messages.append((message, hits))
    retained = [window['claude_session_id'] or _dominant_session(inside[index]) for index, window in enumerate(result['windows'])]
    for message, hits in hit_messages:
        if any(_counts_in_session(message, retained[index], result['windows'][index]['claude_session_id']) for index in hits):
            _add(by_model.setdefault(message['model'], _empty()), message)
            _add(result['total'], message)
        else:
            _add(side_by_model.setdefault(message['model'], _empty()), message)
            _add(result['side_total'], message)
    result['by_model'] = dict(sorted(by_model.items()))
    result['side_by_model'] = dict(sorted(side_by_model.items()))
    return result


def _row(label, values):
    return '| %s | %d | %d | %d | %d | %d |' % ((label, values['messages']) + tuple(values[key] for key in COUNTERS))


def _parse(argv):
    values = {'--usine': None, '--claude-dir': None}
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
    if len(positional) != 1 or values['--usine'] is None:
        return None
    return Path(positional[0]), values['--usine'], values['--claude-dir']


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parsed = _parse(argv)
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return EXIT_USAGE
    root, name, claude_dir = parsed
    if not FACTORY_NAME.fullmatch(name):
        print("factory_cost: nom d'usine invalide (caractères admis : A-Z a-z 0-9 . _ -, 1 à 64)", file=sys.stderr)
        return EXIT_USAGE
    result = collect(root, name, claude_dir)
    if not result['windows']:
        print("aucune fenêtre pour l'usine %s" % name, file=sys.stderr)
        return EXIT_NO_WINDOW
    lines = ['Usine %s : orchestrateur' % name]
    for window in result['windows']:
        lines.append(
            'Fenêtre %s → %s, dossier %s (%s)'
            % (window['start'], 'en cours' if window['open'] else window['end'], window['folder'], SOURCE_LABELS[window['cwd_source']])
        )
    lines += ['| Modèle | Messages | Entrée hors cache | Cache lu | Cache créé | Sortie |', '| --- | --- | --- | --- | --- | --- |']
    lines += [_row(model, values) for model, values in result['by_model'].items()]
    lines.append(_row('Total', result['total']))
    lines.append(_row(SIDE_LABEL, result['side_total']))
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())
