from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import workdir  # noqa: E402


class WorkdirTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(os.path.realpath(self.temp.name))
        self.work = self.base / 'work'
        self.work.mkdir()
        self.root = self.base / 'project'
        self.root.mkdir()
        patcher = mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': str(self.work)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def suffix(self, root):
        return hashlib.sha256(str(Path(root).resolve()).encode('utf-8')).hexdigest()[:12]

    def test_work_base_uses_variable_and_project_subfolder(self):
        self.assertEqual(workdir.work_base(self.root), self.work / ('project-' + self.suffix(self.root)))

    def test_work_base_default_is_in_home(self):
        home = self.base / 'home'
        home.mkdir()
        with mock.patch.dict(os.environ, {'HOME': str(home)}):
            os.environ.pop('WORKSPACE_WORK_DIR')
            self.assertEqual(workdir.work_base(self.root), home / '.workspace-ia/work' / ('project-' + self.suffix(self.root)))

    def test_work_base_hashes_the_real_path(self):
        link = self.base / 'link'
        link.symlink_to(self.root)
        self.assertEqual(workdir.work_base(link), workdir.work_base(self.root))

    def test_relative_variable_is_refused(self):
        with mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': 'relative/dir'}):
            with self.assertRaises(ValueError):
                workdir.work_base(self.root)
            with self.assertRaises(ValueError):
                workdir.task_worktree(self.root, 'task-1')

    def test_empty_variable_is_refused(self):
        with mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': ''}):
            with self.assertRaises(ValueError):
                workdir.work_base(self.root)

    def test_task_worktree_is_base_slash_id(self):
        self.assertEqual(workdir.task_worktree(self.root, 'task-1'), workdir.work_base(self.root) / 'task-1')

    def test_task_worktree_inside_project_is_refused(self):
        inside = self.root / 'work'
        with mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': str(inside)}):
            with self.assertRaises(ValueError):
                workdir.task_worktree(self.root, 'task-1')
        with mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': str(self.root)}):
            with self.assertRaises(ValueError):
                workdir.task_worktree(self.root, 'task-1')

    def test_task_worktree_refuses_bad_identifiers(self):
        for bad in ('', '..', '../x', 'a/b', 'UPPER', 'a b'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                workdir.task_worktree(self.root, bad)

    def test_two_projects_with_same_name_and_task_do_not_collide(self):
        other = self.base / 'elsewhere' / 'project'
        other.mkdir(parents=True)
        self.assertEqual(self.root.name, other.name)
        self.assertNotEqual(workdir.work_base(self.root), workdir.work_base(other))
        self.assertNotEqual(workdir.task_worktree(self.root, 'same'), workdir.task_worktree(other, 'same'))

    def test_variable_through_a_symlink_is_resolved(self):
        link = self.base / 'alias'
        link.symlink_to(self.work)
        with mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': str(link)}):
            self.assertEqual(workdir.work_base(self.root), self.work / ('project-' + self.suffix(self.root)))

    def test_state_worktree_absolute_is_kept(self):
        recorded = str(self.work / 'x' / 'task-1')
        self.assertEqual(workdir.state_worktree(self.root, {'worktree': recorded}), Path(recorded))

    def test_state_worktree_relative_is_read_from_the_root(self):
        self.assertEqual(
            workdir.state_worktree(self.root, {'worktree': '.workspace/integration/task-1'}), self.root / '.workspace/integration/task-1'
        )

    def test_state_worktree_missing_or_invalid_is_refused(self):
        for state in ({}, {'worktree': ''}, {'worktree': None}, {'worktree': 3}, None):
            with self.subTest(state=state), self.assertRaises(ValueError):
                workdir.state_worktree(self.root, state)

    def test_is_clone_state(self):
        self.assertTrue(workdir.is_clone_state({'worktree': str(self.work / 'a')}))
        self.assertFalse(workdir.is_clone_state({'worktree': '.workspace/worktrees/a'}))
        self.assertFalse(workdir.is_clone_state({}))

    def test_containing_task(self):
        clone = workdir.task_worktree(self.root, 'task-1')
        (clone / 'src/deep').mkdir(parents=True)
        self.assertEqual(workdir.containing_task(self.root, clone / 'src/deep'), clone)
        self.assertEqual(workdir.containing_task(self.root, clone), clone)
        self.assertIsNone(workdir.containing_task(self.root, self.root))
        self.assertIsNone(workdir.containing_task(self.root, workdir.work_base(self.root)))

    def test_ensure_private_dir_creates_0700_parents(self):
        base = workdir.work_base(self.root)
        workdir.ensure_private_dir(base, base)
        self.assertEqual(base.stat().st_mode & 0o777, 0o700)

    def test_ensure_private_dir_creates_missing_folders_in_0700_whatever_the_umask(self):
        base = workdir.work_base(self.root)
        previous = os.umask(0)
        try:
            workdir.ensure_private_dir(base / 'a' / 'b', base)
        finally:
            os.umask(previous)
        for directory in (base, base / 'a', base / 'a' / 'b'):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700, directory)

    def test_group_or_world_writable_base_folder_is_refused_and_nothing_is_created(self):
        base = workdir.work_base(self.root)
        for mode in (0o777, 0o770, 0o707, 0o722, 0o775, 0o757):
            with self.subTest(mode=oct(mode)):
                self.work.chmod(mode)
                with self.assertRaises(ValueError) as caught:
                    workdir.ensure_private_dir(base, base)
                self.assertIn(str(self.work), str(caught.exception))
                self.assertIn('chmod 700', str(caught.exception))
                self.assertFalse(base.exists())
        self.work.chmod(0o700)
        workdir.ensure_private_dir(base, base)

    def test_readable_but_not_writable_base_folder_is_accepted(self):
        base = workdir.work_base(self.root)
        for mode in (0o700, 0o750, 0o755, 0o711):
            with self.subTest(mode=oct(mode)):
                self.work.chmod(mode)
                workdir.ensure_private_dir(base, base)

    def test_group_or_world_writable_project_folder_is_refused(self):
        base = workdir.work_base(self.root)
        workdir.ensure_private_dir(base, base)
        base.chmod(0o770)
        with self.assertRaises(ValueError) as caught:
            workdir.ensure_private_dir(base, base)
        self.assertIn(str(base), str(caught.exception))
        self.assertIn('chmod 700', str(caught.exception))
        base.chmod(0o700)
        workdir.ensure_private_dir(base, base)

    def test_base_folder_of_another_user_is_refused_and_nothing_is_created(self):
        base = workdir.work_base(self.root)
        with mock.patch.object(workdir.os, 'getuid', return_value=os.getuid() + 1):
            with self.assertRaises(ValueError) as caught:
                workdir.ensure_private_dir(base, base)
        self.assertIn(str(self.work), str(caught.exception))
        self.assertFalse(base.exists())

    def test_default_base_folder_in_home_is_checked_too(self):
        home = self.base / 'home'
        (home / '.workspace-ia/work').mkdir(parents=True)
        (home / '.workspace-ia/work').chmod(0o777)
        with mock.patch.dict(os.environ, {'HOME': str(home)}):
            os.environ.pop('WORKSPACE_WORK_DIR')
            base = workdir.work_base(self.root)
            with self.assertRaises(ValueError):
                workdir.ensure_private_dir(base, base)
        self.assertFalse(base.exists())

    def test_missing_default_base_folder_is_created_private(self):
        home = self.base / 'home'
        home.mkdir()
        with mock.patch.dict(os.environ, {'HOME': str(home)}):
            os.environ.pop('WORKSPACE_WORK_DIR')
            base = workdir.work_base(self.root)
            workdir.ensure_private_dir(base, base)
        self.assertEqual((home / '.workspace-ia/work').stat().st_mode & 0o777, 0o700)
        self.assertEqual(base.stat().st_mode & 0o777, 0o700)

    def test_symlink_below_base_is_refused_but_above_is_not(self):
        base = workdir.work_base(self.root)
        workdir.ensure_private_dir(base, base)
        elsewhere = self.base / 'elsewhere'
        elsewhere.mkdir()
        (base / 'task-1').symlink_to(elsewhere)
        with self.assertRaises(ValueError):
            workdir.check_no_symlinks_below(base, base / 'task-1')
        with self.assertRaises(ValueError):
            workdir.check_no_symlinks_below(base, elsewhere)

    def test_git_command_is_hardened(self):
        command = workdir.git_command('status', cwd=Path('/x'))
        self.assertEqual(command[0], 'git')
        self.assertIn('core.hooksPath=/dev/null', command)
        self.assertIn('core.fsmonitor=false', command)
        self.assertEqual(command[-3:], ['-C', '/x', 'status'])


if __name__ == '__main__':
    unittest.main()
