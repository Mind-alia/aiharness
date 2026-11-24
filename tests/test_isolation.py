from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = ROOT / '.workspace/tmp-tests'
TEST_TMP.mkdir(parents=True, exist_ok=True)


class IsolationTests(unittest.TestCase):
    def setUp(self):
        # Import task and isolation modules directly from template
        spec_task = importlib.util.spec_from_file_location('task', ROOT / 'template/scripts/lib/task.py')
        self.task_mod = importlib.util.module_from_spec(spec_task)
        spec_task.loader.exec_module(self.task_mod)

        spec_iso = importlib.util.spec_from_file_location('isolation', ROOT / 'template/scripts/lib/isolation.py')
        self.iso_mod = importlib.util.module_from_spec(spec_iso)
        spec_iso.loader.exec_module(self.iso_mod)

        self.temp_dir = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def write_task_file(self, content: str) -> Path:
        p = self.root / 'task.md'
        p.write_text(content, encoding='utf-8')
        return p

    def test_case_1_explicit_isolation_none_overrides_profile(self):
        # Même sur un profil node qui demande normalement exchange, isolation: none prime
        task = {'id': 't1', 'agent': 'codex', 'allowed': ['src/**'], 'mode': 'write', 'isolation': 'none'}
        level, reason = self.iso_mod.choose_isolation(task, profile='node')
        self.assertEqual(level, 'none')
        self.assertIn('none', reason.lower())

    def test_case_2_explicit_isolation_exchange_overrides_review(self):
        # Même en mode review ou sur un profil docs, isolation: exchange prime
        task = {'id': 't2', 'agent': 'codex', 'allowed': ['docs/**'], 'mode': 'review', 'isolation': 'exchange'}
        level, reason = self.iso_mod.choose_isolation(task, profile='docs')
        self.assertEqual(level, 'exchange')
        self.assertIn('exchange', reason.lower())

    def test_case_3_explicit_isolation_full_fails_cleanly(self):
        # Règle 4 : full n'est jamais choisi automatiquement et le demander explicitement échoue
        task = {'id': 't3', 'agent': 'codex', 'allowed': ['src/**'], 'mode': 'write', 'isolation': 'full'}
        with self.assertRaises(ValueError) as ctx:
            self.iso_mod.choose_isolation(task, profile='node')
        self.assertTrue(any(w in str(ctx.exception).lower() for w in ['full', 's3', 'supportée', 'réservée']))

    def test_case_4_default_mode_review_is_none(self):
        # Règle 2 : tâche en mode review -> none
        task = {'id': 't4', 'agent': 'codex', 'allowed': ['src/**'], 'mode': 'review'}
        level, reason = self.iso_mod.choose_isolation(task, profile='node')
        self.assertEqual(level, 'none')
        self.assertIn('review', reason.lower())

    def test_case_5_default_profile_docs_is_none(self):
        # Règle 2 : projet docs -> none
        task = {'id': 't5', 'agent': 'codex', 'allowed': ['docs/**'], 'mode': 'write'}
        level, reason = self.iso_mod.choose_isolation(task, profile='docs')
        self.assertEqual(level, 'none')
        self.assertIn('docs', reason.lower())

    def test_case_6_default_profile_node_python_docker_is_exchange(self):
        # Règle 3 : profils node, python, docker -> exchange
        for prof in ('node', 'python', 'docker'):
            task = {'id': f't-{prof}', 'agent': 'codex', 'allowed': ['src/**'], 'mode': 'write'}
            level, reason = self.iso_mod.choose_isolation(task, profile=prof)
            self.assertEqual(level, 'exchange', f"Échec pour profil {prof}")
            self.assertIn(prof, reason.lower())

    def test_unknown_profile_raises_error(self):
        task = {'id': 't', 'agent': 'codex', 'allowed': ['src/**'], 'mode': 'write'}
        with self.assertRaises(ValueError):
            self.iso_mod.choose_isolation(task, profile='unknown_xyz')

    def test_choose_isolation_with_file_path(self):
        content = "---\nid: file-task\nagent: codex\nallowed: [src/**]\nisolation: exchange\n---\nBody\n"
        path = self.write_task_file(content)
        level, _ = self.iso_mod.choose_isolation(path, profile='docs')
        self.assertEqual(level, 'exchange')

    def test_task_parser_new_fields(self):
        # Lecture correcte des nouveaux champs
        content = "---\nid: t-new\nagent: codex\nallowed: [src/**]\nisolation: exchange\nnetwork: on\n---\n"
        path = self.write_task_file(content)
        parsed = self.task_mod.read_task(path)
        self.assertEqual(parsed['isolation'], 'exchange')
        self.assertEqual(parsed['network'], 'on')

        # Valeurs par défaut quand les champs sont omis (sans casser les tâches existantes)
        content_old = "---\nid: t-old\nagent: codex\nallowed: [src/**]\n---\n"
        path_old = self.write_task_file(content_old)
        parsed_old = self.task_mod.read_task(path_old)
        self.assertIsNone(parsed_old.get('isolation'))
        self.assertEqual(parsed_old.get('network'), 'off')

    def test_task_parser_rejects_invalid_values(self):
        bad_isolation = "---\nid: t-bad\nagent: codex\nallowed: [src/**]\nisolation: super_isolated\n---\n"
        with self.assertRaises(ValueError):
            self.task_mod.read_task(self.write_task_file(bad_isolation))

        bad_network = "---\nid: t-bad2\nagent: codex\nallowed: [src/**]\nnetwork: internet\n---\n"
        with self.assertRaises(ValueError):
            self.task_mod.read_task(self.write_task_file(bad_network))


if __name__ == '__main__':
    unittest.main()
