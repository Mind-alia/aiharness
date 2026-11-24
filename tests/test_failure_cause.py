import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'template/scripts/lib'))
import events
import failure_cause

SYNTAX_ERROR_OUTPUT = (
    'ERROR: test_acceptance (tests.test_acceptance)\n'
    'Traceback (most recent call last):\n'
    '  File "/work/tests/test_acceptance.py", line 3\n'
    '    def broken(:\n'
    '               ^\n'
    'SyntaxError: invalid syntax\n'
)
ASSERTION_OUTPUT = (
    'FAIL: test_x (tests.test_acceptance.T)\n'
    'Traceback (most recent call last):\n'
    '  File "/work/tests/test_acceptance.py", line 9, in test_x\n'
    '    self.assertEqual(1, 2)\n'
    'AssertionError: 1 != 2\n'
)
MODULE_OUTPUT = (
    'ERROR: test_acceptance\n'
    '  File "/work/tests/test_acceptance.py", line 1, in <module>\n'
    '    import prod\n'
    'ModuleNotFoundError: No module named \'prod\'\n'
)


class FailureCauseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def acceptance(self, text):
        directory = self.root / '.workspace/run/delegate/demo'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'acceptance.txt').write_text(text, encoding='utf-8')

    def cause(self, outcome):
        return failure_cause.cause_of(self.root, 'demo', outcome)

    def test_no_cause(self):
        for outcome in failure_cause.NO_CAUSE:
            self.assertIsNone(self.cause(outcome))
        self.assertIsNone(self.cause(None))
        self.assertIsNone(self.cause(3))

    def test_outcome_mapping(self):
        table = {
            'out_of_scope': 'scope',
            'control_tampered': 'scope',
            'acceptance_vacuous': 'test_invalid',
            'verify_failed': 'implementation',
            'no_implementation': 'implementation',
            'isolation_unavailable': 'infra',
            'permission_denied': 'infra',
            'interrupted': 'infra',
            'quota': 'quota',
            'failed': 'unknown',
            'merge_conflict': 'unknown',
            'integration_failed': 'unknown',
            'secret_in_prompt': 'unknown',
            'whatever': 'unknown',
        }
        for outcome, expected in table.items():
            self.assertEqual(self.cause(outcome), expected, outcome)
            self.assertIn(self.cause(outcome), failure_cause.CAUSES)

    def test_acceptance_failed(self):
        self.assertEqual(self.cause('acceptance_failed'), 'implementation')
        for text, expected in (
            (SYNTAX_ERROR_OUTPUT, 'test_invalid'),
            (ASSERTION_OUTPUT, 'implementation'),
            (MODULE_OUTPUT, 'implementation'),
        ):
            self.acceptance(text)
            self.assertEqual(self.cause('acceptance_failed'), expected)

    def test_acceptance_symlink_ignored(self):
        directory = self.root / '.workspace/run/delegate/demo'
        directory.mkdir(parents=True)
        target = self.root / 'elsewhere.txt'
        target.write_text(SYNTAX_ERROR_OUTPUT, encoding='utf-8')
        (directory / 'acceptance.txt').symlink_to(target)
        self.assertEqual(self.cause('acceptance_failed'), 'implementation')

    def test_quota_event_wins(self):
        events.record(self.root, 'demo', 'delegate_started')
        events.record(self.root, 'demo', 'quota')
        self.assertEqual(self.cause('out_of_scope'), 'quota')

    def test_quota_before_last_start_ignored(self):
        events.record(self.root, 'demo', 'delegate_started')
        events.record(self.root, 'demo', 'quota')
        events.record(self.root, 'demo', 'delegate_started')
        self.assertEqual(self.cause('out_of_scope'), 'scope')

    def test_quota_without_start_counts(self):
        events.record(self.root, 'demo', 'quota')
        self.assertEqual(self.cause('verify_failed'), 'quota')

    def test_quota_of_other_task_ignored(self):
        events.record(self.root, 'other', 'quota')
        self.assertEqual(self.cause('verify_failed'), 'implementation')

    def test_unreadable_journal(self):
        original = events.read

        def broken(*args, **kwargs):
            raise OSError('boom')

        events.read = broken
        self.addCleanup(setattr, events, 'read', original)
        self.assertEqual(self.cause('verify_failed'), 'implementation')

    def test_closing_cause(self):
        self.assertEqual(failure_cause.closing_cause(self.root, 'demo'), 'unknown')
        events.record(self.root, 'demo', 'outcome', {'outcome': 'verify_failed'})
        self.assertEqual(failure_cause.closing_cause(self.root, 'demo'), 'implementation')
        events.record(self.root, 'demo', 'outcome', {'outcome': 'failed', 'cause': 'infra'})
        self.assertEqual(failure_cause.closing_cause(self.root, 'demo'), 'infra')
        events.record(self.root, 'demo', 'outcome', {'outcome': 'out_of_scope', 'cause': 'bogus'})
        self.assertEqual(failure_cause.closing_cause(self.root, 'demo'), 'scope')
        events.record(self.root, 'demo', 'outcome', {'outcome': 'done'})
        self.assertEqual(failure_cause.closing_cause(self.root, 'demo'), 'unknown')


if __name__ == '__main__':
    unittest.main()
