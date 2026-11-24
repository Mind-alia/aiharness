"""Read the final envelope of an Antigravity (agy) run for scripts/gemini.sh: the answer on stdout, the reason on stderr.

Night of 3 November 2026: `report-check.sh` stopped twice on "Gemini failed (exit 0)" with no cause, because the relay
read only the last line of the events, as one JSON object, and threw the reason away (`2>/dev/null`). This module finds
the last envelope even when agy pretty-prints it or adds lines after it, and always says why an answer is refused:
no envelope, a status other than SUCCESS, an empty response, or refused actions. In `review` mode refused actions are
reported (`DENIED: <name>` lines, read by delegate.py) without discarding a complete answer; in `write` mode they fail.

Usage: python3 agy_envelope.py <events.jsonl> review|write  (exit 0 answer accepted, 1 refused, 64 usage)
"""

import json
import sys
from pathlib import Path

LIMIT = 4 * 1024 * 1024
MODES = ('review', 'write')


def last_envelope(text):
    """Last JSON object of the events that carries a `status` or a `response`; None if there is none."""
    decoder = json.JSONDecoder()
    lines = (text or '').splitlines()
    for start in range(len(lines) - 1, -1, -1):
        if not lines[start].lstrip().startswith('{'):
            continue
        try:
            value, _end = decoder.raw_decode('\n'.join(lines[start:]).lstrip())
        except ValueError:
            continue
        if isinstance(value, dict) and ('status' in value or 'response' in value):
            return value
    return None


def denied_names(envelope):
    names = []
    for action in envelope.get('denied_actions') or []:
        name = action.get('display_name') or action.get('name') if isinstance(action, dict) else action
        names.append(str(name or '?'))
    return names


def judge(text, mode):
    """(answer or None, reasons to print on stderr): the answer is None when it must be refused."""
    envelope = last_envelope(text)
    if envelope is None:
        return None, ['agy : enveloppe JSON introuvable dans les événements']
    denied = denied_names(envelope)
    notes = [f'DENIED: {name}' for name in denied]
    status, response = envelope.get('status'), envelope.get('response')
    if status != 'SUCCESS':
        detail = envelope.get('error') or envelope.get('message') or ''
        return None, notes + [f'agy : statut {status!r}' + (f' ({str(detail)[:200]})' if detail else '')]
    if not isinstance(response, str) or not response.strip():
        return None, notes + ['agy : réponse vide']
    if denied and mode == 'write':
        return None, notes + ['agy : actions refusées en mode write : ' + ', '.join(denied)]
    if denied:
        notes.append('agy : actions refusées en lecture seule, réponse gardée : ' + ', '.join(denied))
    return response, notes


def main(argv):
    if len(argv) != 2 or argv[1] not in MODES:
        print('usage: agy_envelope.py <events.jsonl> review|write', file=sys.stderr)
        return 64
    try:
        with Path(argv[0]).open(encoding='utf-8', errors='replace') as stream:
            text = stream.read(LIMIT)
    except OSError as error:
        print(f'agy : événements illisibles ({error})', file=sys.stderr)
        return 1
    answer, notes = judge(text, argv[1])
    for note in notes:
        print(note, file=sys.stderr)
    if answer is None:
        return 1
    print(answer)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
