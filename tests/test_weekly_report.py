"""Tests de template/scripts/weekly-report.sh : le vrai script, lancé dans un projet Git temporaire (aucun réseau).

`report-check.sh` est remplacé par un faux script qui journalise ses arguments et sort avec `FAKE_EXIT`.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / 'template/scripts'
IGNORED = shutil.ignore_patterns('__tests__', '__pycache__')
FAKE_CHECK = '#!/bin/sh\nprintf \'%s\\n\' "$@" >> "$FAKE_LOG"\nexit "${FAKE_EXIT:-0}"\n'
TRAVAUX = (
    '# Travaux\n\n| Id | Travail | But |\n|---|---|---|\n| T01 | Premier | un |\n| T02 | Deuxième | deux |\n| T03 | Troisième | trois |\n'
)
AUTHOR_ENV = {
    'GIT_AUTHOR_NAME': 'Test',
    'GIT_AUTHOR_EMAIL': 'test@example.invalid',
    'GIT_COMMITTER_NAME': 'Test',
    'GIT_COMMITTER_EMAIL': 'test@example.invalid',
}


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'projet'
        self.root.mkdir()
        shutil.copytree(TEMPLATE, self.root / 'scripts', ignore=IGNORED)
        self.script = self.root / 'scripts/weekly-report.sh'
        self.log = self.root.parent / 'check-calls.txt'
        fake = self.root / 'scripts/report-check.sh'
        fake.write_text(FAKE_CHECK, encoding='utf-8')
        fake.chmod(0o755)
        self.write('docs/TRAVAUX.md', TRAVAUX)
        self.init_repo('2026-10-12T10:00:00+00:00')

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def write_jsonl(self, relative, rows):
        self.write(relative, ''.join(row if isinstance(row, str) else json.dumps(row) + '\n' for row in rows))

    def git(self, *args, date=None):
        env = dict(os.environ, **AUTHOR_ENV)
        if date is not None:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        subprocess.run(['git', '-C', str(self.root), '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True, env=env)

    def init_repo(self, date):
        """Dépôt neuf dont le premier commit est daté `date` ; le contenu courant est commité."""
        shutil.rmtree(self.root / '.git', ignore_errors=True)
        self.git('init', '-q')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'base', date=date)

    def commit(self, date, message):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message, date=date)

    def run_weekly(self, *args, fake_exit='0'):
        env = dict(os.environ, FAKE_LOG=str(self.log), FAKE_EXIT=fake_exit)
        return subprocess.run(['sh', str(self.script), *args], capture_output=True, text=True, timeout=120, env=env)

    def report(self, day):
        return (self.root / 'docs/RAPPORTS' / ('semaine-%s.md' % day)).read_text(encoding='utf-8')

    def seed_week(self):
        """Une semaine 2026-W43 remplie : défauts, coûts, bancs, étalons, travaux retirés."""
        self.write_jsonl(
            '.workspace/events.jsonl',
            [
                {'at': '2026-10-05T09:00:00+00:00', 'task_id': 't0', 'event': 'merged', 'detail': None},
                {'at': '2026-10-06T09:00:00+00:00', 'task_id': 't0', 'event': 'note', 'detail': {'kind': 'defect'}},
                {'at': '2026-10-20T09:00:00+00:00', 'task_id': 't1', 'event': 'merged', 'detail': None},
                {'at': '2026-10-21T09:00:00+00:00', 'task_id': 't1', 'event': 'note', 'detail': {'kind': 'defect'}},
                {'at': '2026-10-22T09:00:00+00:00', 'task_id': 't1', 'event': 'note', 'detail': {'kind': 'subagent'}},
            ],
        )
        claude = {'role': 'execution', 'provider': 'claude'}
        self.write_jsonl(
            '.workspace/metrics/consumption.jsonl',
            [
                dict(claude, at='2026-10-18T23:59:59+00:00', tokens={'input': 99, 'cached_input': 99, 'cache_creation': 99, 'output': 99}),
                dict(
                    claude,
                    at='2026-10-19T00:00:00+00:00',
                    tokens={'input': 10, 'cached_input': 1000, 'cache_creation': 200, 'output': 50, 'reasoning': 7},
                ),
                dict(claude, at='2026-10-24T18:00:00Z', tokens={'input': 5, 'cached_input': 500, 'cache_creation': 100, 'output': 25}),
                dict(claude, at='2026-10-26T00:00:00+00:00', tokens={'input': 99, 'cached_input': 99, 'cache_creation': 99, 'output': 99}),
                dict(
                    role='execution', provider='codex', at='2026-10-21T10:00:00+00:00', tokens={'input': 3, 'cached_input': 0, 'output': 2}
                ),
                '{pas du json\n',
            ],
        )
        self.write('docs/security/attack-bench-2026-10-19.md', '# banc\n')
        self.write('docs/security/attack-bench-2026-10-20-2.md', '# banc\n')
        self.write('docs/security/attack-bench-2026-10-12.md', '# banc hors semaine\n')
        self.write('docs/VERIFICATEUR-taux.md', '# Taux\n\n- Date : 2026-10-20\n\nfaux rejets : 0/3\nfausses acceptations : 0/3\n')
        self.write('docs/TRAVAUX.md', '# Travaux\n\n| Id | Travail | But |\n|---|---|---|\n| T01 | Premier | un |\n')
        self.commit('2026-10-20T12:00:00+00:00', 'retire T02 et T03')

    def test_full_week_report_and_check(self):
        self.seed_week()
        result = self.run_weekly('--date', '2026-10-25')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'Rapport écrit : docs/RAPPORTS/semaine-2026-10-25.md\n')
        text = self.report('2026-10-25')
        self.assertTrue(text.startswith('# Rapport de la semaine 2026-W43\n\nDu 2026-10-19 au 2026-10-25 (UTC).\n'))
        headings = ['## Défauts après fusion', '## Coût par fournisseur', '## Bancs et étalons', '## Travaux sortis de la table']
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('Défauts trouvés après fusion cette semaine : 1', text)
        self.assertIn('| Fournisseur | Appels | Entrée | Cache lu | Cache créé | Sortie | Total |\n|---|---|---|---|---|---|---|\n', text)
        self.assertIn('| claude | 2 | 15 | 1500 | 300 | 75 | 1890 |', text)
        self.assertIn('| codex | 1 | 3 | 0 | 0 | 2 | 5 |', text)
        self.assertIn('- banc d’attaque : attack-bench-2026-10-19.md\n- banc d’attaque : attack-bench-2026-10-20-2.md', text)
        self.assertNotIn('attack-bench-2026-10-12.md', text)
        self.assertIn('- étalons : faux rejets : 0/3\n- étalons : fausses acceptations : 0/3', text)
        self.assertIn('- T02 · Deuxième\n- T03 · Troisième', text)
        calls = self.log.read_text(encoding='utf-8').splitlines()
        self.assertEqual(calls, [str(self.root / 'docs/RAPPORTS/semaine-2026-10-25.md')])

    def test_empty_week_has_four_sections_and_none(self):
        result = self.run_weekly('--date', '2026-11-02')
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.report('2026-11-02')
        for heading in ('## Défauts après fusion', '## Coût par fournisseur', '## Bancs et étalons', '## Travaux sortis de la table'):
            self.assertIn(heading, text)
        self.assertIn('Défauts trouvés après fusion cette semaine : 0', text)
        self.assertIn('|---|---|---|---|---|---|---|\n', text)
        self.assertEqual(text.count('Aucun.'), 2)

    def test_no_commit_before_week_gives_insufficient_history(self):
        self.init_repo('2026-10-20T10:00:00+00:00')
        result = self.run_weekly('--date', '2026-10-25')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Historique insuffisant.', self.report('2026-10-25'))

    def test_invalid_date_is_refused_without_writing(self):
        result = self.run_weekly('--date', '2026-13-40')
        self.assertEqual(result.returncode, 64)
        self.assertEqual(result.stdout, '')
        self.assertIn('date invalide', result.stderr)
        self.assertFalse((self.root / 'docs/RAPPORTS').exists())

    def test_unknown_option_is_usage_error(self):
        result = self.run_weekly('--bogus')
        self.assertEqual(result.returncode, 64)
        self.assertEqual(result.stdout, '')
        self.assertFalse((self.root / 'docs/RAPPORTS').exists())

    def test_failed_check_keeps_report_and_exits_one(self):
        result = self.run_weekly('--date', '2026-10-25', fake_exit='4')
        self.assertEqual(result.returncode, 1)
        self.assertIn('report-check.sh en échec', result.stderr)
        self.assertTrue((self.root / 'docs/RAPPORTS/semaine-2026-10-25.md').is_file())

    def test_no_check_skips_report_check(self):
        result = self.run_weekly('--date', '2026-10-25', '--no-check', fake_exit='4')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())

    def test_rewrite_replaces_existing_report(self):
        self.run_weekly('--date', '2026-10-25', '--no-check')
        self.write('docs/RAPPORTS/semaine-2026-10-25.md', 'ancien contenu\n')
        result = self.run_weekly('--date', '2026-10-25', '--no-check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.report('2026-10-25').startswith('# Rapport de la semaine 2026-W43'))


if __name__ == '__main__':
    unittest.main()
