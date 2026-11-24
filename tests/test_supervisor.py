import datetime as dt
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'template/scripts/lib/supervisor.py'


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'scripts').mkdir()
        self.directory = self.root / '.workspace/orchestration/tasks/demo'
        self.directory.mkdir(parents=True)
        self.task = self.root / 'task with spaces.md'
        self.write_task()
        self.run = self.root / '.workspace/run/delegate/demo'
        self.run.mkdir(parents=True)
        self.status = dict(outcome='failed', started_at='2026-10-17T10:00:00+00:00', ended_at='2026-10-17T10:00:00+00:00')
        (self.run / 'status.json').write_text(json.dumps(self.status))
        (self.run / 'events').write_text('usage limit reached; try again at 11:00 AM')
        (self.directory / 'state.json').write_text(
            json.dumps(dict(task_id='demo', source_task=self.task.name, status='failed', worktree='.workspace/worktrees/demo'))
        )
        self.calls = self.root / 'calls'
        for name in ('delegate.sh', 'sandbox.sh'):
            p = self.root / 'scripts' / name
            p.write_text('#!/bin/sh\necho "$@" >> "' + str(self.calls) + '"\n')
            p.chmod(0o755)
        spec = importlib.util.spec_from_file_location('supervisor', LIB)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.now = dt.datetime.fromisoformat('2026-10-17T10:00:00+00:00')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        docker = self.bin / 'docker'
        docker.write_text("""#!/usr/bin/env python3
import json
from pathlib import Path
import sys
filters = [sys.argv[i + 1][6:] for i, arg in enumerate(sys.argv) if arg == '--filter' and sys.argv[i + 1].startswith('label=')]
for line in Path(__file__).resolve().parents[1].joinpath('containers').read_text().splitlines():
    row = json.loads(line)
    labels = row.get('Labels', '').split(',')
    if all(value in labels for value in filters):
        print(line)
""")
        docker.chmod(0o755)
        ps = self.bin / 'ps'
        ps.write_text('#!/bin/sh\nexit 0\n')
        ps.chmod(0o755)
        (self.root / 'containers').write_text('')
        self.oldpath = os.environ['PATH']
        os.environ['PATH'] = str(self.bin) + os.pathsep + self.oldpath
        self.addCleanup(os.environ.__setitem__, 'PATH', self.oldpath)

    def write_task(self, retry='on'):
        self.task.write_text('---\nid: demo\nagent: codex\nallowed: [src/**]\nauto_retry: ' + retry + '\n---\nTask\n')

    def once(self, dry=False, alive=False):
        return self.module.Supervisor(
            self.root,
            clock=lambda: self.now,
            processes=lambda: [str(self.root / 'scripts/delegate.sh') + ' ' + str(self.task)] if alive else [],
        ).once(dry)

    def test_limit_phrases(self):
        cases = [
            ('usage limit reached try again at 11:30 AM', '2026-10-17T11:30:00+00:00'),
            ('usage limit reached try again at 09:30', '2026-10-18T09:30:00+00:00'),
            ('usage limit reached try again at 12:30 PM', '2026-10-17T12:30:00+00:00'),
            ('Resets in 1h2m3s', '2026-10-17T11:02:03+00:00'),
            ('Individual quota', None),
            ('hit your session limit · resets 2pm', '2026-10-17T14:00:00+00:00'),
        ]
        for message, expected in cases:
            with self.subTest(message=message):
                value = self.module.retry_time(message, self.now)
                self.assertEqual(value.isoformat() if value else None, expected)

    def test_retry_once_per_blockage_and_ceiling(self):
        self.once()
        self.assertFalse(self.calls.exists())
        self.now += dt.timedelta(hours=2)
        self.once()
        self.once()
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)
        for _number in (2, 3):
            self.status['ended_at'] = self.now.isoformat()
            (self.run / 'status.json').write_text(json.dumps(self.status))
            (self.run / 'events').write_text('Resets in 1s')
            self.once()
            self.now += dt.timedelta(seconds=2)
            self.once()
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_opt_in_dry_run_and_active(self):
        self.once()
        self.now += dt.timedelta(hours=2)
        self.write_task('off')
        self.once()
        self.write_task()
        self.once(dry=True)
        self.once(alive=True)
        self.assertFalse(self.calls.exists())
        self.once()
        self.assertTrue(self.calls.exists())

    def test_orphan_grace_and_active_container(self):
        (self.root / 'containers').write_text(
            json.dumps(
                {
                    'ID': 'abc',
                    'Labels': 'workspace.role=sandbox,workspace.task_id=demo,workspace.project='
                    + hashlib.sha256(str(self.root).encode()).hexdigest()[:10],
                }
            )
            + '\n'
        )
        self.write_task('off')
        self.once()
        self.now += dt.timedelta(minutes=11)
        self.once(alive=True)
        self.assertFalse(self.calls.exists())
        self.once()
        self.now += dt.timedelta(minutes=11)
        self.once()
        self.assertEqual(self.calls.read_text().strip(), 'stop demo')

    def test_other_project_containers_are_never_reported_or_stopped(self):
        self.write_task('off')
        foreign = hashlib.sha256(str(self.root / 'other').encode()).hexdigest()[:10]
        containers = [
            {
                'ID': task,
                'Names': 'workspace-sandbox-' + foreign + '-' + task,
                'Labels': 'workspace.role=sandbox,workspace.task_id=' + task + ',workspace.project=' + foreign,
            }
            for task in ('demo', 'unknown')
        ]
        (self.root / 'containers').write_text(''.join(json.dumps(row) + '\n' for row in containers))
        for _ in range(2):
            rows = self.once()
            self.assertFalse(any(row['rule'] == 'orphan' for row in rows))
            self.now += dt.timedelta(minutes=11)
        self.assertFalse(self.calls.exists())
        memory = json.loads((self.directory.parent.parent / 'supervisor.json').read_text())
        self.assertEqual(memory['orphans'], {})

    def test_lock(self):
        with self.module.supervisor_lock(self.root):
            with self.assertRaises(RuntimeError):
                with self.module.supervisor_lock(self.root):
                    pass

    def test_closed_worktree_report(self):
        state = json.loads((self.directory / 'state.json').read_text())
        state['status'] = 'merged'
        (self.directory / 'state.json').write_text(json.dumps(state))
        (self.root / state['worktree']).mkdir(parents=True)
        self.assertTrue(any(row['rule'] == 'worktree' for row in self.once()))
        self.assertFalse(self.calls.exists())

    def test_python_delegate_identity(self):
        supervisor = self.module.Supervisor(self.root, processes=lambda: ['python3 - ' + str(self.root) + ' ' + str(self.task)])
        self.assertTrue(supervisor.alive(self.task))
        supervisor.processes = lambda: ['python3 - /another/root ' + str(self.task)]
        self.assertFalse(supervisor.alive(self.task))

    def test_dry_run_orphan_preserves_state(self):
        self.write_task('off')
        (self.root / 'containers').write_text(
            json.dumps(
                {
                    'ID': 'abc',
                    'Labels': 'workspace.role=sandbox,workspace.task_id=demo,workspace.project='
                    + hashlib.sha256(str(self.root).encode()).hexdigest()[:10],
                }
            )
            + '\n'
        )
        self.once()
        state = (self.directory.parent.parent / 'supervisor.json').read_bytes()
        self.now += dt.timedelta(minutes=11)
        self.assertTrue(any(r['action'] == 'would_stop' for r in self.once(dry=True)))
        self.assertEqual((self.directory.parent.parent / 'supervisor.json').read_bytes(), state)
        self.assertFalse(self.calls.exists())

    def test_success_ignores_old_quota(self):
        self.status['outcome'] = 'done'
        (self.run / 'status.json').write_text(json.dumps(self.status))
        self.now += dt.timedelta(hours=3)
        self.once()
        self.assertFalse(self.calls.exists())

    def test_watch_signal_and_cli_lock(self):
        import signal
        import time

        command = ['python3', str(LIB), str(self.root), 'watch', '--interval', '60']
        watch = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: watch.poll() is None and watch.kill())
        deadline = time.monotonic() + 5
        while not (self.directory.parent.parent / 'supervisor.json').exists():
            if time.monotonic() > deadline:
                self.fail('watch did not start')
            time.sleep(0.02)
        blocked = subprocess.run(['python3', str(LIB), str(self.root), 'once'], capture_output=True, text=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn('déjà actif', blocked.stderr)
        watch.send_signal(signal.SIGTERM)
        watch.communicate(timeout=5)
        self.assertEqual(watch.returncode, 0)

    def test_stop_during_retry_keeps_child_lock(self):
        import signal
        import time

        delegate = self.root / 'scripts/delegate.sh'
        delegate.write_text('#!/bin/sh\nsleep 3\n')
        self.status['ended_at'] = (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)).isoformat()
        (self.run / 'status.json').write_text(json.dumps(self.status))
        (self.run / 'events').write_text('Resets in 1s')
        command = ['python3', str(LIB), str(self.root), 'watch']
        watch = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: watch.poll() is None and watch.kill())
        deadline = time.monotonic() + 5
        while True:
            path = self.directory.parent.parent / 'supervisor.json'
            if path.exists() and json.loads(path.read_text())['tasks'].get('demo', {}).get('retried'):
                break
            if time.monotonic() > deadline:
                self.fail('retry did not start')
            time.sleep(0.02)
        time.sleep(0.1)
        watch.send_signal(signal.SIGTERM)
        watch.wait(timeout=1)
        blocked = subprocess.run(['python3', str(LIB), str(self.root), 'once'], capture_output=True, text=True)
        self.assertNotEqual(blocked.returncode, 0)
        watch.communicate(timeout=5)

    def test_process_inspection_failure_blocks_actions(self):
        self.once()
        self.now += dt.timedelta(hours=2)
        (self.root / 'containers').write_text(
            json.dumps(
                {
                    'ID': 'abc',
                    'Labels': 'workspace.role=sandbox,workspace.task_id=demo,workspace.project='
                    + hashlib.sha256(str(self.root).encode()).hexdigest()[:10],
                }
            )
            + '\n'
        )

        def unavailable():
            raise OSError('ps unavailable')

        supervisor = self.module.Supervisor(self.root, clock=lambda: self.now, processes=unavailable)
        rows = supervisor.once()
        self.assertFalse(self.calls.exists())
        self.assertTrue(any('ps unavailable' in row['reason'] for row in rows))
