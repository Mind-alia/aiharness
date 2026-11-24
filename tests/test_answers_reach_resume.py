import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'template/scripts/lib'
TEST_TMP = ROOT / '.workspace/tmp-tests'
TASK = 'demo'

# Records the prompt received on stdin; prints a valid Claude JSON answer.
FAKE_CLAUDE = '''#!/usr/bin/env python3
import json, os, pathlib, sys
if '--version' in sys.argv:
    sys.exit(0)
record = pathlib.Path(os.environ['RECORD_DIR'])
count = record / 'count'
n = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(n))
(record / ('stdin-%d.txt' % n)).write_text(sys.stdin.read())
print(json.dumps({'result': 'answer'}))
'''


def load(name):
    sys.path.insert(0, str(LIB))
    try:
        spec = importlib.util.spec_from_file_location(name, str(LIB / (name + '.py')))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(LIB))


class ResumeBase(unittest.TestCase):
    def setUp(self):
        TEST_TMP.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=str(TEST_TMP))
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        self.context = self.root / '.workspace/orchestration/tasks' / TASK / 'context'

    def put(self, kind, number, text, where=None):
        directory = (where or self.context) / kind
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ('%s.md' % number)).write_text(text)


class UnitTests(ResumeBase):
    def setUp(self):
        super().setUp()
        self.questions = load('questions')

    def test_nothing_when_no_answers(self):
        self.assertEqual(self.questions.unread_answers(self.root, TASK), [])
        self.assertEqual(self.questions.resume_section(self.root, TASK), '')

    def test_sorted_triples_with_stripped_text_and_missing_question(self):
        self.put('answers', '002', ' deux \n')
        self.put('answers', '001', 'un\n')
        self.put('questions', '001', '\n  Q un ?  \n')
        self.assertEqual(self.questions.unread_answers(self.root, TASK), [('001', 'Q un ?', 'un'), ('002', '', 'deux')])
        section = self.questions.resume_section(self.root, TASK)
        self.assertTrue(section.startswith(self.questions.RESUME_TITLE + '\n'))
        self.assertIn('ne repose pas ces questions.', section)
        self.assertLess(section.index('Question 001 : Q un ?'), section.index('Question 002 : '))
        self.assertIn('Réponse : un', section)

    def test_registry_marker_marks_read_and_creates_nothing_else(self):
        self.put('answers', '001', 'a')
        self.questions.mark_answers_read(self.root, TASK, ['001'])
        marker = self.context / 'read/001.md'
        self.assertTrue(marker.is_file())
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(marker.parent.stat().st_mode), 0o700)
        self.assertEqual(self.questions.unread_answers(self.root, TASK), [])

    def test_empty_list_creates_no_read_directory(self):
        self.put('answers', '001', 'a')
        self.questions.mark_answers_read(self.root, TASK, [])
        self.questions.unread_answers(self.root, TASK)
        self.questions.resume_section(self.root, TASK)
        self.assertFalse((self.context / 'read').exists())

    def test_worktree_marker_counts_only_when_worktree_given(self):
        self.put('answers', '001', 'a')
        worktree = self.base / 'wt'
        self.put('read', '001', 'x', where=worktree / '.workspace/exchange')
        self.assertEqual(len(self.questions.unread_answers(self.root, TASK)), 1)
        self.assertEqual(self.questions.unread_answers(self.root, TASK, worktree=worktree), [])

    def test_question_falls_back_to_worktree_copy(self):
        self.put('answers', '001', 'a')
        worktree = self.base / 'wt'
        self.put('questions', '001', 'depuis le worktree', where=worktree / '.workspace/exchange')
        self.assertEqual(self.questions.unread_answers(self.root, TASK, worktree=worktree), [('001', 'depuis le worktree', 'a')])

    def test_symlinked_answer_is_ignored(self):
        self.put('answers', '002', 'b')
        target = self.base / 'secret.md'
        target.write_text('secret')
        (self.context / 'answers/001.md').symlink_to(target)
        self.assertEqual([row[0] for row in self.questions.unread_answers(self.root, TASK)], ['002'])

    def test_mark_refuses_bad_number_and_symlinked_read_dir(self):
        for bad in ('1234', 'ab', ''):
            with self.assertRaises(ValueError):
                self.questions.mark_answers_read(self.root, TASK, [bad])
        self.context.mkdir(parents=True)
        (self.context / 'read').symlink_to(self.base)
        with self.assertRaises(ValueError):
            self.questions.mark_answers_read(self.root, TASK, ['001'])
        self.assertEqual(list(self.base.glob('*.md')), [])


