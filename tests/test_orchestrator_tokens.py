"""Économies de tokens du programme d'orchestration, mesurées sur E13 bras D (27/10) : format corrigé sans modèle,
lint sans diagnostic (T99, T114), corrections masquées avant la consigne (T100),
relecture dans le clone et relecture ratée dans le rapport (T101), relecture qui juge sans fouiller (T112),
seconde relecture limitée à la ronde de correction (T113), rapport qui compte tout (T102). Faux circuit et faux `claude` de test_orchestrator_run."""

import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel et sous-processus ; suite complète seulement')

try:
    from tests.test_orchestrator_run import ProjectCase
except ImportError:
    from test_orchestrator_run import ProjectCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'orchestrator'))

import review  # noqa: E402

BASE = 'session: S1\nby: owner\nverify: python3 checks/quality.py {}\nreport: docs/RAPPORT.md\n'

# Vérification factice : rend la sortie unittest de la suite de qualité tant que `src/t1.py` garde `x=1`.
QUALITY_CHECK = r'''import sys
from pathlib import Path
kind, path = sys.argv[1], Path('src/t1.py')
if path.is_file() and 'x=1' in path.read_text():
    name = 'test_ruff_%s_is_clean' % kind
    print('=' * 70)
    print('FAIL: %s (tests.test_code_quality.CodeQualityTests.%s)' % (name, name))
    print('-' * 70)
    print('AssertionError: 1 != 0 : src/t1.py:1:2: E225 Missing whitespace around operator')
    print('-' * 70)
    print('Ran 4 tests in 0.100s\n\nFAILED (failures=1)')
    sys.exit(1)
print('Ran 4 tests in 0.100s\n\nOK')
'''

# Faux ruff : met `x = 1` en forme dans les fichiers donnés et note l'appel dans le journal du faux circuit.
FAKE_RUFF = r'''import sys
from pathlib import Path
names = sys.argv[sys.argv.index('--') + 1:]
for name in names:
    Path(name).write_text(Path(name).read_text().replace('x=1', 'x = 1'))
with open(sys.argv[1], 'a') as stream:
    stream.write('ruff %s\n' % ' '.join(sys.argv[2:]))
'''


class TokensCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write('checks/quality.py', QUALITY_CHECK)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'checks')
        ruff = self.base / 'fake-ruff.py'
        ruff.write_text(FAKE_RUFF)
        calls = self.root / '.workspace' / 'fake' / 'calls.log'
        self.env['ORCHESTRATOR_RUFF'] = ' '.join(shlex.quote(part) for part in (sys.executable, str(ruff), str(calls)))

    def plan(self, kind, *lines, name='docs/plan-t.md'):
        self.write(name, BASE.format(kind) + ''.join(line + '\n' for line in lines))
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'plan ' + name)
        return name


