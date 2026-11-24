import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import task_cost  # noqa: E402


class TaskCostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def status(self, task_id, **fields):
        directory = self.root / '.workspace/run/delegate' / task_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'status.json').write_text(json.dumps(dict(id=task_id, **fields)))

    def journal(self, *rows, raw=()):
        directory = self.root / '.workspace/metrics'
        directory.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(row) for row in rows] + list(raw)
        (directory / 'consumption.jsonl').write_text('\n'.join(lines) + '\n')

    @staticmethod
    def row(provider, task_id='t1', role='execution', **tokens):
        return dict(task_id=task_id, provider=provider, role=role, tokens=tokens)

    def test_claude_agent_with_codex_review(self):
        self.status(
            't1',
            agent='claude-sonnet',
            attempts=9,
            metrics=dict(duration_seconds=12.6, attempts=2, tokens=dict(input=10, output=20, cached_input=30, cache_creation=40)),
        )
        self.journal(
            self.row('claude', input=1000, output=1000, cached_input=1000, cache_creation=1000),
            self.row('codex', input=5, output=6, cached_input=99),
            self.row('codex', role='relecture', input=7, output=8),
            self.row('codex', task_id='t2', input=500, output=500),
            raw=['not json', '[]'],
        )
        self.assertEqual(
            task_cost.cost_line(self.root, 't1'),
            'coût t1 : claude in=10 out=20 cache_lu=30 cache_cree=40, codex in=12 out=14, antigravity total=0, durée=13s, tentatives=2',
        )

    def test_antigravity_agent_ignores_its_journal_rows(self):
        self.status('t1', agent='antigravity', attempts=3, metrics=dict(duration_seconds=4, tokens=dict(total=77)))
        self.journal(
            self.row('antigravity', total=5000),
            self.row('claude', role='orchestration', input=1, output=2, cached_input=3, cache_creation=4),
            self.row('claude', role='relecture', input=1, output=2, cached_input=3, cache_creation=4),
        )
        self.assertEqual(
            task_cost.cost_line(self.root, 't1'),
            'coût t1 : claude in=2 out=4 cache_lu=6 cache_cree=8, codex in=0 out=0, antigravity total=77, durée=4s, tentatives=3',
        )

    def test_codex_agent_reads_its_status_tokens(self):
        self.status('t1', agent='codex', metrics=dict(duration_seconds=1.2, attempts=1, tokens=dict(input=3, output=4)))
        self.journal(self.row('codex', input=100, output=100), self.row('antigravity', total=9))
        self.assertEqual(
            task_cost.cost_line(self.root, 't1'),
            'coût t1 : claude in=0 out=0 cache_lu=0 cache_cree=0, codex in=3 out=4, antigravity total=9, durée=1s, tentatives=1',
        )

    def test_without_status_everything_comes_from_the_journal(self):
        self.journal(self.row('codex', input=1, output=2), self.row('codex', input=3, output=4), self.row('antigravity', total=8))
        self.assertEqual(
            task_cost.cost_line(self.root, 't1'),
            'coût t1 : claude in=0 out=0 cache_lu=0 cache_cree=0, codex in=4 out=6, antigravity total=8, durée=?s, tentatives=?',
        )

    def test_without_any_data_counters_are_zero(self):
        self.assertEqual(
            task_cost.cost_line(self.root, 't1'),
            'coût t1 : claude in=0 out=0 cache_lu=0 cache_cree=0, codex in=0 out=0, antigravity total=0, durée=?s, tentatives=?',
        )

    def test_unknown_values_are_never_invented(self):
        self.status('t1', agent='claude-haiku', attempts=2, metrics=dict(tokens=dict(input=10, output=None, cached_input=30)))
        self.journal(self.row('codex', input=1, output=2), self.row('codex', input=None, output=3), self.row('antigravity', total=None))
        self.assertEqual(
            task_cost.cost_line(self.root, 't1'),
            'coût t1 : claude in=10 out=? cache_lu=30 cache_cree=?, codex in=? out=5, antigravity total=?, durée=?s, tentatives=2',
        )

    def test_symlinked_sources_are_refused(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'consumption.jsonl').write_text(json.dumps(self.row('codex', input=1, output=1)) + '\n')
        (self.root / '.workspace').mkdir()
        (self.root / '.workspace/metrics').symlink_to(outside)
        self.status('t1', agent='codex', metrics=dict(tokens=dict(input=1, output=1)))
        status_file = self.root / '.workspace/run/delegate/t1/status.json'
        real = self.root / 'real-status.json'
        real.write_text(status_file.read_text())
        status_file.unlink()
        status_file.symlink_to(real)
        self.assertEqual(
            task_cost.cost_line(self.root, 't1'),
            'coût t1 : claude in=? out=? cache_lu=? cache_cree=?, codex in=? out=?, antigravity total=?, durée=?s, tentatives=?',
        )

    def test_invalid_identifier_is_an_error(self):
        for bad in ('../etc', 'A', 'a/b', '', 'x y', None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                task_cost.cost_line(self.root, bad)


class TaskCostCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / 'template/scripts', self.root / 'scripts')

    def report(self, *args):
        return subprocess.run(['bash', str(self.root / 'scripts/report.sh')] + list(args), text=True, capture_output=True)

    def test_task_without_events_prints_the_cost_line(self):
        directory = self.root / '.workspace/run/delegate/t1'
        directory.mkdir(parents=True)
        (directory / 'status.json').write_text(
            json.dumps(dict(id='t1', agent='codex', metrics=dict(attempts=1, duration_seconds=2, tokens=dict(input=3, output=4))))
        )
        result = self.report('--task', 't1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            'coût t1 : claude in=0 out=0 cache_lu=0 cache_cree=0, codex in=3 out=4, '
            'antigravity total=0, durée=2s, tentatives=1\n'
            'tests cachés : non joués\n'
            "Lecture avant d'agir : non mesurée.\n"
            'Interventions humaines : 0 intervention, 0 min\n',
        )

    def test_task_with_invalid_identifier_fails(self):
        result = self.report('--task', '../x')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_task_with_events_keeps_listing_events(self):
        result = self.report('--task', 't1', '--events')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('coût', result.stdout)


if __name__ == '__main__':
    unittest.main()
