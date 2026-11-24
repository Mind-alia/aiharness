import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BASE_METRICS = {
    "project": "test-project",
    "generated_at": "2026-10-17T00:00:00+00:00",
    "since": None,
    "delegation": {
        "tasks": 5,
        "first_try_success_rate": 0.8,
        "mean_attempts": 1.2,
        "mean_duration_seconds": 3.0,
        "merged": 4,
        "abandoned": 1,
        "outcomes": {"done": 4, "failed": 1},
        "final_states": {"merged": 4, "abandoned": 1},
        "by_agent": {
            "codex": {"tasks": 5, "first_try_success_rate": 0.8, "mean_attempts": 1.2, "mean_duration_seconds": 3.0, "tokens": 1500}
        },
    },
    "advisor": {
        "questions": 10,
        "answered": 8,
        "unknown": 1,
        "unavailable": 1,
        "research": 0,
        "answer_rate": 0.8,
        "mean_seconds": 2.0,
        "pending_for_orchestrator": 2,
    },
    "tokens": {
        "claude": {"messages": 10, "input": 1000, "cache_creation": 200, "cache_read": 300, "output": 500, "by_model": {}},
        "codex": {"turns": 12, "input": 1200, "cached_input": 400, "output": 600, "reasoning_output": 100},
    },
    "git": {"commits": 4, "merges": 1},
    "bench": {"advisor": {"accuracy": 0.85}},
}


def setup_workspace(tmp):
    root = Path(tmp)
    shutil.copytree(ROOT / 'template/scripts', root / 'scripts')
    mock_metrics_py = root / 'scripts/lib/metrics.py'
    mock_metrics_py.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "data_file = root / 'mock_metrics.json'\n"
        "print(data_file.read_text(encoding='utf-8'))\n"
    )
    set_metrics(root, BASE_METRICS)
    return root


def set_metrics(root, data):
    (root / 'mock_metrics.json').write_text(json.dumps(data, indent=2))


def run_experiment(root, *args):
    return subprocess.run(['bash', str(root / 'scripts/experiment.sh'), *args], capture_output=True, text=True)


