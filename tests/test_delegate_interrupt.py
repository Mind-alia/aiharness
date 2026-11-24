import contextlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import delegate_interrupt  # noqa: E402
import events  # noqa: E402
import workdir  # noqa: E402

FAST = os.environ.get('WORKSPACE_TESTS') == 'fast'
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
USAGE_PREFIX = 'usage:'
SECTION = '## Reprise après interruption'
SENTENCE = 'Le travail précédent a été interrompu : relis ces fichiers avant de continuer.'


def git(clone, *args):
    done = subprocess.run(
        ['git', '-C', str(clone), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return done.stdout


class CloneCase(unittest.TestCase):
    """A project with one prepared task whose clone is a small real repository."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.temp.cleanup)
        tmp = Path(os.path.realpath(self.temp.name))
        patcher = mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': str(tmp / 'work')})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = tmp / 'project'
        self.root.mkdir()
        self.clone = workdir.task_worktree(self.root, 't')
        self.clone.mkdir(parents=True)
        git(self.clone, 'init', '-q')
        (self.clone / 'tracked.txt').write_text('before\n')
        (self.clone / '.gitignore').write_text('ignored.txt\n')
        git(self.clone, 'add', '-A')
        git(self.clone, 'commit', '-qm', 'base')
        self.base = git(self.clone, 'rev-parse', 'HEAD').strip()
        self.write_state({'task_id': 't', 'status': 'prepared', 'worktree': str(self.clone), 'base_commit': self.base})

    def write_state(self, state):
        path = self.root / '.workspace/orchestration/tasks/t/state.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))


class MeasureTests(CloneCase):
    def test_clean_clone(self):
        found = delegate_interrupt.measure(self.root, 't')
        self.assertEqual((found.dirty, found.count, found.lines, found.committed), (False, 0, [], []))

    def test_modified_and_untracked_files_count_once_each(self):
        (self.clone / 'tracked.txt').write_text('after\n')
        (self.clone / 'new.txt').write_text('new\n')
        found = delegate_interrupt.measure(self.root, 't')
        self.assertEqual((found.dirty, found.count), (True, 2))
        self.assertIn(' M tracked.txt', found.lines)
        self.assertIn('?? new.txt', found.lines)

    def test_untracked_files_are_listed_one_by_one(self):
        (self.clone / 'sub').mkdir()
        (self.clone / 'sub/a.txt').write_text('a')
        (self.clone / 'sub/b.txt').write_text('b')
        self.assertEqual(delegate_interrupt.measure(self.root, 't').count, 2)

    def test_ignored_file_does_not_dirty(self):
        (self.clone / 'ignored.txt').write_text('x')
        found = delegate_interrupt.measure(self.root, 't')
        self.assertEqual((found.dirty, found.count), (False, 0))

    def test_commits_make_it_dirty_and_each_committed_file_adds_one(self):
        (self.clone / 'a.txt').write_text('a')
        (self.clone / 'b.txt').write_text('b')
        git(self.clone, 'add', '-A')
        git(self.clone, 'commit', '-qm', 'model work')
        found = delegate_interrupt.measure(self.root, 't')
        self.assertEqual((found.lines, found.committed, found.dirty, found.count), ([], ['a.txt', 'b.txt'], True, 2))
        (self.clone / 'c.txt').write_text('c')
        self.assertEqual(delegate_interrupt.measure(self.root, 't').count, 3)

    def test_an_empty_commit_moves_head_without_files(self):
        git(self.clone, 'commit', '-q', '--allow-empty', '-m', 'nothing')
        found = delegate_interrupt.measure(self.root, 't')
        self.assertEqual((found.dirty, found.count), (True, 0))

    def test_no_state_means_nothing_measured(self):
        (self.root / '.workspace/orchestration/tasks/t/state.json').unlink()
        (self.clone / 'new.txt').write_text('new\n')
        found = delegate_interrupt.measure(self.root, 't')
        self.assertEqual((found.dirty, found.count), (False, 0))

    def test_invalid_state_or_foreign_clone_means_nothing_measured(self):
        (self.clone / 'new.txt').write_text('new\n')
        for state in ({'task_id': 't'}, {'task_id': 't', 'worktree': str(self.root)}, {'task_id': 'other', 'worktree': str(self.clone)}):
            with self.subTest(state=state):
                self.write_state(state)
                self.assertEqual(delegate_interrupt.measure(self.root, 't').count, 0)

    def test_interruption_keys(self):
        (self.clone / 'new.txt').write_text('new\n')
        found = delegate_interrupt.interruption(self.root, 't', 'SIGHUP')
        self.assertEqual(set(found), {'interrupted_at', 'signal', 'worktree_dirty', 'dirty_files'})
        self.assertRegex(found['interrupted_at'], '^' + STAMP.pattern + '$')
        self.assertEqual((found['signal'], found['worktree_dirty'], found['dirty_files']), ('SIGHUP', True, 1))
        self.assertIs(type(found['worktree_dirty']), bool)
        self.assertIs(type(found['dirty_files']), int)

    def test_unknown_signal_is_named_sigint(self):
        self.assertEqual(delegate_interrupt.interruption(self.root, 't', None)['signal'], 'SIGINT')


class PureTests(unittest.TestCase):
    def test_utc_stamp_has_no_fraction(self):
        self.assertRegex(delegate_interrupt.utc_stamp(), '^' + STAMP.pattern + '$')

    def test_event_detail_only_for_interrupted(self):
        status = {'outcome': 'interrupted', 'signal': 'SIGTERM', 'worktree_dirty': True, 'dirty_files': 2, 'attempts': 1}
        self.assertEqual(delegate_interrupt.event_detail(status), {'signal': 'SIGTERM', 'worktree_dirty': True, 'dirty_files': 2})
        self.assertEqual(delegate_interrupt.event_detail(dict(status, outcome='done')), {})
        self.assertEqual(delegate_interrupt.event_detail({'outcome': 'interrupted'}), {})

    def test_left_dirty(self):
        self.assertTrue(delegate_interrupt.left_dirty({'outcome': 'interrupted', 'worktree_dirty': True}))
        self.assertTrue(delegate_interrupt.left_dirty({'outcome': 'worktree_dirty', 'worktree_dirty': True}))
        self.assertFalse(delegate_interrupt.left_dirty({'outcome': 'interrupted', 'worktree_dirty': False}))
        self.assertFalse(delegate_interrupt.left_dirty({'outcome': 'interrupted'}))
        self.assertFalse(delegate_interrupt.left_dirty({'outcome': 'failed', 'worktree_dirty': True}))
        self.assertFalse(delegate_interrupt.left_dirty({'outcome': 'interrupted', 'worktree_dirty': 1}))

    def test_resume_section_lists_every_line_then_the_sentence(self):
        text = delegate_interrupt.resume_section([' M a.txt', 'b.txt'])
        self.assertEqual(text.splitlines()[0], SECTION)
        self.assertIn('- ' + ' M a.txt', text.splitlines())
        self.assertIn('- b.txt', text.splitlines())
        self.assertEqual(text.splitlines()[-1], SENTENCE)

    def test_with_resume_goes_before_the_report_section(self):
        report = delegate_interrupt.REPORT_SECTION
        prompt = 'BODY\n\n' + report
        result = delegate_interrupt.with_resume(prompt, ['x'])
        self.assertLess(result.index(SECTION), result.index(report))
        self.assertTrue(result.startswith('BODY'))
        empty = delegate_interrupt.with_resume(prompt, [])
        self.assertIn(SECTION, empty)
        self.assertIn(delegate_interrupt.NO_FILE_LINE, empty)
        self.assertIn(SENTENCE, empty)
        self.assertTrue(delegate_interrupt.with_resume('BODY', ['x']).endswith(SENTENCE))

    def test_sheet_name_is_relative_inside_the_project_and_absolute_outside(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None) as temp:
            base = Path(os.path.realpath(temp))
            (base / 'project/.workspace/tasks').mkdir(parents=True)
            sheet = base / 'project/.workspace/tasks/demo.md'
            sheet.write_text('x')
            outside = base / 'outside.md'
            outside.write_text('x')
            self.assertEqual(delegate_interrupt.sheet_name(base / 'project', sheet), '.workspace/tasks/demo.md')
            self.assertEqual(delegate_interrupt.sheet_name(base / 'project', outside), str(outside))

    def test_read_previous_refuses_a_symbolic_link(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None) as temp:
            run = Path(temp)
            (run / 'real.json').write_text('{}')
            (run / 'status.json').symlink_to(run / 'real.json')
            with self.assertRaises(ValueError):
                delegate_interrupt.read_previous(run, run)

    def test_read_previous_refuses_a_symbolic_link_on_the_way_from_the_root(self):
        # Relecture du 3/11 : le refus de lien ne contrôlait que le dernier composant ; safety.check_no_symlinks les contrôle tous.
        with tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None) as temp:
            root = Path(os.path.realpath(temp)) / 'project'
            elsewhere = Path(os.path.realpath(temp)) / 'elsewhere' / 'delegate' / 't'
            elsewhere.mkdir(parents=True)
            (elsewhere / 'status.json').write_text('{"outcome": "interrupted"}')
            (root / '.workspace').mkdir(parents=True)
            (root / '.workspace' / 'run').symlink_to(elsewhere.parent.parent)
            with self.assertRaises(ValueError):
                delegate_interrupt.read_previous(root, root / '.workspace/run/delegate/t')

    def test_read_previous_refuses_a_symbolic_run_folder(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None) as temp:
            root = Path(os.path.realpath(temp)) / 'project'
            elsewhere = Path(os.path.realpath(temp)) / 'elsewhere' / 't'
            elsewhere.mkdir(parents=True)
            (elsewhere / 'status.json').write_text('{"outcome": "interrupted"}')
            run = root / '.workspace/run/delegate/t'
            run.parent.mkdir(parents=True)
            run.symlink_to(elsewhere)
            with self.assertRaises(ValueError):
                delegate_interrupt.read_previous(root, run)

    def test_resume_section_without_lines_still_says_head_moved(self):
        # Relecture du 3/11 : sans fichier listé (HEAD seul a bougé), la section de reprise disparaissait.
        report = delegate_interrupt.REPORT_SECTION
        result = delegate_interrupt.with_resume('BODY\n\n' + report, [])
        self.assertLess(result.index(SECTION), result.index(report))
        self.assertIn('- ' + delegate_interrupt.NO_FILE_LINE, result.splitlines())
        self.assertIn(SENTENCE, result)

    def test_read_previous_tolerates_absence_and_garbage(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None) as temp:
            run = Path(temp)
            self.assertEqual(delegate_interrupt.read_previous(run, run), {})
            (run / 'status.json').write_text('not json')
            self.assertEqual(delegate_interrupt.read_previous(run, run), {})
            (run / 'status.json').write_text('[1]')
            self.assertEqual(delegate_interrupt.read_previous(run, run), {})


class GateTests(CloneCase):
    def setUp(self):
        super().setUp()
        self.run_dir = self.root / '.workspace/run/delegate/t'
        self.run_dir.mkdir(parents=True)
        self.sheet = self.root / '.workspace/tasks/t.md'
        self.d = SimpleNamespace(root=self.root, task={'id': 't'})

    def previous(self, **keys):
        status = dict(id='t', attempts=1, outcome='interrupted', worktree_dirty=True, dirty_files=1, metrics={'attempts': 1})
        status.update(keys)
        (self.run_dir / 'status.json').write_text(json.dumps(status))
        return status

    def gate(self, requested):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = delegate_interrupt.gate(self.d, self.sheet, requested)
        return code, out.getvalue(), err.getvalue()

    def stored(self):
        return json.loads((self.run_dir / 'status.json').read_text())

    def test_the_refusal_is_written_0600_through_the_safety_primitive(self):
        self.previous()
        (self.clone / 'new.txt').write_text('partial\n')
        with mock.patch.object(delegate_interrupt.safety, 'write_file_secure', wraps=delegate_interrupt.safety.write_file_secure) as write:
            code, _out, _err = self.gate(False)
        self.assertEqual(code, delegate_interrupt.EXIT_REFUSED)
        self.assertEqual(write.call_args.args[0], self.run_dir / 'status.json')
        self.assertEqual((self.run_dir / 'status.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.stored()['outcome'], 'worktree_dirty')

    def test_an_empty_commit_resumes_with_the_section(self):
        self.previous()
        git(self.clone, 'commit', '-q', '--allow-empty', '-m', 'vide')
        code, _out, _err = self.gate(True)
        self.assertIsNone(code)
        self.assertEqual((self.d.resumed_dirty, self.d.resume_lines), (True, []))
        prompt = delegate_interrupt.with_resume('BODY', self.d.resume_lines)
        self.assertIn(SECTION, prompt)

    def test_no_previous_status_is_ordinary(self):
        self.assertEqual(self.gate(False), (None, '', ''))
        self.assertFalse(self.d.resumed_dirty)
        self.assertEqual(self.d.resume_lines, [])

    def test_option_without_a_dirty_interruption_is_a_usage_error_and_writes_nothing(self):
        for previous in (None, dict(worktree_dirty=False), dict(outcome='done'), dict(worktree_dirty=None)):
            with self.subTest(previous=previous):
                (self.run_dir / 'status.json').unlink(missing_ok=True)
                before = self.previous(**previous) if previous is not None else None
                code, out, err = self.gate(True)
                self.assertEqual((code, out), (64, ''))
                self.assertTrue(err.startswith(USAGE_PREFIX))
                self.assertFalse((self.root / '.workspace/events.jsonl').exists())
                if before is not None:
                    self.assertEqual(self.stored(), before)
                else:
                    self.assertFalse((self.run_dir / 'status.json').exists())

    def test_dirty_clone_after_a_dirty_interruption_is_refused(self):
        before = self.previous()
        (self.clone / 'new.txt').write_text('new\n')
        (self.clone / 'other.txt').write_text('other\n')
        code, out, err = self.gate(False)
        self.assertEqual((code, err), (1, ''))
        self.assertEqual(
            out,
            'délégation : refusée (clone sale après interruption : 2 fichiers) ; '
            'reprendre avec delegate.sh .workspace/tasks/t.md --resume-dirty, ou close --abandoned puis prepare --retry\n',
        )
        stored = self.stored()
        self.assertEqual(stored['outcome'], 'worktree_dirty')
        self.assertEqual(stored['dirty_files'], 2)
        self.assertEqual(stored['attempts'], 1)
        self.assertRegex(stored['ended_at'], r'^\d{4}-\d{2}-\d{2}T')
        changed = {key for key in set(stored) | set(before) if stored.get(key) != before.get(key)}
        self.assertEqual(changed, {'outcome', 'dirty_files', 'ended_at'})
        written = [entry for entry in events.read(self.root, task_id='t') if entry['event'] == 'outcome']
        self.assertEqual([entry['detail']['outcome'] for entry in written], ['worktree_dirty'])
        self.assertEqual(written[0]['detail']['dirty_files'], 2)

    def test_the_refusal_repeats_and_the_option_is_then_accepted(self):
        self.previous()
        (self.clone / 'new.txt').write_text('new\n')
        self.assertEqual(self.gate(False)[0], 1)
        (self.clone / 'other.txt').write_text('other\n')
        code, out, _ = self.gate(False)
        self.assertEqual(code, 1)
        self.assertIn('2 fichiers', out)
        self.assertEqual(self.gate(True)[0], None)
        self.assertTrue(self.d.resumed_dirty)
        self.assertEqual(sorted(self.d.resume_lines), ['?? new.txt', '?? other.txt'])

    def test_resumed_lines_hold_the_committed_files_after_the_porcelain_ones(self):
        self.previous()
        (self.clone / 'a.txt').write_text('a')
        git(self.clone, 'add', '-A')
        git(self.clone, 'commit', '-qm', 'model work')
        (self.clone / 'new.txt').write_text('new\n')
        self.assertEqual(self.gate(True)[0], None)
        self.assertEqual(self.d.resume_lines, ['?? new.txt', 'a.txt'])

    def test_a_clean_clone_makes_it_ordinary_with_or_without_the_option(self):
        self.previous()
        for requested in (False, True):
            with self.subTest(requested=requested):
                self.assertEqual(self.gate(requested), (None, '', ''))
                self.assertFalse(self.d.resumed_dirty)
                self.assertEqual(self.d.resume_lines, [])

    def test_a_clean_previous_interruption_is_ordinary(self):
        self.previous(worktree_dirty=False)
        (self.clone / 'new.txt').write_text('new\n')
        self.assertEqual(self.gate(False), (None, '', ''))

    def test_an_old_interrupted_status_without_the_key_is_ordinary(self):
        status = self.previous()
        del status['worktree_dirty']
        (self.run_dir / 'status.json').write_text(json.dumps(status))
        (self.clone / 'new.txt').write_text('new\n')
        self.assertEqual(self.gate(False), (None, '', ''))

    def test_without_a_prepared_clone_the_refusal_does_not_apply(self):
        self.previous()
        (self.root / '.workspace/orchestration/tasks/t/state.json').unlink()
        self.assertEqual(self.gate(False), (None, '', ''))


FAKE_CLAUDE = '''#!/usr/bin/env python3
import json, os, pathlib, signal, subprocess, sys, time

stdin = sys.stdin.read()
with open(os.environ['FAKE_CLAUDE_LOG'], 'a') as log:
    log.write(json.dumps({'cwd': os.getcwd(), 'stdin': stdin}) + '\\n')
mode = os.environ.get('FAKE_CLAUDE_MODE', 'ok')
here = pathlib.Path('.')
if mode == 'hang':
    (here / 'new.txt').write_text('partial\\n')
elif mode == 'hang-ignored':
    (here / '__pycache__').mkdir(exist_ok=True)
    (here / '__pycache__/junk.pyc').write_text('x')
elif mode == 'hang-commit':
    for name in ('a.txt', 'b.txt'):
        (here / name).write_text(name)
    subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@x', 'add', 'a.txt', 'b.txt'], check=True)
    subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@x', 'commit', '-qm', 'work'], check=True)
    (here / 'new.txt').write_text('partial\\n')
elif mode == 'dying':
    def late(signum, frame):
        (here / 'late.txt').write_text('written while dying\\n')
        sys.exit(1)
    signal.signal(signal.SIGTERM, late)
if mode != 'ok':
    pathlib.Path(os.environ['FAKE_CLAUDE_READY']).write_text('1')
    time.sleep(30)
print(json.dumps({'result': 'answer', 'usage': {'input_tokens': 1, 'output_tokens': 1}, 'total_cost_usd': 0.01, 'num_turns': 1}))
'''

if FAST:
    Harness = unittest.TestCase
else:
    try:
        from tests import test_delegate as base
    except ImportError:
        import test_delegate as base

    class Harness(unittest.TestCase):
        """The temporary project, fake docker and helpers of tests/test_delegate.py, without its tests."""

        setUp = base.DelegateTests.setUp
        setup_fake_docker = base.DelegateTests.setup_fake_docker
        get_docker_calls = base.DelegateTests.get_docker_calls
        git = base.DelegateTests.git
        executable = base.DelegateTests.executable
        write_task = base.DelegateTests.write_task
        prepare_exchange_task = base.DelegateTests.prepare_exchange_task
        status = base.DelegateTests.status
        clone = base.DelegateTests.clone


@unittest.skipIf(FAST, 'module lent : Git réel, sous-processus ou Docker simulé ; suite complète seulement')
class EndToEndTests(Harness):
    TASK = 'demo-resume'

    def setUp(self):
        super().setUp()
        self.executable('bin/claude', FAKE_CLAUDE)
        self.claude_log = self.root.parent / (self.root.name + '-claude.log')
        self.ready = self.root.parent / (self.root.name + '-ready')
        self.sheet = self.prepare_exchange_task(self.TASK, profile='python', isolation='exchange', agent='claude-haiku')
        self.env = dict(self.env, FAKE_CLAUDE_LOG=str(self.claude_log), FAKE_CLAUDE_READY=str(self.ready))
        self.run_dir = self.root / '.workspace/run/delegate' / self.TASK

    def calls(self):
        if not self.claude_log.exists():
            return []
        return [json.loads(line) for line in self.claude_log.read_text().splitlines()]

    def delegate(self, *args, mode='ok'):
        self.ready.unlink(missing_ok=True)
        return subprocess.Popen(
            ['bash', str(self.root / 'scripts/delegate.sh'), *args],
            cwd=self.root,
            env=dict(self.env, FAKE_CLAUDE_MODE=mode),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def interrupt(self, mode, signum=signal.SIGTERM):
        proc = self.delegate(str(self.sheet), mode=mode)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not self.ready.exists():
            time.sleep(0.02)
        self.assertTrue(self.ready.exists(), 'le faux claude aurait dû démarrer')
        proc.send_signal(signum)
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 130)
        return self.status(self.TASK)

    def outcome_events(self):
        return [entry for entry in events.read(self.root, task_id=self.TASK) if entry['event'] == 'outcome']

    def test_sigterm_records_the_signal_and_the_dirty_clone(self):
        status = self.interrupt('hang')
        self.assertEqual(status['outcome'], 'interrupted')
        self.assertEqual(status['signal'], 'SIGTERM')
        self.assertIs(status['worktree_dirty'], True)
        self.assertEqual(status['dirty_files'], 1)
        self.assertRegex(status['interrupted_at'], '^' + STAMP.pattern + '$')
        detail = self.outcome_events()[-1]['detail']
        self.assertEqual((detail['outcome'], detail['attempts']), ('interrupted', status['attempts']))
        self.assertEqual((detail['signal'], detail['worktree_dirty'], detail['dirty_files']), ('SIGTERM', True, 1))

    def test_sigint_and_sighup_are_named(self):
        for signum in (signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=signum.name):
                (self.clone(self.TASK) / 'new.txt').unlink(missing_ok=True)
                (self.run_dir / 'status.json').unlink(missing_ok=True)
                self.assertEqual(self.interrupt('hang', signum)['signal'], signum.name)

    def test_an_ignored_file_leaves_the_clone_clean(self):
        status = self.interrupt('hang-ignored')
        self.assertIs(status['worktree_dirty'], False)
        self.assertEqual(status['dirty_files'], 0)

    def test_commits_count_as_dirty_files(self):
        status = self.interrupt('hang-commit')
        self.assertIs(status['worktree_dirty'], True)
        self.assertEqual(status['dirty_files'], 3)

    def test_a_file_written_while_the_model_dies_is_counted(self):
        status = self.interrupt('dying')
        self.assertTrue((self.clone(self.TASK) / 'late.txt').exists())
        self.assertEqual(status['dirty_files'], 1)

    def test_a_dirty_interruption_refuses_the_next_delegation_before_any_model_call(self):
        attempts = self.interrupt('hang')['attempts']
        calls = len(self.calls())
        docker_calls = len(self.get_docker_calls())
        rows = (self.root / '.workspace/consumption.jsonl').read_text() if (self.root / '.workspace/consumption.jsonl').exists() else ''
        proc = self.delegate(str(self.sheet))
        out, _ = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(len(self.calls()), calls)
        self.assertEqual(len(self.get_docker_calls()), docker_calls)
        self.assertEqual(
            out,
            'délégation : refusée (clone sale après interruption : 1 fichiers) ; '
            'reprendre avec delegate.sh tasks/%s.md --resume-dirty, ou close --abandoned puis prepare --retry\n' % self.TASK,
        )
        status = self.status(self.TASK)
        self.assertEqual((status['outcome'], status['dirty_files'], status['attempts']), ('worktree_dirty', 1, attempts))
        self.assertIs(status['worktree_dirty'], True)
        self.assertEqual(self.outcome_events()[-1]['detail']['outcome'], 'worktree_dirty')
        consumption = self.root / '.workspace/consumption.jsonl'
        self.assertEqual(consumption.read_text() if consumption.exists() else '', rows)

    def test_resume_dirty_runs_and_tells_the_model_to_reread(self):
        self.interrupt('hang')
        proc = self.delegate(str(self.sheet), '--resume-dirty')
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, out + err)
        status = self.status(self.TASK)
        self.assertIs(status['resumed_dirty'], True)
        prompt = self.calls()[-1]['stdin']
        self.assertIn('\n' + SECTION + '\n', prompt)
        self.assertIn('\n- ?? new.txt\n', prompt)
        self.assertIn(SENTENCE, prompt)

    def test_resume_dirty_may_come_first(self):
        self.interrupt('hang')
        proc = self.delegate('--resume-dirty', str(self.sheet))
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, out + err)
        self.assertIs(self.status(self.TASK)['resumed_dirty'], True)

    def test_resume_dirty_without_a_dirty_interruption_is_a_usage_error(self):
        proc = self.delegate(str(self.sheet), '--resume-dirty')
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 64)
        self.assertEqual(out, '')
        self.assertTrue(err.startswith(USAGE_PREFIX))
        self.assertFalse(self.run_dir.exists())
        self.assertEqual(self.calls(), [])
        self.assertEqual([entry for entry in events.read(self.root, task_id=self.TASK) if entry['event'] == 'outcome'], [])

    def test_a_cleaned_clone_starts_an_ordinary_delegation(self):
        self.interrupt('hang')
        (self.clone(self.TASK) / 'new.txt').unlink()
        proc = self.delegate(str(self.sheet))
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, out + err)
        self.assertNotIn('resumed_dirty', self.status(self.TASK))
        self.assertNotIn(SECTION, self.calls()[-1]['stdin'])

    def test_a_cascade_after_a_dirty_interruption_is_refused_and_keeps_the_work(self):
        # Relecture du 3/11 : `--cascade` lançait cascade.py sans ce refus, et son redémarrage remettait le clone à sa
        # base (git reset --hard, git clean) : la perte même que la fiche empêche.
        attempts = self.interrupt('hang')['attempts']
        calls = len(self.calls())
        proc = self.delegate('--cascade', 'haiku,sonnet', str(self.sheet))
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 1, out + err)
        self.assertEqual(out, delegate_interrupt.REFUSAL % (1, 'tasks/%s.md' % self.TASK) + '\n')
        self.assertEqual(len(self.calls()), calls)
        self.assertEqual((self.clone(self.TASK) / 'new.txt').read_text(), 'partial\n')
        status = self.status(self.TASK)
        self.assertEqual((status['outcome'], status['attempts']), ('worktree_dirty', attempts))
        self.assertFalse((self.run_dir / 'cascade.json').exists())

    def test_a_clean_interruption_starts_an_ordinary_delegation(self):
        self.interrupt('hang-ignored')
        proc = self.delegate(str(self.sheet))
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, out + err)
        self.assertNotIn(SECTION, self.calls()[-1]['stdin'])


@unittest.skipIf(FAST, 'module lent : sous-processus ; suite complète seulement')
class UsageTests(Harness):
    def run_sh(self, *args):
        return subprocess.run(
            ['bash', str(self.root / 'scripts/delegate.sh'), *args], cwd=self.root, env=self.env, text=True, capture_output=True
        )

    def test_invalid_combinations_are_usage_errors(self):
        for args in (
            ['--resume-dirty'],
            ['--resume-dirty', '--resume-dirty', 'x.md'],
            ['x.md', '--resume-dirty', '--resume-dirty'],
            ['--cascade', 'haiku', 'x.md', '--resume-dirty'],
            ['--resume-dirty', '--cascade', 'haiku', 'x.md'],
            ['--cascade', '--resume-dirty', 'x.md'],
            ['--resume-dirty', '--cascade'],
            ['a.md', 'b.md'],
            [],
        ):
            with self.subTest(args=args):
                done = self.run_sh(*args)
                self.assertEqual(done.returncode, 64)
                self.assertEqual(done.stderr, 'usage: scripts/delegate.sh [--cascade <liste>] <fichier-de-tâche> [--resume-dirty]\n')


if __name__ == '__main__':
    unittest.main()
