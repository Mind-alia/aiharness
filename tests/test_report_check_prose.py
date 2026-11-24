import argparse
import datetime
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template' / 'scripts' / 'lib'))
import report_check as rc
import report_day

UNTIL = datetime.datetime(2026, 10, 24, 12, 0, 0, tzinfo=datetime.UTC)

BEGIN, END = rc.GENERATED_BEGIN, rc.GENERATED_END
STOP_MALFORMED = 'blocs générés mal formés'
STOP_NO_PROSE = 'aucune prose à vérifier'
MALFORMED_EXIT = 64
EMPTY_EXIT = 2
GENERATED = BEGIN + '\n| a | 1 |\n' + END + '\n'


class ProseTests(unittest.TestCase):
    def test_text_without_markers_is_unchanged(self):
        self.assertEqual(rc.prose('# T\n\nUne phrase.\n'), '# T\n\nUne phrase.\n')

    def test_generated_block_and_following_newline_removed(self):
        self.assertEqual(rc.prose('avant\n' + GENERATED + 'après\n'), 'avant\naprès\n')

    def test_only_one_newline_removed(self):
        self.assertEqual(rc.prose(BEGIN + 'x' + END + '\n\nreste'), '\nreste')

    def test_two_blocks_leave_only_prose(self):
        text = GENERATED + 'Un.\n' + GENERATED + 'Deux.\n'
        self.assertEqual(rc.prose(text), 'Un.\nDeux.\n')

    def test_verdict_block_removed_first(self):
        text = '# T\n' + rc.BEGIN + '\n> v\n' + rc.END + '\nCorps.\n'
        self.assertEqual(rc.prose(text), '# T\nCorps.\n')

    def test_malformed_markers(self):
        for text in (BEGIN + 'x', 'x' + END, BEGIN + BEGIN + END, END + BEGIN, BEGIN + END + END):
            with self.subTest(text=text):
                with self.assertRaises(rc.Stop) as caught:
                    rc.prose(text)
                self.assertEqual(caught.exception.code, MALFORMED_EXIT)
                self.assertIn(STOP_MALFORMED, str(caught.exception))


class RunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.report = self.root / 'rapport.md'

    def run_report(self, text):
        self.report.write_text(text, encoding='utf-8')
        args = argparse.Namespace(root=self.root, report=self.report, verifier='codex', repo=[], limit=20)
        extract = mock.Mock(side_effect=rc.Stop(EMPTY_EXIT, 'stop extraction'))
        with (
            mock.patch.object(rc, 'read_documents', return_value=('r', 'i')),
            mock.patch.object(rc, 'read_decoys', return_value=['a', 'b', 'c']),
            mock.patch.object(rc, 'extract', extract),
            mock.patch.object(rc, 'verify') as verify,
        ):
            try:
                rc.run(args)
            except rc.Stop as stop:
                return stop, extract, verify
        self.fail('Stop attendu')

    def regenerable(self):
        return report_day.render(self.root, '2026-10-22', until=UNTIL)

    def test_extraction_receives_prose_only(self):
        text = '# T\n' + rc.BEGIN + '\n> v\n' + rc.END + '\n' + self.regenerable() + 'Prose.\n'
        _, extract, _ = self.run_report(text)
        self.assertEqual(extract.call_args[0][3], '# T\nProse.\n')
        self.assertEqual(self.report.read_text(encoding='utf-8'), text)

    def test_only_generated_stops_without_calls(self):
        stop, extract, verify = self.run_report(self.regenerable() + '\n  \n')
        self.assertEqual(stop.code, EMPTY_EXIT)
        self.assertIn(STOP_NO_PROSE, str(stop))
        extract.assert_not_called()
        verify.assert_not_called()

    def test_orphan_end_stops_and_report_unchanged(self):
        text = 'Prose.\n' + END + '\n'
        stop, extract, _ = self.run_report(text)
        self.assertEqual(stop.code, MALFORMED_EXIT)
        self.assertIn(STOP_MALFORMED, str(stop))
        extract.assert_not_called()
        self.assertEqual(self.report.read_text(encoding='utf-8'), text)

    def test_inserted_block_keeps_generated_blocks(self):
        text = '# T\n' + GENERATED + 'Prose.\n'
        block = rc.BEGIN + '\n> v\n' + rc.END + '\n'
        self.assertIn(GENERATED, rc.insert_block(text, block))
        self.assertIn(GENERATED, rc.insert_block(rc.insert_block(text, block), block))


if __name__ == '__main__':
    unittest.main()
