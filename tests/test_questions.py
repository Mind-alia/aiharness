import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import workdir  # noqa: E402


class QuestionsTests(unittest.TestCase):
    def setUp(self):
        # Les clones de tâche vont hors du projet, jamais dans le vrai ~/.workspace-ia.
        self.work_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.work_dir.cleanup)
        env_patch = mock.patch.dict(os.environ, {'WSC_NTFY_TOPIC': '', 'WORKSPACE_WORK_DIR': os.path.realpath(self.work_dir.name)})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        shutil.copytree(ROOT / 'template/scripts', self.root / 'scripts')
        for script in (self.root / 'scripts').glob('*.sh'):
            script.chmod(0o755)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def run_questions(self, *args, cwd=None):
        return subprocess.run(
            ['bash', str(self.root / 'scripts/questions.sh'), *args], cwd=str(cwd or self.root), capture_output=True, text=True
        )

    def test_unanswered_sources_dates_answers_and_links(self):
        import importlib.util
        import sys

        with mock.patch.object(sys, 'path', [str(self.root / 'scripts/lib')] + sys.path):
            spec = importlib.util.spec_from_file_location('task_questions', self.root / 'scripts/lib/questions.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        ctx = self.root / '.workspace/orchestration/tasks/demo/context'
        wt = self.root / 'worktree'
        exchange = wt / '.workspace/exchange'
        self.assertEqual(module.unanswered(self.root, 'demo'), [])
        for base in (ctx, exchange):
            (base / 'questions').mkdir(parents=True)
            (base / 'answers').mkdir()
        for base, names in ((ctx, ('001', '002', '003')), (exchange, ('002', '004'))):
            for name in names:
                path = base / 'questions' / (name + '.md')
                path.write_text('question')
                os.utime(path, (100, 100))
        (exchange / 'answers/001.md').write_text('answer')
        (ctx / 'answers/004.md').write_text('answer')
        (ctx / 'questions/not-a-number.md').write_text('ignored')
        (ctx / 'questions/005.md').symlink_to(ctx / 'questions/002.md')
        (ctx / 'answers/002.md').symlink_to(exchange / 'answers/001.md')
        self.assertEqual(module.unanswered(self.root, 'demo', since=100, worktree=wt), ['002', '003'])
        self.assertEqual(module.unanswered(self.root, 'demo', since=101, worktree=wt), [])
        (ctx / 'questions').rename(ctx / 'real-questions')
        (ctx / 'questions').symlink_to(ctx / 'real-questions', target_is_directory=True)
        self.assertEqual(module.unanswered(self.root, 'demo'), [])

    def test_suspended_questions_first_and_invalid_status_ignored(self):
        for name, stamp in (('ordinary', 10), ('suspended', 20)):
            folder = self.root / '.workspace/orchestration/tasks' / name / 'context/questions'
            folder.mkdir(parents=True)
            question = folder / '001.md'
            question.write_text(name + ' question')
            os.utime(question, (stamp, stamp))
        status = self.root / '.workspace/run/delegate/suspended/status.json'
        status.parent.mkdir(parents=True)
        status.write_text(json.dumps({'outcome': 'waiting_answer'}))
        result = self.run_questions()
        self.assertLess(result.stdout.index('suspended question'), result.stdout.index('ordinary question'))
        self.assertIn('tâche suspendue', result.stdout)
        self.assertIn('2', self.run_questions('--short').stdout)
        self.assertEqual(self.run_questions('--check').returncode, 1)
        for value in ('invalid json', '[]'):
            status.write_text(value)
            result = self.run_questions()
            self.assertNotIn('tâche suspendue', result.stdout)
            self.assertLess(result.stdout.index('ordinary question'), result.stdout.index('suspended question'))

    def test_alerts_only_unanswered_with_age(self):
        ctx = self.root / '.workspace/orchestration/tasks/demo/context'
        (ctx / 'questions').mkdir(parents=True)
        (ctx / 'answers').mkdir()
        (ctx / 'questions/001.md').write_text('private question')
        res = self.run_questions('--alerts')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('demo/001', res.stdout)
        self.assertIn('il y a', res.stdout)
        self.assertNotIn('private question', res.stdout)
        (ctx / 'answers/001.md').write_text('done')
        self.assertNotIn('demo/001', self.run_questions('--alerts').stdout)

    def test_doc_questions_open_answered_and_without_date(self):
        docs_dir = self.root / 'docs'
        docs_dir.mkdir(parents=True)
        (docs_dir / 'QUESTIONS.md').write_text(
            "# Questions pour le propriétaire\n\n"
            "## Questions\n"
            "- [ ] 2026-10-15 · plan · Question ouverte avec plan\n"
            "- [x] 2026-10-14 · dev · Question répondue en minuscule → réponse : faite\n"
            "- [X] 2026-10-13 · dev · Question répondue en majuscule → réponse : OK\n"
            "- [ ] Question ouverte sans date ni source\n",
            encoding='utf-8',
        )

        res = self.run_questions()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Question ouverte avec plan", res.stdout)
        self.assertIn("[plan]", res.stdout)
        self.assertIn("Question ouverte sans date ni source", res.stdout)
        self.assertNotIn("Question répondue en minuscule", res.stdout)
        self.assertNotIn("Question répondue en majuscule", res.stdout)

    def test_missing_questions_file(self):
        res = self.run_questions()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Aucune question en attente.", res.stdout)

    def test_agent_questions_unanswered_answered_and_advisor_unknown(self):
        tasks_base = self.root / '.workspace/orchestration/tasks'

        # 1. Question d'agent sans réponse
        t1 = tasks_base / 'task-open'
        q1_dir = t1 / 'context/questions'
        q1_dir.mkdir(parents=True)
        (q1_dir / '001.md').write_text("Faut-il migrer le schéma SQL ?", encoding='utf-8')

        # 2. Question d'agent avec réponse
        t2 = tasks_base / 'task-answered'
        q2_dir = t2 / 'context/questions'
        a2_dir = t2 / 'context/answers'
        q2_dir.mkdir(parents=True)
        a2_dir.mkdir(parents=True)
        (q2_dir / '001.md').write_text("Quelle version de Node ?", encoding='utf-8')
        (a2_dir / '001.md').write_text("Version 20 LTS.", encoding='utf-8')

        # 3. Question d'agent avec note advisor 'unknown'
        t3 = tasks_base / 'task-advisor'
        q3_dir = t3 / 'context/questions'
        adv3_dir = t3 / 'context/advisor'
        q3_dir.mkdir(parents=True)
        adv3_dir.mkdir(parents=True)
        (q3_dir / '001.md').write_text("Quel fournisseur d'authentification ?", encoding='utf-8')
        (adv3_dir / '001.json').write_text(json.dumps({"status": "unknown"}), encoding='utf-8')

        res = self.run_questions()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Faut-il migrer le schéma SQL ?", res.stdout)
        self.assertIn("task-open", res.stdout)
        self.assertNotIn("Quelle version de Node ?", res.stdout)
        self.assertNotIn("task-answered", res.stdout)
        self.assertIn("Quel fournisseur d'authentification ?", res.stdout)
        self.assertIn("le conseiller local ne sait pas", res.stdout)

    def test_closed_task_with_unanswered_question(self):
        tasks_base = self.root / '.workspace/orchestration/tasks'

        # Tâche avec statut merged
        t_merged = tasks_base / 'task-merged'
        (t_merged / 'context/questions').mkdir(parents=True)
        (t_merged / 'state.json').write_text(json.dumps({"status": "merged"}), encoding='utf-8')
        (t_merged / 'context/questions/001.md').write_text("Question dans tâche fusionnée", encoding='utf-8')

        # Tâche avec statut abandoned
        t_abandoned = tasks_base / 'task-abandoned'
        (t_abandoned / 'context/questions').mkdir(parents=True)
        (t_abandoned / 'state.json').write_text(json.dumps({"status": "abandoned"}), encoding='utf-8')
        (t_abandoned / 'context/questions/001.md').write_text("Question dans tâche abandonnée", encoding='utf-8')

        res = self.run_questions()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("question restée sans réponse dans une tâche close", res.stdout)
        self.assertIn("Question dans tâche fusionnée", res.stdout)
        self.assertIn("Question dans tâche abandonnée", res.stdout)

        # Les questions de tâches closes ne bloquent pas d'agents actifs
        res_short = self.run_questions('--short')
        self.assertEqual(res_short.returncode, 0, res_short.stderr)
        self.assertEqual(res_short.stdout.strip(), "Aucune question en attente.")

    def test_invalid_task_id_and_symlinks_ignored(self):
        tasks_base = self.root / '.workspace/orchestration/tasks'

        # 1. Identifiant invalide
        t_invalid = tasks_base / 'INVALID_ID_UPPER'
        (t_invalid / 'context/questions').mkdir(parents=True)
        (t_invalid / 'context/questions/001.md').write_text("Ignorée car id invalide", encoding='utf-8')

        # 2. Tâche sous forme de lien symbolique
        outside_task = self.root / 'outside-task'
        (outside_task / 'context/questions').mkdir(parents=True)
        (outside_task / 'context/questions/001.md').write_text("Ignorée car symlink", encoding='utf-8')
        tasks_base.mkdir(parents=True, exist_ok=True)
        (tasks_base / 'task-symlink').symlink_to(outside_task)

        # 3. Fichier question sous forme de lien symbolique
        t_sym_file = tasks_base / 'task-sym-file'
        (t_sym_file / 'context/questions').mkdir(parents=True)
        outside_file = self.root / 'outside_q.md'
        outside_file.write_text("Ignorée car fichier symlink", encoding='utf-8')
        (t_sym_file / 'context/questions/001.md').symlink_to(outside_file)

        # 4. docs/QUESTIONS.md sous forme de lien symbolique
        docs_dir = self.root / 'docs'
        docs_dir.mkdir(parents=True)
        outside_doc = self.root / 'outside_doc.md'
        outside_doc.write_text("- [ ] 2026-10-15 · doc · Ignorée car doc symlink\n", encoding='utf-8')
        (docs_dir / 'QUESTIONS.md').symlink_to(outside_doc)

        # 5. state.json invalide
        t_bad_json = tasks_base / 'task-bad-json'
        (t_bad_json / 'context/questions').mkdir(parents=True)
        (t_bad_json / 'state.json').write_text("{invalid json", encoding='utf-8')
        (t_bad_json / 'context/questions/001.md').write_text("Question avec state.json invalide", encoding='utf-8')

        res = self.run_questions()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("Ignorée car id invalide", res.stdout)
        self.assertNotIn("Ignorée car symlink", res.stdout)
        self.assertNotIn("Ignorée car fichier symlink", res.stdout)
        self.assertNotIn("Ignorée car doc symlink", res.stdout)
        # La tâche avec JSON invalide est tolérée et sa question reste visible
        self.assertIn("Question avec state.json invalide", res.stdout)

    def test_short_option(self):
        # Cas sans question
        res_empty = self.run_questions('--short')
        self.assertEqual(res_empty.returncode, 0, res_empty.stderr)
        self.assertEqual(res_empty.stdout.strip(), "Aucune question en attente.")

        # Cas avec questions
        docs_dir = self.root / 'docs'
        docs_dir.mkdir(parents=True)
        (docs_dir / 'QUESTIONS.md').write_text(
            "- [ ] 2026-10-15 · plan · Question doc 1\n- [ ] 2026-10-16 · plan · Question doc 2\n", encoding='utf-8'
        )

        tasks_base = self.root / '.workspace/orchestration/tasks'
        t_open = tasks_base / 'task-agent'
        (t_open / 'context/questions').mkdir(parents=True)
        (t_open / 'context/questions/001.md').write_text("Question agent", encoding='utf-8')

        res = self.run_questions('--short')
        self.assertEqual(res.returncode, 0, res.stderr)
        lines = res.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], "Questions en attente : 3 (dont 1 d'agents qui attendent)")

    def test_check_option(self):
        # 1. Aucune question en attente -> exit code 0
        res_empty = self.run_questions('--check')
        self.assertEqual(res_empty.returncode, 0)

        # 2. Ajout d'une question en attente -> exit code 1
        docs_dir = self.root / 'docs'
        docs_dir.mkdir(parents=True)
        (docs_dir / 'QUESTIONS.md').write_text("- [ ] Question en attente\n", encoding='utf-8')

        res_check = self.run_questions('--check')
        self.assertEqual(res_check.returncode, 1)

        # 3. Sans --check -> exit code 0 même s'il y a des questions
        res_normal = self.run_questions()
        self.assertEqual(res_normal.returncode, 0)

    def test_two_roots(self):
        root1 = self.root / 'project-alpha'
        root2 = self.root / 'project-beta'
        root1.mkdir(parents=True)
        root2.mkdir(parents=True)

        # Projet 1 : question d'agent
        t1 = root1 / '.workspace/orchestration/tasks/task-a'
        (t1 / 'context/questions').mkdir(parents=True)
        (t1 / 'context/questions/001.md').write_text("Question pour Alpha", encoding='utf-8')

        # Projet 2 : question du document QUESTIONS.md
        docs2 = root2 / 'docs'
        docs2.mkdir(parents=True)
        (docs2 / 'QUESTIONS.md').write_text("- [ ] 2026-10-15 · spec · Question pour Beta\n", encoding='utf-8')

        res = self.run_questions(str(root1), str(root2))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("project-alpha :", res.stdout)
        self.assertIn("Question pour Alpha", res.stdout)
        self.assertIn("project-beta :", res.stdout)
        self.assertIn("Question pour Beta", res.stdout)

        res_short = self.run_questions('--short', str(root1), str(root2))
        self.assertEqual(res_short.returncode, 0, res_short.stderr)
        self.assertEqual(res_short.stdout.strip(), "Questions en attente : 2 (dont 1 d'agents qui attendent)")

    def test_age_formatting_and_oldest_first(self):
        tasks_base = self.root / '.workspace/orchestration/tasks'
        now = time.time()

        # Question agent 1 : modifiée il y a 3 heures
        t1 = tasks_base / 'task-1'
        (t1 / 'context/questions').mkdir(parents=True)
        q1 = t1 / 'context/questions/001.md'
        q1.write_text("Question ancienne", encoding='utf-8')
        os.utime(q1, (now - 3 * 3600, now - 3 * 3600))

        # Question agent 2 : modifiée il y a 2 jours
        t2 = tasks_base / 'task-2'
        (t2 / 'context/questions').mkdir(parents=True)
        q2 = t2 / 'context/questions/001.md'
        q2.write_text("Question très ancienne", encoding='utf-8')
        os.utime(q2, (now - 2 * 86400, now - 2 * 86400))

        res = self.run_questions()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("il y a 3 h", res.stdout)
        self.assertIn("il y a 2 j", res.stdout)

        # La plus ancienne (2 j) doit apparaître avant l'autre (3 h)
        idx_t2 = res.stdout.index("task-2")
        idx_t1 = res.stdout.index("task-1")
        self.assertLess(idx_t2, idx_t1)

    def make_task(self, task_id='demo', questions=('001',)):
        task = self.root / '.workspace/orchestration/tasks' / task_id
        (task / 'context/questions').mkdir(parents=True)
        (task / 'state.json').write_text(json.dumps({"status": "running"}), encoding='utf-8')
        for num in questions:
            (task / 'context/questions' / f'{num}.md').write_text(f"Question {num} de {task_id}", encoding='utf-8')
        return task

    def read_events(self):
        path = self.root / '.workspace/events.jsonl'
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]

    def test_answer_writes_reply_and_event(self):
        task = self.make_task()
        res = self.run_questions('answer', 'demo/1', 'Réponse\nsur deux lignes', '--by', 'console')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout, "réponse écrite : demo/001 (par console)\n")
        self.assertEqual((task / 'context/answers/001.md').read_text(encoding='utf-8'), 'Réponse\nsur deux lignes\n')
        events = self.read_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['event'], 'question_answered')
        self.assertEqual(events[0]['task_id'], 'demo')
        self.assertEqual(events[0]['detail'], {'question': '001', 'by': 'console', 'forced': False})
        self.assertNotIn("Question 001 de demo", self.run_questions().stdout)
        self.assertEqual(self.run_questions('--short').stdout.strip(), "Aucune question en attente.")

    def test_answer_copies_question_from_worktree(self):
        task = self.make_task(questions=())
        exchange = workdir.task_worktree(self.root, 'demo') / '.workspace/exchange'
        (exchange / 'questions').mkdir(parents=True)
        (exchange / 'questions/002.md').write_text('Question de la copie', encoding='utf-8')
        res = self.run_questions('answer', 'demo/002', 'OK', '--by', 'console')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual((task / 'context/answers/002.md').read_text(encoding='utf-8'), 'OK\n')
        self.assertEqual((exchange / 'answers/002.md').read_text(encoding='utf-8'), 'OK\n')

    def test_answer_usage_errors_exit_64_without_writing(self):
        task = self.make_task()
        cases = (
            ('Demo/001', 'x', '--by', 'a'),
            ('demo', 'x', '--by', 'a'),
            ('demo/1234567', 'x', '--by', 'a'),
            ('demo/', 'x', '--by', 'a'),
            ('demo/001', '   ', '--by', 'a'),
            ('demo/001', 'x'),
            ('demo/001', 'x', '--by', ''),
            ('demo/001', 'x', '--by', 'a' * 65),
            ('demo/001', 'x', '--by', 'a\tb'),
            ('demo/001', 'x', '--by', 'a', 'en trop'),
        )
        for args in cases:
            with self.subTest(args=args):
                res = self.run_questions('answer', *args)
                self.assertEqual(res.returncode, 64, res.stderr)
                self.assertEqual(res.stdout, '')
                self.assertEqual(len(res.stderr.strip().splitlines()), 1)
        self.assertFalse((task / 'context/answers').exists())
        self.assertEqual(self.read_events(), [])

    def test_answer_by_accepts_64_characters(self):
        self.make_task()
        res = self.run_questions('answer', 'demo/1', 'x', '--by', 'a' * 64)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_answer_refusals_exit_1_without_overwriting(self):
        task = self.make_task()
        res = self.run_questions('answer', 'demo/9', 'x', '--by', 'a')
        self.assertEqual(res.returncode, 1)
        self.assertIn('question inconnue', res.stderr)
        self.run_questions('answer', 'demo/1', 'première', '--by', 'a')
        res = self.run_questions('answer', 'demo/1', 'seconde', '--by', 'b')
        self.assertEqual(res.returncode, 1)
        self.assertIn('déjà répondue', res.stderr)
        self.assertEqual((task / 'context/answers/001.md').read_text(encoding='utf-8'), 'première\n')
        self.assertEqual(len(self.read_events()), 1)

    def test_answer_force_replaces_and_marks_event(self):
        task = self.make_task()
        self.run_questions('answer', 'demo/1', 'première', '--by', 'a')
        res = self.run_questions('answer', 'demo/1', 'seconde', '--by', 'b', '--force')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout, "réponse écrite : demo/001 (par b)\n")
        self.assertEqual((task / 'context/answers/001.md').read_text(encoding='utf-8'), 'seconde\n')
        events = self.read_events()
        self.assertEqual([event['detail']['forced'] for event in events], [False, True])
        self.assertEqual(events[1]['detail'], {'question': '001', 'by': 'b', 'forced': True})

    def test_answer_refuses_symlinked_question_and_task(self):
        task = self.make_task(questions=())
        outside = self.root / 'outside'
        (outside / 'questions').mkdir(parents=True)
        (outside / 'questions/001.md').write_text('externe', encoding='utf-8')
        (task / 'context/questions/001.md').symlink_to(outside / 'questions/001.md')
        res = self.run_questions('answer', 'demo/1', 'x', '--by', 'a')
        self.assertEqual(res.returncode, 1)
        self.assertIn('lien symbolique', res.stderr)
        self.assertFalse((task / 'context/answers/001.md').exists())

        real = self.root / 'real-demo'
        (real / 'context/questions').mkdir(parents=True)
        (real / 'context/questions/003.md').write_text('question', encoding='utf-8')
        (real / 'state.json').write_text(json.dumps({"status": "running"}), encoding='utf-8')
        tasks = self.root / '.workspace/orchestration/tasks'
        (tasks / 'linked').symlink_to(real, target_is_directory=True)
        res = self.run_questions('answer', 'linked/3', 'x', '--by', 'a')
        self.assertEqual(res.returncode, 1)
        self.assertIn('lien symbolique', res.stderr)
        self.assertFalse((real / 'context/answers/003.md').exists())
        self.assertEqual(self.read_events(), [])

    def test_answer_keeps_listing_options_unchanged(self):
        self.make_task(questions=('001', '002'))
        self.run_questions('answer', 'demo/1', 'ok', '--by', 'a')
        self.assertEqual(self.run_questions('--short').stdout.strip(), "Questions en attente : 1 (dont 1 d'agents qui attendent)")
        self.assertEqual(self.run_questions('--check').returncode, 1)
        self.assertEqual(
            self.run_questions(str(self.root), '--short').stdout.strip(), "Questions en attente : 1 (dont 1 d'agents qui attendent)"
        )
        self.run_questions('answer', 'demo/2', 'ok', '--by', 'a')
        self.assertEqual(self.run_questions('--short').stdout.strip(), "Aucune question en attente.")
        self.assertEqual(self.run_questions('--check').returncode, 0)


if __name__ == '__main__':
    unittest.main()
