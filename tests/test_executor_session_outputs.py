import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import executor_guard  # noqa: E402

SESSION = 'dd7c9422-1c89-4018-93a3-c86fb6345339'
OTHER_SESSION = 'not-a-uuid'
UPPER_SESSION = SESSION.upper()


class SessionOutputTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.worktree = base / 'proj_1' / 'wt'
        self.worktree.mkdir(parents=True)
        (self.worktree.parent / 'AGENTS.md').write_text('secret\n')
        self.config = base / 'config'
        self.config.mkdir()
        patcher = mock.patch.dict(os.environ, {executor_guard.CONFIG_DIR_VARIABLE: str(self.config)})
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp_root, config_root = [Path(r) for r in executor_guard.session_output_roots(str(self.worktree))]
        self.tmp_root, self.config_root = tmp_root, config_root
        self.addCleanup(shutil.rmtree, str(tmp_root), True)
        self.addCleanup(self._remove_empty_uid_dir)

    def _remove_empty_uid_dir(self):
        try:
            self.tmp_root.parent.rmdir()
        except OSError:
            pass

    def make(self, root, *parts):
        path = root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('out\n')
        return path

    def decide(self, tool, path):
        key = 'path' if tool in ('Glob', 'Grep', 'LS') else 'file_path'
        return executor_guard.decide({'tool_name': tool, 'tool_input': {key: str(path)}, 'session_id': SESSION}, str(self.worktree))

    def test_slug(self):
        self.assertEqual(executor_guard.project_slug('/Users/a/.workspace-ia/work/p_1'), '-Users-a--workspace-ia-work-p-1')

    def test_roots(self):
        slug = executor_guard.project_slug(str(self.worktree))
        self.assertEqual(self.tmp_root, Path(os.path.realpath('/tmp')) / ('claude-%d' % os.getuid()) / slug)
        self.assertEqual(self.config_root, Path(os.path.realpath(str(self.config))) / 'projects' / slug)

    def test_relative_config_dir_ignored(self):
        with mock.patch.dict(os.environ, {executor_guard.CONFIG_DIR_VARIABLE: 'rel/dir'}):
            roots = executor_guard.session_output_roots(str(self.worktree))
        home = os.path.realpath(os.path.expanduser('~/.claude'))
        self.assertTrue(roots[1].startswith(home + os.sep))

    def test_tmp_task_output_read_only(self):
        path = self.make(self.tmp_root, SESSION, 'tasks', 'b1.output')
        self.assertIsNone(self.decide('Read', path))
        self.assertIsNone(
            self.make(self.tmp_root, SESSION, 'tool-results', 'r.txt')
            and self.decide('Read', self.tmp_root / SESSION / 'tool-results' / 'r.txt')
        )
        for tool in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Glob', 'Grep', 'LS', 'NotebookRead'):
            self.assertIn('hors du worktree', self.decide(tool, path) or '', tool)

    def test_config_tool_results(self):
        path = self.make(self.config_root, SESSION, 'tool-results', 'r.txt')
        self.assertIsNone(self.decide('Read', path))
        self.assertIsNotNone(self.decide('Write', path))
        self.assertIsNotNone(self.decide('Grep', path))

    def test_config_refusals(self):
        transcript = self.make(self.config_root, SESSION + '.jsonl')
        tasks = self.make(self.config_root, SESSION, 'tasks', 'x')
        other = self.make(self.config, 'settings.json')
        for path in (transcript, tasks, other):
            self.assertIn('hors du worktree', self.decide('Read', path))

    def test_tmp_refusals(self):
        other_slug = self.tmp_root.parent / (self.tmp_root.name + '-other')
        self.addCleanup(shutil.rmtree, str(other_slug), True)
        cases = [
            self.make(other_slug, SESSION, 'tasks', 'x'),
            self.make(self.tmp_root, SESSION, 'scratchpad', 'x'),
            self.make(self.tmp_root, OTHER_SESSION, 'tasks', 'x'),
            self.make(self.tmp_root, UPPER_SESSION, 'tasks', 'x'),
            self.make(self.tmp_root, SESSION, 'tasks', 'x'),
        ]
        self.make(self.tmp_root, 'x')
        cases[-1] = self.tmp_root / SESSION / 'tasks' / '..' / '..' / 'x'
        cases.append(self.tmp_root / SESSION / 'tasks')
        cases.append(self.tmp_root / SESSION / 'tool-results')
        for path in cases:
            self.assertIn('hors du worktree', self.decide('Read', path), str(path))

    def test_symlink_out_of_tasks_refused(self):
        tasks = self.make(self.tmp_root, SESSION, 'tasks', 'b1.output').parent
        link = tasks / 'l'
        link.symlink_to(self.worktree.parent / 'AGENTS.md')
        self.assertIn('hors du worktree', self.decide('Read', link))

    def test_is_session_output_direct(self):
        wt = str(self.worktree)
        good = str(self.tmp_root / SESSION / 'tasks' / 'a')
        self.assertTrue(executor_guard.is_session_output(good, wt, SESSION))
        self.assertFalse(executor_guard.is_session_output(good, wt))
        self.assertFalse(executor_guard.is_session_output(str(self.tmp_root / SESSION / 'tasks'), wt, SESSION))
        self.assertFalse(executor_guard.is_session_output(str(self.tmp_root / SESSION / 'tasks') + 'x/a', wt, SESSION))


if __name__ == '__main__':
    unittest.main()
