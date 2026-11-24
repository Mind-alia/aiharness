import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'template/scripts/lib'))
import selfcheck

SYNTAX_LOG = '''E
======================================================================
ERROR: test_x (unittest.loader._FailedTest)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/work/tests/test_x.py", line 3
    def (:
        ^
SyntaxError: invalid syntax

----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (errors=1)
'''
FAIL_LOG = '''F
FAIL: test_a (tests.test_x.T)
Traceback (most recent call last):
  File "/work/tests/test_x.py", line 5, in test_a
AssertionError: 1 != 2
Ran 1 test in 0.001s
FAILED (failures=1)
'''


class FailureSummaryTests(unittest.TestCase):
    def test_syntax_error(self):
        self.assertEqual(
            selfcheck.failure_summary(SYNTAX_LOG),
            [
                'ERROR: test_x (unittest.loader._FailedTest)',
                'File "/work/tests/test_x.py", line 3',
                'SyntaxError: invalid syntax',
                'Ran 1 test in 0.000s',
                'FAILED (errors=1)',
            ],
        )

    def test_assertion_failure(self):
        lines = selfcheck.failure_summary(FAIL_LOG)
        self.assertEqual(lines[0], 'FAIL: test_a (tests.test_x.T)')
        self.assertIn('AssertionError: 1 != 2', lines)
        self.assertEqual(lines[-1], 'FAILED (failures=1)')

    def test_ok_and_duplicates_and_empty(self):
        self.assertEqual(selfcheck.failure_summary('OK\nOK\n'), ['OK'])
        self.assertEqual(selfcheck.failure_summary(''), [])

    def test_cap_and_width(self):
        text = ''.join('FAIL: t%d\n' % i for i in range(30)) + 'FAILED (failures=30)\n'
        lines = selfcheck.failure_summary(text)
        self.assertEqual(len(lines), selfcheck.SUMMARY_MAX_LINES)
        self.assertEqual(lines[-1], 'FAILED (failures=30)')
        self.assertEqual(lines[:2], ['FAIL: t0', 'FAIL: t1'])
        long = selfcheck.failure_summary('ValueError: ' + 'x' * 500)
        self.assertEqual(len(long[0]), selfcheck.SUMMARY_LINE_WIDTH)


class ExecLogsTests(unittest.TestCase):
    def test_sorted_numerically_and_symlinks_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / '.workspace/orchestration/tasks/demo/sandbox'
            folder.mkdir(parents=True)
            for name in ('exec-010.log', 'exec-009.log', 'other.log'):
                (folder / name).write_text('x')
            os.symlink(folder / 'exec-009.log', folder / 'exec-011.log')
            self.assertEqual([p.name for p in selfcheck.exec_logs(tmp, 'demo')], ['exec-009.log', 'exec-010.log'])
            self.assertEqual(selfcheck.exec_logs(tmp, 'absent'), [])


class PrepareOutputTests(unittest.TestCase):
    def prepare(self, log_text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name))  # /var est un lien symbolique sous macOS
        task_dir = root / '.workspace/orchestration/tasks/demo'
        task_dir.mkdir(parents=True)
        (task_dir / 'state.json').write_text(json.dumps({'source_task': 'demo.md'}))
        (root / 'demo.md').write_text('---\n---\n')
        folder = task_dir / 'sandbox'

        def fake_run(*args):
            if log_text is not None:
                folder.mkdir(exist_ok=True)
                (folder / 'exec-001.log').write_text(log_text, encoding='utf-8')
            return 1

        with (
            mock.patch.object(selfcheck, 'read_task', return_value={'id': 'demo'}),
            mock.patch.object(selfcheck.breaker, 'has_acceptance', return_value=True),
            mock.patch.object(selfcheck.workdir, 'state_worktree', return_value=root),
            mock.patch.object(selfcheck, 'run', side_effect=fake_run),
            mock.patch.object(selfcheck.subprocess, 'run') as sub,
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            sub.return_value.returncode, sub.return_value.stdout, sub.return_value.stderr = 0, '', ''
            result = selfcheck.prepare_selfcheck(root, 'demo')
        self.assertEqual(result, 0)
        return out.getvalue()

    def test_syntax_error_log_is_summarised(self):
        out = self.prepare(SYNTAX_LOG)
        sys.stderr.write(out)
        self.assertIn('(code 1), délégation possible\n' + selfcheck.SUMMARY_TITLE + '\n', out)
        self.assertIn('  File "/work/tests/test_x.py", line 3\n  SyntaxError: invalid syntax\n', out)

    def test_no_log_and_empty_log(self):
        self.assertIn(selfcheck.NO_LOG, self.prepare(None))
        self.assertIn(selfcheck.NO_LOG, self.prepare(''))

    def test_fallback_last_five_lines(self):
        out = self.prepare('\n'.join('line%d' % i for i in range(8)) + '\n')
        self.assertIn(selfcheck.SUMMARY_TITLE + '\n  line3\n  line4\n  line5\n  line6\n  line7\n', out)


if __name__ == '__main__':
    unittest.main()
