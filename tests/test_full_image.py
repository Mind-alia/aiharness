from __future__ import annotations

import importlib.util
import json
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

import workdir  # noqa: E402

TEST_TMP = Path(os.environ.get('WORKSPACE_TEST_TMP') or ROOT / '.workspace/tmp-tests')
BUILD_KIT = 'docker build -t workspace-node-git:24-bookworm -f template/docker/node-git.Dockerfile template/docker'
BUILD_PROJECT = 'docker build -t workspace-node-git:24-bookworm -f docker/node-git.Dockerfile docker'

FAKE_DOCKER = '''#!/usr/bin/env python3
import os, sys

if sys.argv[1:4] == ['image', 'inspect', '--format'] and os.environ.get('FAKE_IMAGE_PRESENT') == '1':
    print('sha256:fake-image-id')
    sys.exit(0)
sys.exit(1 if sys.argv[1:2] == ['image'] else 0)
'''


class FullImageTests(unittest.TestCase):
    def setUp(self):
        TEST_TMP.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.temp.cleanup)
        self.root = (Path(self.temp.name) / 'node project').resolve()
        self.root.mkdir(parents=True)

        scripts_dir = self.root / 'scripts'
        (scripts_dir / 'lib').mkdir(parents=True)
        for lib_file in (ROOT / 'template/scripts/lib').glob('*.py'):
            shutil.copy2(lib_file, scripts_dir / 'lib' / lib_file.name)
        shutil.copy2(ROOT / 'template/scripts/task-workspace.sh', scripts_dir / 'task-workspace.sh')
        self.git('init', '-q')
        (self.root / '.gitignore').write_text('.workspace/\ntasks/\n__pycache__/\n')
        (self.root / 'project.profile').write_text('node\n')
        (self.root / 'src').mkdir()
        (self.root / 'src/app.py').write_text('print("initial")\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial commit')

        self.bin_dir = self.temp.name + '-bin'
        Path(self.bin_dir).mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.bin_dir, True)
        docker = Path(self.bin_dir) / 'docker'
        docker.write_text(FAKE_DOCKER, encoding='utf-8')
        docker.chmod(0o755)

        # Les clones de tâche vont hors du dépôt, jamais dans le vrai ~/.workspace-ia.
        self.work_dir = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.work_dir.cleanup)
        self.env = dict(
            os.environ, PATH=self.bin_dir + os.pathsep + os.environ.get('PATH', ''), WORKSPACE_WORK_DIR=os.path.realpath(self.work_dir.name)
        )
        self.env.pop('WORKSPACE_IMAGE', None)
        self.env.pop('WORKSPACE_IMAGE_NODE', None)
        self.env.pop('FAKE_IMAGE_PRESENT', None)

    def git(self, *args):
        return subprocess.check_output(
            ['git', '-C', str(self.root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'core.hooksPath=', *args],
            text=True,
        )

    def commit_file(self, rel_path, content):
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        self.git('add', rel_path)
        self.git('commit', '-qm', f'add {rel_path}')

    def write_task(self, task_id='task-1', extra_header=''):
        task_file = self.root / f'tasks/{task_id}.md'
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text(
            f"---\nid: {task_id}\nagent: codex\nallowed:\n  - src/**\n{extra_header}---\nConsigne de test\n", encoding='utf-8'
        )
        return task_file

    def prepare(self, task_file, *args, image_present=False, extra_env=None):
        env = dict(self.env)
        if image_present:
            env['FAKE_IMAGE_PRESENT'] = '1'
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ['bash', str(self.root / 'scripts/task-workspace.sh'), 'prepare', str(task_file), *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=env,
        )

    def state_of(self, task_id='task-1'):
        state_file = self.root / '.workspace/orchestration/tasks' / task_id / 'state.json'
        return json.loads(state_file.read_text(encoding='utf-8'))

    def assert_nothing_created(self, task_id='task-1'):
        self.assertEqual(self.git('branch', '--list', f'workspace-task/{task_id}').strip(), '')
        self.assertFalse((self.root / '.workspace/worktrees' / task_id).exists())
        with mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': self.env['WORKSPACE_WORK_DIR']}):
            self.assertFalse(workdir.task_worktree(self.root, task_id).exists())
        self.assertFalse((self.root / '.workspace/orchestration/tasks' / task_id).exists())

    def test_absent_image_refuses_in_one_line_before_any_creation(self):
        res = self.prepare(self.write_task())
        self.assertEqual(res.returncode, 3, res.stderr)
        lines = res.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, res.stderr)
        self.assertIn(BUILD_KIT, lines[0])
        self.assertIn('--allow-slim-image', lines[0])
        self.assertIn('workspace-node-git:24-bookworm absente', lines[0])
        self.assert_nothing_created()

    def test_refusal_names_project_recipe_when_project_ships_one(self):
        self.commit_file('docker/node-git.Dockerfile', 'FROM node:24-bookworm-slim\n')
        res = self.prepare(self.write_task())
        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertIn(BUILD_PROJECT, res.stderr)
        self.assertNotIn(BUILD_KIT, res.stderr)
        self.assert_nothing_created()

    def test_allow_slim_image_prepares_and_records_the_flag(self):
        res = self.prepare(self.write_task(), '--allow-slim-image')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIs(self.state_of()['allow_slim_image'], True)

    def test_without_flag_the_state_has_no_allow_key(self):
        res = self.prepare(self.write_task(), image_present=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn('allow_slim_image', self.state_of())

    def test_present_image_prepares_normally(self):
        res = self.prepare(self.write_task(), image_present=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stderr, '')
        self.assertEqual(self.state_of()['status'], 'prepared')

    def test_workspace_image_node_skips_the_check(self):
        res = self.prepare(self.write_task(), extra_env={'WORKSPACE_IMAGE_NODE': 'custom:node'})
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_isolation_none_skips_the_check(self):
        res = self.prepare(self.write_task(extra_header='isolation: none\n'))
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_python_profile_skips_the_check(self):
        self.git('rm', '-q', 'project.profile')
        (self.root / 'project.profile').write_text('python\n')
        self.git('add', 'project.profile')
        self.git('commit', '-qm', 'python profile')
        res = self.prepare(self.write_task())
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_build_command_follows_the_project_recipe(self):
        spec = importlib.util.spec_from_file_location('sandbox_full', ROOT / 'template/scripts/lib/sandbox.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.node_build_command(self.root), BUILD_KIT)
        (self.root / 'docker').mkdir()
        (self.root / 'docker/node-git.Dockerfile').write_text('FROM node:24-bookworm-slim\n')
        self.assertEqual(module.node_build_command(self.root), BUILD_PROJECT)


if __name__ == '__main__':
    unittest.main()
