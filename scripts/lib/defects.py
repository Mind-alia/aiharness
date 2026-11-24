"""Défauts trouvés après fusion : comptés par semaine ISO, depuis le journal et depuis docs/LOG.md (Python 3.14, stdlib seule).

Un défaut est une entrée qui suit une fusion verte de la même tâche : un événement `outcome`, `question_answered`
ou `note` dont le détail porte `kind: defect` (`note.sh defect`), daté après un événement `merged`. Le journal
`docs/LOG.md` compte chaque ligne contenant `MARKER`, datée du titre `## ` daté le plus proche au-dessus.
"""

import datetime
import re
from pathlib import Path

import events
import safety

MARKER = '[défaut après fusion]'
DEFECT_KIND = 'defect'
NOTE_EVENTS = ('outcome', 'question_answered', 'note')
LOG_PATH = 'docs/LOG.md'
HEADING_PREFIX = '## '
DATE_PATTERN = re.compile(r'(?:\[(\d{4}-\d{2}-\d{2})\]|(\d{4}-\d{2}-\d{2}))')


def _week(day):
    """Semaine ISO `AAAA-Www` d'une date."""
    iso = day.isocalendar()
    return '%04d-W%02d' % (iso[0], iso[1])


def _heading_date(text):
    """Date d'un titre commençant par `[AAAA-MM-JJ]` ou `AAAA-MM-JJ`, `None` sinon."""
    match = DATE_PATTERN.match(text)
    if match is None:
        return None
    try:
        return datetime.date.fromisoformat(match.group(1) or match.group(2))
    except ValueError:
        return None


def _utc_day(instant):
    return instant.astimezone(datetime.UTC).date()


def from_events(root, since=None, until=None):
    """Une date UTC par note de défaut postérieure à une fusion de sa tâche ; `since` exclut ce qui précède,
    `until` (instant UTC) ce qui suit."""
    rows = events.read(root)
    if until is not None:
        rows = [row for row in rows if events._instant(row['at']) <= events._instant(until)]
    first_merge = {}
    for row in rows:
        if row['event'] == 'merged':
            instant = events._instant(row['at'])
            first_merge[row['task_id']] = min(instant, first_merge.get(row['task_id'], instant))
    found = []
    for row in rows:
        detail = row.get('detail')
        if row['event'] not in NOTE_EVENTS or not isinstance(detail, dict) or detail.get('kind') != DEFECT_KIND:
            continue
        merged = first_merge.get(row['task_id'])
        instant = events._instant(row['at'])
        if merged is None or instant <= merged:
            continue
        day = _utc_day(instant)
        if since is None or day >= since:
            found.append(day)
    return found


def from_log(root, since=None):
    """Une date par ligne de docs/LOG.md contenant `MARKER` ; fichier absent ou lien symbolique : liste vide."""
    path = Path(root) / LOG_PATH
    try:
        safety.check_no_symlinks(path, Path(root))
        if not path.is_file():
            return []
        text = path.read_text(encoding='utf-8')
    except (OSError, ValueError):
        return []
    found, current = [], None
    for line in text.splitlines():
        if line.startswith(HEADING_PREFIX):
            current = _heading_date(line[len(HEADING_PREFIX) :]) or current
        if MARKER in line and current is not None and (since is None or current >= since):
            found.append(current)
    return found


def weekly(root, since=None, today=None, until=None):
    """Totaux par semaine ISO non vide, et total de la semaine de `today` (UTC par défaut) ; `until` borne le journal."""
    counts = {}
    for origin, days in (('events', from_events(root, since, until)), ('log', from_log(root, since))):
        for day in days:
            counts.setdefault(_week(day), {'events': 0, 'log': 0})[origin] += 1
    weeks = []
    for week in sorted(counts):
        entry = counts[week]
        weeks.append({'week': week, 'events': entry['events'], 'log': entry['log'], 'total': entry['events'] + entry['log']})
    today = today or datetime.datetime.now(datetime.UTC).date()
    current = counts.get(_week(today), {'events': 0, 'log': 0})
    return {'weeks': weeks, 'this_week': current['events'] + current['log']}
