from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel, sous-processus ou Docker simulé ; suite complète seulement')

ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = Path(os.environ.get('WORKSPACE_TEST_TMP') or ROOT / '.workspace/tmp-tests')
TEST_TMP.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import workdir  # noqa: E402


class AskAnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.temp.cleanup)
        # Les clones de tâche vont hors du dépôt, jamais dans le vrai ~/.workspace-ia.
        self.work_dir = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.work_dir.cleanup)
        patcher = mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': os.path.realpath(self.work_dir.name)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = (Path(self.temp.name) / 'project with spaces').resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.scripts_dir = self.root / 'scripts'
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.lib_dir = self.scripts_dir / 'lib'
        self.lib_dir.mkdir(parents=True, exist_ok=True)

        # Copie des scripts et libs
        lib_source = ROOT / 'template/scripts/lib'
        if lib_source.exists():
            for lib_file in lib_source.glob('*.py'):
                shutil.copy2(lib_file, self.lib_dir / lib_file.name)

        for script_name in ('task-workspace.sh', 'ask.sh', 'answer.sh'):
            src = ROOT / 'template/scripts' / script_name
            if src.exists():
                dest = self.scripts_dir / script_name
                shutil.copy2(src, dest)
                dest.chmod(0o755)

        # Initialisation Git du dépôt de test
        cmd = [
            'git',
            '-C',
            str(self.root),
            '-c',
            'user.name=Test',
            '-c',
            'user.email=test@example.invalid',
            '-c',
            'core.hooksPath=',
        ]
        subprocess.run([*cmd, 'init', '-q'], check=True)
        (self.root / '.gitignore').write_text('.workspace/\ntasks/\n')
        (self.root / 'README.md').write_text('# Test project\n')
        (self.root / 'src').mkdir()
        (self.root / 'src/app.py').write_text('print("initial")\n')
        subprocess.run([*cmd, 'add', '.'], check=True)
        subprocess.run([*cmd, 'commit', '-qm', 'initial commit'], check=True)

    def prepare_task(self, task_id='task-ask'):
        task_file = self.root / f'tasks/{task_id}.md'
        task_file.parent.mkdir(parents=True, exist_ok=True)
        content = f"---\nid: {task_id}\nagent: codex\nallowed:\n  - src/**\n---\nConsigne de test\n"
        task_file.write_text(content, encoding='utf-8')

        res = subprocess.run(
            ['bash', str(self.scripts_dir / 'task-workspace.sh'), 'prepare', str(task_file)],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        return task_id

    def run_ask(self, *args, env=None, cwd=None):
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        return subprocess.run(
            ['bash', str(self.scripts_dir / 'ask.sh'), *args], cwd=str(cwd or self.root), capture_output=True, text=True, env=run_env
        )

    def run_answer(self, *args, env=None, cwd=None):
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        return subprocess.run(
            ['bash', str(self.scripts_dir / 'answer.sh'), *args], cwd=str(cwd or self.root), capture_output=True, text=True, env=run_env
        )

    def test_nested_worktree_uses_its_project_registry_not_the_outer_task(self):
        nested = self.root.parent / '.workspace/worktrees/outer/nested-project'
        nested.parent.mkdir(parents=True)
        shutil.move(str(self.root), str(nested))
        self.root = nested
        self.scripts_dir = nested / 'scripts'
        self.lib_dir = self.scripts_dir / 'lib'
        task_id = self.prepare_task('task-inner')
        worktree = workdir.task_worktree(nested, task_id)
        result = self.run_ask('Question de la tâche interne ?', cwd=worktree, env={'ASK_TIMEOUT': '0'})
        self.assertNotIn('Tâche inconnue', result.stderr)
        question = nested / '.workspace/orchestration/tasks' / task_id / 'context/questions/001.md'
        self.assertTrue(question.is_file())
        self.assertIn('tâche interne', question.read_text())

    def test_nominal_ask_and_answer_protocol(self):
        task_id = self.prepare_task('task-1')

        # Thread orchestrateur qui répond après un court délai
        def delayed_answer():
            time.sleep(0.3)
            res = self.run_answer(task_id, '001', 'Voici la réponse officielle.')
            self.assertEqual(res.returncode, 0, res.stderr)

        t = threading.Thread(target=delayed_answer)
        t.start()

        res_ask = self.run_ask(task_id, 'Comment configurer le port ?', env={'ASK_TIMEOUT': '10'})
        t.join()

        self.assertEqual(res_ask.returncode, 0, res_ask.stderr)
        self.assertIn('Voici la réponse officielle.', res_ask.stdout)

        # Vérification des fichiers et permissions
        q_file = self.root / f'.workspace/orchestration/tasks/{task_id}/context/questions/001.md'
        a_file = self.root / f'.workspace/orchestration/tasks/{task_id}/context/answers/001.md'
        self.assertTrue(q_file.is_file())
        self.assertTrue(a_file.is_file())

        self.assertEqual(q_file.read_text(encoding='utf-8').strip(), 'Comment configurer le port ?')
        self.assertEqual(a_file.read_text(encoding='utf-8').strip(), 'Voici la réponse officielle.')

        # Permissions 0600 pour les fichiers et 0700 pour les dossiers
        self.assertEqual(stat.S_IMODE(q_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(a_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(q_file.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(a_file.parent.stat().st_mode), 0o700)

    def test_question_numbering_sequential(self):
        task_id = self.prepare_task('task-seq')

        # Question 1 avec timeout immédiat 0
        self.run_ask(task_id, 'Première question', env={'ASK_TIMEOUT': '0'})
        q1 = self.root / f'.workspace/orchestration/tasks/{task_id}/context/questions/001.md'
        self.assertTrue(q1.is_file())

        # Question 2 avec timeout immédiat 0
        self.run_ask(task_id, 'Deuxième question', env={'ASK_TIMEOUT': '0'})
        q2 = self.root / f'.workspace/orchestration/tasks/{task_id}/context/questions/002.md'
        self.assertTrue(q2.is_file())
        self.assertEqual(q2.read_text(encoding='utf-8').strip(), 'Deuxième question')

    def test_ask_timeout_bounded(self):
        task_id = self.prepare_task('task-timeout')

        start = time.monotonic()
        res = self.run_ask(task_id, 'Question sans réponse ?', env={'ASK_TIMEOUT': '0'})
        duration = time.monotonic() - start

        # Doit revenir immédiatement (délai borné à 0)
        self.assertLess(duration, 2.0)
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in (res.stdout + res.stderr).lower() for w in ['délai', 'dépassé', 'timeout', 'aucune réponse']))

        # La question a néanmoins été écrite
        q_file = self.root / f'.workspace/orchestration/tasks/{task_id}/context/questions/001.md'
        self.assertTrue(q_file.is_file())

    def test_answer_nonexistent_question_fails(self):
        task_id = self.prepare_task('task-noq')
        res = self.run_answer(task_id, '999', 'Réponse dans le vide')
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('introuvable', res.stderr.lower())

    def test_unknown_task_fails(self):
        res_ask = self.run_ask('non-existent-task', 'Question ?')
        self.assertNotEqual(res_ask.returncode, 0)

        res_ans = self.run_answer('non-existent-task', '001', 'Réponse')
        self.assertNotEqual(res_ans.returncode, 0)

    def test_refuse_symlink_in_context(self):
        task_id = self.prepare_task('task-sym-ctx')
        task_dir = self.root / f'.workspace/orchestration/tasks/{task_id}'
        ctx_dir = task_dir / 'context'
        ctx_dir.mkdir(mode=0o700, exist_ok=True)

        # On crée un symlink vers src pour questions
        q_dir = ctx_dir / 'questions'
        q_dir.symlink_to(self.root / 'src')

        res = self.run_ask(task_id, 'Question piégée ?', env={'ASK_TIMEOUT': '0'})
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in (res.stderr + res.stdout).lower() for w in ['symbolique', 'symlink']))

    def test_ask_inside_worktree_infers_task_id(self):
        task_id = self.prepare_task('task-infer')
        worktree_dir = workdir.task_worktree(self.root, task_id)

        # Appel depuis le dossier du worktree sans passer <task-id>
        self.run_ask('Question depuis le worktree ?', cwd=worktree_dir, env={'ASK_TIMEOUT': '0'})
        q_file = self.root / f'.workspace/orchestration/tasks/{task_id}/context/questions/001.md'
        self.assertTrue(q_file.is_file())

    def test_ask_with_the_worktree_copy_of_the_script(self):
        # A non-isolated agent runs the scripts of its own worktree: the registry
        # still lives in the main repository.
        task_id = self.prepare_task('task-copy')
        worktree_dir = workdir.task_worktree(self.root, task_id)
        res = subprocess.run(
            ['bash', str(worktree_dir / 'scripts/ask.sh'), 'Question ?'],
            cwd=str(worktree_dir),
            capture_output=True,
            text=True,
            env=dict(os.environ, ASK_TIMEOUT='0'),
        )
        self.assertNotIn('Tâche inconnue', res.stderr)
        q_file = self.root / f'.workspace/orchestration/tasks/{task_id}/context/questions/001.md'
        self.assertTrue(q_file.is_file())

    def test_project_pointer_of_a_clone_is_verified_against_the_registry(self):
        task_id = self.prepare_task('task-pointer')
        clone = workdir.task_worktree(self.root, task_id)
        self.assertEqual(workdir.project_of(clone), self.root)
        pointer = clone / '.git/workspace-root'
        # Another project which does not register this clone is never believed.
        other = Path(self.temp.name) / 'other'
        (other / '.workspace/orchestration/tasks' / task_id).mkdir(parents=True)
        (other / '.workspace/orchestration/tasks' / task_id / 'state.json').write_text(json.dumps({'worktree': str(other / 'somewhere')}))
        pointer.write_text(str(other))
        self.assertIsNone(workdir.project_of(clone))
        pointer.write_text('relative/path')
        self.assertIsNone(workdir.project_of(clone))
        pointer.unlink()
        self.assertIsNone(workdir.project_of(clone))

    def test_project_under_a_delegated_worktree_keeps_its_own_registry(self):
        # A test project is not a worktree because one of its ancestors is: the
        # search for .workspace/exchange must stop at the project root.
        enclosing_exchange = Path(self.temp.name) / '.workspace/exchange'
        enclosing_exchange.mkdir(parents=True)
        task_id = self.prepare_task('task-enclosed')
        res = self.run_ask(task_id, 'Question du projet englobé ?', env={'ASK_TIMEOUT': '0'})
        self.assertNotIn('Tâche inconnue', res.stderr)
        q_file = self.root / f'.workspace/orchestration/tasks/{task_id}/context/questions/001.md'
        self.assertTrue(q_file.is_file())
        self.assertIn('projet englobé', q_file.read_text())
        self.assertEqual(list(enclosing_exchange.rglob('*.md')), [])


if __name__ == '__main__':
    unittest.main()
