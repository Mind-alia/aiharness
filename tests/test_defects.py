"""Tests unitaires de template/scripts/lib/defects.py : journal et LOG fabriqués dans un dossier temporaire."""

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / 'template/scripts/lib'
sys.path.insert(0, str(LIB))

import defects  # noqa: E402

DEFECT = {'kind': 'defect', 'label': 'régression'}
MARKER_LINE = '- Relecture : ' + defects.MARKER + ' régression sur le quota'


class DefectsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / '.workspace/events.jsonl'

    def put(self, *rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8') as stream:
            for at, task, event, detail in rows:
                stream.write(json.dumps(dict(at=at, task_id=task, event=event, detail=detail)) + '\n')

    def log(self, text):
        (self.root / 'docs').mkdir(exist_ok=True)
        (self.root / defects.LOG_PATH).write_text(text, encoding='utf-8')

    def test_note_after_merge_is_counted_on_its_utc_day(self):
        self.put(('2026-10-19T08:00:00+00:00', 'a', 'merged', None), ('2026-10-19T09:30:00+00:00', 'a', 'note', DEFECT))
        self.assertEqual(defects.from_events(self.root), [datetime.date(2026, 10, 19)])

    def test_outcome_and_question_answered_can_carry_a_defect(self):
        self.put(
            ('2026-10-19T08:00:00+00:00', 'a', 'merged', None),
            ('2026-10-19T09:00:00+00:00', 'a', 'outcome', DEFECT),
            ('2026-10-19T10:00:00+00:00', 'a', 'question_answered', DEFECT),
        )
        self.assertEqual(len(defects.from_events(self.root)), 2)

    def test_defect_without_merge_is_ignored(self):
        self.put(('2026-10-19T09:00:00+00:00', 'jamais', 'note', DEFECT))
        self.assertEqual(defects.from_events(self.root), [])

    def test_defect_dated_before_merge_is_ignored(self):
        self.put(('2026-10-18T09:00:00+00:00', 'a', 'note', DEFECT), ('2026-10-19T08:00:00+00:00', 'a', 'merged', None))
        self.assertEqual(defects.from_events(self.root), [])

    def test_defect_at_the_merge_instant_is_ignored(self):
        self.put(('2026-10-19T08:00:00+00:00', 'a', 'merged', None), ('2026-10-19T08:00:00+00:00', 'a', 'note', DEFECT))
        self.assertEqual(defects.from_events(self.root), [])

    def test_other_detail_or_other_event_is_not_a_defect(self):
        self.put(
            ('2026-10-19T08:00:00+00:00', 'a', 'merged', None),
            ('2026-10-19T09:00:00+00:00', 'a', 'note', {'kind': 'other'}),
            ('2026-10-19T09:00:00+00:00', 'a', 'note', None),
            ('2026-10-19T09:00:00+00:00', 'a', 'closed', DEFECT),
        )
        self.assertEqual(defects.from_events(self.root), [])

    def test_since_excludes_earlier_days(self):
        self.put(
            ('2026-10-10T08:00:00+00:00', 'a', 'merged', None),
            ('2026-10-11T09:00:00+00:00', 'a', 'note', DEFECT),
            ('2026-10-19T09:00:00+00:00', 'a', 'note', DEFECT),
        )
        self.assertEqual(defects.from_events(self.root, since=datetime.date(2026, 10, 12)), [datetime.date(2026, 10, 19)])

    def test_two_defects_the_same_day_count_twice(self):
        self.put(
            ('2026-10-19T08:00:00+00:00', 'a', 'merged', None),
            ('2026-10-19T09:00:00+00:00', 'a', 'note', DEFECT),
            ('2026-10-19T11:00:00+00:00', 'a', 'note', DEFECT),
        )
        self.assertEqual(len(defects.from_events(self.root)), 2)

    def test_missing_or_damaged_journal_gives_empty_list(self):
        self.assertEqual(defects.from_events(self.root), [])
        self.put(('2026-10-19T08:00:00+00:00', 'a', 'merged', None))
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write('{broken\n')
        self.assertEqual(defects.from_events(self.root), [])

    def test_log_line_takes_date_of_nearest_dated_heading_above(self):
        self.log(
            '# Journal\n\n## [2026-10-19] feat | quota\n\n- a\n' + MARKER_LINE + '\n\n## 2026-10-20 fix | suite\n' + MARKER_LINE + '\n'
        )
        self.assertEqual(defects.from_log(self.root), [datetime.date(2026, 10, 19), datetime.date(2026, 10, 20)])

    def test_log_heading_without_date_is_skipped_for_the_date(self):
        self.log('## [2026-10-14] feat | début\n\n## Sans date\n' + MARKER_LINE + '\n')
        self.assertEqual(defects.from_log(self.root), [datetime.date(2026, 10, 14)])

    def test_log_line_without_dated_heading_is_ignored(self):
        self.log('texte libre\n' + MARKER_LINE + '\n## Titre sans date\n' + MARKER_LINE + '\n')
        self.assertEqual(defects.from_log(self.root), [])

    def test_log_impossible_date_is_not_a_heading(self):
        self.log('## [2026-13-45] feat | erreur\n' + MARKER_LINE + '\n')
        self.assertEqual(defects.from_log(self.root), [])

    def test_log_since_and_missing_file(self):
        self.assertEqual(defects.from_log(self.root), [])
        self.log('## [2026-10-10] a\n' + MARKER_LINE + '\n## [2026-10-19] b\n' + MARKER_LINE + '\n')
        self.assertEqual(defects.from_log(self.root, since=datetime.date(2026, 10, 15)), [datetime.date(2026, 10, 19)])

    def test_weekly_groups_by_iso_week_in_ascending_order(self):
        # 2026-10-19 is a Monday of ISO week 43; 2026-10-18 is a Sunday of week 42.
        self.put(
            ('2026-10-19T08:00:00+00:00', 'a', 'merged', None),
            ('2026-10-19T09:00:00+00:00', 'a', 'note', DEFECT),
            ('2026-10-18T09:00:00+00:00', 'a', 'note', DEFECT),
        )
        self.log('## [2026-10-19] feat | quota\n' + MARKER_LINE + '\n')
        result = defects.weekly(self.root, today=datetime.date(2026, 10, 21))
        self.assertEqual(result, {'weeks': [{'week': '2026-W43', 'events': 1, 'log': 1, 'total': 2}], 'this_week': 2})

    def test_weekly_this_week_is_zero_when_no_defect_in_week(self):
        self.log('## [2026-10-10] feat | ancien\n' + MARKER_LINE + '\n')
        result = defects.weekly(self.root, today=datetime.date(2026, 10, 21))
        self.assertEqual(result, {'weeks': [{'week': '2026-W41', 'events': 0, 'log': 1, 'total': 1}], 'this_week': 0})

    def test_weekly_empty_when_no_journal_and_no_log(self):
        self.assertEqual(defects.weekly(self.root, today=datetime.date(2026, 10, 19)), {'weeks': [], 'this_week': 0})

    def test_weekly_defaults_today_to_utc_day(self):
        result = defects.weekly(self.root)
        self.assertEqual(result, {'weeks': [], 'this_week': 0})


if __name__ == '__main__':
    unittest.main()
