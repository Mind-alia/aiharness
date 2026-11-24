"""Append-only model-call accounting. Python 3.14, standard library only."""

import argparse
import fcntl
import json
import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.dont_write_bytecode = True
import secrets

import context

ROLES = ('orchestration', 'execution', 'relecture', 'conseil', 'recherche', 'reprise')
PROVIDERS = ('claude', 'codex', 'antigravity', 'ollama')


def token_total(tokens, provider=None):
    """Observed token volume, never a price; unknown components stay unknown.

    Codex input includes cache reads. Claude input excludes both cache buckets.
    Reasoning tokens are already part of output and must not be added again.
    """
    if not isinstance(tokens, dict):
        return None
    if 'total' in tokens:
        value = tokens['total']
        return value if type(value) is int and value >= 0 else None
    claude = (
        provider.startswith('claude')
        if isinstance(provider, str)
        else 'cache_creation' in tokens or 'cache_creation_input_tokens' in tokens
    )
    if 'input_tokens' in tokens or 'output_tokens' in tokens:
        keys = ('input_tokens', 'output_tokens')
        if claude:
            keys += ('cache_read_input_tokens', 'cache_creation_input_tokens')
    else:
        keys = ('input', 'output')
        if claude:
            keys += ('cached_input', 'cache_creation')
    values = [tokens.get(key) for key in keys]
    if any(type(value) is not int or value < 0 for value in values):
        return None
    return sum(values)


def record(root, **fields):  # noqa: C901
    """Validate an allowlisted metadata record, then append under an exclusive lock."""
    allowed = {
        'role',
        'provider',
        'model',
        'task_id',
        'attempt',
        'input',
        'cached_input',
        'output',
        'cache_creation',
        'reasoning',
        'turns',
        'duration',
        'limit_reached',
        'quota_detail',
        'exit',
        'tokens_complete',
        'cost_usd',
    }
    if set(fields) - allowed:
        raise ValueError('Unknown consumption field')
    if fields.get('role') not in ROLES or fields.get('provider') not in PROVIDERS:
        raise ValueError('Invalid consumption role or provider')
    for key in ('attempt', 'input', 'cached_input', 'cache_creation', 'output', 'reasoning', 'turns', 'exit'):
        value = fields.get(key)
        if value is not None and (type(value) is not int or (key != 'exit' and value < 0)):
            raise ValueError('Invalid consumption integer: ' + key)
    duration = fields.get('duration')
    if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
        raise ValueError('Invalid consumption duration')
    for key in ('model', 'task_id', 'quota_detail'):
        if fields.get(key) is not None and not isinstance(fields[key], str):
            raise ValueError('Invalid consumption string: ' + key)
    cost = price(fields.get('cost_usd'))
    if fields.get('cost_usd') is not None and cost is None:
        raise ValueError('Invalid consumption cost')
    if type(fields.get('limit_reached', False)) is not bool:
        raise ValueError('Invalid consumption quota flag')
    if 'tokens_complete' in fields and type(fields['tokens_complete']) is not bool:
        raise ValueError('Invalid consumption completeness flag')
    root = Path(root).absolute()
    detail = fields.get('quota_detail')
    if detail is not None:
        detail = secrets.mask_secrets(detail, secrets.known_values(root))[:120]
    row = dict(
        at=datetime.now(UTC).isoformat(),
        role=fields['role'],
        provider=fields['provider'],
        model=fields.get('model'),
        task_id=fields.get('task_id'),
        attempt=fields.get('attempt'),
        tokens={key: fields.get(key) for key in ('input', 'cached_input', 'output', 'reasoning')},
        proxy=dict(turns=fields.get('turns'), duration_seconds=float(duration) if duration is not None else None),
        quota=dict(limit_reached=fields.get('limit_reached', False), detail=detail),
        exit=fields.get('exit'),
    )
    if fields['provider'] == 'claude' or 'cache_creation' in fields:
        row['tokens']['cache_creation'] = fields.get('cache_creation')
    if cost is not None:
        # Dollars reported by the provider for this call (`total_cost_usd` of `claude -p`), never estimated.
        row['cost_usd'] = cost
    if 'tokens_complete' in fields:
        row['tokens_complete'] = fields['tokens_complete']
        if not fields['tokens_complete']:
            # Existing total consumers must not mistake observed subtotals for a full call.
            row['tokens']['total'] = None
    directory = root / '.workspace/metrics'
    path = directory / 'consumption.jsonl'
    context.check_no_symlinks(path, root)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a', encoding='utf-8') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        os.fchmod(stream.fileno(), 0o600)
        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()
    return row


CLAUDE_USAGE_FIELDS = (
    ('input', 'input_tokens'),
    ('cached_input', 'cache_read_input_tokens'),
    ('cache_creation', 'cache_creation_input_tokens'),
    ('output', 'output_tokens'),
)


