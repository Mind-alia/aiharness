import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'template/scripts/lib'
sys.path.insert(0, str(LIB))
import factory_cost  # noqa: E402
import metrics_claude  # noqa: E402
import orchestrator  # noqa: E402

UTC = datetime.UTC
NOW = datetime.datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)
SESSION_A = '11111111-1111-4111-8111-111111111111'
SESSION_B = '22222222-2222-4222-8222-222222222222'


class ClaudeSessionIdTests(unittest.TestCase):
    def test_first_present_variable_wins(self):
        self.assertEqual(orchestrator.claude_session_id({'CLAUDE_CODE_SESSION_ID': SESSION_A, 'CLAUDE_SESSION_ID': SESSION_B}), SESSION_A)

    def test_fallback_and_absent(self):
        self.assertEqual(orchestrator.claude_session_id({'CLAUDE_SESSION_ID': SESSION_B}), SESSION_B)
        self.assertEqual(orchestrator.claude_session_id({'CLAUDE_CODE_SESSION_ID': '', 'CLAUDE_SESSION_ID': SESSION_B}), SESSION_B)
        self.assertIsNone(orchestrator.claude_session_id({}))
        self.assertIsNone(orchestrator.claude_session_id({'CLAUDE_CODE_SESSION_ID': '', 'CLAUDE_SESSION_ID': ''}))

    def test_invalid_value_is_never_recorded(self):
        self.assertIsNone(orchestrator.claude_session_id({'CLAUDE_CODE_SESSION_ID': 'ABC', 'CLAUDE_SESSION_ID': SESSION_B}))
        self.assertIsNone(orchestrator.claude_session_id({'CLAUDE_CODE_SESSION_ID': 'ABCDEF12-1111-4111-8111-111111111111'}))
        self.assertIsNone(orchestrator.claude_session_id({'CLAUDE_CODE_SESSION_ID': SESSION_A + '\n'}))
        self.assertIsNone(orchestrator.claude_session_id({'CLAUDE_CODE_SESSION_ID': ' ' + SESSION_A}))


class CostBySessionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(os.path.realpath(tmp.name))
        self.root = base / 'project'
        self.root.mkdir()
        (self.root / '.workspace').mkdir()
        self.claude = base / 'claude'
        self.claude.mkdir()
        self.counter = 0

    def claim(self, at, session='u', claude_session_id=None):
        detail = {'session': session, 'model': 'opus', 'cwd': str(self.root)}
        if claude_session_id is not None:
            detail['claude_session_id'] = claude_session_id
        self.write_event({'at': at, 'task_id': 'orchestrator', 'event': 'orchestrator_claimed', 'detail': detail})

    def release(self, at, session='u'):
        self.write_event({'at': at, 'task_id': 'orchestrator', 'event': 'orchestrator_released', 'detail': {'session': session}})

    def write_event(self, line):
        with (self.root / '.workspace/events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(line) + '\n')

    def message(self, at, model, session_id, entrypoint='claude-desktop', msg_id=None):
        self.counter += 1
        record = {
            'type': 'assistant',
            'cwd': str(self.root),
            'timestamp': at,
            'entrypoint': entrypoint,
            'message': {'id': msg_id or 'id%d' % self.counter, 'model': model, 'usage': {'input_tokens': 1, 'output_tokens': 1}},
        }
        if session_id is not None:
            record['sessionId'] = session_id
        with (self.claude / 'a.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record) + '\n')

    def collect(self):
        return factory_cost.collect(self.root, 'u', claude_dir=self.claude, now=NOW)

    def test_session_id_field_of_messages(self):
        self.message('2026-10-01T10:00:00Z', 'm', SESSION_A, msg_id='dup')
        self.message('2026-10-01T10:00:00Z', 'm', None, msg_id='none')
        found = metrics_claude.assistant_messages(self.claude)
        self.assertEqual((found['dup']['session_id'], found['none']['session_id']), (SESSION_A, None))

    def test_duplicate_completes_missing_session_id(self):
        self.message('2026-10-01T10:00:00Z', 'm', None, msg_id='dup')
        self.message('2026-10-01T10:00:00Z', 'm', SESSION_B, msg_id='dup')
        self.assertEqual(metrics_claude.assistant_messages(self.claude)['dup']['session_id'], SESSION_B)

    def test_window_carries_claude_session_id(self):
        self.claim('2026-10-01T10:00:00Z', claude_session_id=SESSION_A)
        self.claim('2026-10-01T11:00:00Z')
        found = factory_cost.windows(self.root, 'u')
        self.assertEqual([w['claude_session_id'] for w in found], [SESSION_A, None])

    def test_window_with_identifier_excludes_claude_p(self):
        self.claim('2026-10-01T10:00:00Z', claude_session_id=SESSION_A)
        self.release('2026-10-01T11:00:00Z')
        self.message('2026-10-01T10:10:00Z', 'opus', SESSION_A)
        self.message('2026-10-01T10:20:00Z', 'sonnet', SESSION_B)
        self.message('2026-10-01T10:30:00Z', 'sonnet', SESSION_B)
        result = self.collect()
        self.assertEqual(list(result['by_model']), ['opus'])
        self.assertEqual(result['total']['messages'], 1)
        self.assertEqual(list(result['side_by_model']), ['sonnet'])
        self.assertEqual(result['side_total']['messages'], 2)

    def test_window_without_identifier_keeps_dominant_session(self):
        self.claim('2026-10-01T10:00:00Z')
        self.release('2026-10-01T11:00:00Z')
        for minute in range(5):
            self.message('2026-10-01T10:%02d:00Z' % (10 + minute), 'opus', SESSION_A)
        for minute in range(2):
            self.message('2026-10-01T10:%02d:00Z' % (30 + minute), 'sonnet', SESSION_B)
        result = self.collect()
        self.assertEqual((result['total']['messages'], list(result['by_model'])), (5, ['opus']))
        self.assertEqual((result['side_total']['messages'], list(result['side_by_model'])), (2, ['sonnet']))

    def test_tie_goes_to_oldest_first_message(self):
        self.claim('2026-10-01T10:00:00Z')
        self.message('2026-10-01T10:30:00Z', 'sonnet', SESSION_B)
        self.message('2026-10-01T10:10:00Z', 'opus', SESSION_A)
        result = self.collect()
        self.assertEqual(list(result['by_model']), ['opus'])
        self.assertEqual(list(result['side_by_model']), ['sonnet'])

    def test_messages_without_session_id_keep_current_rule(self):
        self.claim('2026-10-01T10:00:00Z')
        self.message('2026-10-01T10:10:00Z', 'opus', SESSION_A)
        self.message('2026-10-01T10:20:00Z', 'old', None)
        self.message('2026-10-01T10:30:00Z', 'direct', None, entrypoint='sdk-cli')
        result = self.collect()
        self.assertEqual(sorted(result['by_model']), ['old', 'opus'])
        self.assertEqual(list(result['side_by_model']), ['direct'])

    def test_each_window_has_its_own_session(self):
        self.claim('2026-10-01T10:00:00Z', claude_session_id=SESSION_A)
        self.release('2026-10-01T11:00:00Z')
        self.claim('2026-10-01T12:00:00Z', claude_session_id=SESSION_B)
        self.message('2026-10-01T10:10:00Z', 'first', SESSION_A)
        self.message('2026-10-01T10:20:00Z', 'other', SESSION_B)
        self.message('2026-10-01T12:10:00Z', 'second', SESSION_B)
        self.message('2026-10-01T12:20:00Z', 'foreign', SESSION_A)
        result = self.collect()
        self.assertEqual(sorted(result['by_model']), ['first', 'second'])
        self.assertEqual(sorted(result['side_by_model']), ['foreign', 'other'])


if __name__ == '__main__':
    unittest.main()
