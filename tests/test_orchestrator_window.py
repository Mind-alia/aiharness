import datetime
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'template/scripts/lib'
sys.path.insert(0, str(LIB))
import factory_cost  # noqa: E402
import metrics_claude  # noqa: E402
import report_check  # noqa: E402
import report_day  # noqa: E402

NOW = datetime.datetime(2026, 10, 23, 12, 0, 0, tzinfo=datetime.UTC)
AT = '2026-10-23T10:30:00Z'
SLUG = re.compile(r'[a-z0-9-]+')


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(os.path.realpath(tmp.name))
        self.root = base / 'project'
        (self.root / '.workspace/metrics').mkdir(parents=True)
        self.claude = base / 'claude'
        self.claude.mkdir()
        self.counter = 0
        line = {
            'at': '2026-10-23T10:00:00Z',
            'task_id': 'orchestrator',
            'event': 'orchestrator_claimed',
            'orchestrator': 'u',
            'detail': {'session': 'u', 'cwd': str(self.root)},
        }
        (self.root / '.workspace/events.jsonl').write_text(json.dumps(line) + '\n', encoding='utf-8')

    def message(self, entrypoint='claude-desktop', sidechain=None, msg_id=None, usage=(1, 2, 3, 4), name='a.jsonl'):
        self.counter += 1
        record = {
            'type': 'assistant',
            'cwd': str(self.root),
            'timestamp': AT,
            'message': {
                'id': msg_id or 'id%d' % self.counter,
                'model': 'm',
                'usage': {
                    'input_tokens': usage[0],
                    'cache_read_input_tokens': usage[1],
                    'cache_creation_input_tokens': usage[2],
                    'output_tokens': usage[3],
                },
            },
        }
        if entrypoint is not None:
            record['entrypoint'] = entrypoint
        if sidechain is not None:
            record['isSidechain'] = sidechain
        with (self.claude / name).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record) + '\n')

    def collect(self):
        return factory_cost.collect(self.root, 'u', claude_dir=self.claude, now=NOW)


class AssistantMessagesTests(Base):
    def test_entrypoint_and_sidechain_fields(self):
        self.message(entrypoint='sdk-py', sidechain=True)
        self.message(entrypoint=None, sidechain=False)
        self.message(entrypoint='cli', sidechain='yes')
        found = list(metrics_claude.assistant_messages(self.claude).values())
        self.assertEqual([(m['entrypoint'], m['sidechain']) for m in found], [('sdk-py', True), (None, False), ('cli', False)])

    def test_duplicate_keeps_first_entrypoint_and_any_sidechain(self):
        self.message(entrypoint=None, sidechain=False, msg_id='same')
        self.message(entrypoint='cli', sidechain=True, msg_id='same', name='b.jsonl')
        self.message(entrypoint='sdk-cli', sidechain=False, msg_id='same', name='c.jsonl')
        found = metrics_claude.assistant_messages(self.claude)['same']
        self.assertEqual((found['entrypoint'], found['sidechain']), ('cli', True))


class SessionSplitTests(Base):
    def test_session_and_side_messages_are_separated(self):
        self.message(entrypoint='claude-desktop')
        self.message(entrypoint='cli', sidechain=False)
        self.message(entrypoint='claude-desktop', sidechain=True)
        self.message(entrypoint='sdk-py')
        self.message(entrypoint='sdk-cli')
        self.message(entrypoint=None)
        result = self.collect()
        self.assertEqual(result['total'], {'messages': 2, 'input': 2, 'cache_read': 4, 'cache_creation': 6, 'output': 8, 'total': 20})
        self.assertEqual(
            result['side_total'], {'messages': 4, 'input': 4, 'cache_read': 8, 'cache_creation': 12, 'output': 16, 'total': 40}
        )
        self.assertEqual(result['by_model']['m']['messages'], 2)
        self.assertEqual(result['side_by_model']['m']['messages'], 4)

    def test_no_side_message_gives_zero_side_total(self):
        self.message()
        result = self.collect()
        self.assertEqual((result['side_by_model'], result['side_total']['messages']), ({}, 0))

    def test_report_day_lines(self):
        self.message()
        self.message(entrypoint='sdk-py', usage=(5, 6, 7, 8))
        lines = report_day.render(self.root, '2026-10-23', 'u', self.claude).splitlines()
        self.assertIn('| orchestrateur | claude | 1 | 1 | 2 | 3 | 4 |', lines)
        self.assertIn('| sous-agents et appels directs | claude | 1 | 5 | 6 | 7 | 8 |', lines)
        self.assertIn('| Total | | 0 | 6 | 8 | 10 | 12 |', lines)


class ReportTaskIdTests(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(
            report_check.report_task_id('RAPPORT-JOUR-2026-10-23-comptabilite.md'), 'report-check-rapport-jour-2026-10-23-comptabilite'
        )
        self.assertEqual(report_check.report_task_id('Été 2026 !.md'), 'report-check-t-2026')
        self.assertEqual(report_check.report_task_id('---.md'), 'report-check-rapport')
        self.assertEqual(report_check.report_task_id('.md'), 'report-check-md')

    def test_length_and_alphabet(self):
        for name in ('a' * 200 + '.md', 'a' * 50 + '-' + 'b' * 50 + '.md', 'x' * 50 + ' ' + 'y.md', 'é.md', ''):
            task_id = report_check.report_task_id(name)
            self.assertLessEqual(len(task_id), 64, name)
            self.assertTrue(SLUG.fullmatch(task_id), task_id)
            self.assertFalse(task_id.endswith('-'), task_id)
        self.assertEqual(report_check.report_task_id('x' * 50 + ' ' + 'y.md'), 'report-check-' + 'x' * 50)


if __name__ == '__main__':
    unittest.main()
