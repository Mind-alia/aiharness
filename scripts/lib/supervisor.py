"""Deterministic supervision, Python 3.14, no model dependencies.

Delegate execs `python3 - <root> <absolute task>` and has no PID record.
ps therefore checks both delegate.sh and that exact Python invocation.
Unknown process visibility inhibits actions; no process ever receives a signal.
The global flock covers watch and synchronous retries (including children).
"""

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workdir
from sandbox import get_container_name, get_project_fingerprint
from task import read_task, safe_path


def retry_time(message, reference):
    """Resolve reset relative to failure time, never to the next polling time.

    Individual quota without a reset time is deliberately unschedulable.
    Naive local datetimes use system timezone rules, including DST.
    """
    duration = re.search(r'(?:resets?\s+in)\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?', message, re.I)
    if duration and any(duration.groups()):
        h, m, s = (int(v or 0) for v in duration.groups())
        return reference + dt.timedelta(hours=h, minutes=m, seconds=s)
    clock = re.search(r'(?:try again at|resets(?:\s+at)?)\s+(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?\b', message, re.I)
    if not clock:
        return None
    hour, minute, period = clock.groups()
    hour, minute = int(hour), int(minute or 0)
    if minute > 59 or hour > 23 or (period and not 1 <= hour <= 12):
        return None
    if period:
        hour = hour % 12 + (12 if period.lower() == 'pm' else 0)
    result = reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if result <= reference:
        result += dt.timedelta(days=1)
    return result


def read_json(path, default=None):
    path = safe_path(path)
    return json.loads(path.read_text()) if path.exists() else default


def atomic_json(path, value):
    path = safe_path(path)
    temporary = safe_path(path.with_name(path.name + '.tmp'))
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    os.replace(str(temporary), str(path))


@contextmanager
def supervisor_lock(root):
    directory = safe_path(Path(root) / '.workspace/orchestration')
    directory.mkdir(parents=True, exist_ok=True)
    with safe_path(directory / 'supervisor.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Un superviseur est déjà actif')
        # Close our descriptor only: an in-flight delegate inherits the lock.
        yield stream.fileno()


def process_commands():
    return subprocess.run(
        ['ps', '-axo', 'command='], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ).stdout.splitlines()


