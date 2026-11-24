"""Phase B de T14 : le programme s'arrête où le propriétaire décide (fusion, proposition, question), répartit par vagues,
diagnostique, relit les fiches importantes et écrit l'entrée du LOG. Faux circuit et faux `claude` de test_orchestrator_run."""

import os
import signal
import sys
import unittest
from pathlib import Path

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel et sous-processus ; suite complète seulement')

try:
    from tests.test_orchestrator_run import ProjectCase
except ImportError:
    from test_orchestrator_run import ProjectCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'orchestrator'))

import plan as planning  # noqa: E402
import steps  # noqa: E402

WAITING = 4
BASE = 'session: S1\nby: owner\nverify: test -d "$WORKSPACE_TEST_TMP"\nreport: docs/RAPPORT.md\n'

FAKE_PROPOSE = r'''#!/usr/bin/env python3
import sys
from pathlib import Path
args = sys.argv[1:]
task_id = args[args.index('--from-demande') + 2]
with open('.workspace/fake/calls.log', 'a') as stream:
    stream.write('propose %s %s\n' % (task_id, args[args.index('--executor') + 1]))
folder = Path('.workspace/proposals') / task_id
(folder / 'acceptance' / 'tests').mkdir(parents=True)
(folder / 'task.md').write_text('---\nid: %s\nagent: bric\n---\n# Fiche proposée %s\n' % (task_id, task_id))
(folder / 'acceptance' / 'tests' / 'test_cache.py').write_text('def test_cache():\n    pass\n')
'''

FAKE_PLAN_CHECK = r'''#!/usr/bin/env python3
import json, sys
from pathlib import Path
with open('.workspace/fake/calls.log', 'a') as stream:
    stream.write('plan-check %s\n' % sys.argv[1])
# Le vrai plan-check lit les tests cachés de la fiche dans .workspace/acceptance/<id>/.
if not Path('.workspace/acceptance/t3/tests/test_cache.py').is_file():
    print('verdict : à reprendre (test caché absent)')
    sys.exit(1)
control = Path('.workspace/fake/control.json')
bad = control.exists() and json.loads(control.read_text()).get('plan_check_fail')
print('verdict : ' + ('à reprendre (1 défaut)' if bad else 'bon'))
sys.exit(1 if bad else 0)
'''


class PhaseBCase(ProjectCase):
    def setUp(self):
        super().setUp()
        for name, body in (('prepare-task.sh', FAKE_PROPOSE), ('plan-check.sh', FAKE_PLAN_CHECK)):
            self.write('scripts/fake-' + name + '.py', body)
            self.write('scripts/' + name, '#!/bin/bash\nd=$(cd "$(dirname "$0")" && pwd)\nexec python3 "$d/fake-%s.py" "$@"\n' % name)
        (self.root / '.workspace' / 'fake').mkdir(parents=True, exist_ok=True)

    def plan(self, *lines, name='docs/plan-b.md'):
        self.write(name, BASE + ''.join(line + '\n' for line in lines))
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'plan ' + name)
        return name

    def add(self, name, line):
        path = self.root / name
        path.write_text(path.read_text() + line + '\n')

    def notice(self, kind, task_id):
        return (self.root / '.workspace' / 'orchestrator-run' / f'{kind}-{task_id}.md').read_text()


