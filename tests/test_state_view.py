"""Tests de template/scripts/lib/state_view.py : le journal est la source de l'état des tâches."""

import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import events  # noqa: E402
import state_view  # noqa: E402
import workdir  # noqa: E402

STATE = {'task_id': 'demo', 'status': 'prepared', 'phase': 'prepared', 'allowed': ['é/**'], 'z': 1, 'a': None}


class StateViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def view(self, task_id='demo'):
        return self.root / '.workspace/orchestration/tasks' / task_id / 'state.json'

    def stderr_of(self, function, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            function(*args)
        return err.getvalue()

    def rebuild(self, **options):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = state_view.rebuild(self.root, **options)
        return code, out.getvalue().splitlines()

    def drop_state_events(self, keep):
        path = self.root / events.EVENTS_PATH
        lines = path.read_text(encoding='utf-8').splitlines()
        path.write_text(''.join(line + '\n' for line in lines if keep(json.loads(line))), encoding='utf-8')

    def test_serialize_is_canonical_json(self):
        text = state_view.serialize(STATE)
        self.assertEqual(text, json.dumps(STATE, indent=2, ensure_ascii=False, sort_keys=True) + '\n')
        self.assertIn('é/**', text)
        self.assertLess(text.index('"a"'), text.index('"z"'))
        self.assertEqual(json.loads(text), STATE)

    def test_write_records_the_event_then_writes_the_view(self):
        state_view.write(self.root, 'demo', STATE, 'prepare')
        entries = events.read(self.root)
        self.assertEqual(
            [(e['task_id'], e['event'], e['detail']) for e in entries], [('demo', 'state', {'reason': 'prepare', 'state': STATE})]
        )
        self.assertEqual(self.view().read_text(encoding='utf-8'), state_view.serialize(STATE))
        self.assertEqual(stat.S_IMODE(self.view().stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.view().parent.stat().st_mode), 0o700)
        self.assertEqual([p.name for p in self.view().parent.iterdir()], ['state.json'])

    def test_write_uses_an_exclusive_nofollow_temporary_and_replace(self):
        flags = []
        real_open = os.open

        def spy(path, flag, *args):
            flags.append(flag)
            return real_open(path, flag, *args)

        with mock.patch('os.open', side_effect=spy), mock.patch('os.replace', wraps=os.replace) as replace:
            state_view.write(self.root, 'demo', STATE, 'prepare')
        wanted = os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        self.assertTrue(any(flag & wanted == wanted for flag in flags))
        source, target = replace.call_args[0]
        self.assertEqual(Path(source).parent, self.view().parent)
        self.assertEqual(Path(target), self.view())

    def test_failed_replace_leaves_no_temporary_and_keeps_the_old_view(self):
        state_view.write(self.root, 'demo', STATE, 'prepare')
        before = self.view().read_text(encoding='utf-8')
        with mock.patch('os.replace', side_effect=PermissionError('refusé')):
            with self.assertRaises(PermissionError):
                state_view.write(self.root, 'demo', dict(STATE, phase='failed'), 'phase')
        self.assertEqual(self.view().read_text(encoding='utf-8'), before)
        self.assertEqual([p.name for p in self.view().parent.iterdir()], ['state.json'])

    def test_view_is_written_even_when_the_event_cannot_be_written(self):
        (self.root / '.workspace').mkdir()
        (self.root / events.EVENTS_PATH).mkdir()
        message = self.stderr_of(state_view.write, self.root, 'demo', STATE, 'prepare')
        self.assertIn('events: ', message)
        self.assertIn('state_view: ', message)
        self.assertEqual(self.view().read_text(encoding='utf-8'), state_view.serialize(STATE))

    def test_symlinked_view_directory_is_refused(self):
        target = self.root / 'ailleurs'
        target.mkdir()
        self.view().parent.parent.mkdir(parents=True)
        self.view().parent.symlink_to(target)
        with self.assertRaises(ValueError):
            state_view.write(self.root, 'demo', STATE, 'prepare')
        self.assertEqual(list(target.iterdir()), [])

    def test_invalid_task_ids_are_refused_before_any_write(self):
        for task_id in ('', '../x', 'A', 'a/b'):
            with self.subTest(task_id=task_id), self.assertRaises(ValueError):
                state_view.write(self.root, task_id, STATE, 'prepare')
        self.assertFalse((self.root / '.workspace').exists())

    def test_removed_records_none_and_touches_no_view(self):
        state_view.write(self.root, 'demo', STATE, 'prepare')
        before = self.view().read_bytes()
        state_view.removed(self.root, 'demo', 'archive')
        self.assertEqual(events.read(self.root)[-1]['detail'], {'reason': 'archive', 'state': None})
        self.assertEqual(self.view().read_bytes(), before)
        self.assertEqual(state_view.latest(self.root), {'demo': None})

    def test_latest_keeps_the_last_state_of_each_task_in_file_order(self):
        state_view.write(self.root, 'a', {'v': 1}, 'prepare')
        state_view.write(self.root, 'b', {'v': 2}, 'prepare')
        state_view.write(self.root, 'a', {'v': 3}, 'phase')
        events.record(self.root, 'a', 'note', {'state': {'v': 99}})
        events.record(self.root, 'b', 'state', {'reason': 'sans état'})
        events.record(self.root, 'b', 'state', 'pas un objet')
        self.assertEqual(state_view.latest(self.root), {'a': {'v': 3}, 'b': {'v': 2}})

    def test_latest_on_a_missing_journal_is_empty(self):
        self.assertEqual(state_view.latest(self.root), {})

    def test_rebuild_recreates_missing_views_with_the_same_bytes(self):
        state_view.write(self.root, 'demo', STATE, 'prepare')
        state_view.write(self.root, 'autre', {'task_id': 'autre', 'status': 'merged'}, 'close')
        expected = {name: self.view(name).read_bytes() for name in ('demo', 'autre')}
        for name in expected:
            self.view(name).unlink()
        old = os.umask(0o022)
        self.addCleanup(os.umask, old)
        code, lines = self.rebuild()
        self.assertEqual(code, 0)
        self.assertEqual(lines, ['recréée : autre', 'recréée : demo', 'bilan : 2 recréée(s), 0 identique(s), 0 écart(s)'])
        for name, content in expected.items():
            self.assertEqual(self.view(name).read_bytes(), content)
            self.assertEqual(stat.S_IMODE(self.view(name).stat().st_mode), 0o600)

    def test_rebuild_from_the_journal_alone_in_a_fresh_directory(self):
        state_view.write(self.root, 'demo', STATE, 'prepare')
        state_view.write(self.root, 'demo', dict(STATE, phase='done'), 'close')
        expected = self.view().read_bytes()
        fresh = Path(self.tmp.name) / 'neuf'
        (fresh / '.workspace').mkdir(parents=True)
        shutil.copy2(self.root / events.EVENTS_PATH, fresh / events.EVENTS_PATH)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(state_view.rebuild(fresh), 0)
        self.assertEqual((fresh / '.workspace/orchestration/tasks/demo/state.json').read_bytes(), expected)

    def test_second_rebuild_finds_nothing_to_do(self):
        state_view.write(self.root, 'demo', STATE, 'prepare')
        self.view().unlink()
        self.assertEqual(self.rebuild()[0], 0)
        code, lines = self.rebuild()
        self.assertEqual((code, lines), (0, ['bilan : 0 recréée(s), 1 identique(s), 0 écart(s)']))

    def test_rebuild_reports_a_different_view_and_leaves_it_alone(self):
        state_view.write(self.root, 'demo', STATE, 'prepare')
        self.view().write_text('{"à la main": true}\n', encoding='utf-8')
        code, lines = self.rebuild()
        self.assertEqual(code, 1)
        self.assertEqual(lines, ['écart : demo : vue différente du journal', 'bilan : 0 recréée(s), 0 identique(s), 1 écart(s)'])
        self.assertEqual(self.view().read_text(encoding='utf-8'), '{"à la main": true}\n')

    def test_rebuild_reports_a_view_whose_state_was_removed(self):
        state_view.write(self.root, 'demo', STATE, 'prepare')
        state_view.removed(self.root, 'demo', 'archive')
        code, lines = self.rebuild()
        self.assertEqual(code, 1)
        self.assertEqual(lines[0], 'écart : demo : vue présente, retirée dans le journal')
        self.view().unlink()
        self.assertEqual(self.rebuild()[0], 0)

    def test_rebuild_with_the_last_state_event_cut_flags_that_task_only(self):
        state_view.write(self.root, 'a', {'v': 1}, 'prepare')
        state_view.write(self.root, 'b', {'v': 1}, 'prepare')
        state_view.write(self.root, 'b', {'v': 2}, 'phase')
        seen = []
        self.drop_state_events(lambda e: not (e['task_id'] == 'b' and seen.append(1) is None and len(seen) == 2))
        code, lines = self.rebuild()
        self.assertEqual(code, 1)
        self.assertEqual([line for line in lines if line.startswith('écart : ')], ['écart : b : vue différente du journal'])
        self.assertEqual(json.loads(self.view('b').read_text(encoding='utf-8')), {'v': 2})

    def test_view_without_event_is_a_gap_then_adopted(self):
        self.view().parent.mkdir(parents=True)
        self.view().write_text('{"task_id":"demo","b":1,"a":2}', encoding='utf-8')
        code, lines = self.rebuild()
        self.assertEqual((code, lines[0]), (1, 'écart : demo : vue sans événement d\'état'))
        self.assertEqual(events.read(self.root), [])
        code, lines = self.rebuild(adopt=True)
        self.assertEqual((code, lines), (0, ['adoptée : demo', 'bilan : 0 recréée(s), 0 identique(s), 0 écart(s)']))
        self.assertEqual(events.read(self.root)[0]['detail'], {'reason': 'adopt', 'state': {'task_id': 'demo', 'b': 1, 'a': 2}})
        self.assertEqual(self.view().read_text(encoding='utf-8'), state_view.serialize({'task_id': 'demo', 'b': 1, 'a': 2}))
        self.assertEqual(self.rebuild(), (0, ['bilan : 0 recréée(s), 1 identique(s), 0 écart(s)']))

    def test_adopt_does_not_adopt_an_unreadable_or_non_object_view(self):
        for content in ('{oups', '[1, 2]'):
            with self.subTest(content=content):
                self.view().parent.mkdir(parents=True, exist_ok=True)
                self.view().write_text(content, encoding='utf-8')
                code, lines = self.rebuild(adopt=True)
                self.assertEqual(code, 1)
                self.assertEqual(lines[0], 'écart : demo : vue sans événement d\'état')
                self.assertEqual(self.view().read_text(encoding='utf-8'), content)
        self.assertEqual(events.read(self.root), [])

    def test_ids_outside_the_pattern_are_ignored_and_counted_as_gaps(self):
        events.record(self.root, 'Mauvais_Id', 'state', {'reason': 'x', 'state': {'v': 1}})
        bad = self.root / '.workspace/orchestration/tasks/Autre_Id'
        bad.mkdir(parents=True)
        (bad / 'state.json').write_text('{}', encoding='utf-8')
        code, lines = self.rebuild(adopt=True)
        self.assertEqual(code, 1)
        self.assertEqual(
            [line for line in lines if line.startswith('écart : ')],
            ['écart : Mauvais_Id : identifiant invalide, ignoré', 'écart : Autre_Id : identifiant invalide, ignoré'],
        )
        self.assertFalse((self.root / '.workspace/orchestration/tasks/Mauvais_Id').exists())

    def test_rebuild_refuses_a_symlinked_view_without_following_it(self):
        state_view.write(self.root, 'demo', STATE, 'prepare')
        outside = self.root / 'dehors.json'
        outside.write_text(state_view.serialize(STATE), encoding='utf-8')
        self.view().unlink()
        self.view().symlink_to(outside)
        code, lines = self.rebuild()
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].startswith('écart : demo : vue illisible'))
        self.assertTrue(self.view().is_symlink())

    def test_rebuild_without_any_journal(self):
        self.assertEqual(self.rebuild(), (0, ['bilan : 0 recréée(s), 0 identique(s), 0 écart(s)']))


