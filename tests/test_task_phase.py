"""Tests unitaires de template/scripts/lib/task_phase.py (aucun accès réseau)."""

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parents[1] / 'template/scripts/lib'
sys.path.insert(0, str(LIB))

import task_phase  # noqa: E402


class TaskPhaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.directory = self.root / '.workspace/orchestration/tasks/demo'
        self.state = self.directory / 'state.json'

    def put_state(self, data=None):
        self.directory.mkdir(parents=True)
        self.state.write_text(
            json.dumps(data if data is not None else {'task_id': 'demo', 'status': 'prepared', 'allowed': ['a']}), encoding='utf-8'
        )

    def phase_stderr(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            task_phase.set_phase(self.root, *args)
        return err.getvalue()

    def test_writes_phase_and_keeps_other_keys(self):
        self.put_state()
        self.assertEqual(self.phase_stderr('demo', 'delegated'), '')
        self.assertEqual(
            json.loads(self.state.read_text(encoding='utf-8')),
            {'task_id': 'demo', 'status': 'prepared', 'allowed': ['a'], 'phase': 'delegated'},
        )

    def test_replaces_previous_phase(self):
        self.put_state({'task_id': 'demo', 'phase': 'prepared'})
        task_phase.set_phase(self.root, 'demo', 'attempt 2')
        self.assertEqual(json.loads(self.state.read_text(encoding='utf-8'))['phase'], 'attempt 2')

    def test_write_is_atomic_0600_and_leaves_no_temporary_file(self):
        self.put_state()
        old = os.umask(0o022)
        self.addCleanup(os.umask, old)
        with mock.patch('os.replace', wraps=os.replace) as replace:
            task_phase.set_phase(self.root, 'demo', 'verifying')
        source, target = replace.call_args[0]
        self.assertEqual(Path(source).parent, self.directory)
        self.assertEqual(Path(target), self.state)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)
        self.assertEqual([p.name for p in self.directory.iterdir()], ['state.json'])

    def test_failed_replace_keeps_the_old_state(self):
        self.put_state()
        before = self.state.read_text(encoding='utf-8')
        with mock.patch('os.replace', side_effect=PermissionError('refusé')):
            message = self.phase_stderr('demo', 'merging')
        self.assertEqual(message, 'task_phase: refusé\n')
        self.assertEqual(self.state.read_text(encoding='utf-8'), before)
        self.assertEqual([p.name for p in self.directory.iterdir()], ['state.json'])

    def test_missing_state_creates_nothing(self):
        self.assertEqual(self.phase_stderr('demo', 'delegated'), '')
        self.assertFalse((self.root / '.workspace').exists())

    def test_accepted_values(self):
        self.put_state()
        for phase in (
            'prepared',
            'delegated',
            'attempt 1',
            'attempt 12',
            'verifying',
            'acceptance',
            'merging',
            'done',
            'failed',
            'abandoned',
        ):
            with self.subTest(phase=phase):
                task_phase.set_phase(self.root, 'demo', phase)
                self.assertEqual(json.loads(self.state.read_text(encoding='utf-8'))['phase'], phase)

    def test_other_values_are_programming_errors(self):
        self.put_state()
        before = self.state.read_text(encoding='utf-8')
        for phase in ('', 'running', 'attempt', 'attempt 0', 'attempt -1', 'attempt 1.5', 'attempt  1', 'attempt 1\n', 'Done', None, 3):
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                task_phase.set_phase(self.root, 'demo', phase)
        self.assertEqual(self.state.read_text(encoding='utf-8'), before)

    def test_invalid_value_is_refused_even_without_state(self):
        with self.assertRaises(ValueError):
            task_phase.set_phase(self.root, 'demo', 'running')

    def test_symlinked_state_is_refused_without_exception(self):
        target = self.root / 'elsewhere.json'
        target.write_text('{"task_id": "demo"}', encoding='utf-8')
        self.directory.mkdir(parents=True)
        self.state.symlink_to(target)
        self.assertTrue(self.phase_stderr('demo', 'done').startswith('task_phase: '))
        self.assertEqual(target.read_text(encoding='utf-8'), '{"task_id": "demo"}')
        self.assertTrue(self.state.is_symlink())

    def test_symlinked_parent_is_refused_without_exception(self):
        elsewhere = self.root / 'elsewhere'
        (elsewhere / 'demo').mkdir(parents=True)
        (elsewhere / 'demo/state.json').write_text('{"task_id": "demo"}', encoding='utf-8')
        (self.root / '.workspace/orchestration').mkdir(parents=True)
        (self.root / '.workspace/orchestration/tasks').symlink_to(elsewhere)
        self.assertTrue(self.phase_stderr('demo', 'done').startswith('task_phase: '))
        self.assertEqual((elsewhere / 'demo/state.json').read_text(encoding='utf-8'), '{"task_id": "demo"}')
        self.assertEqual(sorted(p.name for p in (elsewhere / 'demo').iterdir()), ['state.json'])

    def test_unreadable_json_is_reported_and_left_untouched(self):
        self.directory.mkdir(parents=True)
        self.state.write_text('{pas du json', encoding='utf-8')
        message = self.phase_stderr('demo', 'failed')
        self.assertTrue(message.startswith('task_phase: '))
        self.assertEqual(message.count('\n'), 1)
        self.assertEqual(self.state.read_text(encoding='utf-8'), '{pas du json')

    def test_non_object_json_is_reported_and_left_untouched(self):
        self.put_state([1, 2])
        self.assertTrue(self.phase_stderr('demo', 'failed').startswith('task_phase: '))
        self.assertEqual(json.loads(self.state.read_text(encoding='utf-8')), [1, 2])


if __name__ == '__main__':
    unittest.main()
