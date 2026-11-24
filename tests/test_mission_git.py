"""Task clone: what it inherits from the project (E15)."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import mission_git  # noqa: E402
import workdir  # noqa: E402

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel et sous-processus ; suite complète seulement')

TASK = '---\nid: scope-test\nagent: codex\nallowed:\n  - src/**\n---\nTask\n'


class TaskCloneExcludesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.temp.cleanup)
        base = Path(os.path.realpath(self.temp.name))
        patcher = mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': str(base / 'work')})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = base / 'project'
        shutil.copytree(ROOT / 'template/scripts', self.root / 'scripts')
        self.git(self.root, 'init', '-q', '-b', 'main')
        # Adopted mission: the client's .gitignore does not name `.workspace/`, only the local exclude does.
        (self.root / '.gitignore').write_text('*.pyc\n')
        (self.root / 'task.md').write_text(TASK)
        (self.root / 'src').mkdir()
        (self.root / 'src/a.txt').write_text('original')
        self.git(self.root, 'add', '.')
        self.git(self.root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')

    def git(self, cwd, *args):
        return subprocess.check_output(['git', '-C', str(cwd), *args], text=True)

    def clone(self, exclude):
        if exclude is not None:
            (self.root / '.git/info').mkdir(exist_ok=True)
            (self.root / '.git/info/exclude').write_text(exclude)
        self.git(self.root, 'branch', 'workspace-task/scope-test')
        clone = workdir.task_worktree(self.root, 'scope-test')
        mission_git.create_task_clone(self.root, 'workspace-task/scope-test', clone)
        return clone

    def scope(self, clone, name):
        return subprocess.run([str(clone / 'scripts' / name), 'task.md'], cwd=clone, text=True, capture_output=True)

    def exchange(self, clone):
        for name in ('requests/001.json', 'results/001.json'):
            path = clone / '.workspace/exchange' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')

    def test_circuit_files_stay_out_of_the_scope_check(self):
        # E15: a conforming delegation ended `out_of_scope` on `.workspace/exchange/{requests,results}/00N.json`.
        clone = self.clone('# local\n.workspace/\n')
        self.assertEqual(self.scope(clone, 'scope-baseline.sh').returncode, 0)
        self.exchange(clone)
        (clone / 'src/a.txt').write_text('changed')
        result = self.scope(clone, 'check-scope.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git(clone, 'status', '--porcelain', '--untracked-files=all').split(), ['M', 'src/a.txt'])
        self.assertIn('.workspace/', (clone / '.git/info/exclude').read_text().splitlines())

    def test_scope_check_still_refuses_other_files(self):
        clone = self.clone('.workspace/\n')
        self.assertEqual(self.scope(clone, 'scope-baseline.sh').returncode, 0)
        self.exchange(clone)
        (clone / 'outside.txt').write_text('new')
        result = self.scope(clone, 'check-scope.sh')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual([line for line in result.stdout.splitlines() if line.startswith('hors')], ['hors périmètre : outside.txt'])

    def test_without_local_exclude_nothing_is_added(self):
        (self.root / '.git/info/exclude').unlink(missing_ok=True)
        clone = self.clone(None)
        text = (clone / '.git/info/exclude').read_text() if (clone / '.git/info/exclude').is_file() else ''
        self.assertNotIn('.workspace/', text.splitlines())


if __name__ == '__main__':
    unittest.main()
