"""Tests of template/scripts/lib/agy_envelope.py and of scripts/gemini.sh reading it: the last agy envelope is found even
pretty-printed or followed by other lines, and every refused answer says why (night of 3/11, report-check.sh stopped twice
on "Gemini failed (exit 0)" with no cause)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'template' / 'scripts' / 'lib'
sys.path.insert(0, str(LIB))

import agy_envelope  # noqa: E402

SUCCESS = {'status': 'SUCCESS', 'response': 'id 1 : vrai\nid 2 : faux'}


class JudgeTests(unittest.TestCase):
    def test_one_line_envelope_after_warnings(self):
        text = 'Warning: something\n' + json.dumps(SUCCESS) + '\n'
        self.assertEqual(agy_envelope.judge(text, 'review'), (SUCCESS['response'], []))

    def test_pretty_printed_envelope_followed_by_a_line(self):
        text = '{"type": "event"}\n' + json.dumps(SUCCESS, indent=2) + '\nsession closed\n'
        self.assertEqual(agy_envelope.judge(text, 'review')[0], SUCCESS['response'])

    def test_event_lines_without_status_are_not_the_envelope(self):
        self.assertEqual(
            agy_envelope.judge('{"type": "event"}\n{"type": "message"}\n', 'review'),
            (None, ['agy : enveloppe JSON introuvable dans les événements']),
        )
        self.assertEqual(agy_envelope.judge('', 'review')[1], ['agy : enveloppe JSON introuvable dans les événements'])

    def test_failed_status_and_empty_response_say_why(self):
        failed = json.dumps({'status': 'ERROR', 'error': 'quota exceeded', 'response': ''})
        self.assertEqual(agy_envelope.judge(failed, 'review'), (None, ["agy : statut 'ERROR' (quota exceeded)"]))
        empty = json.dumps({'status': 'SUCCESS', 'response': '  '})
        self.assertEqual(agy_envelope.judge(empty, 'review'), (None, ['agy : réponse vide']))

    def test_refused_actions_keep_a_review_answer_and_fail_a_write(self):
        envelope = dict(SUCCESS, denied_actions=[{'display_name': 'run_command'}, {'name': 'ViewFile'}])
        answer, notes = agy_envelope.judge(json.dumps(envelope), 'review')
        self.assertEqual(answer, SUCCESS['response'])
        self.assertEqual(notes[:2], ['DENIED: run_command', 'DENIED: ViewFile'])
        self.assertIn('réponse gardée', notes[2])
        answer, notes = agy_envelope.judge(json.dumps(envelope), 'write')
        self.assertIsNone(answer)
        self.assertEqual(notes[-1], 'agy : actions refusées en mode write : run_command, ViewFile')


class RelayTests(unittest.TestCase):
    """scripts/gemini.sh with a fake agy that writes a given envelope."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(folder.cleanup)
        self.project = Path(os.path.realpath(folder.name)) / 'project'
        (self.project / 'scripts' / 'lib').mkdir(parents=True)
        shutil.copy(ROOT / 'template' / 'scripts' / 'gemini.sh', self.project / 'scripts' / 'gemini.sh')
        shutil.copy(LIB / 'agy_envelope.py', self.project / 'scripts' / 'lib' / 'agy_envelope.py')
        (self.project / 'prompt.md').write_text('consigne\n')
        self.agy = self.project.parent / 'fake-agy'

    def relay(self, events, mode='review'):
        self.agy.write_text('#!/bin/sh\ncat <<\'EOF\'\n' + events + '\nEOF\n')
        self.agy.chmod(0o755)
        env = dict(os.environ, AGY_BIN=str(self.agy), WORKSPACE_PYTHON=sys.executable)
        env.pop('WORKSPACE_AGENT_ROOT', None)
        return subprocess.run(
            ['bash', 'scripts/gemini.sh', mode, 'prompt.md', 'answer.md'],
            cwd=str(self.project),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_pretty_printed_success_is_an_answer(self):
        done = self.relay(json.dumps(SUCCESS, indent=2))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.project / 'answer.md').read_text(), SUCCESS['response'] + '\n')

    def test_a_refused_answer_names_its_reason_before_the_failure_line(self):
        done = self.relay(json.dumps({'status': 'SUCCESS', 'response': ''}))
        self.assertEqual(done.returncode, 2)
        lines = done.stderr.strip().splitlines()
        self.assertIn('agy : réponse vide', lines)
        self.assertTrue(lines[-1].startswith('Gemini failed (exit 0); events in .workspace/run/gemini/'), done.stderr)

    def test_refused_actions_reach_stderr_for_delegate(self):
        envelope = dict(SUCCESS, denied_actions=[{'display_name': 'run_command'}])
        done = self.relay(json.dumps(envelope), mode='write')
        self.assertEqual(done.returncode, 2)
        self.assertIn('DENIED: run_command\n', done.stderr)


if __name__ == '__main__':
    unittest.main()