class Supervisor:
    def __init__(self, root, clock=None, processes=None, lock_fd=None, stop=None):
        self.root = Path(root).resolve()
        self.clock = clock or dt.datetime.now
        self.processes = processes or process_commands
        self.lock_fd = lock_fd
        self.stop = stop or threading.Event()
        self.directory = safe_path(self.root / '.workspace/orchestration')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / 'supervisor.json'

    def alive(self, task_path):
        # ps does not escape spaces on macOS; inspect the complete suffix too.
        for command in self.processes():
            if 'delegate.sh' in command and (str(task_path) in command or task_path.name in command):
                return True
            python = re.search(r'(?:^|\s)(?:\S*/)?python(?:3(?:\.\d+)?)?\s+-\s+', command)
            if python and command[python.end() :].strip() == str(self.root) + ' ' + str(task_path):
                return True
        return False

    def execute(self, arguments):
        options = {'pass_fds': (self.lock_fd,)} if self.lock_fd is not None else {}
        child = subprocess.Popen(arguments, cwd=str(self.root), **options)
        while child.poll() is None:
            if self.stop.wait(0.05):
                return 'encore en cours, verrou conservé par la délégation'
        return child.returncode

    def once(self, dry_run=False):  # noqa: C901, PLR0915
        now = self.clock()
        memory = read_json(self.state_path, {'tasks': {}, 'orphans': {}})
        rows = []

        def record(rule, task_id, action, reason, **extra):
            row = dict(timestamp=now.isoformat(), rule=rule, task=task_id, action=action, reason=reason, dry_run=dry_run, **extra)
            rows.append(row)
            with safe_path(self.directory / 'supervisor.log').open('a') as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            print('{rule} {task}: {action} — {reason}'.format(**row), flush=True)

        tasks = {}
        registry = safe_path(self.directory / 'tasks')
        for directory in sorted(registry.iterdir()) if registry.exists() else []:
            try:
                if not re.fullmatch('[a-z0-9-]+', directory.name):
                    continue
                state = read_json(directory / 'state.json', {})
                task_id = directory.name
                source = safe_path(self.root / state['source_task'])
                source.relative_to(self.root)
                tasks[task_id] = source
                if state.get('status') in ('merged', 'abandoned'):
                    worktree = safe_path(
                        workdir.state_worktree(self.root, state) if state.get('worktree') else self.root / '.workspace/worktrees' / task_id
                    )
                    if worktree.exists():
                        record('worktree', task_id, 'report', 'Tâche close avec worktree présent')
                    continue
                run = safe_path(self.root / '.workspace/run/delegate' / task_id)
                status = read_json(run / 'status.json', {})
                if status.get('outcome') != 'failed':
                    continue
                events = safe_path(run / 'events')
                message = events.read_text(errors='replace') if events.exists() else ''
                if not re.search(r'usage limit|Individual quota|Resets in|hit your session limit', message, re.I):
                    continue
                identity = hashlib.sha256((json.dumps(status, sort_keys=True) + message).encode()).hexdigest()
                entry = memory['tasks'].setdefault(task_id, {'retries': 0})
                if entry.get('blockage') != identity:
                    reference = dt.datetime.fromisoformat(status['ended_at'].replace('Z', '+00:00')).astimezone()
                    if now.tzinfo is None:
                        reference = reference.replace(tzinfo=None)
                    else:
                        reference = reference.astimezone(now.tzinfo)
                    retry = retry_time(message, reference)
                    entry.update(blockage=identity, retry_at=retry.isoformat() if retry else None, retried=False)
                retry = dt.datetime.fromisoformat(entry['retry_at']) if entry['retry_at'] else None
                task = read_task(source)
                reason = 'Quota sans heure de reprise connue' if retry is None else 'Reprise possible à ' + retry.isoformat()
                action = 'report'
                if retry and now >= retry and task['auto_retry'] == 'on' and not entry['retried'] and entry['retries'] < 2:
                    if self.alive(source):
                        reason = 'Délégation déjà active'
                    else:
                        action = 'would_retry' if dry_run else 'retry'
                        if not dry_run:
                            # Persist before launching: a crash cannot duplicate a retry.
                            entry['retried'] = True
                            entry['retries'] += 1
                            atomic_json(self.state_path, memory)
                            record('quota', task_id, action, reason, retry_at=entry['retry_at'])
                            code = self.execute([str(self.root / 'scripts/delegate.sh'), str(source)])
                            record('quota', task_id, 'result', 'delegate.sh terminé : ' + str(code))
                            continue
                elif task['auto_retry'] != 'on':
                    reason += '; auto_retry off'
                elif entry['retries'] >= 2:
                    reason += '; plafond de 2 relances atteint'
                elif entry['retried']:
                    reason += '; blocage déjà relancé'
                record('quota', task_id, action, reason, retry_at=entry['retry_at'])
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                record('safety', directory.name, 'report', str(error))

        try:
            project = get_project_fingerprint(self.root)
            listing = subprocess.run(
                [
                    'docker',
                    'ps',
                    '--filter',
                    'label=workspace.role=sandbox',
                    '--filter',
                    'label=workspace.project=' + project,
                    '--format',
                    '{{json .}}',
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            seen = set()
            for line in listing.stdout.splitlines():
                container = json.loads(line)
                labels = dict(item.split('=', 1) for item in container.get('Labels', '').split(',') if '=' in item)
                if labels.get('workspace.project') != project:
                    continue
                task_id = labels.get('workspace.task_id')
                if labels.get('workspace.role') != 'sandbox' or task_id not in tasks:
                    record('orphan', task_id, 'report', 'Conteneur sans tâche locale connue')
                    continue
                # sandbox.sh addresses this exact name, never an arbitrary ID.
                expected_name = get_container_name(self.root, task_id)
                if container.get('Names', expected_name) != expected_name:
                    record('orphan', task_id, 'report', 'Nom de conteneur incohérent')
                    continue
                key = container['ID']
                seen.add(key)
                if self.alive(tasks[task_id]):
                    memory['orphans'].pop(key, None)
                    record('orphan', task_id, 'keep', 'Délégation active')
                    continue
                since = memory['orphans'].setdefault(key, now.isoformat())
                if (now - dt.datetime.fromisoformat(since)).total_seconds() > 600:
                    record('orphan', task_id, 'would_stop' if dry_run else 'stop', 'Aucune délégation depuis plus de 10 minutes')
                    if not dry_run:
                        code = self.execute([str(self.root / 'scripts/sandbox.sh'), 'stop', task_id])
                        record('orphan', task_id, 'result', 'sandbox.sh terminé : ' + str(code))
                        if code == 0:
                            memory['orphans'].pop(key, None)
                else:
                    record('orphan', task_id, 'wait', 'Délai de grâce de 10 minutes')
            memory['orphans'] = {k: v for k, v in memory['orphans'].items() if k in seen}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            record('orphan', None, 'report', 'Inspection impossible : ' + str(error))
        if not dry_run:
            atomic_json(self.state_path, memory)
        print('Superviseur : {} décision(s){}'.format(len(rows), ' (simulation)' if dry_run else ''), flush=True)
        return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('mode', choices=('once', 'watch'))
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error('--interval doit être positif')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        with supervisor_lock(args.root) as descriptor:
            supervisor = Supervisor(args.root, lock_fd=descriptor, stop=stop)
            while not stop.is_set():
                supervisor.once(args.dry_run)
                if args.mode == 'once':
                    break
                stop.wait(args.interval)
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print('supervise.sh : ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
