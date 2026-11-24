"""Tests de template/scripts/lib/reading.py : mesure de la lecture avant d'agir."""

import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import metrics_format  # noqa: E402
import reading  # noqa: E402


def detail(common, task, pitfalls, role='execution', **override):
    data = {'role': role, 'agent': 'codex', 'estimate': 'caractères / 4'}
    for part, size in (('common', common), ('task', task), ('pitfalls', pitfalls)):
        data[part + '_chars'] = size
        data[part + '_tokens_estimated'] = size // 4
    data.update(override)
    return data


class MeasureTests(unittest.TestCase):
    def test_known_texts(self):
        task, pitfalls = 'é' * 10, 'p' * 7
        prompt = 'c' * 21 + pitfalls + task
        self.assertEqual(
            reading.measure(prompt, task, pitfalls),
            {
                'common_chars': 21,
                'common_tokens_estimated': 5,
                'task_chars': 10,
                'task_tokens_estimated': 2,
                'pitfalls_chars': 7,
                'pitfalls_tokens_estimated': 1,
                'estimate': 'caractères / 4',
            },
        )

    def test_missing_pitfalls_count_zero(self):
        result = reading.measure('abcdefgh', 'efgh', '')
        self.assertEqual((result['pitfalls_chars'], result['pitfalls_tokens_estimated']), (0, 0))
        self.assertEqual(result['common_chars'], 4)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, *entries, raw=()):
        (self.root / '.workspace').mkdir(exist_ok=True)
        lines = [json.dumps(entry) if isinstance(entry, dict) else entry for entry in entries] + list(raw)
        (self.root / '.workspace/events.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    @staticmethod
    def event(task_id, payload, at='2026-05-01T10:00:00+00:00', kind='reading_measured'):
        return {'at': at, 'task_id': task_id, 'event': kind, 'detail': payload}

    def test_task_line_without_event(self):
        self.assertEqual(reading.task_line(self.root, 't1'), "Lecture avant d'agir : non mesurée.")
        self.write(self.event('t2', detail(8, 8, 8)))
        self.assertEqual(reading.task_line(self.root, 't1'), "Lecture avant d'agir : non mesurée.")

    def test_task_line_uses_last_event(self):
        self.write(self.event('t1', detail(8, 8, 8)), self.event('t1', detail(401, 90, 0)))
        self.assertEqual(
            reading.task_line(self.root, 't1'),
            "Lecture avant d'agir : consigne commune 401 caractères (~100 tokens estimés), "
            'fiche 90 caractères (~22 tokens estimés), pièges 0 caractères (~0 tokens estimés).',
        )

    def test_collect_means_by_role_and_since(self):
        self.write(
            self.event('t1', detail(10, 20, 30)),
            self.event('t2', detail(21, 41, 1)),
            self.event('t3', detail(4, 4, 4, role='review')),
            self.event('t4', detail(1000, 1000, 1000), at='2026-04-01T00:00:00+00:00'),
        )
        self.assertEqual(
            reading.collect(self.root),
            {
                'execution': {'delegations': 3, 'common_chars': 343, 'task_chars': 353, 'pitfalls_chars': 343},
                'review': {'delegations': 1, 'common_chars': 4, 'task_chars': 4, 'pitfalls_chars': 4},
            },
        )
        self.assertEqual(
            reading.collect(self.root, datetime.date(2026, 5, 1))['execution'],
            {'delegations': 2, 'common_chars': 15, 'task_chars': 30, 'pitfalls_chars': 15},
        )
        self.assertEqual(reading.collect(self.root, datetime.date(2026, 6, 1)), {})

    def test_collect_without_journal(self):
        self.assertEqual(reading.collect(self.root), {})

    def test_invalid_events_are_ignored(self):
        good = detail(8, 8, 8)
        bad = [
            None,
            'texte',
            [1],
            {k: v for k, v in good.items() if k != 'task_chars'},
            detail(8, 8, 8, task_chars='8'),
            detail(8, 8, 8, task_chars=8.0),
            detail(8, 8, 8, task_chars=True, task_tokens_estimated=0),
            detail(8, 8, 8, common_chars=-4, common_tokens_estimated=-1),
            detail(8, 8, 8, pitfalls_tokens_estimated=3),
            detail(8, 8, 8, role=None),
            detail(8, 8, 8, role=''),
            detail(8, 8, 8, role=7),
        ]
        self.write(*[self.event('t1', payload) for payload in bad], raw=['{pas du json', ''])
        self.assertEqual(reading.task_line(self.root, 't1'), "Lecture avant d'agir : non mesurée.")
        self.assertEqual(reading.collect(self.root), {})
        self.write(*[self.event('t1', payload) for payload in bad], self.event('t1', good))
        self.assertIn('consigne commune 8 caractères', reading.task_line(self.root, 't1'))
        self.assertEqual(
            reading.collect(self.root), {'execution': {'delegations': 1, 'common_chars': 8, 'task_chars': 8, 'pitfalls_chars': 8}}
        )

    def test_lines_that_break_the_journal_reader_are_skipped_alone(self):
        good = self.event('t1', detail(8, 8, 8))
        broken = [
            '{"at": 5, "task_id": "t1", "event": "reading_measured", "detail": 1}',
            '{"at": null, "task_id": "t1", "event": "reading_measured", "detail": 1}',
            '[' * 100000,
        ]
        self.write(*broken, good)
        self.assertIn('consigne commune 8 caractères', reading.task_line(self.root, 't1'))
        self.assertEqual(reading.collect(self.root)['execution']['delegations'], 1)
        self.assertEqual(reading.collect(self.root, datetime.date(2026, 6, 1)), {})


class FormatTests(unittest.TestCase):
    def test_empty_section(self):
        self.assertEqual(
            metrics_format.format_reading({}),
            [
                "\n## Lecture avant d'agir, par rôle",
                'Moyenne par délégation ; tokens estimés = caractères / 4 (estimation, pas un compte).',
                '- aucune mesure',
            ],
        )

    def test_table_sorted_by_role(self):
        lines = metrics_format.format_reading(
            {
                'review': {'delegations': 1, 'common_chars': 9, 'task_chars': 5, 'pitfalls_chars': 3},
                'execution': {'delegations': 2, 'common_chars': 400, 'task_chars': 80, 'pitfalls_chars': 0},
            }
        )
        self.assertEqual(
            lines[2:],
            [
                '| Rôle | Délégations | Consigne commune | Fiche | Pièges |',
                '| --- | --- | --- | --- | --- |',
                '| execution | 2 | 400 car. (~100 tokens) | 80 car. (~20 tokens) | 0 car. (~0 tokens) |',
                '| review | 1 | 9 car. (~2 tokens) | 5 car. (~1 tokens) | 3 car. (~0 tokens) |',
            ],
        )

    def test_role_stays_in_one_cell(self):
        for role in ('execution | 999 | x |\n| injected', 'execution | 999 | x |\r| injected'):
            lines = metrics_format.format_reading({role: {'delegations': 1, 'common_chars': 4, 'task_chars': 4, 'pitfalls_chars': 4}})
            self.assertEqual(len(lines), 5)
            self.assertTrue(lines[4].startswith('| execution \\| 999 \\| x \\| | 1 | '), lines[4])
            self.assertNotIn('injected', lines[4])
            self.assertNotIn('\n', lines[4])
            self.assertNotIn('\r', lines[4])


if __name__ == '__main__':
    unittest.main()