class MergeValidationTests(PhaseBCase):
    def test_ready_task_waits_for_validation_then_merges_once(self):
        name = self.plan('- fiche: fiches/t1.md')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, WAITING, done.stderr + done.stdout)
        self.assertNotIn('merge t1', self.calls())
        data = self.journal()
        self.assertEqual((data['status'], data['stop_reason'], data['done']), ('stopped', 'validation', []))
        notice = self.notice('PRET', 't1')
        self.assertIn('- fusion validée: t1', notice)
        self.assertIn('src/t1.txt', notice)
        self.assertIn("en attente du propriétaire", (self.root / 'docs' / 'RAPPORT.md').read_text())
        self.assertFalse(self.lock().exists())
        # Relancer sans la ligne : rien n'est refait, toujours en attente.
        self.assertEqual(self.run_cli(plan=name).returncode, WAITING)
        self.add(name, '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        calls = self.calls()
        self.assertEqual((calls.count('delegate t1'), calls.count('merge t1'), calls.count('prepare t1')), (1, 1, 1))
        self.assertEqual(self.journal()['done'], ['t1'])

    def test_decision_lines_do_not_change_the_plan_digest(self):
        name = self.plan('- fiche: fiches/t1.md')
        before = planning.load(self.root, name)['sha256']
        self.add(name, '- fusion validée: t1')
        self.add(name, '* proposition acceptée: `t9`')
        after = planning.load(self.root, name)
        self.assertEqual(after['sha256'], before)
        self.assertEqual((after['merge_validated'], after['accepted']), ({'t1'}, {'t9'}))


class WaveTests(PhaseBCase):
    def test_a_wave_delegates_together_with_the_executor_of_the_plan(self):
        self.control(sleeps=[1.0])
        name = self.plan(
            '- fiche: fiches/t1.md | vague: 1 | exécutant: claude-haiku',
            '- fiche: fiches/t2.md | vague: 1',
            '- fusion validée: t1',
            '- fusion validée: t2',
        )
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        first, second = self.delegate_env('t1'), self.delegate_env('t2')
        self.assertEqual((len(first), len(second)), (1, 1))
        self.assertLess(max(first[0]['start'], second[0]['start']), min(first[0]['end'], second[0]['end']), 'délégations en série')
        self.assertEqual((first[0]['override'], second[0]['override']), ('claude-haiku', None))
        calls = self.calls()
        self.assertGreater(
            min(calls.index('merge t1'), calls.index('merge t2')), max(calls.index('delegate t1'), calls.index('delegate t2'))
        )
        self.assertTrue(any('délégations lancées ensemble' in text for text in self.journal()['decisions']))

    def test_kill_during_a_wave_waits_for_both_children_without_redelegating(self):
        self.control(sleeps=[3])
        name = self.plan(
            '- fiche: fiches/t1.md | vague: 1', '- fiche: fiches/t2.md | vague: 1', '- fusion validée: t1', '- fusion validée: t2'
        )
        program = self.detach(plan=name)
        children = self.wait_for(
            lambda: len((self.journal() or {}).get('children') or {}) == 2 and self.journal()['children'], 'vague lancée'
        )
        self.wait_for(lambda: 'delegate t1' in self.calls() and 'delegate t2' in self.calls(), 'faux délégués lancés')
        os.kill(program, signal.SIGKILL)
        self.wait_for(lambda: not steps.alive(program), 'programme tué')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        for task_id in ('t1', 't2'):
            # Attendus l'un après l'autre : le second a souvent fini pendant l'attente du premier.
            self.assertIn('étape delegate de {} : le processus enfant {}'.format(task_id, children[task_id]['pid']), done.stdout)
            self.assertEqual((self.calls().count('delegate ' + task_id), self.calls().count('merge ' + task_id)), (1, 1))
        self.assertEqual(self.journal()['children'], {})

    def test_plan_wave_rules(self):
        for name in ('t3', 't4'):
            self.write(f'fiches/{name}.md', f'---\nid: {name}\n---\n')
        cases = {
            'vague trop pleine': [f'- fiche: fiches/t{n}.md | vague: 1' for n in (1, 2, 3, 4)],
            'vague partielle': ['- fiche: fiches/t1.md | vague: 1', '- fiche: fiches/t2.md'],
            'exécutant inconnu': ['- fiche: fiches/t1.md | exécutant: gpt'],
            'attribut inconnu': ['- fiche: fiches/t1.md | priorité: haute'],
            'relecture inconnue': ['- fiche: fiches/t1.md | relecture: haiku'],
            'demande sans id': ['- demande: fiches/t1.md'],
        }
        for label, lines in cases.items():
            with self.subTest(label):
                self.write('docs/p.md', BASE + '\n'.join(lines) + '\n')
                with self.assertRaises(planning.PlanError):
                    planning.load(self.root, 'docs/p.md')


class QuestionTests(PhaseBCase):
    def test_a_question_stops_the_plan_and_the_answer_resumes_it(self):
        self.write('docs/QUESTIONS.md', '# Questions pour le propriétaire\n\n## Questions\n\n- [x] 2026-10-01 · ancienne · déjà répondue\n')
        self.control(outcomes={'t1': ['waiting_answer', 'done']})
        name = self.plan('- fiche: fiches/t1.md', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, WAITING, done.stderr + done.stdout)
        self.assertNotIn('merge t1', self.calls())
        questions = (self.root / 'docs' / 'QUESTIONS.md').read_text()
        self.assertIn('- [ ] ', questions)
        self.assertIn('· t1 (question 001, usine) · Quelle base de données faut-il garder ?', questions)
        self.assertTrue(questions.startswith('# Questions pour le propriétaire\n\n## Questions\n\n- [x] 2026-10-01'))
        self.assertEqual(self.git('status', '--porcelain', '--', 'docs/QUESTIONS.md'), '', 'QUESTIONS.md commité')
        self.assertEqual(self.journal()['stop_reason'], 'question')
        # Sans réponse : pas de nouvelle délégation, pas de ligne en double.
        self.assertEqual(self.run_cli(plan=name).returncode, WAITING)
        self.assertEqual(self.calls().count('delegate t1'), 1)
        self.assertEqual((self.root / 'docs' / 'QUESTIONS.md').read_text().count('t1 (question 001'), 1)
        answers = self.root / '.workspace' / 'orchestration' / 'tasks' / 't1' / 'context' / 'answers'
        answers.mkdir(parents=True)
        (answers / '001.md').write_text('Garder SQLite.\n')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual((self.calls().count('delegate t1'), self.calls().count('merge t1')), (2, 1))
        self.assertEqual(self.journal()['tasks']['t1']['failures'], 0)


class ReviewTests(PhaseBCase):
    def test_review_corrections_go_through_a_correction_round(self):
        self.control(claude={'opus': ['VERDICT: à corriger\n- src/t1.txt : écrire « livré » sans faute', 'VERDICT: bon']})
        name = self.plan('- fiche: fiches/t1.md | relecture: opus', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        calls = [call for call in self.calls() if not call.startswith('report')]
        self.assertEqual(calls, ['prepare t1', 'delegate t1', 'claude opus', 'delegate t1', 'claude opus', 'merge t1'])
        second = self.delegate_env('t1')[1]
        self.assertEqual(second['override'], 'claude-haiku')
        self.assertIn('écrire « livré » sans faute', second['context'])
        entry = self.journal()['tasks']['t1']
        self.assertEqual((entry['failures'], entry['review_verdict']), (1, 'bon (Opus)'))
        self.assertIn('## Diff', (self.root / '.workspace' / 'fake' / 'prompt-opus.txt').read_text())
        # La relecture ouvre les fichiers du diff : plus de tours que le diagnostic (E13 bras D, 27/10).
        args = (self.root / '.workspace' / 'fake' / 'args-opus.txt').read_text().splitlines()
        self.assertEqual(args[args.index('--max-turns') + 1], '12')
        # Les réponses brutes des deux relectures sont gardées pour le coût en dollars.
        kept = sorted(p.name for p in (self.root / '.workspace' / 'orchestrator-run').glob('t1-relecture-opus-*.json'))
        self.assertEqual(kept, ['t1-relecture-opus-1.json', 't1-relecture-opus-2.json'])

    def test_two_blocking_reviews_stop_the_task(self):
        blocking = 'VERDICT: à corriger\n- tout reprendre'
        self.control(claude={'opus': [blocking, blocking]})
        name = self.plan('- fiche: fiches/t1.md | relecture: opus', '- fusion validée: t1')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
        self.assertEqual(self.journal()['failed'], ['t1'])
        self.assertNotIn('merge t1', self.calls())


class DemandeTests(PhaseBCase):
    def test_a_demande_becomes_a_task_only_after_acceptance(self):
        self.write('docs/demande.md', '# Demande\n\n## Besoin\nUn cache.\n')
        name = self.plan('- demande: docs/demande.md | id: t3 | exécutant: claude-haiku')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, WAITING, done.stderr + done.stdout)
        self.assertEqual(self.calls(), ['propose t3 claude-haiku', 'plan-check .workspace/proposals/t3/task.md'])
        self.assertIn('- proposition acceptée: t3', self.notice('PROPOSITION', 't3'))
        self.assertFalse((self.root / '.workspace' / 'tasks' / 't3.md').exists())
        self.add(name, '- proposition acceptée: t3')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, WAITING, done.stderr + done.stdout)
        self.assertIn('Fiche proposée t3', (self.root / '.workspace' / 'tasks' / 't3.md').read_text())
        self.assertTrue((self.root / '.workspace' / 'acceptance' / 't3' / 'tests' / 'test_cache.py').is_file())
        self.assertEqual(self.journal()['stop_reason'], 'validation')
        self.add(name, '- fusion validée: t3')
        self.assertEqual(self.run_cli(plan=name).returncode, 0)
        self.assertEqual((self.calls().count('propose t3 claude-haiku'), self.calls().count('merge t3')), (1, 1))

    def test_a_proposal_refused_by_plan_check_fails(self):
        self.write('docs/demande.md', '# Demande\n')
        self.control(plan_check_fail=True)
        name = self.plan('- demande: docs/demande.md | id: t3')
        done = self.run_cli(plan=name)
        self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
        self.assertIn('plan-check', self.journal()['tasks']['t3']['reason'])
        self.assertNotIn('prepare t3', self.calls())