class RealCircuitTests(unittest.TestCase):
    """Un historique réel : préparation, abandon, reprise, travail, fusion."""

    def setUp(self):
        base = ROOT / '.workspace/tmp-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.tmp.cleanup)
        # Les clones de tâche vont hors du dépôt, jamais dans le vrai ~/.workspace-ia.
        self.work_dir = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.work_dir.cleanup)
        patcher = mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': os.path.realpath(self.work_dir.name)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = (Path(self.tmp.name) / 'projet').resolve()
        (self.root / 'scripts').mkdir(parents=True)
        shutil.copytree(ROOT / 'template/scripts/lib', self.root / 'scripts/lib', ignore=shutil.ignore_patterns('__pycache__', '__tests__'))
        for name in ('task-workspace.sh', 'report.sh'):
            shutil.copy2(ROOT / 'template/scripts' / name, self.root / 'scripts' / name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 't@example.invalid')
        (self.root / '.gitignore').write_text('.workspace/\ntasks/\nscripts/\n', encoding='utf-8')
        (self.root / 'src').mkdir()
        (self.root / 'src/app.py').write_text('print("initial")\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial commit')
        (self.root / 'tasks').mkdir()
        (self.root / 'tasks/t.md').write_text(
            '---\nid: t\nagent: codex\nallowed:\n  - src/**\n---\n# feat: changement\nSuite\n', encoding='utf-8'
        )

    def git(self, *args, cwd=None):
        return subprocess.check_output(
            [
                'git',
                '-C',
                str(cwd or self.root),
                '-c',
                'user.name=Test',
                '-c',
                'user.email=t@example.invalid',
                '-c',
                'core.hooksPath=',
                *args,
            ],
            text=True,
        )

    def run_script(self, name, *args):
        return subprocess.run(['bash', str(self.root / 'scripts' / name), *args], cwd=str(self.root), capture_output=True, text=True)

    def check_views_match_the_journal(self):
        views = sorted((self.root / '.workspace/orchestration/tasks').glob('*/state.json'))
        self.assertTrue(views)
        latest = state_view.latest(self.root)
        for view in views:
            self.assertEqual(view.read_text(encoding='utf-8'), state_view.serialize(latest[view.parent.name]))
        done = self.run_script('report.sh', '--rebuild')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn('écart : ', done.stdout)

    def test_history_is_rebuilt_without_gap(self):
        steps = [('prepare', 'tasks/t.md'), ('close', 't', '--abandoned'), ('prepare', 'tasks/t.md', '--retry')]
        for step in steps:
            done = self.run_script('task-workspace.sh', *step)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.check_views_match_the_journal()
        worktree = workdir.task_worktree(self.root, 't')
        (worktree / 'src/app.py').write_text('changed\n', encoding='utf-8')
        done = self.run_script('task-workspace.sh', 'merge', 't', '--no-verify-again', '--no-integration')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.check_views_match_the_journal()
        reasons = [e['detail']['reason'] for e in events.read(self.root) if e['event'] == 'state']
        for reason in ('prepare', 'close', 'retry', 'archive', 'phase', 'integration'):
            self.assertIn(reason, reasons)
        self.assertEqual(state_view.latest(self.root)['t']['status'], 'merged')
        self.assertNotIn('json.dump', (ROOT / 'template/scripts/lib/mission.py').read_text(encoding='utf-8').replace('json.dumps', ''))

    def test_events_listing_shows_only_the_reason_of_a_state_event(self):
        self.assertEqual(self.run_script('task-workspace.sh', 'prepare', 'tasks/t.md').returncode, 0)
        done = self.run_script('report.sh', '--events')
        self.assertEqual(done.returncode, 0, done.stderr)
        rows = [line for line in done.stdout.splitlines() if '  state  ' in line]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].endswith('{"reason":"prepare"}'), rows[0])

    def test_rebuild_exit_code_one_on_a_gap_and_adopt_requires_rebuild(self):
        self.assertEqual(self.run_script('task-workspace.sh', 'prepare', 'tasks/t.md').returncode, 0)
        view = self.root / '.workspace/orchestration/tasks/t/state.json'
        view.write_text('{}\n', encoding='utf-8')
        done = self.run_script('report.sh', '--rebuild')
        self.assertEqual(done.returncode, 1)
        self.assertIn('écart : t : vue différente du journal', done.stdout)
        self.assertNotEqual(self.run_script('report.sh', '--adopt').returncode, 0)


if __name__ == '__main__':
    unittest.main()
