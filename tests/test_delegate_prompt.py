"""The executor's prompt built by delegate_prompt.py, and where a secret found in it came from."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import delegate_prompt
from delegate_run import REPORT_SECTION


def sheet(**fields):
    task = dict(id='t-demo', agent='codex', mode='write', allowed=['src/a.py', 'tests/test_a.py'], verify='make test', body='# Faire A\n')
    task.update(fields)
    return task


class BuildTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / 'docs').mkdir()
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('WORKSPACE_STAGE_CONTEXT', None)

    def build(self, task, isolation='full', work_dir=None):
        warnings = []
        prompt, pitfalls, rows = delegate_prompt.build(self.root, task, isolation, work_dir or self.root, warnings)
        return prompt, pitfalls, rows, warnings

    def test_sections_come_in_order_and_end_with_the_report(self):
        (self.root / 'docs/BRIEF-AGENTS.md').write_text('BRIEF\n', encoding='utf-8')
        (self.root / 'docs/PIEGES.md').write_text('PIEGES\n', encoding='utf-8')
        prompt, pitfalls, rows, warnings = self.build(sheet())
        order = ['Racine absolue du projet : ' + str(self.root), 'BRIEF', 'PIEGES', 'Périmètre autorisé :\n- src/a.py\n- tests/test_a.py']
        order += ['Commande de vérification : make test', '# Faire A']
        positions = [prompt.index(part) for part in order]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(prompt.endswith(REPORT_SECTION))
        self.assertEqual((pitfalls, rows, warnings), ('PIEGES\n', [], []))

    def test_missing_shared_documents_become_warnings(self):
        prompt, pitfalls, _, warnings = self.build(sheet(verify=''))
        self.assertEqual(warnings, ['Fichier facultatif absent : docs/BRIEF-AGENTS.md', 'Fichier facultatif absent : docs/PIEGES.md'])
        self.assertEqual(pitfalls, '')
        self.assertIn('Commande de vérification : vérification du profil du projet', prompt)

    def test_exchange_mode_names_the_worktree_and_the_sandbox_rules(self):
        worktree = self.root / 'clone'
        prompt, *_ = self.build(sheet(), isolation='exchange', work_dir=worktree)
        self.assertIn("Atelier de l'exécutant (worktree %s)" % worktree, prompt)
        self.assertIn('Racine absolue du worktree : ' + str(worktree), prompt)
        self.assertIn(delegate_prompt.EXCHANGE_RULES, prompt)
        self.assertNotIn('Racine absolue du projet', prompt)

    def test_antigravity_writing_by_files_gets_its_own_section_instead(self):
        worktree = self.root / 'clone'
        prompt, *_ = self.build(sheet(agent='antigravity'), isolation='exchange', work_dir=worktree)
        self.assertNotIn(delegate_prompt.EXCHANGE_RULES, prompt)
        self.assertIn('Racine absolue du projet : ' + str(self.root), prompt)

    def test_late_answers_follow_the_sheet(self):
        context = self.root / '.workspace/orchestration/tasks/t-demo/context'
        (context / 'questions').mkdir(parents=True)
        (context / 'answers').mkdir()
        (context / 'questions/001.md').write_text('Quelle base ?\n', encoding='utf-8')
        (context / 'answers/001.md').write_text('main\n', encoding='utf-8')
        prompt, _, rows, _ = self.build(sheet())
        self.assertEqual([row[0] for row in rows], ['001'])
        self.assertLess(prompt.index('# Faire A'), prompt.index('Quelle base ?'))
        self.assertLess(prompt.index('Quelle base ?'), prompt.index(REPORT_SECTION))

    def test_stage_context_is_appended_last(self):
        stage = self.root / 'stage.md'
        stage.write_text('ÉTAPE\n', encoding='utf-8')
        os.environ['WORKSPACE_STAGE_CONTEXT'] = str(stage)
        prompt, *_ = self.build(sheet())
        self.assertTrue(prompt.endswith(REPORT_SECTION + '\n\nÉTAPE\n'))


class SecretOriginTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / 'docs').mkdir()
        self.task_path = self.root / 'task.md'
        self.task_path.write_text('# Faire A\n', encoding='utf-8')

    def test_a_secret_is_traced_to_its_file_and_line(self):
        (self.root / 'docs/PIEGES.md').write_text('un\ndeux\nexport GITHUB_TOKEN=ghp_abc123def456\n', encoding='utf-8')  # gitleaks:allow
        self.task_path.write_text('# Faire A\nvaleur connue : s3cr3t-valeur\n', encoding='utf-8')
        origins = delegate_prompt.secret_origins(self.root, self.task_path, 'consigne', ['s3cr3t-valeur'])
        self.assertEqual(origins, [str(self.task_path) + ':2', 'docs/PIEGES.md:3'])

    def test_the_verify_output_appended_for_a_retry_is_a_source(self):
        prompt = 'consigne\n\n' + delegate_prompt.VERIFY_FAILURE + 'ligne\nGITHUB_TOKEN=ghp_abc123def456\n'  # gitleaks:allow
        self.assertEqual(delegate_prompt.secret_origins(self.root, self.task_path, prompt, []), ['verify.txt:2'])

    def test_an_untraced_secret_points_at_the_prompt(self):
        self.assertEqual(delegate_prompt.secret_origins(self.root, self.task_path, 'ghp_abc123def456', []), ['prompt.md:1'])


if __name__ == '__main__':
    unittest.main()
