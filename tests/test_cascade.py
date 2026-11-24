import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import cascade  # noqa: E402
import task_cost  # noqa: E402

DELEGATE_SH = ROOT / 'template/scripts/delegate.sh'


def git(*args):
    subprocess.run(['git'] + list(args), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class PassRatioTests(unittest.TestCase):
    def test_all_green(self):
        self.assertEqual(cascade.pass_ratio('Ran 3 tests in 0.1s\n\nOK'), 1.0)
        self.assertEqual(cascade.pass_ratio('Ran 3 tests\n\nOK (skipped=1)'), 1.0)

    def test_failures_and_errors(self):
        self.assertAlmostEqual(cascade.pass_ratio('Ran 9 tests in 0.1s\n\nFAILED (failures=2, errors=1)'), 6 / 9)
        self.assertAlmostEqual(cascade.pass_ratio('Ran 4 tests\n\nFAILED (errors=1, skipped=2)'), 3 / 4)

    def test_unknown(self):
        for text in ('', 'boom', 'Ran 0 tests\n\nOK', 'Ran 2 tests\n\nstopped'):
            self.assertIsNone(cascade.pass_ratio(text), text)


class ParseModelsTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(cascade.parse_models('haiku,sonnet'), ['claude-haiku', 'claude-sonnet'])
        self.assertEqual(cascade.parse_models('opus'), ['claude-opus'])

    def test_invalid(self):
        for text in ('', 'haiku,', 'haiku,haiku', 'gpt', 'haiku,sonnet,opus,opus'):
            with self.assertRaises(ValueError, msg=text):
                cascade.parse_models(text)


class StagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'project'
        self.root.mkdir()
        self.work = self.root / 'work'
        self.work.mkdir()
        git('init', '-q', '-b', 'main', str(self.work))
        git('-C', str(self.work), 'config', 'user.email', 't@example.org')
        git('-C', str(self.work), 'config', 'user.name', 'T')
        (self.work / '.gitignore').write_text('ignored.txt\n')
        (self.work / 'tracked.txt').write_text('base')
        git('-C', str(self.work), 'add', '.')
        git('-C', str(self.work), 'commit', '-qm', 'base')
        self.base = subprocess.check_output(['git', '-C', str(self.work), 'rev-parse', 'HEAD'], universal_newlines=True).strip()

    def play(self, results, agents=('claude-haiku', 'claude-sonnet', 'claude-opus')):
        calls = []

        def runner(agent, context):
            calls.append((agent, context))
            return results[len(calls) - 1]

        return cascade.run_stages(self.root, 'task-1', list(agents), runner, self.work, self.base), calls

    @staticmethod
    def stage(outcome, acceptance='', diff='', attempts=1, tokens=None):
        return dict(outcome=outcome, attempts=attempts, tokens=tokens or {'output': 5}, acceptance=acceptance, diff=diff)

    def test_resume_then_done_never_resets(self):
        (self.work / 'tracked.txt').write_text('haiku work')
        partial = 'Ran 9 tests in 1s\n\nFAILED (failures=1)'
        result, calls = self.play([self.stage('failed', partial, 'DIFF'), self.stage('done')])
        self.assertEqual([s['next'] for s in result['stages']], ['resume', 'stop'])
        self.assertEqual(calls[0], ('claude-haiku', ''))
        self.assertEqual(calls[1][1], '\n\n'.join([cascade.RESUME_HEADER, 'DIFF', partial]))
        self.assertEqual((self.work / 'tracked.txt').read_text(), 'haiku work')
        self.assertEqual(result['outcome'], 'done')
        self.assertAlmostEqual(result['stages'][0]['ratio'], 8 / 9)

    def test_restart_resets_tracked_untracked_but_keeps_ignored(self):
        (self.work / 'tracked.txt').write_text('bad')
        (self.work / 'new.txt').write_text('x')
        (self.work / 'ignored.txt').write_text('keep')
        bad = 'Ran 9 tests\n\nFAILED (failures=8)'
        result, calls = self.play(
            [self.stage('failed', bad, 'BAD DIFF'), self.stage('done_with_warning')], agents=('claude-haiku', 'claude-sonnet')
        )
        self.assertEqual(result['stages'][0]['next'], 'restart')
        self.assertEqual(calls[1][1], '\n\n'.join([cascade.RESTART_HEADER, 'BAD DIFF']))
        self.assertEqual((self.work / 'tracked.txt').read_text(), 'base')
        self.assertFalse((self.work / 'new.txt').exists())
        self.assertEqual((self.work / 'ignored.txt').read_text(), 'keep')

    def test_stop_outcomes_end_the_cascade(self):
        for outcome in cascade.STOP_OUTCOMES:
            result, calls = self.play([self.stage(outcome, 'Ran 9 tests\n\nFAILED (failures=1)')])
            self.assertEqual(len(calls), 1, outcome)
            self.assertEqual(result['stages'][0]['next'], 'stop')
            self.assertEqual(result['outcome'], outcome)

    def test_unknown_ratio_restarts_and_last_stage_stops(self):
        result, calls = self.play(
            [self.stage('failed', 'crash'), self.stage('failed', 'Ran 3 tests\n\nOK')], agents=('claude-haiku', 'claude-sonnet')
        )
        self.assertEqual([s['next'] for s in result['stages']], ['restart', 'stop'])
        self.assertEqual(result['outcome'], 'failed')

    def test_writes_cascade_json_and_events(self):
        self.play([self.stage('done', attempts=2, tokens={'output': 7})], agents=('claude-haiku',))
        data = json.loads((self.root / '.workspace/run/delegate/task-1/cascade.json').read_text())
        self.assertEqual(data['outcome'], 'done')
        self.assertEqual(data['stages'][0]['attempts'], 2)
        self.assertEqual(data['stages'][0]['tokens'], {'output': 7})
        lines = (self.root / '.workspace/events.jsonl').read_text().splitlines()
        found = [json.loads(line) for line in lines if '"cascade_step"' in line]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['task_id'], 'task-1')
        self.assertEqual(found[0]['detail']['agent'], 'claude-haiku')
        self.assertEqual(found[0]['detail']['next'], 'stop')

    def test_reset_failure_raises(self):
        with self.assertRaises(RuntimeError):
            cascade.reset_to_base(self.work, 'deadbeef')

    def test_reset_refuses_non_hex_base_without_running_git(self):
        for base in ('HEAD', '--hard', 'abc12', 'g' * 8, 'a' * 41, '', None):
            with mock.patch.object(cascade.subprocess, 'run') as run:
                with self.assertRaises(ValueError, msg=repr(base)):
                    cascade.reset_to_base(self.work, base)
                run.assert_not_called()

    def test_reset_uses_hardened_git(self):
        done = subprocess.CompletedProcess([], 0, stdout='')
        with mock.patch.object(cascade.subprocess, 'run', return_value=done) as run:
            cascade.reset_to_base(self.work, self.base)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands,
            [
                cascade.workdir.git_command('reset', '-q', '--hard', self.base, cwd=self.work),
                cascade.workdir.git_command('clean', '-fdq', cwd=self.work),
            ],
        )
        for command in commands:
            self.assertIn('core.hooksPath=/dev/null', command)


class CheckWorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.root = base / 'project'
        self.root.mkdir()
        patcher = mock.patch.dict('os.environ', {'WORKSPACE_WORK_DIR': str(base / 'work')})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.worktree = cascade.workdir.task_worktree(self.root, 'task-1')
        self.worktree.mkdir(parents=True)
        self.state = self.root / '.workspace/orchestration/tasks/task-1/state.json'
        self.state.parent.mkdir(parents=True)
        self.state.write_text(json.dumps({'task_id': 'task-1'}))

    def test_valid(self):
        self.assertIsNone(cascade.check_worktree(self.root, 'task-1', self.worktree))

    def test_symlink_refused(self):
        moved = self.worktree.with_name('moved')
        self.worktree.rename(moved)
        self.worktree.symlink_to(moved)
        with self.assertRaises(ValueError):
            cascade.check_worktree(self.root, 'task-1', self.worktree)

    def test_other_path_refused(self):
        other = self.worktree.with_name('other')
        other.mkdir()
        with self.assertRaises(ValueError):
            cascade.check_worktree(self.root, 'task-1', other)

    def test_state_mismatch_or_missing_refused(self):
        self.state.write_text(json.dumps({'task_id': 'task-2'}))
        with self.assertRaises(ValueError):
            cascade.check_worktree(self.root, 'task-1', self.worktree)
        self.state.write_text('not json')
        with self.assertRaises(ValueError):
            cascade.check_worktree(self.root, 'task-1', self.worktree)
        self.state.unlink()
        with self.assertRaises(ValueError):
            cascade.check_worktree(self.root, 'task-1', self.worktree)

    def test_runner_reports_control_tampered(self):
        run = self.root / '.workspace/run/delegate/task-1'
        run.mkdir(parents=True)
        (run / 'status.json').write_text(json.dumps({'outcome': 'failed'}))
        self.state.write_text(json.dumps({'task_id': 'task-2'}))
        runner = cascade.make_runner(self.root, self.root / 'fiche.md', 'task-1', self.worktree)
        with mock.patch.object(cascade.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            result = runner('claude-haiku', '')
        self.assertEqual(result['outcome'], 'control_tampered')


class CostLinesTests(unittest.TestCase):
    def test_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(task_cost.cascade_lines(root, 't1'), [])
            run = root / '.workspace/run/delegate/t1'
            run.mkdir(parents=True)
            (run / 'cascade.json').write_text(
                json.dumps(
                    {
                        'outcome': 'done',
                        'stages': [
                            {
                                'stage': 1,
                                'agent': 'claude-haiku',
                                'outcome': 'failed',
                                'attempts': 2,
                                'tokens': {'output': 10, 'cached_input': 4},
                            },
                            {'stage': 2, 'agent': 'claude-sonnet', 'outcome': 'done', 'attempts': 1, 'tokens': None},
                        ],
                    }
                )
            )
            self.assertEqual(
                task_cost.cascade_lines(root, 't1'),
                [
                    'étage 1 claude-haiku : failed, tentatives 2, sortie 10, cache lu 4',
                    'étage 2 claude-sonnet : done, tentatives 1, sortie ?, cache lu ?',
                    'cascade : 2 étage(s), issue done',
                ],
            )
            self.assertEqual(task_cost.cost_line(root, 't1').splitlines()[1:], task_cost.cascade_lines(root, 't1'))


class DelegateShTests(unittest.TestCase):
    def usage(self, *args):
        return subprocess.run(['sh', str(DELEGATE_SH)] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_bad_forms_exit_64(self):
        for args in (
            ('--cascade',),
            ('--other', 'fiche.md'),
            ('--cascade', 'haiku'),
            ('a', 'b'),
            (),
            ('--cascade', 'haiku', 'fiche.md', 'extra'),
        ):
            result = self.usage(*args)
            self.assertEqual(result.returncode, 64, args)
            self.assertIn('usage', result.stderr)

    def test_invalid_list_exits_64_through_cascade(self):
        result = self.usage('--cascade', 'haiku,gpt', 'fiche.md')
        self.assertEqual(result.returncode, 64)
        self.assertIn('liste de modèles invalide', result.stderr)


class MainTests(unittest.TestCase):
    def test_invalid_list_and_unprepared_task(self):
        self.assertEqual(cascade.main(['/nonexistent', 'haiku,gpt', 'fiche.md']), 64)
        self.assertEqual(cascade.main(['/nonexistent', 'haiku']), 64)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cascade.main([tmp, 'haiku', str(Path(tmp) / 'missing.md')]), 64)


if __name__ == '__main__':
    unittest.main()
