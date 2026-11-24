import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'template/scripts/lib'))
import breaker
import correction

BREAKER_TEST = '__breaker__/test_hole.py'
PASSING = 'import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n'


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.root, self.wt = base / 'root', base / 'wt'
        self.root.mkdir()
        self.wt.mkdir()
        self.git('init', '-q')
        (self.wt / 'tracked.txt').write_text('base\n')
        (self.wt / 'gone.txt').write_text('gone\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.task = {'id': 'demo', 'acceptance': []}
        self.dest = self.root / '.workspace/orchestration/tasks/demo' / correction.SNAPSHOT_DIR

    def git(self, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
        return subprocess.run(['git', '-C', str(self.wt), *args], check=True, env=env, text=True, stdout=subprocess.PIPE).stdout

    def manifest(self, name, *names):
        directory = self.root / '.workspace/acceptance/demo'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text('\n'.join(names) + '\n')

    def status(self, **fields):
        directory = self.root / '.workspace/run/delegate/demo'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'status.json').write_text(json.dumps(fields))

    def events(self):
        path = self.root / '.workspace/events.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def state_before_round(self):
        (self.wt / 'tracked.txt').write_text('good\n')
        (self.wt / 'early.txt').write_text('early\n')
        (self.wt / 'gone.txt').unlink()

    def break_it(self):
        (self.wt / 'tracked.txt').write_text('broken\n')
        (self.wt / 'late.txt').write_text('late\n')
        (self.wt / 'early.txt').unlink()
        (self.wt / 'gone.txt').write_text('back\n')

    def assert_restored(self):
        self.assertEqual((self.wt / 'tracked.txt').read_text(), 'good\n')
        self.assertEqual((self.wt / 'early.txt').read_text(), 'early\n')
        self.assertFalse((self.wt / 'late.txt').exists())
        self.assertFalse((self.wt / 'gone.txt').exists())

    def test_snapshot_then_restore(self):
        self.state_before_round()
        correction.snapshot(self.wt, self.base, self.dest)
        self.assertEqual(self.dest.stat().st_mode & 0o777, 0o700)
        manifest = json.loads((self.dest / 'manifest.json').read_text())
        self.assertEqual(manifest, {'base': self.base, 'files': ['early.txt', 'tracked.txt'], 'deleted': ['gone.txt']})
        self.assertEqual((self.dest / 'files/tracked.txt').read_text(), 'good\n')
        self.break_it()
        correction.restore(self.wt, self.base, self.dest)
        self.assert_restored()

    def test_snapshot_empties_existing_destination(self):
        self.dest.mkdir(parents=True)
        (self.dest / 'old.txt').write_text('x')
        correction.snapshot(self.wt, self.base, self.dest)
        self.assertFalse((self.dest / 'old.txt').exists())

    def test_snapshot_refuses_symlink(self):
        (self.wt / 'link').symlink_to('tracked.txt')
        with self.assertRaises(ValueError):
            correction.snapshot(self.wt, self.base, self.dest)

    def test_restore_other_base_or_no_manifest_leaves_worktree(self):
        with self.assertRaises(ValueError):
            correction.restore(self.wt, self.base, self.dest)
        self.state_before_round()
        correction.snapshot(self.wt, self.base, self.dest)
        self.break_it()
        with self.assertRaises(ValueError):
            correction.restore(self.wt, 'f' * 40, self.dest)
        self.assertEqual((self.wt / 'tracked.txt').read_text(), 'broken\n')
        self.assertTrue((self.wt / 'late.txt').exists())

    def test_restore_git_failure(self):
        manifest = {'base': 'f' * 40, 'files': [], 'deleted': []}
        self.dest.mkdir(parents=True)
        (self.dest / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(RuntimeError):
            correction.restore(self.wt, 'f' * 40, self.dest)

    def test_is_correction_round(self):
        self.assertFalse(correction.is_correction_round(self.root, self.task))
        self.status(outcome='done')
        self.assertFalse(correction.is_correction_round(self.root, self.task))
        self.manifest('extra.txt', BREAKER_TEST)
        self.assertTrue(correction.is_correction_round(self.root, self.task))
        self.status(outcome='done_with_warning')
        self.assertTrue(correction.is_correction_round(self.root, self.task))
        self.status(outcome='acceptance_failed')
        self.assertFalse(correction.is_correction_round(self.root, self.task))
        (self.root / '.workspace/run/delegate/demo/status.json').write_text('{')
        self.assertFalse(correction.is_correction_round(self.root, self.task))

    def test_is_correction_round_with_instruments(self):
        self.status(outcome='done')
        self.manifest('instrument.txt', BREAKER_TEST)
        self.assertTrue(correction.is_correction_round(self.root, self.task))

    def test_round_keeping_original_tests_green_reverts_nothing(self):
        self.manifest('extra.txt', BREAKER_TEST)
        self.state_before_round()
        correction.snapshot(self.wt, self.base, self.dest)
        result = correction.run_round(self.root, self.task, self.wt, self.base, lambda: (self.break_it(), 'done')[1], lambda: (0, ''))
        self.assertEqual(result, {'reverted': False, 'outcome': 'done', 'unresolved': []})
        self.assertEqual((self.wt / 'tracked.txt').read_text(), 'broken\n')
        self.assertEqual(self.events(), [])

    def test_round_breaking_original_tests_is_reverted(self):
        self.manifest('extra.txt', BREAKER_TEST)
        self.manifest('instrument.txt', '__breaker__/test_other.py')
        self.state_before_round()
        correction.snapshot(self.wt, self.base, self.dest)
        result = correction.run_round(self.root, self.task, self.wt, self.base, lambda: (self.break_it(), 'done')[1], lambda: (1, 'rouge'))
        names = [BREAKER_TEST, '__breaker__/test_other.py']
        self.assertEqual(result, {'reverted': True, 'outcome': 'done_with_warning', 'unresolved': names})
        self.assert_restored()
        found = [e for e in self.events() if e['event'] == 'correction_reverted']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['task_id'], 'demo')
        self.assertEqual(found[0]['detail'], {'unresolved': names, 'outcome': 'done', 'cause': 'implementation'})

    def test_unresolved(self):
        self.assertEqual(correction.unresolved(self.root, 'demo'), [])
        self.status(outcome='done_with_warning')
        self.assertEqual(correction.unresolved(self.root, 'demo'), [])
        self.status(outcome='done_with_warning', correction_reverted={'unresolved': ['a', 'b'], 'round_outcome': 'failed'})
        self.assertEqual(correction.unresolved(self.root, 'demo'), ['a', 'b'])

    def test_run_acceptance_skip(self):
        second = '__breaker__/test_second.py'
        self.manifest('extra.txt', BREAKER_TEST, second)
        breaker_dir = self.root / '.workspace/acceptance/demo/breaker/__breaker__'
        breaker_dir.mkdir(parents=True)
        (breaker_dir / 'test_hole.py').write_text(PASSING)
        (breaker_dir / 'test_second.py').write_text(PASSING)
        launched = []

        def execute(args, cwd=None):
            launched.append(args[-1])
            return 0, json.dumps({'breaker_result': True, 'tests': 1, 'failures': 0, 'errors': 0, 'skipped': 0}), ''

        task = {'id': 'demo', 'acceptance': []}
        self.assertEqual(breaker.run_acceptance(task, self.root, self.wt, execute), (0, ''))
        self.assertEqual(launched, [BREAKER_TEST, second])
        del launched[:]
        self.assertEqual(breaker.run_acceptance(task, self.root, self.wt, execute, skip=[BREAKER_TEST]), (0, ''))
        self.assertEqual(launched, [second])
        self.assertFalse((self.wt / '__breaker__').exists())


if __name__ == '__main__':
    unittest.main()
