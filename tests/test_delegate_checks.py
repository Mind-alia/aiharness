"""Checks after each attempt (delegate_checks.py), and the shape of delegate.py once split (T14 phase A)."""

import ast
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import delegate_checks
from delegate_checks import END, RETRY
from scope import fingerprint

MAIN_LIMIT = 80


class FakeExecute:
    """Answers each script with the next (code, out, err) given for it; records every call."""

    def __init__(self, **answers):
        self.answers = {name: list(values) for name, values in answers.items()}
        self.calls = []

    def __call__(self, args, cwd=None):
        name = Path(args[1]).name
        self.calls.append(name)
        return self.answers[name].pop(0)


class ChecksTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / 'scripts').mkdir()
        for name in ('check-scope.sh', 'verify-task.sh'):
            (self.root / 'scripts' / name).write_text('exit 0\n')
        run = self.root / '.workspace/run/delegate/t-demo'
        run.mkdir(parents=True)
        contract = run / 'task.md'
        contract.write_text('# Faire A\n')
        self.d = types.SimpleNamespace(
            root=self.root,
            task=dict(id='t-demo', attempts=2, acceptance=[], verify='make test'),
            run=run,
            contract=contract,
            seals={contract: fingerprint(contract)},
            isolation='full',
            work_dir=self.root,
            status=dict(outcome='done', warnings=[], acceptance_attempts=0),
            prompt='consigne',
            followup=None,
            breaker_run=False,
            started=0,
            study_force_fail=False,
            mask=lambda text: text.replace('s3cr3t', '***'),
            known=lambda: [],
        )

    def test_a_clean_attempt_ends_done(self):
        self.d.execute = FakeExecute(**{'check-scope.sh': [(0, '', '')] * 2, 'verify-task.sh': [(0, 'ok', '')]})
        self.assertEqual(delegate_checks.judge(self.d, 1, True), END)
        self.assertEqual(self.d.status['outcome'], 'done')
        self.assertEqual(self.d.execute.calls, ['check-scope.sh', 'verify-task.sh', 'check-scope.sh'])
        self.assertEqual(self.d.status['verify'], {'command': 'make test', 'exit': 0})

    def test_out_of_scope_stops_before_verify(self):
        self.d.execute = FakeExecute(**{'check-scope.sh': [(1, 'hors périmètre : b.py', '')]})
        self.assertEqual(delegate_checks.judge(self.d, 1, True), END)
        self.assertEqual(self.d.status['outcome'], 'out_of_scope')
        self.assertEqual((self.d.run / 'scope.txt').read_text(), 'hors périmètre : b.py')

    def test_a_red_verify_goes_back_to_the_executor_masked_on_disk(self):
        self.d.execute = FakeExecute(**{'check-scope.sh': [(0, '', '')], 'verify-task.sh': [(1, 'échec s3cr3t', '')]})
        self.assertEqual(delegate_checks.judge(self.d, 1, True), RETRY)
        self.assertEqual(self.d.status['outcome'], 'failed')
        self.assertEqual(self.d.followup, delegate_checks.VERIFY_FAILURE + 'échec s3cr3t')
        self.assertTrue(self.d.prompt.endswith('\n\n' + self.d.followup))
        self.assertEqual((self.d.run / 'verify.txt').read_text(), 'échec ***')

    def test_a_red_verify_on_the_last_attempt_ends_verify_failed(self):
        self.d.execute = FakeExecute(**{'check-scope.sh': [(0, '', '')] * 2, 'verify-task.sh': [(1, 'échec', '')]})
        self.assertEqual(delegate_checks.judge(self.d, 2, True), END)
        self.assertEqual(self.d.status['outcome'], 'verify_failed')
        self.assertIsNone(self.d.followup)

    def test_a_tampered_control_wins_over_everything(self):
        def execute(args, cwd=None):
            self.d.contract.write_text('# Faire autre chose\n')
            return 0, '', ''

        self.d.execute = execute
        self.assertEqual(delegate_checks.judge(self.d, 1, True), END)
        self.assertEqual(self.d.status['outcome'], 'control_tampered')

    def test_red_hidden_tests_retry_with_their_feedback(self):
        self.d.execute = FakeExecute(**{'check-scope.sh': [(0, '', '')], 'verify-task.sh': [(0, '', '')]})
        with (
            mock.patch.object(delegate_checks, 'has_acceptance', return_value=True),
            mock.patch.object(delegate_checks, 'run_acceptance', return_value=(1, 'test_a rouge')),
        ):
            self.assertEqual(delegate_checks.judge(self.d, 1, True), RETRY)
        self.assertEqual(self.d.status['outcome'], 'acceptance_failed')
        self.assertEqual(self.d.status['acceptance_attempts'], 1)
        self.assertEqual(self.d.followup, delegate_checks.ACCEPTANCE_FAILURE + 'test_a rouge')

    def test_the_breaker_run_skips_the_hidden_tests(self):
        self.d.breaker_run = True
        self.d.execute = FakeExecute(**{'check-scope.sh': [(0, '', '')] * 2, 'verify-task.sh': [(0, '', '')]})
        with mock.patch.object(delegate_checks, 'run_acceptance') as acceptance:
            self.assertEqual(delegate_checks.judge(self.d, 1, True), END)
        acceptance.assert_not_called()
        self.assertEqual(self.d.status['acceptance_attempts'], 0)

    def test_a_pending_question_turns_a_retry_into_waiting_answer(self):
        self.d.execute = FakeExecute(**{'check-scope.sh': [(0, '', '')], 'verify-task.sh': [(1, 'échec', '')]})
        with mock.patch.object(delegate_checks.questions, 'unanswered', return_value=['001']):
            self.assertEqual(delegate_checks.judge(self.d, 1, True), END)
        self.assertEqual(self.d.status['outcome'], 'waiting_answer')
        self.assertEqual(self.d.status['waiting_questions'], ['001'])

    def test_no_correction_round_means_no_referee(self):
        self.d.correction_round = False
        self.assertIsNone(delegate_checks.correction_step(self.d))


def function(tree, name):
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)


class SplitShapeTests(unittest.TestCase):
    """delegate.main only chains the steps; the prompt and the checks live in their own modules."""

    def test_main_stays_a_short_chain_without_waivers(self):
        source = (ROOT / 'template/scripts/lib/delegate.py').read_text(encoding='utf-8')
        main = function(ast.parse(source), 'main')
        assert main.end_lineno is not None
        self.assertLessEqual(main.end_lineno - main.lineno + 1, MAIN_LIMIT)
        self.assertNotIn('noqa', source.splitlines()[main.lineno - 1])

    def test_delegate_uses_the_split_modules(self):
        source = (ROOT / 'template/scripts/lib/delegate.py').read_text(encoding='utf-8')
        self.assertIn('delegate_prompt.build(', source)
        self.assertIn('delegate_checks.judge(', source)
        self.assertNotIn('Périmètre autorisé', source)


if __name__ == '__main__':
    unittest.main()
