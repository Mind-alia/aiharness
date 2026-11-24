import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.campaign import export as campaign_export


class CampaignExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(dir=os.environ.get('WORKSPACE_TEST_TMP'))).resolve()
        self.addCleanup(shutil.rmtree, self.directory)
        self.results = self.directory / 'results.jsonl'

    def write_rows(self, rows):
        self.results.write_text(''.join(json.dumps(row) + '\n' for row in rows) + '{bad json\n', encoding='utf-8')

    def test_exports_ordered_text_rows_and_replaces_outputs(self):
        self.write_rows(
            [
                dict(
                    task='m1',
                    arm='bras',
                    repetition=2,
                    status='completed',
                    attempts=1,
                    duration_seconds=1.26,
                    evaluation=dict(passed=2, total=3),
                    consumption={'z': {'b': {'total_tokens': None}}, 'a': {'x': {'total_tokens': 4}}},
                    journal=dict(human_interventions=1, human_minutes=5, scope_violations=2, secret_violations=1),
                    judge=dict(verdict='accepté'),
                ),
                dict(task='m2', arm='bras2', repetition=1, status='failed'),
            ]
        )
        (self.directory / 'courses.csv').write_text('old')
        csv_path, json_path = campaign_export.export(self.directory)
        self.assertEqual(Path(csv_path), self.directory / 'courses.csv')
        self.assertEqual(Path(json_path), self.directory / 'courses.json')
        with open(csv_path, newline='', encoding='utf-8') as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[0], list(campaign_export.COLUMNS))
        self.assertEqual(
            rows[1], ['m1', 'bras', '2', 'non', 'oui', '1', '1.3', 'a/x=4; z/b=non mesuré', '1', '5', 'périmètre=2; secret=1', 'accepté']
        )
        self.assertEqual(
            rows[2],
            [
                'm2',
                'bras2',
                '1',
                'non mesuré',
                'non mesuré',
                'non mesuré',
                'non mesuré',
                'non mesuré',
                'non mesuré',
                'non mesuré',
                'non mesuré',
                'non mesuré',
            ],
        )
        data = json.loads(Path(json_path).read_text(encoding='utf-8'))
        self.assertEqual(list(data[0]), list(campaign_export.COLUMNS))
        self.assertEqual(data[0]['tokens'], rows[1][7])

    def test_journal_summary_skips_invalid_lines_and_counts(self):
        worktree = self.directory / 'worktree'
        journal = worktree / '.workspace/events.jsonl'
        journal.parent.mkdir(parents=True)
        journal.write_text(
            '\n'.join(
                [
                    '{bad',
                    json.dumps(dict(type='human_intervention', detail=dict(minutes=3))),
                    json.dumps(dict(type='human_intervention', detail=dict(minutes='4'))),
                    json.dumps(dict(type='outcome', detail=dict(outcome='out_of_scope'))),
                    json.dumps(dict(type='outcome', detail=dict(outcome='secret_detected'))),
                ]
            ),
            encoding='utf-8',
        )
        self.assertEqual(
            campaign_export.journal_summary(worktree), dict(human_interventions=2, human_minutes=3, scope_violations=1, secret_violations=1)
        )
        self.assertIsNone(campaign_export.journal_summary(self.directory / 'absent'))

    def test_export_skips_lines_with_invalid_utf8(self):
        self.results.write_bytes(b'{"task":"ok","arm":"a","repetition":1}\n\xff\n')
        json_path = campaign_export.export(self.directory)[1]
        rows = json.loads(Path(json_path).read_text(encoding='utf-8'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['mission'], 'ok')

    def test_cli_invalid_directories_return_two_without_stdout_or_files(self):
        script = Path(__file__).resolve().parents[1] / 'tools/campaign/campaign.py'
        for path in (self.directory / 'missing', self.directory):
            proc = subprocess.run(
                [sys.executable, str(script), 'export', str(path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, '')
            self.assertTrue(proc.stderr)
            self.assertFalse((path / 'courses.csv').exists())
            self.assertFalse((path / 'courses.json').exists())


if __name__ == '__main__':
    unittest.main()
