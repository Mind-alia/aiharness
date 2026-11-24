"""Défauts du circuit vus la nuit du 3/11, sur le faux circuit de test_orchestrator_run : vérification sur l'hôte dans un
environnement contrôlé (T115), vérification rouge classée exécutant, environnement ou fiche (T117), diff exporté et clone
fermé quand une fiche est arrêtée (T120), budget de types dépassé envoyé en ronde de correction sans échec compté (T118), diagnostic nourri des seuls tests
rouges et reprise qui les reçoit même quand le modèle échoue (T119)."""

import json
import os
import sys
import unittest

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel et sous-processus ; suite complète seulement')

try:
    from tests.test_orchestrator_run import ProjectCase
except ImportError:
    from test_orchestrator_run import ProjectCase

# Vérification factice : rend un test rouge de la suite quand `marker` existe ; `kind` dit lequel.
CHECK = r'''import os, sys
from pathlib import Path
marker, test_id = sys.argv[1], sys.argv[2]
if os.path.exists(marker):
    print('=' * 70)
    print('FAIL: %s (%s)' % (test_id.rsplit('.', 1)[-1], test_id))
    print('-' * 70)
    print('AssertionError: rouge')
    print('-' * 70)
    print('Ran 4 tests in 0.100s\n\nFAILED (failures=1)')
    sys.exit(1)
print('Ran 4 tests in 0.100s\n\nOK')
'''
FROZEN = 'tests.test_frozen.FrozenTests.test_report_order'
BUDGET = 'tests.test_code_quality.CodeQualityTests.test_type_errors_stay_within_budget'
# Vérification factice du budget de types : rouge tant que `src/t1.py` contient `bad`.
TYPES_CHECK = r'''import sys
from pathlib import Path
path = Path('src/t1.py')
if path.is_file() and 'bad' in path.read_text():
    print('=' * 70)
    print('FAIL: test_type_errors_stay_within_budget (%s)' % sys.argv[1])
    print('-' * 70)
    print('AssertionError: 195 not less than or equal to 193 : new type errors')
    print('-' * 70)
    print('Ran 4 tests in 0.100s\n\nFAILED (failures=1)')
    sys.exit(1)
print('Ran 4 tests in 0.100s\n\nOK')
'''


class CircuitCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write('checks/red.py', CHECK)
        self.write('tests/test_frozen.py', '# test existant, hors du périmètre de la fiche\n')
        self.write('docs/QUESTIONS.md', '# Questions\n\n')
        self.write('fiches/t1.md', '---\nid: t1\nagent: bric\nallowed:\n  - src/**\n---\n# Fiche t1\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'checks')

    def plan(self, verify, *lines, name='docs/plan-c.md'):
        text = f'session: S1\nby: owner\nverify: {verify}\nreport: docs/RAPPORT.md\n- fiche: fiches/t1.md\n- fusion validée: t1\n'
        self.write(name, text + ''.join(line + '\n' for line in lines))
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'plan ' + name)
        return name

    def steps_called(self):
        return [call for call in self.calls() if not call.startswith('report')]


class VerifyEnvironmentTests(CircuitCase):
    def test_launch_variables_do_not_reach_the_host_verification(self):
        # Nuit du 3/11 : WORKSPACE_IMAGE_PYTHON exportée au lancement a fait rougir test_sandbox dans tous les clones.
        self.env.update(WORKSPACE_IMAGE_PYTHON='workspace-python-git:3.14', WORKSPACE_TESTS='fast', SOME_API_TOKEN='abc123456')
        verify = (
            'test -z "${WORKSPACE_IMAGE_PYTHON+x}" && test -z "${WORKSPACE_TESTS+x}" && test -z "${SOME_API_TOKEN+x}" '
            '&& test -z "${ORCHESTRATOR_TMP+x}" && test -n "$PATH" && test -n "$HOME" && test -f src/t1.txt'
        )
        name = self.plan(verify)
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.steps_called(), ['prepare t1', 'delegate t1', 'merge t1'])


