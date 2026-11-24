"""One-line cost summary of a task, per provider. Python 3.14, standard library only."""

import json
import math
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

# provider -> (label in the line, token key in status.json and in the journal)
COUNTERS = {
    'claude': (('in', 'input'), ('out', 'output'), ('cache_lu', 'cached_input'), ('cache_cree', 'cache_creation')),
    'codex': (('in', 'input'), ('out', 'output')),
    'antigravity': (('total', 'total'),),
}


def agent_provider(agent):
    if not isinstance(agent, str):
        return None
    if agent == 'claude' or agent.startswith('claude-'):
        return 'claude'
    return agent if agent in ('codex', 'antigravity') else None


def read_json_file(root, parts):
    """Parsed file below root, or None when absent, unreadable or reached through a symlink."""
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            return None
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    except (OSError, ValueError):
        return None


def counter(value):
    return value if type(value) is int and value >= 0 else None


def journal_tokens(root, task_id, provider):
    """Sum of the journal rows of one provider for one task; a missing value makes a counter unknown."""
    path = root
    sums = {key: 0 for _, key in COUNTERS[provider]}
    unreadable = {key: None for key in sums}
    for part in ('.workspace', 'metrics', 'consumption.jsonl'):
        path = path / part
        if path.is_symlink():
            return unreadable
    try:
        lines = path.read_text(encoding='utf-8').splitlines() if path.is_file() else []
    except (OSError, ValueError):
        return unreadable
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get('task_id') != task_id or row.get('provider') != provider:
            continue
        tokens = row.get('tokens') if isinstance(row.get('tokens'), dict) else {}
        for key in sums:
            value = counter(tokens.get(key))
            sums[key] = None if sums[key] is None or value is None else sums[key] + value
    return sums


def status_tokens(metrics, provider):
    tokens = metrics.get('tokens') if isinstance(metrics.get('tokens'), dict) else {}
    return {key: counter(tokens.get(key)) for _, key in COUNTERS[provider]}


def shown(value):
    return '?' if value is None else str(value)


def cost_line(root, task_id):
    if not isinstance(task_id, str) or not re.fullmatch(r'[a-z0-9-]+', task_id):
        raise ValueError('identifiant de tâche invalide : ' + repr(task_id))
    root = Path(root)
    status = read_json_file(root, ('.workspace', 'run', 'delegate', task_id, 'status.json'))
    status = status if isinstance(status, dict) else {}
    metrics = status.get('metrics') if isinstance(status.get('metrics'), dict) else {}
    own = agent_provider(status.get('agent'))
    parts = []
    for provider, counters in COUNTERS.items():
        values = status_tokens(metrics, provider) if provider == own else journal_tokens(root, task_id, provider)
        parts.append(provider + ' ' + ' '.join(label + '=' + shown(values.get(key)) for label, key in counters))
    duration = metrics.get('duration_seconds')
    duration = round(duration) if type(duration) in (int, float) and math.isfinite(duration) else None
    attempts = counter(metrics.get('attempts', status.get('attempts')))
    line = 'coût %s : %s, durée=%ss, tentatives=%s' % (task_id, ', '.join(parts), shown(duration), shown(attempts))
    # report.py and print_cost_line print this text as is: the cascade stages follow the cost line.
    return '\n'.join([line] + cascade_lines(root, task_id))


def cascade_lines(root, task_id):
    """One line per stage of a cascade run, then a summary; empty without cascade.json."""
    data = read_json_file(Path(root), ('.workspace', 'run', 'delegate', task_id, 'cascade.json'))
    if not isinstance(data, dict):
        return []
    stages = [stage for stage in data.get('stages', []) if isinstance(stage, dict)]
    lines = []
    for stage in stages:
        tokens = stage.get('tokens') if isinstance(stage.get('tokens'), dict) else {}
        lines.append(
            'étage %s %s : %s, tentatives %s, sortie %s, cache lu %s'
            % (
                shown(stage.get('stage')),
                shown(stage.get('agent')),
                shown(stage.get('outcome')),
                shown(counter(stage.get('attempts'))),
                shown(counter(tokens.get('output'))),
                shown(counter(tokens.get('cached_input'))),
            )
        )
    lines.append('cascade : %d étage(s), issue %s' % (len(stages), shown(data.get('outcome'))))
    return lines


NO_ROLE_CALL = 'rôles : aucun appel enregistré'


def _add(total, value):
    return None if total is None or value is None else total + value


