#!/usr/bin/env python3
"""Local observation bridge. Reads one explicitly selected Codex transcript or an Antigravity hook."""

import argparse
import fcntl
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.request
from datetime import UTC, datetime


def ulid(seed):
    value = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:16], 'big')
    chars = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
    result = ''
    for _ in range(26):
        result = chars[value & 31] + result
        value >>= 5
    return result


def target(args):
    home = pathlib.Path(args.home or pathlib.Path.home() / '.workspace-console')
    lock = json.loads((home / 'service.lock').read_text())
    if pathlib.Path(lock['projectDir']).resolve() != pathlib.Path(args.project).resolve():
        raise ValueError('Different project')
    port = lock['port']
    if not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError('Invalid port')
    token = (pathlib.Path(args.project) / '.workspace/run/token').read_text().strip()
    return lock, port, token


def send(args, provider, event, payload, at=None, key=None):  # noqa: PLR0913
    at = at or datetime.now(UTC).isoformat().replace('+00:00', 'Z')
    body = dict(
        provider=provider,
        event=event,
        payload=payload,
        projectDir=str(pathlib.Path(args.project).resolve()),
        sentAt=at,
        deliveryId=ulid(key or json.dumps([provider, event, payload, at], sort_keys=True)),
    )
    try:
        _, port, token = target(args)
    except (OSError, ValueError, KeyError, TypeError):
        if provider != 'google':
            raise
        spool(args, body)
        return
    req = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/v1/hooks/{provider}',
        data=json.dumps(body).encode(),
        method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'},
    )
    try:
        with urllib.request.urlopen(req, timeout=0.3) as response:
            if response.status != 202:
                raise ValueError('Event rejected')
    except (OSError, ValueError):
        if provider != 'google':
            raise
        spool(args, body)
        return
    if args.capture:
        capture = pathlib.Path(args.capture)
        capture.parent.mkdir(parents=True, exist_ok=True)
        saved = json.loads(json.dumps(payload))
        if provider == 'google' and isinstance(saved.get('toolCall'), dict):
            args = saved['toolCall'].get('args', {})
            saved['toolCall']['args'] = {k: v for k, v in args.items() if k in ('TargetFile', 'FilePath', 'AbsolutePath')}
        with capture.open('a') as out:
            out.write(json.dumps({'provider': provider, 'event': event, 'payload': saved, 'sentAt': at}) + '\n')


def spool(args, body):
    directory = pathlib.Path(args.project) / '.workspace/run'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / 'spool.jsonl'
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        line = (json.dumps(body) + '\n').encode()
        if os.fstat(fd).st_size + len(line) <= 10 * 1024 * 1024:
            os.write(fd, line)
    finally:
        os.close(fd)


def safe_codex(row):
    data = row.get('payload')
    if not isinstance(data, dict):
        return None
    kind = row.get('type')
    ptype = data.get('type')
    if kind == 'session_meta':
        return {'type': kind, 'payload': {}}
    if kind == 'event_msg' and ptype in ('user_message', 'task_complete'):
        return {'type': kind, 'payload': {'type': ptype}}
    if kind == 'event_msg' and ptype == 'token_count':
        usage = (data.get('info') or {}).get('total_token_usage')
        if not isinstance(usage, dict):
            return None
        return {
            'type': kind,
            'payload': {
                'type': ptype,
                'info': {
                    'total_token_usage': {
                        key: value
                        for key, value in usage.items()
                        if key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens')
                        and isinstance(value, int)
                        and value >= 0
                    }
                },
            },
        }
    if kind == 'response_item' and ptype in ('function_call', 'custom_tool_call', 'function_call_output', 'custom_tool_call_output'):
        return {'type': kind, 'payload': {key: data[key] for key in ('type', 'name', 'call_id') if key in data}}
    return None


def codex(args):  # noqa: C901
    path = pathlib.Path(args.transcript).resolve()
    offset = 0
    session_id = None
    generation = None
    sent = 0
    while True:
        try:
            lock, _, _ = target(args)
            current_generation = (lock.get('startedAt'), lock['pid'])
            if generation != current_generation:
                offset = 0
                generation = current_generation
                sent = 0
            with path.open() as source:
                source.seek(offset)
                while True:
                    start = source.tell()
                    line = source.readline()
                    if not line or not line.endswith('\n'):
                        offset = start
                        break
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        offset = source.tell()
                        continue
                    if row.get('type') == 'session_meta':
                        meta = row.get('payload', {})
                        session_id = meta.get('id')
                        source_cwd = pathlib.Path(meta.get('cwd', '')).resolve()
                        project = pathlib.Path(args.project).resolve()
                        allowed_root = pathlib.Path(args.allow_workspace_root).resolve() if args.allow_workspace_root else None
                        if source_cwd != project and project not in source_cwd.parents and source_cwd != allowed_root:
                            raise ValueError('Transcript does not belong to the explicitly selected workspace')
                    safe = safe_codex(row)
                    if safe and session_id:
                        send(
                            args,
                            'codex',
                            'Transcript',
                            {'sessionId': session_id, 'record': safe},
                            row.get('timestamp'),
                            key=f'{session_id}:{start}',
                        )
                        sent += 1
                    offset = source.tell()
            if args.once:
                return 0 if sent > 0 and session_id else 1
        except (OSError, ValueError, KeyError, TypeError):
            if args.once:
                print('Connexion Codex impossible : service, projet ou journal indisponible.', file=sys.stderr)
                return 1
        time.sleep(0.7)


def google_hook(args):
    try:
        data = json.load(sys.stdin)
        project = pathlib.Path(args.project).resolve()
        paths = [pathlib.Path(p).resolve() for p in data.get('workspacePaths', []) if isinstance(p, str)]
        if not any(p == project or project in p.parents for p in paths):
            return
        payload = {key: data[key] for key in ('conversationId', 'workspacePaths', 'stepIdx', 'modelName') if key in data}
        if data.get('error'):
            payload['error'] = 'tool_failed'
        call = data.get('toolCall')
        if isinstance(call, dict):
            raw_args = call.get('args', {})
            payload['toolCall'] = {
                'name': call.get('name'),
                'args': {
                    key: value
                    for key, value in raw_args.items()
                    if key in ('TargetFile', 'FilePath', 'AbsolutePath') and isinstance(value, str)
                }
                if isinstance(raw_args, dict)
                else {},
            }
        send(args, 'google', args.event, payload)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass  # Observation must never prevent agent work or change permissions.
    finally:
        print('{}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('codex', 'google-hook'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--project', required=True)
        cmd.add_argument('--home')
        cmd.add_argument('--capture')
        if name == 'codex':
            cmd.add_argument('--transcript', required=True)
            cmd.add_argument('--once', action='store_true')
            cmd.add_argument('--allow-workspace-root', help='Explicit workspace root for this one pinned conversation')
        else:
            cmd.add_argument('event', choices=['SessionStart', 'PreInvocation', 'PostToolUse', 'Stop'])
    args = parser.parse_args()
    if args.command == 'codex':
        return codex(args)
    google_hook(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