class TriageTests(CircuitCase):
    def test_red_on_the_base_too_counts_nothing_and_reruns_the_same_verification(self):
        broken = self.base / 'environnement-cassé'
        broken.write_text('variable d\'image héritée\n')
        name = self.plan(f'python3 checks/red.py {broken} tests.test_sandbox.SandboxTests.test_default_image')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 4, done.stderr + done.stdout)
        # Ni diagnostic, ni abandon, ni nouvelle délégation : l'exécutant n'est pas en cause.
        self.assertEqual(self.steps_called(), ['prepare t1', 'delegate t1'])
        entry = self.journal()['tasks']['t1']
        self.assertEqual((entry['failures'], entry['verify_category']), (0, 'environnement'))
        self.assertEqual(entry['steps']['verify']['category'], 'environnement')
        self.assertEqual(entry['notice'], '.workspace/orchestrator-run/ENVIRONNEMENT-t1.md')
        notice = (self.root / entry['notice']).read_text()
        self.assertIn('tests.test_sandbox.SandboxTests.test_default_image', notice)
        self.assertIn('Aucune tentative', notice)
        self.assertIn('· t1 (usine, environnement', (self.root / 'docs' / 'QUESTIONS.md').read_text())
        # La ligne de docs/QUESTIONS.md est commitée aussitôt (le rapport attend la vraie fin, comme pour une question).
        self.assertEqual(self.git('status', '--porcelain', '--', 'docs/QUESTIONS.md'), '')
        self.assertTrue(any('catégorie environnement' in text for text in self.journal()['decisions']))
        # L'environnement corrigé, la même commande rejoue la vérification telle quelle et fusionne.
        broken.unlink()
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.steps_called(), ['prepare t1', 'delegate t1', 'merge t1'])
        self.assertEqual(self.journal()['tasks']['t1']['failures'], 0)
        self.assert_delivered()

    def test_new_red_test_in_a_frozen_file_stops_with_a_question_and_counts_nothing(self):
        name = self.plan(f'python3 checks/red.py src/t1.txt {FROZEN}')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 4, done.stderr + done.stdout)
        self.assertEqual(self.steps_called(), ['prepare t1', 'delegate t1'])
        entry = self.journal()['tasks']['t1']
        self.assertEqual((entry['failures'], entry['verify_category']), (0, 'fiche'))
        notice = (self.root / '.workspace' / 'orchestrator-run' / 'FICHE-t1.md').read_text()
        self.assertIn('`tests/test_frozen.py`', notice)
        self.assertIn('allowed', notice)
        self.assertIn('· t1 (usine, fiche', (self.root / 'docs' / 'QUESTIONS.md').read_text())

    def test_a_red_test_the_fiche_allows_still_counts_for_the_executor(self):
        self.write('fiches/t1.md', '---\nid: t1\nagent: bric\nallowed:\n  - src/**\n  - tests/test_frozen.py\n---\n# Fiche t1\n')
        name = self.plan(f'python3 checks/red.py src/t1.txt {FROZEN}')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 1, done.stderr + done.stdout + '\n'.join(self.calls()))
        entry = self.journal()['tasks']['t1']
        self.assertEqual((entry['failures'], entry['verify_category']), (2, 'exécutant'))
        self.assertIn('claude sonnet', self.calls())
        # T120 : le travail de la fiche en échec est dans un diff complet qui s'applique sur la base, son clone est fermé.
        patch = self.root / entry['patch']
        self.assertIn('diff --git a/src/t1.txt b/src/t1.txt', patch.read_text())
        self.assertIn('+livré', patch.read_text())
        self.assertEqual(patch.stat().st_mode & 0o777, 0o600)
        self.git('apply', '--check', str(patch))
        self.assertFalse((self.root / '.workspace' / 'worktrees' / 't1').exists())
        # La base n'est rejouée qu'une fois par passage, même pour la seconde vérification rouge.
        base_log = self.root / '.workspace' / 'orchestrator-run' / 't1-verify_base.log'
        self.assertEqual(base_log.read_text().count('Ran 4 tests'), 1)


