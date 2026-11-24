import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template' / 'scripts' / 'lib'))
import factory_cost  # noqa: E402
import report_check as rc  # noqa: E402
import report_day  # noqa: E402

DAY = '2026-10-22'
UNTIL = datetime.datetime(2026, 10, 22, 12, 0, 0, tzinfo=datetime.UTC)
UNTIL_TEXT = '2026-10-22T12:00:00Z'
TOUCHED_LINE = 4
INVALID_HEADER_STOP = 'sans en-tête valide'
TOUCHED_STOP = 'bloc généré retouché'


class HeaderTests(unittest.TestCase):
    def test_round_trip(self):
        for usine, claude_dir in ((None, None), ('u', None), (None, '/a b/é'), ('u-1.x', '/tmp/claude')):
            with self.subTest(usine=usine, claude_dir=claude_dir):
                line = report_day.block_header(DAY, usine, UNTIL, claude_dir)
                self.assertEqual(report_day.parse_header(line), {'day': DAY, 'usine': usine, 'until': UNTIL, 'claude_dir': claude_dir})

    def test_exact_form(self):
        self.assertEqual(
            report_day.block_header(DAY, 'u', UNTIL, '/a b'),
            '<!-- généré: début day=%s usine=u until=%s claude-dir=/a%%20b -->' % (DAY, UNTIL_TEXT),
        )

    def test_invalid_headers(self):
        good = 'day=%s until=%s' % (DAY, UNTIL_TEXT)
        prefix = report_day.BLOCK_BEGIN_PREFIX
        bad = [
            report_day.BLOCK_BEGIN,
            prefix + ' ' + good + ' extra=1 -->',
            prefix + ' ' + good + ' day=%s -->' % DAY,
            prefix + ' until=%s day=%s -->' % (UNTIL_TEXT, DAY),
            prefix + ' day=%s until=hier -->' % DAY,
            prefix + ' day=2026-13-01 until=%s -->' % UNTIL_TEXT,
            prefix + ' day=%s usine=nom invalide! until=%s -->' % (DAY, UNTIL_TEXT),
            prefix + ' day=%s usine=a/b until=%s -->' % (DAY, UNTIL_TEXT),
            prefix + ' day=%s -->' % DAY,
            prefix + ' ' + good + '  -->',
            prefix + ' ' + good,
        ]
        for line in bad:
            with self.subTest(line=line):
                with self.assertRaises(ValueError):
                    report_day.parse_header(line)


class CapTests(unittest.TestCase):
    def test_closed_window_is_capped_when_now_is_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.workspace').mkdir()
            rows = [
                {'at': '2026-10-22T10:00:00Z', 'task_id': 'orchestrator', 'event': 'orchestrator_claimed', 'detail': {'session': 'u'}},
                {'at': '2026-10-22T14:00:00Z', 'task_id': 'orchestrator', 'event': 'orchestrator_released', 'detail': {'session': 'u'}},
            ]
            (root / '.workspace/events.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
            capped = factory_cost.collect(root, 'u', root / 'none', now=UNTIL)
            self.assertEqual(len(capped['windows']), 1)


class AuthenticateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        (self.root / '.workspace/metrics').mkdir(parents=True)

    def event(self, at, task, kind, **detail):
        line = {'at': at, 'task_id': task, 'event': kind, 'orchestrator': 'u', 'detail': detail}
        with (self.root / '.workspace/events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(line) + '\n')

    def block(self, usine=None):
        return report_day.render(self.root, DAY, usine, until=UNTIL)

    def refused(self, text):
        with self.assertRaises(rc.Stop) as caught:
            rc.authenticate(self.root, text)
        self.assertEqual(caught.exception.code, 1)
        return str(caught.exception)

    def test_no_block_passes(self):
        rc.authenticate(self.root, '# T\n\nDe la prose.\n')

    def test_intact_block_passes(self):
        self.event('2026-10-22T10:00:00Z', 'alpha', 'prepared')
        rc.authenticate(self.root, '# T\n' + self.block() + 'Prose.\n')

    def test_one_changed_character_is_refused_with_line(self):
        self.event('2026-10-22T10:00:00Z', 'alpha', 'prepared')
        block = self.block()
        touched = block.replace('# Rapport', '# Rapports', 1)
        message = self.refused('# T\n' + touched + 'Prose.\n')
        self.assertIn('%s (bloc 1, ligne %d)' % (TOUCHED_STOP, TOUCHED_LINE), message)
        self.assertIn('régénérer par report.sh --day', message)

    def test_added_line_at_end_is_refused(self):
        block = self.block()
        added = block[:-1].replace(report_day.BLOCK_END, 'ajout\n' + report_day.BLOCK_END, 1) + '\n'
        self.assertIn(TOUCHED_STOP, self.refused(added))

    def test_old_header_is_refused(self):
        text = self.block().replace(report_day.block_header(DAY, None, UNTIL), report_day.BLOCK_BEGIN)
        message = self.refused(text)
        self.assertIn(INVALID_HEADER_STOP + ' (bloc 1)', message)

    def test_event_after_until_does_not_change_the_block(self):
        self.event('2026-10-22T10:00:00Z', 'alpha', 'prepared')
        text = self.block()
        self.event('2026-10-22T13:00:00Z', 'beta', 'prepared')
        self.event('2026-10-22T13:01:00Z', 'alpha', 'merged', commit='abcdef0123')
        rc.authenticate(self.root, text)
        self.assertNotEqual(report_day.render(self.root, DAY, until=UNTIL + datetime.timedelta(hours=2)), text)

    def test_second_block_touched_is_named(self):
        block = self.block()
        touched = block.replace('Aucune fiche.', 'Aucune fiche!')
        message = self.refused(block + 'Prose.\n' + touched)
        self.assertIn('(bloc 2, ligne ', message)

    def test_invalid_header_values(self):
        for header in ('usine=nom invalide!', 'until=hier'):
            valid = report_day.block_header(DAY, None, UNTIL)
            line = (
                valid.replace('day=%s' % DAY, 'day=%s %s' % (DAY, header))
                if header.startswith('usine')
                else valid.replace('until=' + UNTIL_TEXT, header)
            )
            with self.subTest(header=header):
                self.assertIn(INVALID_HEADER_STOP, self.refused(line + '\nx\n' + report_day.BLOCK_END + '\n'))

    def test_run_refuses_before_any_call(self):
        report = self.root / 'rapport.md'
        text = '# T\n' + self.block().replace('# Rapport', '# Rapports', 1) + 'Prose.\n'
        report.write_text(text, encoding='utf-8')
        args = type('Args', (), {'root': self.root, 'report': report, 'verifier': 'codex', 'repo': [], 'limit': 20})()
        with self.assertRaises(rc.Stop) as caught:
            rc.run(args)
        self.assertIn(TOUCHED_STOP, str(caught.exception))
        self.assertEqual(report.read_text(encoding='utf-8'), text)

    def test_write_replaces_block_with_new_header(self):
        path = self.root / 'r.md'
        report_day.write(path, self.block())
        report_day.write(path, self.block())
        text = path.read_text(encoding='utf-8')
        self.assertEqual(text.count(report_day.BLOCK_BEGIN_PREFIX), 1)
        rc.authenticate(self.root, text)


if __name__ == '__main__':
    unittest.main()