def _consumption_rows(root, task_id=None):
    """Decoded rows of consumption.jsonl, those of one task when task_id is given."""
    path = root
    for part in ('.workspace', 'metrics', 'consumption.jsonl'):
        path = path / part
        if path.is_symlink():
            return []
    try:
        lines = path.read_text(encoding='utf-8').splitlines() if path.is_file() else []
    except (OSError, ValueError):
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and (task_id is None or row.get('task_id') == task_id):
            rows.append(row)
    return rows


def _row_counters(row):
    """(hors_cache, cache_lu, cache_cree or '-', sortie) of one row; None when unknown."""
    tokens = row.get('tokens') if isinstance(row.get('tokens'), dict) else {}
    given, cached = counter(tokens.get('input')), counter(tokens.get('cached_input'))
    output = counter(tokens.get('output'))
    if row.get('provider') == 'codex':
        fresh = given - cached if given is not None and cached is not None and given >= cached else None
        return fresh, cached, '-', output
    return given, cached, counter(tokens.get('cache_creation')), output


def role_totals(rows) -> dict[tuple[str, str], dict]:
    """Sums of consumption rows per (role, provider); a missing value makes a sum unknown (None)."""
    groups = {}
    for row in rows:
        key = (str(row.get('role')), str(row.get('provider')))
        group = groups.setdefault(key, [0, [0, 0, 0, 0], False])
        group[0] += 1
        counters = _row_counters(row)
        if counters[2] != '-':
            group[2] = True
        for index, value in enumerate(counters):
            if value == '-':
                continue
            group[1][index] = _add(group[1][index], value)
    return {
        key: {
            'calls': calls,
            'uncached': sums[0],
            'cache_read': sums[1],
            'cache_creation': sums[2] if has_creation else '-',
            'output': sums[3],
        }
        for key, (calls, sums, has_creation) in groups.items()
    }


def consumption_rows(root, task_id=None, since=None):
    """Rows of consumption.jsonl (of one task when task_id is given), written at `since` or later when given."""
    rows = _consumption_rows(Path(root), task_id)
    if since is None:
        return rows
    start = _instant(since)
    return [row for row in rows if start is not None and (_instant(row.get('at')) or start) >= start]


def _instant(text):
    try:
        value = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def dollars(rows):
    """(sum of the `cost_usd` given by the provider, number of rows without one)."""
    priced = [row['cost_usd'] for row in rows if type(row.get('cost_usd')) in (int, float) and math.isfinite(row['cost_usd'])]
    return sum(priced), len(rows) - len(priced)


def dollars_text(total, unpriced, calls):
    """`0.4120` when every call is priced, `0.4120+?` when some are not, `?` when none is."""
    if unpriced >= calls:
        return '?'
    return '%.4f%s' % (total, '+?' if unpriced else '')


def role_lines(root, task_id) -> list[str]:
    """Cost of a task per (role, provider) from the consumption journal, then a total line; dollars appear once a
    call of the group carries the provider's price (T102)."""
    if not isinstance(task_id, str) or not re.fullmatch(r'[a-z0-9-]+', task_id):
        raise ValueError('identifiant de tâche invalide : ' + repr(task_id))
    rows = _consumption_rows(Path(root), task_id)
    groups = role_totals(rows)
    if not groups:
        return [NO_ROLE_CALL]
    lines = []
    total = [0, 0, 0, 0]
    for (role, provider), values in sorted(groups.items()):
        sums = [values['uncached'], values['cache_read'], values['cache_creation'], values['output']]
        for index, value in enumerate(sums):
            if value != '-':
                total[index] = _add(total[index], value)
        mine = [row for row in rows if (str(row.get('role')), str(row.get('provider'))) == (role, provider)]
        lines.append(
            'rôle %s %s : hors_cache=%s, cache_lu=%s, cache_cree=%s, sortie=%s, appels=%d'
            % (role, provider, shown(sums[0]), shown(sums[1]), shown(sums[2]), shown(sums[3]), values['calls'])
            + _dollars_part(mine)
        )
    lines.append(
        'total : hors_cache=%s, cache_lu=%s, cache_cree=%s, sortie=%s' % tuple(shown(value) for value in total) + _dollars_part(rows)
    )
    return lines


def _dollars_part(rows):
    spent, unpriced = dollars(rows)
    return '' if unpriced >= len(rows) else ', dollars=' + dollars_text(spent, unpriced, len(rows))


def print_cost_line(root, task_id):
    """Cost line on stderr (stdout stays the JSON state); the merge never fails because of it."""
    try:
        print(cost_line(root, task_id), file=sys.stderr)
    except Exception:
        pass
