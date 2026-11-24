import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'template/scripts/lib'
sys.path.insert(0, str(LIB))
import report_day  # noqa: E402
import task_cost  # noqa: E402

DAY = '2026-10-22'
NEXT_DAY = '2026-10-23'
BEGIN = report_day.BLOCK_BEGIN
END = report_day.BLOCK_END
BENCH_MTIME = 1761000000


class ReportDayTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))
        self.root = self.base / 'project'
        (self.root / '.workspace/metrics').mkdir(parents=True)
        self.claude = self.base / 'claude'
        self.claude.mkdir()

    def event(self, at, task, kind, orchestrator='u', **detail):
        line = {'at': at, 'task_id': task, 'event': kind, 'orchestrator': orchestrator, 'detail': detail}
        with (self.root / '.workspace/events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(line) + '\n')

    def consume(self, at, task, role, provider, **tokens):
        line = {'at': at, 'task_id': task, 'role': role, 'provider': provider, 'tokens': tokens}
        with (self.root / '.workspace/metrics/consumption.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(line) + '\n')

    def render(self, usine='u', day=DAY):
        return report_day.render(self.root, day, usine, self.claude)

    def populate(self):
        self.event('2026-10-22T10:00:00Z', 'orchestrator', 'orchestrator_claimed', session='u')
        self.event('2026-10-22T10:01:00Z', 'alpha', 'prepared')
        self.event('2026-10-22T10:02:00Z', 'alpha', 'delegate_started', agent='codex')
        self.event('2026-10-22T10:03:00Z', 'alpha', 'attempt_started')
        self.event('2026-10-22T10:04:00Z', 'alpha', 'attempt_started')
        self.event('2026-10-22T10:05:00Z', 'alpha', 'outcome', outcome='done', cause='ok')
        self.event('2026-10-22T10:06:00Z', 'alpha', 'merged', commit='0123456789abcdef')
        self.event('2026-10-22T10:07:00Z', 'alpha', 'question_answered', question='2', by='orchestrator')
        self.event('2026-10-22T10:08:00Z', 'beta', 'prepared', orchestrator='autre')
        self.event('2026-10-23T00:30:00Z', 'gamma', 'closed', outcome='abandoned', cause='timeout')
        self.event('2026-10-23T00:31:00Z', 'orchestrator', 'orchestrator_released', session='u')
        tests = self.root / '.workspace/acceptance/alpha/tests'
        tests.mkdir(parents=True)
        (tests / 'test_a.py').write_text(
            'class T:\n    def test_one(self): pass\n    def test_two(self): pass\n    def helper(self): pass\n', encoding='utf-8'
        )
        self.consume('2026-10-22T10:03:00Z', 'alpha', 'worker', 'codex', input=100, cached_input=30, output=5)
        self.consume('2026-10-22T10:04:00Z', 'alpha', 'worker', 'codex', input=50, cached_input=10, output=7)

    def test_select_by_factory_and_by_day(self):
        self.populate()
        by_factory = {entry['task_id'] for entry in report_day.select(self.root, DAY, 'u')}
        self.assertEqual(by_factory, {'orchestrator', 'alpha', 'gamma'})
        by_day = {entry['task_id'] for entry in report_day.select(self.root, DAY)}
        self.assertEqual(by_day, {'orchestrator', 'alpha', 'beta'})

    def test_factory_crossing_midnight_and_other_factory_absent(self):
        self.populate()
        block = self.render()
        self.assertIn('| gamma |', block)
        self.assertNotIn('| beta |', block)
        self.assertTrue(block.startswith(report_day.BLOCK_BEGIN_PREFIX + ' day=%s usine=u until=' % DAY))
        self.assertIn(' -->\nDéfauts trouvés après fusion cette semaine : ', block)
        self.assertTrue(block.endswith(END + '\n'))
        self.assertIn('# Rapport — %s — usine `u`' % DAY, block)

    def test_fiche_row(self):
        self.populate()
        lines = self.render().splitlines()
        row = next(line for line in lines if line.startswith('| alpha |'))
        self.assertEqual(row, '| alpha | codex | 2 | 2 | 0123456 | 180 | 110 | 40 | - | 12 | merged | ok |')
        gamma = next(line for line in lines if line.startswith('| gamma |'))
        self.assertEqual(gamma, '| gamma | - | 0 | - | - | - | - | - | - | - | abandoned | timeout |')

    def test_no_fiche(self):
        block = self.render(usine=None)
        self.assertIn('Aucune fiche.', block)
        self.assertIn('# Rapport — %s\n' % DAY, block)
        self.assertIn('| orchestrateur | claude | non mesuré | - | - | - | - |', block)
        self.assertIn('| sous-agents et appels directs | claude | non mesuré | - | - | - | - |', block)

    def test_cost_section_window_and_total(self):
        self.populate()
        self.consume('2026-10-21T10:00:00Z', 'old', 'worker', 'codex', input=1000, cached_input=0, output=1)
        lines = self.render().splitlines()
        self.assertIn('| worker | codex | 2 | 110 | 40 | - | 12 |', lines)
        self.assertIn('| orchestrateur | claude | 0 | 0 | 0 | 0 | 0 |', lines)
        self.assertIn('| sous-agents et appels directs | claude | 0 | 0 | 0 | 0 | 0 |', lines)
        self.assertIn('| Total | | 2 | 110 | 40 | 0 | 12 |', lines)

    def test_unknown_cost_is_question_mark(self):
        self.populate()
        self.consume('2026-10-22T10:05:00Z', 'alpha', 'checker', 'claude', input=3, output=1)
        lines = self.render().splitlines()
        self.assertIn('| checker | claude | 1 | 3 | ? | ? | 1 |', lines)
        self.assertIn('| Total | | 3 | 113 | ? | ? | 13 |', lines)

    def test_questions_and_bench(self):
        self.populate()
        security = self.root / 'docs/security'
        security.mkdir(parents=True)
        (security / ('attack-bench-%s-b.md' % DAY)).write_text('x', encoding='utf-8')
        (security / ('attack-bench-%s.md' % DAY)).write_text('x', encoding='utf-8')
        for path in security.iterdir():
            os.utime(str(path), (BENCH_MTIME, BENCH_MTIME))
        block = self.render()
        self.assertIn('- Questions répondues : 1\n  - alpha/2 par orchestrator\n', block)
        self.assertIn('- Interventions humaines : 0 intervention, 0 min', block)
        self.assertIn('- Sous-agents hors circuit : 0', block)
        self.assertIn('- docs/security/attack-bench-%s-b.md' % DAY, block)
        self.assertLess(block.index('attack-bench-%s-b.md' % DAY), block.index('attack-bench-%s.md' % DAY))
        self.assertIn('aucun banc d\'attaque daté du ' + NEXT_DAY, self.render(day=NEXT_DAY))

    def test_write_new_and_regenerate_keeps_rest(self):
        path = self.root / 'rapport.md'
        report_day.write(path, BEGIN + '\nA\n' + END + '\n')
        text = path.read_text(encoding='utf-8')
        self.assertTrue(text.endswith(END + '\n\n## Décisions\n\n## Constats\n'))
        edited = 'avant\r\n' + text.replace('## Décisions\n', '## Décisions\nchoix 1\n') + 'fin\n'
        path.write_bytes(edited.encode('utf-8'))
        report_day.write(path, BEGIN + '\nneuf\n' + END + '\n')
        self.assertEqual(
            path.read_bytes().decode('utf-8'), 'avant\r\n' + BEGIN + '\nneuf\n' + END + '\n\n## Décisions\nchoix 1\n\n## Constats\nfin\n'
        )

    def test_write_refuses_bad_markers_and_symlink(self):
        cases = ['rien\n', BEGIN + '\n', END + '\n' + BEGIN + '\n', BEGIN + END + BEGIN + END]
        for content in cases:
            path = self.root / 'r.md'
            path.write_text(content, encoding='utf-8')
            with self.assertRaises(ValueError):
                report_day.write(path, BEGIN + END + '\n')
            self.assertEqual(path.read_text(encoding='utf-8'), content)
        link = self.root / 'link.md'
        link.symlink_to(self.root / 'r.md')
        with self.assertRaises(ValueError):
            report_day.write(link, BEGIN + END + '\n')

    def test_invalid_day_raises_value_error(self):
        for day in ('nope', '2026-13-01', '2026-10-2'):
            with self.assertRaises(ValueError):
                report_day.select(self.root, day)
            with self.assertRaises(ValueError):
                report_day.render(self.root, day, 'u')

    def test_main_codes_and_default_path(self):
        self.populate()
        self.assertEqual(report_day.main([str(self.root), '--day', 'nope']), 64)
        self.assertEqual(report_day.main([str(self.root), '--day', DAY, '--usine', 'a/b']), 64)
        self.assertEqual(report_day.main([str(self.root)]), 64)
        code = report_day.main([str(self.root), '--day', DAY, '--usine', 'u', '--claude-dir', str(self.claude)])
        self.assertEqual(code, 0)
        self.assertTrue((self.root / 'docs/journal' / DAY / ('RAPPORT-JOUR-%s-u.md' % DAY)).is_file())
        bad = self.root / 'bad.md'
        bad.write_text('sans marqueurs', encoding='utf-8')
        self.assertEqual(report_day.main([str(self.root), '--day', DAY, '--out', str(bad)]), 1)


class RoleTotalsTests(unittest.TestCase):
    def test_totals_per_pair(self):
        rows = [
            {'role': 'w', 'provider': 'codex', 'tokens': {'input': 10, 'cached_input': 4, 'output': 2}},
            {'role': 'w', 'provider': 'claude', 'tokens': {'input': 1, 'cached_input': 2, 'cache_creation': 3, 'output': 4}},
            {'role': 'w', 'provider': 'claude', 'tokens': {'input': 1, 'cached_input': 2, 'output': 4}},
        ]
        totals = task_cost.role_totals(rows)
        self.assertEqual(totals[('w', 'codex')], {'calls': 1, 'uncached': 6, 'cache_read': 4, 'cache_creation': '-', 'output': 2})
        self.assertEqual(totals[('w', 'claude')], {'calls': 2, 'uncached': 2, 'cache_read': 4, 'cache_creation': None, 'output': 8})


if __name__ == '__main__':
    unittest.main()


class UntilWithoutMonkeypatchTests(ReportDayTests):
    """T78 (réserve du 24/10) : `until` est passé explicitement, `events.read` n'est jamais remplacé."""

    def test_events_read_is_never_replaced(self):
        import events

        original = events.read
        self.populate()
        self.render()
        self.assertIs(events.read, original)
        self.assertFalse(hasattr(report_day, '_events_until'))

    def test_select_stops_at_until(self):
        import datetime

        self.populate()
        until = datetime.datetime(2026, 10, 22, 10, 5, tzinfo=datetime.UTC)
        kept = {entry['event'] for entry in report_day.select(self.root, DAY, 'u', until)}
        self.assertIn('outcome', kept)
        self.assertNotIn('merged', kept)
        self.assertNotIn('orchestrator_released', kept)

    def test_two_interleaved_generations_keep_their_own_until(self):
        import datetime

        import defects

        self.populate()
        with (self.root / '.workspace/events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(
                json.dumps(
                    {
                        'at': '2026-10-22T10:09:00Z',
                        'task_id': 'alpha',
                        'event': defects.NOTE_EVENTS[0],
                        'orchestrator': 'u',
                        'detail': {'kind': defects.DEFECT_KIND, 'label': 'x'},
                    }
                )
                + '\n'
            )
        early = datetime.datetime(2026, 10, 22, 10, 5, 30, tzinfo=datetime.UTC)
        late = datetime.datetime(2026, 10, 23, 1, 0, tzinfo=datetime.UTC)
        # Une génération « tôt » entamée, puis une « tard », puis la première reprise : chacune garde sa borne.
        first = report_day.select(self.root, DAY, 'u', early)
        second = report_day.render(self.root, DAY, 'u', self.claude, late)
        first_again = report_day.select(self.root, DAY, 'u', early)
        self.assertEqual(first, first_again)
        self.assertNotIn('merged', {e['event'] for e in first})
        self.assertIn('| gamma |', second)
        today = datetime.date(2026, 10, 22)
        self.assertEqual(defects.weekly(self.root, today=today, until=early)['this_week'], 0)
        self.assertEqual(defects.weekly(self.root, today=today, until=late)['this_week'], 1)
        self.assertEqual(defects.weekly(self.root, today=today)['this_week'], 1)