class TypesRoundTests(CircuitCase):
    def setUp(self):
        super().setUp()
        self.write('checks/budget.py', TYPES_CHECK)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'types check')
        # Faux pyright : une erreur dans le fichier de la tâche, une autre ailleurs (déjà dans le budget de la base).
        diagnostics = [
            {
                'file': 'src/t1.py',
                'severity': 'error',
                'message': 'Argument of type "str | None"',
                'rule': 'reportArgumentType',
                'range': {'start': {'line': 210, 'character': 4}},
            },
            {'file': 'tools/old.py', 'severity': 'error', 'message': 'erreur ancienne', 'range': {'start': {'line': 1}}},
        ]
        fake = self.base / 'fake-pyright'
        body = 'import json, os\nd = %r\nfor x in d:\n    x["file"] = os.path.join(os.getcwd(), x["file"])\n' % diagnostics
        fake.write_text('#!%s\n%sprint(json.dumps({"generalDiagnostics": d}))\n' % (sys.executable, body))
        fake.chmod(0o755)
        self.env['ORCHESTRATOR_PYRIGHT'] = str(fake)

    def test_type_budget_goes_once_to_a_correction_round_without_failure(self):
        # gt2-run-states, 3/11 : 195 > 193, pyright absent du conteneur, la fiche finie a été arrêtée.
        self.control(python={'t1': ['x = bad\n', 'x = ok\n']})
        name = self.plan(f'python3 checks/budget.py {BUDGET}')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        # Ni diagnostic de modèle, ni base rejouée, ni abandon : la même tâche repart sur son clone.
        self.assertEqual(self.steps_called(), ['prepare t1', 'delegate t1', 'delegate t1', 'merge t1'])
        self.assertFalse((self.root / '.workspace' / 'orchestrator-run' / 't1-verify_base.log').exists())
        entry = self.journal()['tasks']['t1']
        self.assertEqual((entry['failures'], entry['types_round']), (0, True))
        second = self.delegate_env('t1')[1]
        self.assertEqual(second['override'], 'claude-haiku')
        self.assertIn('budget de types pyright', second['context'])
        self.assertIn('src/t1.py:211:5: error: Argument of type "str | None" (reportArgumentType)', second['context'])
        self.assertNotIn('tools/old.py', second['context'])
        self.assert_delivered()

    def test_a_second_type_budget_failure_counts(self):
        self.control(python={'t1': ['x = bad\n', 'x = bad\n', 'x = bad\n']})
        name = self.plan(f'python3 checks/budget.py {BUDGET}')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
        entry = self.journal()['tasks']['t1']
        self.assertEqual(entry['failures'], 2)
        self.assertEqual(entry['reason'], 'vérification : budget de types pyright rouge après un premier échec')
        self.assertEqual(self.calls().count('delegate t1'), 3)
        self.assertNotIn('claude sonnet', self.calls())
        self.assertIn('x = bad', json.dumps((self.root / entry['patch']).read_text()))


class DiagnosisTests(CircuitCase):
    """T119 : le diagnostic de gt2-run-states (3/11) a fini en `error_max_turns` sur la fin brute du journal."""

    def test_the_diagnosis_gets_the_red_blocks_and_answers_in_one_turn(self):
        # Un test rouge dont le fichier n'existe pas, avec le seul travail de la tâche : l'exécutant est en cause.
        name = self.plan('echo BRUIT-AVANT-LES-TESTS; python3 checks/red.py src/t1.txt tests.test_absent.A.test_lost')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
        prompt = (self.root / '.workspace' / 'fake' / 'prompt-sonnet.txt').read_text()
        journal_part = prompt.split("## Journal de l'étape", 1)[1]
        self.assertIn('FAIL: test_lost (tests.test_absent.A.test_lost)', journal_part)
        self.assertNotIn('BRUIT-AVANT-LES-TESTS', journal_part)
        self.assertIn('réponds en un tour', prompt)

    def test_a_failed_diagnosis_still_hands_the_red_tests_to_the_retry(self):
        marker = self.base / 'rouge-une-fois'
        marker.write_text('1\n')
        # La vérification n'est rouge qu'une fois : la reprise passe, et sa consigne porte les tests rouges.
        verify = f'if test -f src/t1.txt; then python3 checks/red.py {marker} tests.test_absent.A.test_lost; code=$?; rm -f {marker}; exit $code; fi'
        name = self.plan(verify)
        self.control(claude={'sonnet': ['error_max_turns']})
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        retry = self.delegate_env('t1')[1]['context']
        self.assertIn('Diagnostic du modèle impossible (error_max_turns)', retry)
        self.assertIn('FAIL: test_lost (tests.test_absent.A.test_lost)', retry)
        self.assertTrue(any('diagnostic du programme (Sonnet : error_max_turns' in text for text in self.journal()['decisions']))
        self.assertEqual(self.journal()['tasks']['t1']['failures'], 1)


if __name__ == '__main__':
    unittest.main()