def price(value):
    """Dollars of one call: a finite number, zero or more and not a boolean; None otherwise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def claude_cost(output):
    """`total_cost_usd` of a `claude -p --output-format json` answer, None when absent or invalid."""
    return price(output.get('total_cost_usd')) if isinstance(output, dict) else None


def record_claude(root, role, model, task_id, claude_output, duration=None, exit=None):  # noqa: PLR0913
    """Record one `claude -p --output-format json` call; True when written, never raises."""
    try:
        output = claude_output if isinstance(claude_output, dict) else {}
        usage = output.get('usage')
        usage = usage if isinstance(usage, dict) else {}
        fields = {}
        for key, source in CLAUDE_USAGE_FIELDS:
            value = usage.get(source)
            fields[key] = value if type(value) is int and value >= 0 else None
        complete = all(value is not None for value in fields.values())
        used = output.get('modelUsage')
        if isinstance(used, dict) and len(used) == 1 and isinstance(next(iter(used)), str):
            model = next(iter(used))
        record(
            root,
            role=role,
            provider='claude',
            model=model,
            task_id=task_id,
            duration=duration,
            exit=exit,
            tokens_complete=complete,
            cost_usd=claude_cost(output),
            **fields,
        )
        return True
    except Exception:
        return False


def message(event):
    value = event.get('error', event.get('message', ''))
    if isinstance(value, dict):
        value = value.get('message', '')
    return value if isinstance(value, str) else ''


def codex_events(lines):  # noqa: C901
    """Sum observed counters and qualify the whole stream, including unfinished turns."""
    fields = {}
    turns = 0
    active = False
    complete = True
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            complete = False
            continue
        if not isinstance(event, dict):
            complete = False
            continue
        kind = event.get('type')
        if kind == 'turn.started':
            if active:
                complete = False
            active = True
        if kind in ('turn.completed', 'turn.failed'):
            active = False
            if kind == 'turn.completed':
                turns += 1
            else:
                complete = False
            usage = event.get('usage')
            usage = usage if isinstance(usage, dict) else {}
            for key, source in (
                ('input', 'input_tokens'),
                ('cached_input', 'cached_input_tokens'),
                ('output', 'output_tokens'),
                ('reasoning', 'reasoning_output_tokens'),
            ):
                count = usage.get(source)
                if type(count) is int and count >= 0:
                    fields[key] = fields.get(key, 0) + count
                elif key in ('input', 'output'):
                    complete = False
        if kind in ('error', 'turn.failed'):
            complete = False
            detail = message(event)
            if not fields.get('limit_reached') and any(term in detail.lower() for term in ('usage limit', 'rate limit')):
                fields.update(limit_reached=True, quota_detail=detail)
    if turns:
        fields['turns'] = turns
    fields['tokens_complete'] = complete and not active and turns > 0
    return fields


def from_events(path, antigravity=False):
    if not antigravity:
        with Path(path).open(encoding='utf-8', errors='replace') as stream:
            return codex_events(stream)
    fields = {}
    turns = 0
    with Path(path).open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            raw_text = ''
            try:
                event = json.loads(line)
            except ValueError:
                event = {}
                raw_text = line
            if not isinstance(event, dict):
                event = {}
            # Only explicit counters or completed-turn events are a usable proxy.
            proxy_turns = event.get('turns')
            if type(proxy_turns) is int and proxy_turns >= 0:
                fields['turns'] = proxy_turns
            # Inspect diagnostics, never the generated response in a JSON envelope.
            text = message(event) or raw_text
            limited = any(term in text.lower() for term in ('quota', 'rate limit', 'resource_exhausted', 'usage limit'))
            detail = message(event) or 'Antigravity usage limit detected'
            if event.get('type') == 'turn.completed':
                turns += 1
            if limited and not fields.get('limit_reached'):
                fields.update(limit_reached=True, quota_detail=detail)
    if turns:
        fields['turns'] = turns
    return fields


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('command', choices=('record', 'from-codex-events', 'from-antigravity-events'))
    parser.add_argument('events', nargs='?')
    parser.add_argument('--role', required=True)
    parser.add_argument('--provider')
    for key in ('model', 'task-id', 'quota-detail'):
        parser.add_argument('--' + key)
    for key in ('attempt', 'input', 'cached-input', 'cache-creation', 'output', 'reasoning', 'turns', 'exit'):
        parser.add_argument('--' + key, type=int)
    parser.add_argument('--duration', type=float)
    parser.add_argument('--limit-reached', action='store_true')
    args = vars(parser.parse_args(argv))
    root, command, events = (args.pop(key) for key in ('root', 'command', 'events'))
    fields = {key: value for key, value in args.items() if value is not None}
    try:
        if command != 'record':
            if not events:
                raise ValueError('Events file required')
            fields['provider'] = 'codex' if command == 'from-codex-events' else 'antigravity'
            fields.update(from_events(events, antigravity=command == 'from-antigravity-events'))
            if command == 'from-codex-events' and fields.get('exit') not in (None, 0):
                fields['tokens_complete'] = False
        record(root, **fields)
        return 0
    except Exception:
        # Never echo raw events or metadata in diagnostics.
        print('Consumption: unable to record model call', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
