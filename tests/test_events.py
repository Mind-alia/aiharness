"""Tests unitaires de template/scripts/lib/events.py (aucun accès réseau)."""

import contextlib
import datetime
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parents[1] / 'template/scripts/lib'
sys.path.insert(0, str(LIB))

import events  # noqa: E402


class EventsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / events.EVENTS_PATH

    def lines(self):
        return self.path.read_text(encoding='utf-8').splitlines()

    def put(self, *rows):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(''.join(row + '\n' for row in rows), encoding='utf-8')

    @staticmethod
    def row(at, task_id='t', event='outcome', detail=None):
        return json.dumps({'at': at, 'task_id': task_id, 'event': event, 'detail': detail})

    def test_kinds_are_the_agreed_vocabulary(self):
        self.assertEqual(
            events.KINDS,
            (
                'prepared',
                'delegate_started',
                'attempt_started',
                'attempt_finished',
                'verify',
                'acceptance',
                'outcome',
                'merged',
                'closed',
                'quota',
                'orchestrator_claimed',
                'orchestrator_released',
                'selfcheck',
                'subagent_started',
                'subagent_finished',
                'question_answered',
                'note',
                'state',
                'reading_measured',
                'human_intervention',
                'tool_refused',
                'breaker_instrument',
                'cascade_step',
                'correction_reverted',
            ),
        )

    def test_record_appends_sorted_json_lines_in_utc(self):
        events.record(self.root, 'demo', 'attempt_started', {'z': 1, 'attempt': 1, 'text': 'é'})
        events.record(self.root, 'demo', 'closed')
        first, second = self.lines()
        self.assertEqual(list(json.loads(first)), ['at', 'detail', 'event', 'orchestrator', 'task_id'])
        self.assertIn('"detail": {"attempt": 1, "text": "é", "z": 1}', first)
        entry = json.loads(first)
        self.assertTrue(entry['at'].endswith('+00:00'))
        self.assertEqual(datetime.datetime.fromisoformat(entry['at']).utcoffset(), datetime.timedelta(0))
        self.assertEqual((entry['task_id'], entry['event']), ('demo', 'attempt_started'))
        self.assertIsNone(json.loads(second)['detail'])

    def put_lock(self, text):
        lock = self.root / events.LOCK_PATH
        lock.parent.mkdir(exist_ok=True)
        lock.write_text(text, encoding='utf-8')

    def orchestrator_of_new_line(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            events.record(self.root, 'demo', 'outcome')
        self.assertEqual(err.getvalue(), '')
        return json.loads(self.lines()[-1])['orchestrator']

    def test_orchestrator_is_the_session_of_the_lock(self):
        self.put_lock(json.dumps({'session': 'usine-1', 'model': 'm', 'pid': 1}))
        self.assertEqual(self.orchestrator_of_new_line(), 'usine-1')

    def test_orchestrator_is_null_without_lock(self):
        self.assertIsNone(self.orchestrator_of_new_line())

    def test_orchestrator_is_null_with_unreadable_lock(self):
        for text in ('{pas du json', '', '[1, 2]', '"usine"', '{"model": "m"}'):
            with self.subTest(text=text):
                self.put_lock(text)
                self.assertIsNone(self.orchestrator_of_new_line())

    def test_orchestrator_is_null_with_empty_or_non_string_session(self):
        for session in ('', None, 3, ['a']):
            with self.subTest(session=session):
                self.put_lock(json.dumps({'session': session}))
                self.assertIsNone(self.orchestrator_of_new_line())

    def test_orchestrator_is_null_when_lock_is_a_symlink(self):
        target = self.root / 'elsewhere.json'
        target.write_text(json.dumps({'session': 'usine-1'}), encoding='utf-8')
        (self.root / '.workspace').mkdir()
        (self.root / events.LOCK_PATH).symlink_to(target)
        self.assertIsNone(self.orchestrator_of_new_line())

    def test_orchestrator_is_null_when_lock_is_a_directory(self):
        (self.root / events.LOCK_PATH).mkdir(parents=True)
        self.assertIsNone(self.orchestrator_of_new_line())

    def test_every_line_carries_the_orchestrator_key(self):
        self.put_lock(json.dumps({'session': 'usine-1'}))
        for kind in ('prepared', 'closed'):
            events.record(self.root, 'demo', kind)
        self.assertEqual([e['orchestrator'] for e in events.read(self.root)], ['usine-1', 'usine-1'])

    def test_record_creates_workspace_0700_and_file_0600(self):
        old = os.umask(0o022)
        self.addCleanup(os.umask, old)
        events.record(self.root, 'demo', 'prepared', {'branch': 'b'})
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_programming_errors_raise(self):
        with self.assertRaises(ValueError):
            events.record(self.root, 'demo', 'unknown')
        for bad in ('', None):
            with self.assertRaises(ValueError):
                events.record(self.root, bad, 'outcome')
        self.assertFalse(self.path.exists())

    def test_symlinked_file_is_refused_without_exception(self):
        target = self.root / 'elsewhere.jsonl'
        target.write_text('', encoding='utf-8')
        self.path.parent.mkdir()
        self.path.symlink_to(target)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            events.record(self.root, 'demo', 'outcome')
        self.assertTrue(err.getvalue().startswith('events: '))
        self.assertEqual(err.getvalue().count('\n'), 1)
        self.assertEqual(target.read_text(encoding='utf-8'), '')

    def test_symlinked_parent_is_refused_without_exception(self):
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        (self.root / '.workspace').symlink_to(elsewhere)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            events.record(self.root, 'demo', 'outcome')
        self.assertTrue(err.getvalue().startswith('events: '))
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_oserror_is_not_propagated(self):
        err = io.StringIO()
        with mock.patch('os.open', side_effect=PermissionError('refusé')), contextlib.redirect_stderr(err):
            events.record(self.root, 'demo', 'outcome')
        self.assertEqual(err.getvalue(), 'events: refusé\n')

    def test_read_keeps_file_order_and_filters_by_task(self):
        for task_id, kind in (('a', 'prepared'), ('b', 'prepared'), ('a', 'closed')):
            events.record(self.root, task_id, kind)
        self.assertEqual(
            [(e['task_id'], e['event']) for e in events.read(self.root)], [('a', 'prepared'), ('b', 'prepared'), ('a', 'closed')]
        )
        self.assertEqual([e['event'] for e in events.read(self.root, task_id='a')], ['prepared', 'closed'])
        self.assertEqual(events.read(self.root, task_id='none'), [])

    def test_read_without_file_is_empty(self):
        self.assertEqual(events.read(self.root), [])
        self.assertEqual(events.read_errors(self.root), 0)

    def test_since_compares_instants_not_strings(self):
        self.put(
            self.row('2026-10-01T23:30:00+00:00', 'early'),
            self.row('2026-10-02T01:00:00+02:00', 'same-instant-as-early'),
            self.row('2026-10-02T00:00:00+00:00', 'midnight'),
            self.row('2026-10-02T09:00:00Z', 'morning'),
        )

        def names(**kw):
            return [e['task_id'] for e in events.read(self.root, **kw)]

        self.assertEqual(names(since=datetime.date(2026, 10, 2)), ['midnight', 'morning'])
        self.assertEqual(
            names(since=datetime.datetime(2026, 10, 1, 23, 0, tzinfo=datetime.UTC)),
            ['early', 'same-instant-as-early', 'midnight', 'morning'],
        )
        self.assertEqual(
            names(since=datetime.datetime(2026, 10, 2, 0, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))),
            ['early', 'same-instant-as-early', 'midnight', 'morning'],
        )
        self.assertEqual(names(since=datetime.date(2099, 1, 1)), [])

    def test_corrupt_lines_are_skipped_and_counted(self):
        self.put(
            self.row('2026-10-01T00:00:00+00:00', 'one'),
            '',
            '{not json',
            '[1, 2]',
            '{"task_id": "x"}',
            self.row('pas une date', 'bad-date'),
            self.row('2026-10-01T00:00:01+00:00', 'two'),
        )
        self.assertEqual([e['task_id'] for e in events.read(self.root)], ['one', 'two'])
        self.assertEqual(events.read_errors(self.root), 4)

    def test_read_does_not_follow_symlink(self):
        target = self.root / 'elsewhere.jsonl'
        target.write_text(self.row('2026-10-01T00:00:00+00:00') + '\n', encoding='utf-8')
        self.path.parent.mkdir()
        self.path.symlink_to(target)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(events.read(self.root), [])
        self.assertTrue(err.getvalue().startswith('events: '))


if __name__ == '__main__':
    unittest.main()
