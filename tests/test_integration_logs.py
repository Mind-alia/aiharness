import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'template/scripts/lib'
sys.path.insert(0, str(LIB))

import integration_logs  # noqa: E402
import workdir  # noqa: E402

TEST_TMP = Path(os.environ.get('WORKSPACE_TEST_TMP') or ROOT / '.workspace/tmp-tests')
TEST_TMP.mkdir(parents=True, exist_ok=True)
SECRET = 'SECRET_VALUE_1234'


class KeepUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'project'
        self.integration = self.root / '.workspace/integration/task-1'
        self.integration.mkdir(parents=True)

    def read_folder(self, name):
        return (integration_logs.folder(self.root, 'task-1') / name).read_text(encoding='utf-8')

    def test_copies_gate_logs_masked_and_full_output(self):
        (self.integration / '.gate-unit.log').write_text('step ' + SECRET + '\n')
        (self.integration / 'other.log').write_text('not a gate log\n')
        integration_logs.keep(self.root, 'task-1', self.integration, 'out ' + SECRET + '\n', [SECRET])
        self.assertEqual(
            sorted(p.name for p in integration_logs.folder(self.root, 'task-1').iterdir()), ['.gate-unit.log', 'gate-output.log']
        )
        self.assertEqual(self.read_folder('.gate-unit.log'), 'step ***\n')
        self.assertEqual(self.read_folder('gate-output.log'), 'out ***\n')

    def test_permissions_are_0700_directories_and_0600_files(self):
        (self.integration / '.gate-unit.log').write_text('ok\n')
        integration_logs.keep(self.root, 'task-1', self.integration, 'ok\n', [])
        for directory in [
            self.root / '.workspace/orchestration',
            self.root / '.workspace/orchestration/tasks',
            integration_logs.folder(self.root, 'task-1'),
        ]:
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700, str(directory))
        for name in ['.gate-unit.log', 'gate-output.log']:
            path = integration_logs.folder(self.root, 'task-1') / name
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, name)

    def test_existing_files_are_replaced(self):
        (self.integration / '.gate-unit.log').write_text('first\n')
        integration_logs.keep(self.root, 'task-1', self.integration, 'first\n', [])
        (self.integration / '.gate-unit.log').write_text('second\n')
        integration_logs.keep(self.root, 'task-1', self.integration, 'second\n', [])
        self.assertEqual(self.read_folder('.gate-unit.log'), 'second\n')
        self.assertEqual(self.read_folder('gate-output.log'), 'second\n')

    def test_gate_log_keeps_only_last_five_mebibytes(self):
        size = integration_logs.KEEP_BYTES
        (self.integration / '.gate-big.log').write_bytes(b'x' * (size + 100) + b'END')
        integration_logs.keep(self.root, 'task-1', self.integration, '', [])
        kept = (integration_logs.folder(self.root, 'task-1') / '.gate-big.log').read_bytes()
        self.assertEqual(len(kept), size)
        self.assertTrue(kept.endswith(b'END'))

    def test_symbolic_links_and_subdirectories_are_not_copied(self):
        outside = self.root.parent / 'outside.txt'
        outside.write_text('leak ' + SECRET + '\n')
        (self.integration / '.gate-link.log').symlink_to(outside)
        (self.integration / '.gate-dir.log').mkdir()
        integration_logs.keep(self.root, 'task-1', self.integration, 'out\n', [SECRET])
        self.assertEqual([p.name for p in integration_logs.folder(self.root, 'task-1').iterdir()], ['gate-output.log'])

    def test_listing_is_sorted_relative_and_empty_without_folder(self):
        self.assertEqual(integration_logs.listing(self.root, 'task-1'), [])
        (self.integration / '.gate-b.log').write_text('b\n')
        (self.integration / '.gate-a.log').write_text('a\n')
        integration_logs.keep(self.root, 'task-1', self.integration, 'out\n', [])
        self.assertEqual(
            integration_logs.listing(self.root, 'task-1'),
            [
                "journal d'intégration : .workspace/orchestration/tasks/task-1/integration/.gate-a.log",
                "journal d'intégration : .workspace/orchestration/tasks/task-1/integration/.gate-b.log",
                "journal d'intégration : .workspace/orchestration/tasks/task-1/integration/gate-output.log",
            ],
        )

    def test_unsafe_folder_is_refused_without_writing(self):
        target = Path(self.temp.name) / 'elsewhere'
        target.mkdir()
        (self.root / '.workspace/orchestration/tasks/task-1').mkdir(parents=True)
        (self.root / '.workspace/orchestration/tasks/task-1/integration').symlink_to(target)
        with self.assertRaises(ValueError):
            integration_logs.keep(self.root, 'task-1', self.integration, 'out\n', [])
        self.assertEqual(list(target.iterdir()), [])


class MergeIntegrationLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.temp.cleanup)
        # Les clones de tâche vont hors du dépôt, jamais dans le vrai ~/.workspace-ia.
        self.work_dir = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.work_dir.cleanup)
        patcher = mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': os.path.realpath(self.work_dir.name)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = (Path(self.temp.name) / 'project').resolve()
        self.root.mkdir()
        self.outside = Path(self.temp.name) / 'outside.txt'
        self.outside.write_text('leak\n')
        self.scripts_dir = self.root / 'scripts'
        (self.scripts_dir / 'lib').mkdir(parents=True)
        for lib_file in (ROOT / 'template/scripts/lib').glob('*.py'):
            shutil.copy2(lib_file, self.scripts_dir / 'lib' / lib_file.name)
        for name in ('task-workspace.sh', 'report.sh', 'verify-task.sh', 'sandbox.sh'):
            shutil.copy2(ROOT / 'template/scripts' / name, self.scripts_dir / name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.root / '.gitignore').write_text('.workspace/\ntasks/\n.gate-*.log\n.env\n')
        (self.root / '.env').write_text('API_TOKEN=' + SECRET + '\n')
        (self.root / 'src').mkdir()
        (self.root / 'src/app.py').write_text('print("initial")\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial commit')

    def git(self, *args, cwd=None):
        return subprocess.check_output(
            [
                'git',
                '-C',
                str(cwd or self.root),
                '-c',
                'user.name=Test',
                '-c',
                'user.email=test@example.invalid',
                '-c',
                'core.hooksPath=',
                *args,
            ],
            text=True,
        )

    def run_cli(self, *args, env=None):
        run_env = dict(os.environ, **(env or {}))
        return subprocess.run(
            ['bash', str(self.scripts_dir / 'task-workspace.sh'), *args], cwd=str(self.root), capture_output=True, text=True, env=run_env
        )

    def merge_with_gate(self, gate, args=(), env=None, before_merge=None):
        (self.scripts_dir / 'gate.sh').write_text('#!/bin/sh\n' + gate + '\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'verification and integration gate')
        task = self.root / 'tasks/task-1.md'
        task.parent.mkdir(exist_ok=True)
        task.write_text(
            '---\nid: task-1\nagent: codex\nallowed:\n  - src/**\nverify: test -f src/app.py\n'
            'verify_profile: off\nisolation: none\n---\nConsigne test\n'
        )
        self.assertEqual(self.run_cli('prepare', str(task)).returncode, 0)
        worktree = workdir.task_worktree(self.root, 'task-1')
        (worktree / 'src/app.py').write_text('changed\n')
        self.git('add', '.', cwd=worktree)
        self.git('commit', '-qm', 'feat: task', cwd=worktree)
        if before_merge:
            before_merge()
        return self.run_cli('merge', 'task-1', *args, env=env)

    def folder(self):
        return self.root / '.workspace/orchestration/tasks/task-1/integration'

    def report_lines(self):
        output = subprocess.run(
            ['bash', str(self.scripts_dir / 'report.sh'), '--task', 'task-1'], capture_output=True, text=True, check=True
        ).stdout
        return [line for line in output.splitlines() if line.startswith("journal d'intégration")]

    def assert_no_secret(self):
        for path in self.folder().iterdir():
            self.assertNotIn(SECRET, path.read_text(encoding='utf-8'), path.name)

    def test_green_gate_keeps_logs(self):
        result = self.merge_with_gate('echo ok ' + SECRET + ' > .gate-unit.log; echo "gate " ' + SECRET)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(p.name for p in self.folder().iterdir()), ['.gate-unit.log', 'gate-output.log'])
        self.assert_no_secret()
        self.assertIn('***', (self.folder() / 'gate-output.log').read_text(encoding='utf-8'))
        self.assertEqual(len(self.report_lines()), 2)

    def test_red_gate_keeps_logs_and_state_output(self):
        result = self.merge_with_gate('echo ' + SECRET + ' > .gate-unit.log; echo "boom ' + SECRET + '"; exit 42')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sorted(p.name for p in self.folder().iterdir()), ['.gate-unit.log', 'gate-output.log'])
        self.assert_no_secret()
        state = json.loads((self.root / '.workspace/orchestration/tasks/task-1/state.json').read_text())
        self.assertEqual(state['integration']['exit'], 42)
        self.assertNotIn(SECRET, json.dumps(state))
        self.assertEqual(
            self.report_lines(),
            [
                "journal d'intégration : .workspace/orchestration/tasks/task-1/integration/.gate-unit.log",
                "journal d'intégration : .workspace/orchestration/tasks/task-1/integration/gate-output.log",
            ],
        )

    def test_symbolic_gate_log_is_ignored(self):
        result = self.merge_with_gate('ln -s "$OUTSIDE" .gate-link.log; echo ok', env={'OUTSIDE': str(self.outside)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p.name for p in self.folder().iterdir()], ['gate-output.log'])

    def test_no_integration_copies_nothing(self):
        result = self.merge_with_gate('echo ' + SECRET + ' > .gate-unit.log; exit 42', args=['--no-integration'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.folder().exists())
        self.assertEqual(self.report_lines(), [])

    def test_copy_failure_does_not_change_merge_outcome(self):
        target = Path(self.temp.name) / 'elsewhere'
        target.mkdir()

        def replace_folder_with_link():
            self.folder().symlink_to(target)

        result = self.merge_with_gate('echo ok > .gate-unit.log', before_merge=replace_folder_with_link)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Journaux d'intégration non copiés", result.stderr)
        self.assertEqual(json.loads((self.root / '.workspace/orchestration/tasks/task-1/state.json').read_text())['status'], 'merged')


if __name__ == '__main__':
    unittest.main()
