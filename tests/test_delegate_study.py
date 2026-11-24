import json
import shutil
import unittest

from tests import test_delegate_cost as cost

FORCED = 'Échec forcé (étude de reprise) : relance ta vérification ciblée et confirme ton travail.'
PLAN = [
    {'stdout': json.dumps({'result': 'first', 'session_id': 'sess-1'})},
    {'stdout': json.dumps({'result': 'second', 'session_id': 'sess-2'})},
]


class DelegateStudyTests(unittest.TestCase):
    # Same fixture as the cost tests: a temporary project and a fake `claude` that records its calls.
    setUp = cost.DelegateCostTests.setUp
    git = cost.DelegateCostTests.git
    executable = cost.DelegateCostTests.executable
    write_task = cost.DelegateCostTests.write_task
    run_delegate = cost.DelegateCostTests.run_delegate
    status = cost.DelegateCostTests.status
    args = cost.DelegateCostTests.args
    stdin = cost.DelegateCostTests.stdin
    calls = cost.DelegateCostTests.calls
    failing_first_verification = cost.DelegateCostTests.failing_first_verification

    def study(self, **variables):
        self.env.update(variables)

    def verify_text(self):
        return (self.root / '.workspace/run/delegate/demo/verify.txt').read_text()

    def test_forced_failure_resumes_with_only_the_feedback(self):
        self.write_task()
        self.study(WORKSPACE_STUDY_FORCE_FAIL='1')
        result = self.run_delegate(PLAN)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), 2)
        self.assertNotIn('--resume', self.args(1))
        self.assertEqual(self.args(2)[-2:], ['--resume', 'sess-1'])
        self.assertTrue(self.stdin(2).startswith('Échec de vérification, corriger :\n' + FORCED + '\n'))
        self.assertNotIn('TASK BODY', self.stdin(2))
        status = self.status()
        self.assertEqual(status['outcome'], 'done')
        self.assertEqual(status['attempts'], 2)
        self.assertEqual(status['study'], {'resume': True, 'forced_first_failure': True})
        self.assertEqual(self.events('verify'), [{'exit': 1}, {'exit': 0}])

    def test_forced_failure_output_is_the_banner_then_the_real_output(self):
        self.write_task()
        self.executable('scripts/verify-task.sh', '#!/bin/sh\necho REAL OUTPUT\n')
        self.study(WORKSPACE_STUDY_FORCE_FAIL='1')
        self.assertEqual(self.run_delegate(PLAN).returncode, 0)
        self.assertIn('Échec de vérification, corriger :\n' + FORCED + '\nREAL OUTPUT', self.stdin(2))
        # The second verification is not forced: only its own output is left.
        self.assertEqual(self.verify_text(), 'REAL OUTPUT\n')

    def test_forced_failure_without_resume_sends_the_whole_prompt(self):
        self.write_task()
        self.study(WORKSPACE_STUDY_FORCE_FAIL='1', WORKSPACE_DELEGATE_RESUME='0')
        self.assertEqual(self.run_delegate(PLAN).returncode, 0)
        self.assertEqual(self.calls(), 2)
        self.assertNotIn('--resume', self.args(2))
        self.assertIn('TASK BODY', self.stdin(2))
        self.assertIn('Échec de vérification, corriger :\n' + FORCED, self.stdin(2))
        self.assertEqual(self.status()['study'], {'resume': False, 'forced_first_failure': True})
        self.assertEqual([e['resumed'] for e in self.events('attempt_started')], [False, False])

    def test_resume_switch_alone_keeps_the_real_failure_and_drops_the_resume(self):
        self.write_task()
        self.failing_first_verification()
        self.study(WORKSPACE_DELEGATE_RESUME='0')
        self.assertEqual(self.run_delegate(PLAN).returncode, 0)
        self.assertNotIn('--resume', self.args(2))
        self.assertIn('TASK BODY', self.stdin(2))
        self.assertIn('FIX THIS FAILURE', self.stdin(2))
        self.assertNotIn(FORCED, self.stdin(2))
        self.assertEqual(self.status()['study'], {'resume': False, 'forced_first_failure': False})

    def test_any_other_resume_value_keeps_resuming(self):
        for value in ('1', '', 'no'):
            with self.subTest(value):
                shutil.rmtree(self.record)
                self.record.mkdir()
                self.write_task()
                self.failing_first_verification()
                self.study(WORKSPACE_DELEGATE_RESUME=value)
                self.assertEqual(self.run_delegate(PLAN).returncode, 0)
                self.assertEqual(self.args(2)[-2:], ['--resume', 'sess-1'])
                self.assertNotIn('study', self.status())

    def test_single_attempt_is_never_forced(self):
        self.write_task()
        self.task.write_text(self.task.read_text().replace('attempts: 2', 'attempts: 1'))
        self.study(WORKSPACE_STUDY_FORCE_FAIL='1')
        self.assertEqual(self.run_delegate(PLAN).returncode, 0)
        self.assertEqual(self.calls(), 1)
        self.assertNotIn(FORCED, self.verify_text())
        status = self.status()
        self.assertEqual(status['outcome'], 'done')
        self.assertEqual(status['study'], {'resume': True, 'forced_first_failure': False})

    def test_force_switch_needs_the_exact_value_one(self):
        self.write_task()
        self.study(WORKSPACE_STUDY_FORCE_FAIL='0')
        self.assertEqual(self.run_delegate(PLAN).returncode, 0)
        self.assertEqual(self.calls(), 1)
        self.assertNotIn('study', self.status())

    def test_without_variables_behaviour_and_status_are_unchanged(self):
        self.write_task()
        self.failing_first_verification()
        self.assertEqual(self.run_delegate(PLAN).returncode, 0)
        self.assertEqual(self.args(2)[-2:], ['--resume', 'sess-1'])
        self.assertNotIn(FORCED, self.stdin(2))
        self.assertNotIn('study', self.status())

    events = cost.DelegateCostTests.events


if __name__ == '__main__':
    unittest.main()