class ExperimentTests(unittest.TestCase):
    def test_start_and_permissions_and_numbering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = setup_workspace(tmp)
            res1 = run_experiment(root, 'start', 'Amélioration du taux de succès', '--metric', 'delegation.first_try_success_rate')
            self.assertEqual(res1.returncode, 0, res1.stderr)

            exp1_file = root / 'docs/experiments/001-amelioration-du-taux-de-succes.md'
            self.assertTrue(exp1_file.is_file())
            content1 = exp1_file.read_text(encoding='utf-8')
            self.assertIn('# Expérience 001 : amelioration-du-taux-de-succes', content1)
            self.assertIn('- **Statut** : en cours', content1)
            self.assertIn('Amélioration du taux de succès', content1)
            self.assertIn('| delegation.first_try_success_rate | up | 0.800 |', content1)
            self.assertIn(
                "Un seul changement par expérience ; mêmes conditions avant et après ; une seule mesure n'est pas une preuve — refaire si l'écart est faible.",
                content1,
            )

            ws_dir1 = root / '.workspace/experiments/001'
            self.assertTrue(ws_dir1.is_dir())
            self.assertEqual(ws_dir1.stat().st_mode & 0o777, 0o700)

            before1 = ws_dir1 / 'before.json'
            self.assertTrue(before1.is_file())
            self.assertEqual(before1.stat().st_mode & 0o777, 0o600)
            before1_data = json.loads(before1.read_text(encoding='utf-8'))
            self.assertEqual(before1_data['project'], 'test-project')

            res2 = run_experiment(root, 'start', 'Seconde hypothèse', '--metric', 'advisor.answer_rate')
            self.assertEqual(res2.returncode, 0, res2.stderr)
            exp2_file = root / 'docs/experiments/002-seconde-hypothese.md'
            self.assertTrue(exp2_file.is_file())
            self.assertTrue((root / '.workspace/experiments/002/before.json').is_file())

    def test_indicator_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = setup_workspace(tmp)
            res = run_experiment(root, 'start', 'Hypothèse invalide', '--metric', 'delegation.introuvable')
            self.assertEqual(res.returncode, 64)
            output = res.stderr + res.stdout
            self.assertIn("indicateur introuvable", output)
            self.assertIn("delegation.first_try_success_rate", output)
            self.assertIn("delegation.tasks", output)
            self.assertIn("advisor.answer_rate", output)

    def test_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = setup_workspace(tmp)
            run_experiment(root, 'start', 'Test note', '--metric', 'delegation.tasks')
            res = run_experiment(root, 'note', '001', 'Observation intermédiaire importante')
            self.assertEqual(res.returncode, 0, res.stderr)

            exp_file = root / 'docs/experiments/001-test-note.md'
            content = exp_file.read_text(encoding='utf-8')
            self.assertIn('## Journal', content)
            self.assertIn('Observation intermédiaire importante', content)

    def test_end_table_expectations_and_sample_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = setup_workspace(tmp)
            run_experiment(
                root,
                'start',
                'Test clôture',
                '--metric',
                'delegation.first_try_success_rate',
                '--metric',
                'delegation.mean_attempts',
                '--metric',
                'advisor.answer_rate',
                '--expect',
                'up',
                '--metric',
                'advisor.pending_for_orchestrator',
                '--expect',
                'down',
            )

            metrics_after = copy.deepcopy(BASE_METRICS)
            metrics_after['delegation']['first_try_success_rate'] = 0.9
            metrics_after['delegation']['mean_attempts'] = 1.5
            metrics_after['advisor']['answer_rate'] = 0.8
            metrics_after['advisor']['pending_for_orchestrator'] = 1
            metrics_after['delegation']['tasks'] = 6  # delta = 1 < 3
            set_metrics(root, metrics_after)

            res_end = run_experiment(root, 'end', '001', '--decision', 'gardé', '--reason', 'Bons résultats')
            self.assertEqual(res_end.returncode, 0, res_end.stderr)

            exp_file = root / 'docs/experiments/001-test-cloture.md'
            content = exp_file.read_text(encoding='utf-8')
            self.assertIn('- **Statut** : gardé', content)
            self.assertIn('## Décision : gardé', content)
            self.assertIn('Bons résultats', content)
            self.assertIn('| Indicateur | Attendu | Avant | Après | Écart | Écart % | Résultat |', content)
            self.assertIn('| delegation.first_try_success_rate | up | 0.800 | 0.900 | +0.100 | +12.5% | amélioré |', content)
            self.assertIn('| delegation.mean_attempts | down | 1.200 | 1.500 | +0.300 | +25.0% | dégradé |', content)
            self.assertIn('| advisor.answer_rate | up | 0.800 | 0.800 | 0.000 | 0.0% | inchangé |', content)
            self.assertIn('| advisor.pending_for_orchestrator | down | 2 | 1 | -1 | -50.0% | amélioré |', content)
            self.assertIn('Échantillon trop petit pour conclure', content)

            after_json = root / '.workspace/experiments/001/after.json'
            self.assertTrue(after_json.is_file())
            self.assertEqual(after_json.stat().st_mode & 0o777, 0o600)

            # Test suffisant: tasks delta >= 3
            run_experiment(root, 'start', 'Grand échantillon', '--metric', 'delegation.tasks')
            metrics_after2 = copy.deepcopy(metrics_after)
            metrics_after2['delegation']['tasks'] = 12  # delta = 6 >= 3
            set_metrics(root, metrics_after2)
            run_experiment(root, 'end', '002', '--decision', 'gardé')
            exp2_content = (root / 'docs/experiments/002-grand-echantillon.md').read_text(encoding='utf-8')
            self.assertNotIn('Échantillon trop petit pour conclure', exp2_content)

    def test_double_closure_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = setup_workspace(tmp)
            run_experiment(root, 'start', 'Test double clôture', '--metric', 'delegation.tasks')
            res1 = run_experiment(root, 'end', '001', '--decision', 'gardé')
            self.assertEqual(res1.returncode, 0, res1.stderr)

            res2 = run_experiment(root, 'end', '001', '--decision', 'abandonné')
            self.assertNotEqual(res2.returncode, 0)
            self.assertIn('déjà close', res2.stderr + res2.stdout)

    def test_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = setup_workspace(tmp)
            run_experiment(
                root,
                'start',
                'Première hypothèse avec un texte assez long pour vérifier la troncature',
                '--metric',
                'delegation.first_try_success_rate',
            )
            run_experiment(root, 'start', 'Deuxième en cours', '--metric', 'advisor.questions')

            metrics_after = copy.deepcopy(BASE_METRICS)
            metrics_after['delegation']['first_try_success_rate'] = 0.9
            set_metrics(root, metrics_after)
            run_experiment(root, 'end', '001', '--decision', 'gardé')

            res_list = run_experiment(root, 'list')
            self.assertEqual(res_list.returncode, 0, res_list.stderr)
            lines = res_list.stdout.strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn('001', lines[0])
            self.assertIn('gardé', lines[0])
            self.assertIn('delegation.first_try_success_rate', lines[0])
            self.assertIn('+12.5%', lines[0])
            self.assertIn('002', lines[1])
            self.assertIn('en cours', lines[1])

    def test_slug_and_no_overwrite_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = setup_workspace(tmp)
            long_title = "L'optimisation du prompt réduit à fond la latence globale de tous les agents de l'orchestrateur !"
            res = run_experiment(root, 'start', long_title, '--metric', 'delegation.tasks')
            self.assertEqual(res.returncode, 0, res.stderr)

            exp_files = list((root / 'docs/experiments').glob('001-*.md'))
            self.assertEqual(len(exp_files), 1)
            slug = exp_files[0].stem[4:]
            self.assertLessEqual(len(slug), 50)
            self.assertTrue(slug.isascii())
            self.assertNotIn("'", slug)
            self.assertNotIn("é", slug)

            # Refus d'écrasement : si le fichier cible existe déjà
            docs_dir = root / 'docs/experiments'
            target_002 = docs_dir / '002-collision.md'
            target_002.write_text("existant")
            res_collision = subprocess.run(
                ['python3', str(root / 'scripts/lib/experiment.py'), str(root), 'start', 'Collision', '--metric', 'delegation.tasks'],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_collision.returncode, 0, res_collision.stderr)
            self.assertEqual(target_002.read_text(), 'existant')
            self.assertEqual(len(list(docs_dir.glob('003-*.md'))), 1)

            # Refus de lien symbolique
            shutil.rmtree(docs_dir)
            docs_dir.symlink_to(root)
            res_sym = subprocess.run(
                ['python3', str(root / 'scripts/lib/experiment.py'), str(root), 'start', 'Test symlink', '--metric', 'delegation.tasks'],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res_sym.returncode, 0)


if __name__ == '__main__':
    unittest.main()