class SuspendedLineTests(ResumeBase):
    def test_suspended_question_shows_the_command(self):
        self.put('questions', '001', 'Quelle version ?')
        run = self.root / '.workspace/run/delegate' / TASK
        run.mkdir(parents=True)
        (run / 'status.json').write_text(json.dumps({'outcome': 'waiting_answer'}))
        result = subprocess.run([sys.executable, str(LIB / 'questions.py'), str(self.root)], capture_output=True, text=True)
        self.assertIn('(tâche suspendue)', result.stdout)
        self.assertIn(
            'répondre : bash scripts/questions.sh answer demo/001 "<texte>" --by <auteur>, puis relancer delegate.sh', result.stdout
        )


class AskMarkerTests(ResumeBase):
    def test_answer_received_in_time_writes_marker_next_to_answers(self):
        context = load('context')
        answers = self.base / 'wt/.workspace/exchange/answers'
        answers.mkdir(parents=True)
        (answers / '001.md').write_text('ok')
        self.assertEqual(context.wait_for_answer(answers, 1, 1), 'ok')
        context.mark_read(answers.parent / context.READ_DIR, '001')
        self.assertTrue((answers.parent / 'read/001.md').is_file())


@unittest.skipIf(os.environ.get('WORKSPACE_TESTS') == 'fast', 'Git réel et sous-processus ; suite complète seulement')
class DelegateEndToEndTests(ResumeBase):
    def setUp(self):
        super().setUp()
        (self.root / 'scripts/lib').mkdir(parents=True)
        (self.root / 'docs').mkdir()
        (self.root / 'bin').mkdir()
        self.record = self.base / 'record'
        self.record.mkdir()
        for script in (ROOT / 'template/scripts').glob('*.sh'):
            shutil.copy2(str(script), str(self.root / 'scripts' / script.name))
        for lib_file in LIB.glob('*.py'):
            shutil.copy2(str(lib_file), str(self.root / 'scripts/lib' / lib_file.name))
        shutil.copy2(str(ROOT / 'template/.gitignore'), str(self.root / '.gitignore'))
        (self.root / 'project.profile').write_text('docs\n')
        (self.root / 'tracked.txt').write_text('before\n')
        self.git('init', '-q')
        self.git('add', 'tracked.txt', 'project.profile')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
        verify = self.root / 'scripts/verify-task.sh'
        verify.write_text('#!/bin/sh\nexit 0\n')
        verify.chmod(0o755)
        claude = self.root / 'bin/claude'
        claude.write_text(FAKE_CLAUDE)
        claude.chmod(0o755)
        self.task = self.root / 'task.md'
        self.task.write_text(
            '---\nid: %s\nagent: claude-haiku\nmode: write\nallowed: [tracked.txt]\nverify: echo ok\nattempts: 1\n---\nTASK BODY\n' % TASK
        )
        self.env = dict(
            os.environ, PATH=str(self.root / 'bin') + os.pathsep + os.environ['PATH'], DELEGATE_RETRY_DELAY='0', RECORD_DIR=str(self.record)
        )

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True)

    def delegate(self):
        return subprocess.run(
            ['bash', str(self.root / 'scripts/delegate.sh'), str(self.task)],
            cwd=str(self.root / 'docs'),
            env=self.env,
            text=True,
            capture_output=True,
        )

    def prompt(self):
        return (self.root / '.workspace/run/delegate' / TASK / 'prompt.md').read_text()

    def test_answer_reaches_the_resumed_delegation_once(self):
        self.put('questions', '001', 'Quelle version ?')
        self.put('answers', '001', 'Version 3.9')
        first = self.delegate()
        self.assertEqual(first.returncode, 0, first.stderr)
        prompt = self.prompt()
        self.assertIn('## Réponses à tes questions', prompt)
        self.assertIn('Question 001 : Quelle version ?', prompt)
        self.assertIn('Réponse : Version 3.9', prompt)
        self.assertLess(prompt.index('TASK BODY'), prompt.index('## Réponses à tes questions'))
        self.assertTrue((self.context / 'read/001.md').is_file())
        second = self.delegate()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn('## Réponses à tes questions', self.prompt())

    def test_no_answer_leaves_the_prompt_unchanged(self):
        self.assertEqual(self.delegate().returncode, 0)
        self.assertNotIn('## Réponses à tes questions', self.prompt())
        self.assertFalse((self.context / 'read').exists())


if __name__ == '__main__':
    unittest.main()
