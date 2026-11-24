import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'template/scripts/lib'))
import events
import selfcheck

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel ; suite complète seulement')


class SelfcheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.git(self.root, 'init', '-q')
        (self.root / 'tracked.txt').write_text('before\n')
        self.git(self.root, 'add', 'tracked.txt')
        self.git(self.root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
        self.base = self.git(self.root, 'rev-parse', 'HEAD').strip()
        self.worktree = self.root / 'wt'
        self.git(self.root, 'worktree', 'add', '-q', '--detach', str(self.worktree), self.base)
        self.state = self.root / '.workspace/orchestration/tasks/demo/state.json'
        self.state.parent.mkdir(parents=True)
        self.state.write_text(json.dumps({'task_id': 'demo', 'base_commit': self.base}))

    @staticmethod
    def git(cwd, *args):
        return subprocess.run(['git', '-C', str(cwd), *args], check=True, stdout=subprocess.PIPE, text=True).stdout

    def test_at_base_true_on_fresh_worktree(self):
        self.assertTrue(selfcheck.at_base(self.root, 'demo', self.worktree))

    def test_at_base_false_after_tracked_modification(self):
        (self.worktree / 'tracked.txt').write_text('changed\n')
        self.assertFalse(selfcheck.at_base(self.root, 'demo', self.worktree))

    def test_at_base_false_with_untracked_file_in_new_directory(self):
        (self.worktree / 'sub').mkdir()
        (self.worktree / 'sub/new.txt').write_text('x')
        self.assertFalse(selfcheck.at_base(self.root, 'demo', self.worktree))

    def test_at_base_false_after_new_commit(self):
        (self.worktree / 'tracked.txt').write_text('changed\n')
        self.git(self.worktree, 'add', 'tracked.txt')
        self.git(self.worktree, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'work')
        self.assertFalse(selfcheck.at_base(self.root, 'demo', self.worktree))

    def test_at_base_false_when_state_unreadable_or_without_base(self):
        self.state.write_text('{not json')
        self.assertFalse(selfcheck.at_base(self.root, 'demo', self.worktree))
        self.state.write_text(json.dumps({'task_id': 'demo'}))
        self.assertFalse(selfcheck.at_base(self.root, 'demo', self.worktree))
        self.assertFalse(selfcheck.at_base(self.root, 'absent', self.worktree))

    def test_run_records_event_and_returns_code(self):
        task = {'id': 'demo', 'acceptance': ['hidden.py'], 'acceptance_verify': 'python3 hidden.py'}
        seen = []
        original = selfcheck.breaker.run_acceptance
        self.addCleanup(setattr, selfcheck.breaker, 'run_acceptance', original)
        for code in (0, 2):
            selfcheck.breaker.run_acceptance = lambda *args, code=code: seen.append(args) or (code, 'feedback')
            self.assertEqual(selfcheck.run(self.root, task, self.worktree, 'execute'), code)
        self.assertEqual(seen[0], (task, self.root, self.worktree, 'execute', True))
        recorded = [e for e in events.read(self.root, task_id='demo') if e['event'] == 'selfcheck']
        self.assertEqual([e['detail'] for e in recorded], [{'code': 0}, {'code': 2}])

    def prepare_selfcheck(self, code, hidden=True):
        (self.root / 'scripts').mkdir(exist_ok=True)
        sandbox = self.root / 'scripts/sandbox.sh'
        sandbox.write_text('#!/bin/sh\necho "$@" >> "$(dirname "$0")/sandbox.log"\n')
        sandbox.chmod(0o755)
        self.state.write_text(json.dumps({'task_id': 'demo', 'base_commit': self.base, 'worktree': 'wt', 'source_task': 'demo.md'}))
        (self.root / 'demo.md').write_text('---\n---\n')
        task = {'id': 'demo'}
        patches = [
            mock.patch.object(selfcheck, 'read_task', return_value=task),
            mock.patch.object(selfcheck.breaker, 'has_acceptance', return_value=hidden),
            mock.patch.object(selfcheck, 'run', return_value=code),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            result = selfcheck.prepare_selfcheck(self.root, 'demo')
        calls = (self.root / 'scripts/sandbox.log').read_text().splitlines() if hidden else []
        return result, out.getvalue(), err.getvalue(), calls

    def test_prepare_selfcheck_vacuous_test_exits_3_and_stops_container(self):
        result, out, err, calls = self.prepare_selfcheck(0)
        self.assertEqual(result, 3)
        self.assertIn(selfcheck.MESSAGE, err)
        self.assertEqual(calls, ['start demo', 'stop demo'])

    def test_prepare_selfcheck_failing_test_allows_delegation(self):
        result, out, err, calls = self.prepare_selfcheck(1)
        self.assertEqual(result, 0)
        self.assertIn('selfcheck : le test caché échoue sur la base (code 1), délégation possible', out)
        self.assertEqual(calls, ['start demo', 'stop demo'])

    def test_prepare_selfcheck_without_hidden_test_does_not_start_container(self):
        result, out, err, calls = self.prepare_selfcheck(0, hidden=False)
        self.assertEqual(result, 0)
        self.assertFalse((self.root / 'scripts/sandbox.log').exists())

    def test_message_names_the_outcome_and_the_override(self):
        self.assertTrue(selfcheck.MESSAGE.startswith('acceptance_vacuous : '))
        self.assertIn('WORKSPACE_NO_SELFCHECK=1', selfcheck.MESSAGE)


if __name__ == '__main__':
    unittest.main()
