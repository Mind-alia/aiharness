import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FULL_GRID = """# Grille de mission — m1

| Critère | Mesure | Jugement |
| --- | --- | --- |
| Règles · périmètre | 1 hors périmètre sur 3 délégations | — |
| Règles · secrets | 1 refus pour secret sur 3 délégations | — |
| Règles · tests présents | 1/3 tâches avec tests cachés | — |
| Règles · seuils | — | À remplir (jugement humain) |
| Qualité du cadrage | — | À remplir (jugement humain) |
| Qualité du livrable | — | À remplir (jugement humain) |
| Interventions humaines · nombre | 3 questions d'agent ; 4 interventions, 15 min | — |
| Interventions humaines · nature | — | À remplir (jugement humain) |
| Coût · tokens par rôle | execution 15 ; orchestration 3000000 ; relecture incomplet | — |
| Coût · euros (tarif API) | 21.00 € (tarifs du 2026-10-01) | — |
| Délai | 25 h 15 min | — |
"""

TARIFS = {
    'date': '2026-10-01',
    'source': 'test',
    'eur_per_million': {
        'claude/claude-haiku-5-5': {'input_tokens': 3.0, 'output_tokens': 15.0},
        'codex/gpt-x': {'input': 1.0, 'output': 2.0},
    },
}