class QualityTests(TokensCase):
    def test_format_alone_is_fixed_by_the_program_without_model_or_failure(self):
        self.control(python={'t1': ['x=1\n']})
        name = self.plan('format', '- fiche: fiches/t1.md', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        calls = [call for call in self.calls() if not call.startswith('report')]
        self.assertEqual(calls, ['prepare t1', 'delegate t1', 'ruff format --no-cache -- src/t1.py', 'merge t1'])
        entry = self.journal()['tasks']['t1']
        self.assertEqual((entry['failures'], entry['steps']['verify']['status']), (0, 'done'))
        self.assertIn('`ruff format` lancé par le programme', ' '.join(self.journal()['decisions']))

    def test_lint_goes_to_a_short_round_with_the_tool_output_and_no_diagnosis(self):
        self.control(python={'t1': ['x=1\n', 'x = 1\n']})
        name = self.plan('lint', '- fiche: fiches/t1.md', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        calls = [call for call in self.calls() if not call.startswith('report')]
        # Ni diagnostic Sonnet, ni abandon puis reprise complète : la même tâche repart sur son clone.
        self.assertEqual(calls, ['prepare t1', 'delegate t1', 'delegate t1', 'merge t1'])
        second = self.delegate_env('t1')[1]
        self.assertEqual(second['override'], 'claude-haiku')
        self.assertIn('E225 Missing whitespace around operator', second['context'])
        self.assertIn('FAIL: test_ruff_lint_is_clean', second['context'])
        self.assertEqual(self.journal()['tasks']['t1']['failures'], 1)

    def test_a_failure_of_another_test_still_gets_a_diagnosis(self):
        self.control(python={'t1': ['x=1\n']})
        # Rouge avec le travail de la tâche seulement : rouge sur la base aussi, ce serait l'environnement (T117).
        self.write(
            'checks/quality.py',
            'import os, sys\nif os.path.exists("src/t1.txt"):\n    print("FAIL: test_merge (tests.test_x.X.test_merge)")\n    sys.exit(1)\n',
        )
        name = self.plan('format', '- fiche: fiches/t1.md', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
        self.assertIn('claude sonnet', self.calls())
        self.assertFalse([call for call in self.calls() if call.startswith('ruff')])


class MaskTests(TokensCase):
    """Le faux circuit refuse, comme `delegate.py`, une consigne qui porte un secret (« Secret dans la consigne »)."""

    def test_review_quoting_a_secret_assignment_leads_to_a_correction_round_that_starts(self):
        fix = 'VERDICT: à corriger\n- src/t1.txt : la ligne `secret = tmp_value` doit passer par safety.write_file_secure'
        self.control(claude={'opus': [fix, 'VERDICT: bon']})
        name = self.plan('format', '- fiche: fiches/t1.md | relecture: opus', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        second = self.delegate_env('t1')[1]
        self.assertIn('secret = ***', second['context'])
        self.assertIn('safety.write_file_secure', second['context'])
        self.assertIn('merge t1', self.calls())

    def test_a_diagnosis_quoting_a_secret_assignment_reaches_the_retry(self):
        self.control(outcomes={'t1': ['failed']}, claude={'sonnet': ['Cause : `API_TOKEN = "abc123456"` écrit en clair dans le test.']})
        name = self.plan('format', '- fiche: fiches/t1.md', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        retry = self.delegate_env('t1')[1]
        self.assertIn('API_TOKEN = ***', retry['context'])
        self.assertNotIn('abc123456', retry['context'])


class ReviewPlaceTests(TokensCase):
    def test_review_runs_read_only_in_the_task_clone_and_diagnosis_in_the_project(self):
        self.control(outcomes={'t1': ['failed']})
        name = self.plan('format', '- fiche: fiches/t1.md | relecture: opus', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        fake = self.root / '.workspace' / 'fake'
        self.assertEqual((fake / 'cwd-opus.txt').read_text(), str(self.root / '.workspace' / 'worktrees' / 't1'))
        self.assertEqual((fake / 'cwd-sonnet.txt').read_text(), str(self.root))
        for model in ('opus', 'sonnet'):
            args = (fake / f'args-{model}.txt').read_text().splitlines()
            self.assertEqual(args[args.index('--tools') + 1], 'Read,Glob,Grep')
            self.assertEqual(args[args.index('--setting-sources') + 1], 'user')
            self.assertIn('--strict-mcp-config', args)

    def test_a_failed_review_is_in_the_defects_of_the_report(self):
        self.control(claude={'opus': ['error_max_turns']})
        name = self.plan('format', '- fiche: fiches/t1.md | relecture: opus')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 4, done.stderr + done.stdout)
        report = (self.root / 'docs' / 'RAPPORT.md').read_text()
        defects = report.split("## Défauts rencontrés et décisions prises à la place du propriétaire")[1].split('\n## ')[0]
        self.assertIn('relecture ciblée ratée (error_max_turns)', defects)


class ReviewPromptTests(TokensCase):
    def test_review_prompt_gives_new_files_whole_and_the_circuit_primitives(self):
        name = self.plan('format', '- fiche: fiches/t1.md | relecture: opus', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        prompt = (self.root / '.workspace' / 'fake' / 'prompt-opus.txt').read_text()
        # Le contenu vient du fichier du clone (« livré »), pas seulement du diff (« livré (ronde 1) »).
        self.assertIn('## Fichiers neufs, contenu entier\n\n### src/t1.txt\n\n```\nlivré\n\n```', prompt)
        self.assertIn('- `scripts/lib/workdir.py` : clone de tâche', prompt)
        self.assertIn('- `scripts/lib/safety.py` : refus des liens symboliques', prompt)
        self.assertIn('en un ou deux tours', prompt)
        args = (self.root / '.workspace' / 'fake' / 'args-opus.txt').read_text().splitlines()
        self.assertEqual(args[args.index('--max-turns') + 1], '12')


class RecheckTests(TokensCase):
    def test_second_review_gets_the_round_diff_and_the_requested_fixes_only(self):
        fix = 'VERDICT: à corriger\n- src/t1.txt : écrire « livré » sans faute'
        self.control(claude={'opus': [fix, 'VERDICT: bon\nfaite']})
        name = self.plan('format', '- fiche: fiches/t1.md | relecture: opus', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        fake = self.root / '.workspace' / 'fake'
        first, second = (fake / 'prompt-opus-1.txt').read_text(), (fake / 'prompt-opus-2.txt').read_text()
        self.assertIn('+livré (ronde 1)', first)
        self.assertIn('## Diff de la ronde de correction', second)
        self.assertIn('+livré (ronde 2)', second)
        self.assertNotIn('ronde 1', second)
        self.assertIn('écrire « livré » sans faute', second)
        self.assertIn('dis seulement si elle est faite', second)
        self.assertNotIn('## Fichiers neufs', second)
        self.assertEqual(self.journal()['tasks']['t1']['review_verdict'], 'bon (Opus)')

    def test_fixes_not_done_stop_the_task(self):
        fix = 'VERDICT: à corriger\n- src/t1.txt : écrire « livré » sans faute'
        self.control(claude={'opus': [fix, 'VERDICT: à corriger\nnon faite : faute restée']})
        name = self.plan('format', '- fiche: fiches/t1.md | relecture: opus', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
        self.assertEqual(self.journal()['failed'], ['t1'])
        self.assertNotIn('merge t1', self.calls())


class CostTests(TokensCase):
    def test_report_adds_every_call_of_the_task_in_dollars(self):
        # Reprise après un premier échec (diagnostic Sonnet), relecture qui demande une correction, seconde relecture.
        fix = 'VERDICT: à corriger\n- src/t1.txt : écrire « livré » sans faute'
        self.control(outcomes={'t1': ['failed']}, claude={'opus': [fix, 'VERDICT: bon']})
        name = self.plan('format', '- fiche: fiches/t1.md | relecture: opus', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
        logs = self.root / '.workspace' / 'orchestrator-run'
        kept = sorted(logs.glob('t1-*-*-*.json')) + sorted((self.root / '.workspace' / 'fake').glob('executor-t1-*.json'))
        expected = sum(json.loads(path.read_text())['total_cost_usd'] for path in kept)
        names = [path.name for path in kept]
        self.assertEqual(len([n for n in names if n.startswith('executor-')]), 2)
        self.assertEqual(len([n for n in names if '-reprise-' in n]), 1)
        self.assertEqual(len([n for n in names if '-relecture-' in n]), 1)
        report = (self.root / 'docs' / 'RAPPORT.md').read_text()
        total = [line for line in report.splitlines() if line.startswith('| **Total** | 4 |')]
        self.assertEqual(len(total), 1, report)
        self.assertTrue(total[0].endswith(' | %.4f |' % expected), total[0])
        row = [line for line in report.splitlines() if line.startswith('| t1 | 4 | execution ×2, relecture ×1, reprise ×1 |')]
        self.assertEqual(len(row), 1, report)
        self.assertIn('220 / 440 / 660 / 110', row[0])

    def test_calls_of_an_earlier_run_are_not_counted(self):
        sys.path.insert(0, str(self.root / 'scripts' / 'lib'))
        try:
            import consumption
        finally:
            sys.path.pop(0)
        consumption.record(self.root, role='execution', provider='claude', task_id='t1', cost_usd=9.0)
        rows = (self.root / '.workspace' / 'metrics' / 'consumption.jsonl').read_text().splitlines()
        old = json.loads(rows[0])
        old['at'] = '2020-01-01T00:00:00+00:00'
        (self.root / '.workspace' / 'metrics' / 'consumption.jsonl').write_text(json.dumps(old) + '\n')
        name = self.plan('format', '- fiche: fiches/t1.md')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 4, done.stderr + done.stdout)
        report = (self.root / 'docs' / 'RAPPORT.md').read_text()
        self.assertIn('| **Total** | 1 |', report)
        self.assertNotIn('9.', report.split('Coût complet par fiche')[1])


class NewFilesTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name))
        self.clone = self.root / 'clone'
        (self.clone / 'src').mkdir(parents=True)

    def diff(self, *names, new=True):
        mode = 'new file mode 100644\n' if new else 'index 1..2\n'
        return ''.join(f'diff --git a/{name} b/{name}\n{mode}--- /dev/null\n+++ b/{name}\n' for name in names)

    def test_only_new_files_inside_the_clone_and_masked(self):
        (self.clone / 'src' / 'neuf.py').write_text('API_TOKEN = "abc123456"\nx = 1\n')
        (self.clone / 'src' / 'ancien.py').write_text('ancien\n')
        text = review.new_files(self.root, self.clone, self.diff('src/neuf.py', '../dehors.py') + self.diff('src/ancien.py', new=False))
        self.assertIn('### src/neuf.py', text)
        self.assertIn('API_TOKEN = ***\nx = 1', text)
        self.assertNotIn('ancien', text)
        self.assertNotIn('dehors', text)

    def test_caps_per_file_and_in_total(self):
        names = [f'src/f{index}.py' for index in range(4)]
        for name in names:
            (self.clone / name).write_text('a' * (review.NEW_FILE_LIMIT + 10))
        text = review.new_files(self.root, self.clone, self.diff(*names))
        self.assertIn(f'tronqué à {review.NEW_FILE_LIMIT} caractères', text)
        self.assertIn(f'### src/f3.py\n\n(non donné : plafond de {review.NEW_FILES_LIMIT} caractères atteint', text)
        self.assertLess(len(text), review.NEW_FILES_LIMIT + 2000)


if __name__ == '__main__':
    unittest.main()