class LogTests(PhaseBCase):
    def test_finished_plan_appends_and_commits_its_log_entry(self):
        old = '# Journal\n\n## [2026-10-01] feat | Ancienne entrée\nTexte.\n'
        self.write('docs/LOG.md', old)
        name = self.plan('- fiche: fiches/t1.md', '- fusion validée: t1')
        self.assertEqual(self.run_cli(plan=name).returncode, 0)
        log = (self.root / 'docs' / 'LOG.md').read_text()
        self.assertTrue(log.startswith(old))
        self.assertIn('usine | Plan `docs/plan-b.md`, session S1', log)
        self.assertIn('1 fiche(s) fusionnée(s) (t1)', log)
        self.assertEqual(self.git('status', '--porcelain', '--', 'docs/LOG.md'), '')
        self.assertEqual(self.journal()['final']['log']['status'], 'done')

    def test_waiting_stop_writes_no_log_entry(self):
        self.write('docs/LOG.md', '# Journal\n')
        name = self.plan('- fiche: fiches/t1.md')
        self.assertEqual(self.run_cli(plan=name).returncode, WAITING)
        self.assertEqual((self.root / 'docs' / 'LOG.md').read_text(), '# Journal\n')
        self.assertNotIn('report-check docs/RAPPORT.md', self.calls())


if __name__ == '__main__':
    unittest.main()