def write_jsonl(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ''.join(line if isinstance(line, str) else json.dumps(line) + '\n' for line in lines)
    path.write_text(text, encoding='utf-8')


class MissionReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=os.environ.get('WORKSPACE_TEST_TMP'))).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / 'kit'
        shutil.copytree(ROOT / 'template/scripts', self.root / 'scripts')
        self.mission_dir = self.root / '.workspace/missions/m1'
        self.repo = self.mission_dir / 'repo'
        self.write_mission()

    def write_mission(self, **fields):
        mission = dict(name='m1', repo='repo', adopted_at='2026-10-18T08:00:00Z')
        mission.update(fields)
        self.mission_dir.mkdir(parents=True, exist_ok=True)
        (self.mission_dir / 'mission.json').write_text(json.dumps(mission), encoding='utf-8')

    def write_delegation(self, folder, task_id, outcome, metrics=None):
        directory = self.repo / '.workspace/run/delegate' / folder
        directory.mkdir(parents=True)
        status = dict(
            id=task_id,
            agent='claude-haiku',
            outcome=outcome,
            attempts=1,
            isolation='none',
            started_at='2026-10-18T09:00:00Z',
            metrics=metrics or {},
        )
        (directory / 'status.json').write_text(json.dumps(status), encoding='utf-8')

    def write_tarifs(self, data=TARIFS):
        (self.root / 'docs').mkdir(exist_ok=True)
        (self.root / 'docs/TARIFS-API.json').write_text(json.dumps(data), encoding='utf-8')

    def write_full_repo(self):
        self.write_delegation('a', 'ta', 'done', {'questions': 2})
        self.write_delegation('b', 'tb', 'out_of_scope')
        self.write_delegation('c', 'tc', 'refused_secret', {'questions': 1})
        (self.repo / '.workspace/acceptance/ta/sub').mkdir(parents=True)
        (self.repo / '.workspace/acceptance/ta/sub/test_x.py').write_text('', encoding='utf-8')
        (self.repo / '.workspace/acceptance/tb').mkdir(parents=True)
        write_jsonl(
            self.repo / '.workspace/metrics/consumption.jsonl',
            [
                dict(
                    role='orchestration',
                    provider='claude',
                    model='claude-haiku-5-5',
                    tokens=dict(input_tokens=2000000, output_tokens=1000000, cache_read_input_tokens=0, cache_creation_input_tokens=0),
                ),
                dict(role='execution', provider='codex', model='gpt-x', tokens=dict(input=10, output=5)),
                'pas du json\n',
                dict(role='relecture', provider='claude', model='claude-haiku-5-5', tokens=dict(total=None)),
            ],
        )
        write_jsonl(
            self.repo / '.workspace/events.jsonl',
            [
                dict(at='2026-10-18T10:30:00Z', task_id='ta', event='note'),
                'pas du json\n',
                dict(at=5, task_id='tb', event='note'),
                dict(at='2026-10-19T09:15:30+00:00', task_id='tc', event='note'),
                dict(at='2026-10-18T11:00:00+00:00', task_id='ta', event='human_intervention', detail=dict(label='a', minutes=10)),
                dict(at='2026-10-18T12:00:00+00:00', task_id='tb', event='human_intervention', detail=dict(label='b', minutes=5)),
                dict(at='2026-10-18T13:00:00+00:00', task_id='tc', event='human_intervention', detail=dict(label='c')),
                dict(at='2026-10-18T14:00:00+00:00', task_id='tc', event='human_intervention', detail=dict(label='d', minutes='7')),
            ],
        )
        self.write_tarifs()

    def report(self, name='m1'):
        return subprocess.run(['sh', str(self.root / 'scripts/report.sh'), '--mission', name], capture_output=True, text=True)

    def test_full_grid_is_printed_and_written_once(self):
        self.write_full_repo()
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, FULL_GRID)
        self.assertEqual((self.mission_dir / 'grille.md').read_text(encoding='utf-8'), FULL_GRID)

    def test_existing_grid_is_never_rewritten(self):
        self.write_full_repo()
        grid = self.mission_dir / 'grille.md'
        grid.write_text('jugement écrit à la main\n', encoding='utf-8')
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, FULL_GRID)
        self.assertIn('grille.md', result.stderr)
        self.assertEqual(grid.read_text(encoding='utf-8'), 'jugement écrit à la main\n')

    def test_empty_mission_reports_zeros_and_unknowns(self):
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('| Règles · périmètre | 0 hors périmètre sur 0 délégations | — |', result.stdout)
        self.assertIn('| Règles · secrets | 0 refus pour secret sur 0 délégations | — |', result.stdout)
        self.assertIn('| Règles · tests présents | 0/0 tâches avec tests cachés | — |', result.stdout)
        self.assertIn("| Interventions humaines · nombre | 0 questions d'agent ; 0 intervention, 0 min | — |", result.stdout)
        self.assertIn('| Coût · tokens par rôle | aucune consommation relevée | — |', result.stdout)
        self.assertIn('| Coût · euros (tarif API) | inconnu (docs/TARIFS-API.json absent) | — |', result.stdout)
        self.assertIn('| Délai | inconnu (aucun événement) | — |', result.stdout)

    def test_missing_tariff_for_a_model_is_named(self):
        self.write_full_repo()
        tariffs = dict(TARIFS, eur_per_million={'claude/claude-haiku-5-5': TARIFS['eur_per_million']['claude/claude-haiku-5-5']})
        self.write_tarifs(tariffs)
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('| Coût · euros (tarif API) | inconnu (tarif absent : codex/gpt-x) | — |', result.stdout)

    def test_unreadable_tariff_file_is_unknown(self):
        self.write_full_repo()
        (self.root / 'docs/TARIFS-API.json').write_text('{pas du json', encoding='utf-8')
        result = self.report()
        self.assertIn('| Coût · euros (tarif API) | inconnu (docs/TARIFS-API.json illisible) | — |', result.stdout)

    def test_mission_name_must_be_plain(self):
        for name in ('../m1', 'M1', 'm 1', 'absente'):
            with self.subTest(name=name):
                result = self.report(name)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr)
        self.assertFalse((self.mission_dir / 'grille.md').exists())

    def test_mission_without_adopted_at_is_refused(self):
        self.write_mission(adopted_at=None)
        result = self.report()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertTrue(result.stderr)

    def test_repo_outside_mission_is_refused(self):
        self.write_mission(repo='../autre')
        result = self.report()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertTrue(result.stderr)

    def test_no_event_after_adoption_is_unknown_delay(self):
        write_jsonl(self.repo / '.workspace/events.jsonl', [dict(at='2026-10-17T10:00:00Z', task_id='ta')])
        result = self.report()
        self.assertIn("| Délai | inconnu (événement antérieur à l'adoption) | — |", result.stdout)


if __name__ == '__main__':
    unittest.main()
