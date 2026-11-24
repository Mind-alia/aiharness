"""The proof of the hidden tests is kept, green or red (T110): acceptance-full.txt, the summary line, report.sh."""

import json
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import delegate_checks
import report
from delegate_checks import ACCEPTANCE_OUTPUT_LIMIT, END, RETRY

COMMAND = 'python3 -m unittest hidden'
GREEN_ERR = 'Ran 3 tests in 0.012s\n\nOK\n'
RED_ERR = (
    'FAIL: test_a (hidden.T)\nTraceback (most recent call last):\n  File "hidden.py", line 3, in test_a\n'
    '    self.assertEqual(secret_source(), 1)\nAssertionError: 2 != 1\n\nRan 5 tests in 0.100s\n\nFAILED (failures=2)\n'
)


def make_execute(code=0, out='', err='', calls=None):
    def execute(args, cwd=None):
        if calls is not None:
            calls.append(args)
        return code, out, err

    return execute


class ProofTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.run_dir = self.root / '.workspace/run/delegate/t-demo'
        self.run_dir.mkdir(parents=True)
        self.d = types.SimpleNamespace(
            root=self.root,
            task=dict(id='t-demo', attempts=2, acceptance=['a.py'], acceptance_verify=COMMAND),
            run=self.run_dir,
            isolation='full',
            work_dir=self.root,
            status=dict(outcome='done', warnings=[], acceptance_attempts=0),
            prompt='',
            followup=None,
            started=0,
            mask=lambda text: text.replace('s3cr3t', '***'),
            execute=make_execute(),
        )
        patch = mock.patch.object(delegate_checks.events, 'record')
        self.record = patch.start()
        self.addCleanup(patch.stop)
        self.d.seals = {}
        patch = mock.patch.object(delegate_checks.questions, 'unanswered', return_value=[])
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(delegate_checks.task_phase, 'set_phase')
        patch.start()
        self.addCleanup(patch.stop)

    def step(self, run_acceptance, execute, attempt=1):
        self.d.execute = execute
        with mock.patch.object(delegate_checks, 'run_acceptance', run_acceptance):
            return delegate_checks.acceptance_step(self.d, attempt)

    def real_step(self, code, out, err, attempt=1):
        # The real run_acceptance of the kit would stage files; play the command through the wrapper instead.
        def run(task, root, work_dir, execute, exchange):
            result = execute(['/bin/sh', '-c', task['acceptance_verify']], cwd=work_dir)
            return result[0], ('FAIL: test_a\nAssertionError: 2 != 1\n' if result[0] else '')

        return self.step(run, make_execute(code, out, err), attempt)

    def read(self, name):
        return (self.run_dir / name).read_text(encoding='utf-8')

    def test_green_keeps_full_output_and_one_summary_line(self):
        self.assertIsNone(self.real_step(0, 'sortie\n', GREEN_ERR))
        self.assertEqual(self.read('acceptance-full.txt'), 'sortie\n--- stderr ---\n' + GREEN_ERR)
        self.assertEqual(self.read('acceptance.txt'), 'résumé : Ran 3 tests in 0.012s / OK\n')
        self.assertEqual(self.d.status['acceptance_summary'], 'Ran 3 tests in 0.012s / OK')
        self.record.assert_called_with(
            self.root, 't-demo', 'acceptance', {'attempt': 1, 'code': 0, 'summary': 'Ran 3 tests in 0.012s / OK'}
        )

    def test_red_keeps_everything_in_full_and_filtered_text_in_acceptance(self):
        self.d.seals = {}
        with mock.patch.object(delegate_checks.questions, 'unanswered', return_value=[]):
            self.assertEqual(self.real_step(1, '', RED_ERR), RETRY)
        full = self.read('acceptance-full.txt')
        for part in ('Traceback', 'File "hidden.py"', 'secret_source()', '--- stderr ---'):
            self.assertIn(part, full)
        short = self.read('acceptance.txt')
        self.assertTrue(short.endswith('résumé : Ran 5 tests in 0.100s / FAILED (failures=2)\n'))
        for part in ('Traceback', 'File "', 'secret_source', '--- stderr ---'):
            self.assertNotIn(part, short)
        self.assertEqual(self.d.followup, delegate_checks.ACCEPTANCE_FAILURE + 'FAIL: test_a\nAssertionError: 2 != 1\n')
        self.assertNotIn('résumé', self.d.followup)

    def test_separator_stays_alone_with_empty_streams(self):
        self.real_step(0, '', '')
        self.assertEqual(self.read('acceptance-full.txt'), '--- stderr ---\n')
        self.real_step(0, 'sans saut', '')
        self.assertEqual(self.read('acceptance-full.txt'), 'sans saut\n--- stderr ---\n')

    def test_stdout_comes_before_stderr(self):
        self.real_step(0, 'un\n', 'deux\n')
        self.assertEqual(self.read('acceptance-full.txt'), 'un\n--- stderr ---\ndeux\n')

    def test_secrets_are_masked_everywhere(self):
        self.real_step(0, 'x s3cr3t\n', 'Ran 1 test in s3cr3t\nOK\n')
        self.assertNotIn('s3cr3t', self.read('acceptance-full.txt'))
        self.assertEqual(self.d.status['acceptance_summary'], 'Ran 1 test in *** / OK')
        self.assertNotIn('s3cr3t', self.read('acceptance.txt'))
        self.assertIn('***', self.read('acceptance.txt'))
        self.assertNotIn('s3cr3t', json.dumps(self.record.call_args.args[3]))

    def test_long_output_keeps_the_end(self):
        err = 'x' * (ACCEPTANCE_OUTPUT_LIMIT + 50) + '\nRan 1 test in 0s\nOK\n'
        self.real_step(0, '', err)
        text = self.read('acceptance-full.txt')
        first, rest = text.split('\n', 1)
        total = len('--- stderr ---\n' + err)
        self.assertEqual(first, '[… %d caractères omis …]' % (total - ACCEPTANCE_OUTPUT_LIMIT))
        self.assertEqual(len(rest), ACCEPTANCE_OUTPUT_LIMIT)
        self.assertTrue(rest.endswith('OK\n'))
        self.assertEqual(self.d.status['acceptance_summary'], 'Ran 1 test in 0s / OK')

    def test_existing_file_is_rewritten_with_mode_0600(self):
        path = self.run_dir / 'acceptance-full.txt'
        path.write_text('ancien')
        path.chmod(0o644)
        self.real_step(0, 'neuf\n', GREEN_ERR)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn('ancien', path.read_text())

    def test_symlink_full_file_is_refused(self):
        target = self.root / 'cible.txt'
        target.write_text('intact')
        target.chmod(0o644)
        (self.run_dir / 'acceptance-full.txt').symlink_to(target)
        with self.assertRaises(ValueError):
            self.real_step(0, 'x\n', GREEN_ERR)
        self.assertEqual(target.read_text(), 'intact')
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_summary_rules(self):
        summarize = delegate_checks.summarize
        self.assertEqual(summarize('Ran 1 test\nOK\nRan 2 tests\nFAILED (failures=1)\n', 1), 'Ran 2 tests / FAILED (failures=1)')
        self.assertEqual(summarize('Ran 4 tests in 1s\n', 0), 'Ran 4 tests in 1s')
        self.assertEqual(summarize('usage: python\n', 2), 'sans résumé (code 2)')

    def test_no_ran_line_reports_the_command_code(self):
        self.real_step(2, '', 'usage\n')
        self.assertEqual(self.d.status['acceptance_summary'], 'sans résumé (code 2)')
        self.assertIn('résumé : sans résumé (code 2)', self.read('acceptance.txt'))

    def test_breaker_failure_adds_the_suffix_and_keeps_the_diagnostic(self):
        def run(task, root, work_dir, execute, exchange):
            execute(['/bin/sh', '-c', COMMAND], cwd=work_dir)
            execute(['python3', 'test_breaker.py'], cwd=work_dir)
            return 1, 'diagnostic du casseur\n'

        self.step(run, make_execute(0, '', GREEN_ERR))
        self.assertEqual(self.d.status['outcome'], 'acceptance_failed')
        self.assertEqual(self.d.status['acceptance_summary'], 'Ran 3 tests in 0.012s / OK ; casseur : échec')
        self.assertEqual(self.read('acceptance.txt'), 'diagnostic du casseur\nrésumé : Ran 3 tests in 0.012s / OK ; casseur : échec\n')

    def test_breaker_calls_are_not_captured(self):
        def run(task, root, work_dir, execute, exchange):
            execute(['python3', 'test_breaker.py'], cwd=work_dir)
            return 0, ''

        self.step(run, make_execute(0, 'casseur\n', 'Ran 1 test\nOK\n'))
        self.assertEqual(self.read('acceptance-full.txt'), '--- stderr ---\n')
        self.assertEqual(self.d.status['acceptance_summary'], 'sans résumé (code 0)')

    def test_last_attempt_ends_without_retry(self):
        self.d.seals = {}
        self.assertIsNone(self.real_step(1, '', RED_ERR, attempt=2))
        self.assertEqual(self.d.status['outcome'], 'acceptance_failed')

    def test_judge_runs_the_step(self):
        self.d.contract = self.run_dir / 'task.md'
        self.d.seals, self.d.breaker_run = {}, False
        self.d.study_force_fail = False
        self.d.known = lambda: []
        self.d.task['acceptance_verify'] = COMMAND
        with mock.patch.object(delegate_checks, 'acceptance_step', return_value=None) as step:
            self.assertEqual(delegate_checks.judge(self.d, 1, True), END)
        step.assert_called_once()


class ReportLineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.status = self.root / '.workspace/run/delegate/t1/status.json'
        self.status.parent.mkdir(parents=True)

    def test_line_from_status(self):
        self.status.write_text(json.dumps({'acceptance_summary': 'Ran 9 tests in 0.4s / OK'}))
        self.assertEqual(report.hidden_tests_line(self.root, 't1'), 'tests cachés : Ran 9 tests in 0.4s / OK')

    def test_not_played(self):
        self.assertEqual(report.hidden_tests_line(self.root, 't1'), 'tests cachés : non joués')
        for content in ('{}', '[]', 'pas du json', '{"acceptance_summary": ""}', '{"acceptance_summary": 3}'):
            self.status.write_text(content)
            self.assertEqual(report.hidden_tests_line(self.root, 't1'), 'tests cachés : non joués')

    def test_symlink_is_ignored(self):
        target = self.root / 'autre.json'
        target.write_text(json.dumps({'acceptance_summary': 'Ran 1 test / OK'}))
        self.status.symlink_to(target)
        self.assertEqual(report.hidden_tests_line(self.root, 't1'), 'tests cachés : non joués')


if __name__ == '__main__':
    unittest.main()
