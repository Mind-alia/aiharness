from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = Path(os.environ.get('WORKSPACE_TEST_TMP') or ROOT / '.workspace/tmp-tests')


class MissionCheckTests(unittest.TestCase):
    def setUp(self):
        TEST_TMP.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.temp.cleanup)
        self.root = (Path(self.temp.name) / 'project').resolve()
        self.scripts_dir = self.root / 'scripts'
        self.lib_dir = self.scripts_dir / 'lib'
        self.lib_dir.mkdir(parents=True)
        for lib_file in (ROOT / 'template/scripts/lib').glob('*.py'):
            shutil.copy2(lib_file, self.lib_dir / lib_file.name)
        shutil.copy2(ROOT / 'template/scripts/task-workspace.sh', self.scripts_dir / 'task-workspace.sh')

        self.git('init', '-q')
        (self.root / '.gitignore').write_text('.workspace/\ntasks/\n')
        (self.root / 'README.md').write_text('# Test project\n')
        (self.root / 'src').mkdir()
        (self.root / 'src/app.py').write_text('print("initial")\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial commit')

    def git(self, *args):
        cmd = ['git', '-C', str(self.root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'core.hooksPath=', *args]
        return subprocess.check_output(cmd, text=True)

    def write_task(self, rel_path, task_id, allowed):
        task_file = self.root / rel_path
        task_file.parent.mkdir(parents=True, exist_ok=True)
        allowed_lines = '\n'.join(f'  - {p}' for p in allowed)
        content = f"---\nid: {task_id}\nagent: codex\nallowed:\n{allowed_lines}\n---\nConsigne test\n"
        task_file.write_text(content, encoding='utf-8')
        return task_file

    def run_cli(self, *args):
        return subprocess.run(
            ['bash', str(self.scripts_dir / 'task-workspace.sh'), *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )

    def test_check_reports_conflict_with_prepared_task_and_returns_3(self):
        self.write_task('tasks/task-1.md', 'task-1', ['src/**'])
        prepared = self.run_cli('prepare', 'tasks/task-1.md')
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.write_task('tasks/task-2.md', 'task-2', ['src/app.py'])

        result = self.run_cli('check', 'tasks/task-2.md')

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn('conflit : src/app.py chevauche la tâche task-1 (src/**)', result.stdout)

    def test_check_reports_free_task_and_returns_0(self):
        self.write_task('tasks/task-1.md', 'task-1', ['src/**'])
        prepared = self.run_cli('prepare', 'tasks/task-1.md')
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.write_task('tasks/task-2.md', 'task-2', ['docs/**'])

        result = self.run_cli('check', 'tasks/task-2.md')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('libre : task-2', result.stdout)

    def test_check_on_fresh_project_returns_0_and_creates_nothing(self):
        self.write_task('tasks/task-1.md', 'task-1', ['src/**'])
        before = sorted(p.name for p in self.root.iterdir())

        result = self.run_cli('check', 'tasks/task-1.md')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('libre : task-1', result.stdout)
        self.assertFalse((self.root / '.workspace').exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_check_reports_protected_pattern_and_returns_3(self):
        self.write_task('tasks/task-1.md', 'task-1', ['scripts/**'])

        result = self.run_cli('check', 'tasks/task-1.md')

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn('protégé : scripts/**', result.stdout)

    def test_check_without_task_argument_returns_64(self):
        result = self.run_cli('check')

        self.assertEqual(result.returncode, 64)
        self.assertIn('check <task.md>', result.stderr)


if __name__ == '__main__':
    unittest.main()
