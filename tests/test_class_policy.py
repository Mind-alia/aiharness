import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
sys.dont_write_bytecode = True
import breaker  # noqa: E402
import class_policy  # noqa: E402
from task import read_task  # noqa: E402

TEST_SOURCE = 'import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertEqual(%s, 1)\n'


def write_task(directory, header):
    path = Path(directory) / 'task.md'
    path.write_text('---\nid: demo\nagent: claude-sonnet\nallowed: [a.py]\n' + header + '---\nCorps\n', encoding='utf-8')
    return path


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def task(self, header='', agent=None):
        data = read_task(write_task(self.root, header), self.root)
        if agent:
            data['agent'] = agent
        return data

    def test_constants(self):
        self.assertEqual(class_policy.BREAKER_MODE, {'small': 'none', 'medium': 'instrument', 'large': 'blocking'})
        self.assertEqual(class_policy.SMALL_MAX_ATTEMPTS, 2)

    def test_read_task_keeps_class_raw(self):
        self.assertIsNone(self.task().get('class'))
        self.assertEqual(self.task('class: huge\n')['class'], 'huge')

    def test_undeclared_changes_nothing(self):
        result = class_policy.policy(self.task('attempts: 5\n'), self.root)
        self.assertEqual(set(result), {'class', 'declared', 'attempts', 'breaker', 'refusal'})
        self.assertFalse(result['declared'])
        self.assertEqual((result['attempts'], result['breaker'], result['refusal']), (5, 'blocking', None))
        self.assertIn(result['class'], ('small', 'medium', 'large'))

    def test_small_caps_attempts_and_skips_breaker(self):
        result = class_policy.policy(self.task('class: small\nattempts: 5\n'), self.root)
        self.assertEqual((result['declared'], result['attempts'], result['breaker']), (True, 2, 'none'))
        self.assertEqual(class_policy.policy(self.task('class: small\nattempts: 1\n'), self.root)['attempts'], 1)

    def test_medium_is_instrument(self):
        result = class_policy.policy(self.task('class: medium\nattempts: 4\n'), self.root)
        self.assertEqual((result['attempts'], result['breaker'], result['refusal']), (4, 'instrument', None))

    def test_large_refuses_other_executor(self):
        result = class_policy.policy(self.task('class: large\n'), self.root)
        self.assertEqual(result['breaker'], 'blocking')
        for word in ('fiche grande', 'découper', 'Opus'):
            self.assertIn(word, result['refusal'])

    def test_large_accepts_opus(self):
        result = class_policy.policy(self.task('class: large\n', agent='claude-opus'), self.root)
        self.assertEqual((result['breaker'], result['refusal']), ('blocking', None))

    def test_invalid_class(self):
        with self.assertRaisesRegex(ValueError, 'class doit être small, medium ou large'):
            class_policy.policy(self.task('class: huge\n'), self.root)


class InstrumentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'root'
        self.worktree = Path(self.tmp.name).resolve() / 'wt'
        self.worktree.mkdir()
        self.task = {'id': 'demo', 'acceptance': []}
        self.acceptance = self.root / '.workspace/acceptance/demo'
        self.acceptance.mkdir(parents=True)

    def add(self, names):
        (self.acceptance / 'instrument.txt').write_text('\n'.join(names) + '\n', encoding='utf-8')
        for name in names:
            path = self.acceptance / 'breaker' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(TEST_SOURCE % '1', encoding='utf-8')

    def test_absent_manifest(self):
        self.assertEqual(breaker.instrument_names(self.task, self.root), [])
        calls = []
        self.assertEqual(breaker.run_instruments(self.task, self.root, self.worktree, lambda *a, **k: calls.append(a)), [])
        self.assertEqual(calls, [])

    def test_names_validated(self):
        (self.acceptance / 'instrument.txt').write_text('a/test_x.py\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            breaker.instrument_names(self.task, self.root)

    def test_results_in_order_and_files_removed(self):
        names = ['pkg/__breaker__/test_a.py', 'pkg/__breaker__/test_b.py']
        self.add(names)

        def execute(args, cwd=None, env=None):
            ok = 'test_a.py' in args[-1]
            data = {'breaker_result': True, 'tests': 1, 'failures': 0 if ok else 1, 'errors': 0, 'skipped': 0}
            self.assertTrue((Path(cwd) / args[-1]).is_file())
            return (0 if ok else 1), json.dumps(data) + '\n', ''

        result = breaker.run_instruments(self.task, self.root, self.worktree, execute)
        self.assertEqual(result, [{'test': names[0], 'passed': True}, {'test': names[1], 'passed': False}])
        self.assertEqual(list(self.worktree.rglob('*')), [])

    def test_exchange_uses_sandbox(self):
        self.add(['pkg/__breaker__/test_a.py'])
        seen = []

        def execute(args, cwd=None, env=None):
            seen.append(args)
            return 1, '', 'boom'

        result = breaker.run_instruments(self.task, self.root, self.worktree, execute, exchange=True)
        self.assertEqual(result[0]['passed'], False)
        self.assertEqual(seen[0][:2], ['bash', str(self.root / 'scripts/sandbox.sh')])


if __name__ == '__main__':
    unittest.main()
