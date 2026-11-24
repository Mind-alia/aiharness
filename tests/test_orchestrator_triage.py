"""Classement d'une vérification rouge (T117) et environnement de la vérification sur l'hôte (T115) : tests rouges lus
dans la sortie, fichier de test d'un identifiant, catégorie d'après la base et le périmètre de la fiche."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'orchestrator'))

import steps  # noqa: E402
import triage  # noqa: E402

LINE, DASH = '=' * 70, '-' * 70


def red(kind, name, test_id):
    return f'{LINE}\n{kind}: {name} ({test_id})\n{DASH}\nAssertionError\n\n'


SANDBOX = 'tests.test_sandbox.SandboxTests.test_default_image'
NOTE = 'tests.test_note.NoteTests.test_report_task_counts_human_interventions_after_cost_line'
BUDGET = 'tests.test_code_quality.CodeQualityTests.test_type_errors_stay_within_budget'
MISSING = 'unittest.loader._FailedTest.test_acceptance_proof'
FICHE = '---\nid: t1\nagent: claude-sonnet\nallowed:\n  - tools/orchestrator/run.py\n  - tests/test_orchestrator_run.py\n---\n# Fiche\n'


class RedTestsTests(unittest.TestCase):
    def test_unittest_blocks_and_pytest_lines_are_read(self):
        text = red('FAIL', 'test_default_image', SANDBOX) + red('ERROR', 'test_acceptance_proof', MISSING)
        text += 'FAILED tests/test_x.py::test_y - assert 1 == 2\nERROR tests/test_z.py::test_w\n'
        self.assertEqual(triage.red_tests(text), {SANDBOX, MISSING, 'tests/test_x.py::test_y', 'tests/test_z.py::test_w'})

    def test_no_failure_or_no_text_is_empty(self):
        self.assertEqual(triage.red_tests('Ran 3 tests in 0.1s\n\nOK\n'), set())
        self.assertEqual(triage.red_tests(None), set())

    def test_a_block_without_identifier_keeps_its_name(self):
        self.assertEqual(triage.red_tests('ERROR: setUpModule\n'), {'setUpModule'})


class ProjectTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name))
        for name in ('tests/test_sandbox.py', 'tests/test_note.py', 'tests/test_code_quality.py', 'tests/test_orchestrator_run.py'):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text('# test\n')
        (self.root / 'fiche.md').write_text(FICHE)

    def classify(self, output, base, changed=()):
        return triage.classify(self.root, (output, 'fiche.md', list(changed)), base)

    def test_test_file_of_an_identifier(self):
        self.assertEqual(triage.test_file(self.root, NOTE), 'tests/test_note.py')
        self.assertEqual(triage.test_file(self.root, 'tests/test_note.py::test_x'), 'tests/test_note.py')
        self.assertIsNone(triage.test_file(self.root, MISSING))
        self.assertIsNone(triage.test_file(self.root, 'tests.test_absent.A.test_b'))
        self.assertIsNone(triage.test_file(self.root, '../dehors.py::test_x'))

    def test_red_on_the_base_too_is_the_environment(self):
        # Nuit du 3/11 : la variable d'image héritée fait rougir test_sandbox avec ou sans le travail de la tâche.
        category, detail = self.classify(red('FAIL', 'test_default_image', SANDBOX), (1, {SANDBOX, NOTE}))
        self.assertEqual(category, triage.ENVIRONMENT)
        self.assertEqual((detail['red'], detail['new']), ([SANDBOX], []))

    def test_a_base_failing_without_named_test_is_the_environment_when_the_task_names_none(self):
        self.assertEqual(self.classify('sh: python3.14: not found\n', (127, set()))[0], triage.ENVIRONMENT)
        self.assertEqual(self.classify('sh: python3.14: not found\n', (0, set()))[0], triage.EXECUTOR)

    def test_new_red_test_in_a_frozen_file_is_the_fiche(self):
        # gt4-acceptance-proof, 3/11 : la règle 9 de la fiche contredit tests/test_note.py, hors `allowed`.
        category, detail = self.classify(red('FAIL', 'test_x', NOTE), (0, set()))
        self.assertEqual(category, triage.FICHE)
        self.assertEqual(detail['frozen'], ['tests/test_note.py'])
        self.assertIn('tests/test_note.py', triage.summary(detail))

    def test_executor_cases(self):
        cases = {
            'test du périmètre': (red('FAIL', 'test_x', 'tests.test_orchestrator_run.RunTests.test_x'), ()),
            'suite de qualité': (red('FAIL', 'test_type_errors_stay_within_budget', BUDGET), ()),
            'module introuvable': (red('ERROR', 'test_acceptance_proof', MISSING), ()),
            'fichier touché par la tâche': (red('FAIL', 'test_x', NOTE), ('tests/test_note.py',)),
            'gelé et neuf ensemble': (red('FAIL', 'test_x', NOTE) + red('ERROR', 'test_acceptance_proof', MISSING), ()),
        }
        for label, (output, changed) in cases.items():
            with self.subTest(label):
                self.assertEqual(self.classify(output, (0, set()), changed)[0], triage.EXECUTOR)

    def test_red_on_the_base_and_new_red_of_the_task_counts_for_the_executor(self):
        output = red('FAIL', 'test_default_image', SANDBOX) + red('FAIL', 'test_x', 'tests.test_orchestrator_run.RunTests.test_x')
        category, detail = self.classify(output, (1, {SANDBOX}))
        self.assertEqual(category, triage.EXECUTOR)
        self.assertEqual(detail['base_red'], [SANDBOX])

    def test_no_base_or_unreadable_fiche_counts_for_the_executor(self):
        self.assertEqual(self.classify(red('FAIL', 'test_x', NOTE), None)[0], triage.EXECUTOR)
        (self.root / 'fiche.md').write_text('# sans en-tête\n')
        self.assertEqual(self.classify(red('FAIL', 'test_x', NOTE), (0, set()))[0], triage.EXECUTOR)

    def test_summary_is_bounded(self):
        many = {f'tests.test_note.N.test_{number}' for number in range(30)}
        text = triage.summary({'red': sorted(many), 'base_red': [], 'new': sorted(many), 'frozen': []})
        self.assertIn('(+18)', text)
        self.assertIn('rouges sur la base : aucun', text)


class VerifyEnvTests(unittest.TestCase):
    def test_only_the_whitelist_reaches_the_verification(self):
        source = {
            'PATH': '/usr/bin',
            'HOME': '/home/a',
            'LC_ALL': 'fr_FR.UTF-8',
            'UV_CACHE_DIR': '/cache/uv',
            'GIT_AUTHOR_NAME': 'A',
            'DOCKER_HOST': 'unix:///var/run/docker.sock',
            # Nuit du 3/11 : la variable d'image du lancement ne doit plus atteindre la suite.
            'WORKSPACE_IMAGE_PYTHON': 'workspace-python-git:3.14',
            'WORKSPACE_TESTS': 'fast',
            'WORKSPACE_AGENT_ROOT': '/x',
            'ORCHESTRATOR_TMP': '/tmp/o',
            'PYTHONPATH': '/elsewhere',
            'UV_PUBLISH_TOKEN': 'abc',
            'ANTHROPIC_API_KEY': 'abc',
        }
        self.assertEqual(
            steps.verify_env(source),
            {
                'PATH': '/usr/bin',
                'HOME': '/home/a',
                'LC_ALL': 'fr_FR.UTF-8',
                'UV_CACHE_DIR': '/cache/uv',
                'GIT_AUTHOR_NAME': 'A',
                'DOCKER_HOST': 'unix:///var/run/docker.sock',
            },
        )

    def test_default_source_is_the_process_environment(self):
        self.assertEqual(steps.verify_env().get('PATH'), os.environ.get('PATH'))


class DiagnosisCauseTests(unittest.TestCase):
    """E15 (12/11) : la ligne `CAUSE:` du diagnostic décide si la tâche est reprise ; dans le doute, elle l'est."""

    def test_each_known_value_is_read(self):
        for value, cause in (
            ('executant', triage.EXECUTOR),
            ('exécutant', triage.EXECUTOR),
            ('fiche', triage.FICHE),
            ('environnement', triage.ENVIRONMENT),
            ('circuit', triage.CIRCUIT),
        ):
            with self.subTest(value=value):
                self.assertEqual(triage.diagnosis_cause(f'1. Cause probable : x.\n2. Faire y.\nCAUSE: {value}\n'), cause)

    def test_the_last_line_wins(self):
        self.assertEqual(triage.diagnosis_cause('CAUSE: circuit\nEn fait non.\nCAUSE: executant'), triage.EXECUTOR)
        self.assertEqual(triage.diagnosis_cause('CAUSE: executant\nCAUSE: circuit'), triage.CIRCUIT)

    def test_markdown_around_the_line_and_case_of_the_value_are_tolerated(self):
        self.assertEqual(triage.diagnosis_cause('Texte.\n`CAUSE: fiche`'), triage.FICHE)
        self.assertEqual(triage.diagnosis_cause('Texte.\n**CAUSE: Circuit.**'), triage.CIRCUIT)

    def test_missing_malformed_or_unknown_means_executor(self):
        for text in (
            None,
            '',
            'Cause probable : test rouge.',
            'CAUSE circuit',
            'CAUSE: circuit ou fiche',
            'CAUSE: circuit\nCAUSE: inconnue',
            'CAUSE:',
            'La CAUSE: circuit',
        ):
            with self.subTest(text=text):
                self.assertEqual(triage.diagnosis_cause(text), triage.EXECUTOR)


if __name__ == '__main__':
    unittest.main()
