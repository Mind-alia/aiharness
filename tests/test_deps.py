"""Dependencies contract exercised through the real sandbox CLI and stateful Docker."""

import json
import os
import shlex
import subprocess
import unittest

import workdir  # le module test_sandbox ajoute template/scripts/lib au chemin
from test_sandbox import SandboxTests as _SandboxTests

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel, sous-processus ou Docker simulé ; suite complète seulement')


class DepsTests(unittest.TestCase):
    # Share the existing CLI fixture, without inheriting its test cases.
    setUp = _SandboxTests.setUp
    git = _SandboxTests.git
    setup_fake_docker = _SandboxTests.setup_fake_docker
    get_docker_calls = _SandboxTests.get_docker_calls
    prepare_task = _SandboxTests.prepare_task
    run_sandbox = _SandboxTests.run_sandbox

    def node_task(self, workspace=False):
        (self.root / 'package.json').write_text(
            json.dumps({'name': 'fixture', 'workspaces': ['packages/*']} if workspace else {'name': 'fixture'})
        )
        packages = {'': {'name': 'fixture'}}
        if workspace:
            (self.root / 'packages/a').mkdir(parents=True)
            (self.root / 'packages/a/package.json').write_text('{"name":"a"}')
            packages['packages/a'] = {'name': 'a'}
            packages['packages/a/node_modules/other'] = {'version': '1.0.0'}
        (self.root / 'package-lock.json').write_text(json.dumps({'lockfileVersion': 3, 'packages': packages}))
        self.git('add', '.')
        self.git('commit', '-qm', 'dependencies')
        task = self.prepare_task(profile='node', network='on')
        self.wt = workdir.task_worktree(self.root, task)
        return task

    def installs(self):
        return [c for c in self.get_docker_calls() if c[:1] == ['run'] and 'workspace.role=deps' in c]

    def test_deps_reuse_fingerprint_and_rebuild(self):
        task = self.node_task()
        for _ in range(2):
            res = self.run_sandbox('deps', task)
            self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(len(self.installs()), 1)
        names = [c[-1] for c in self.get_docker_calls() if c[:2] == ['volume', 'create']]
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r'^workspace-deps-[a-z0-9-]+-[a-f0-9]{16}$')
        (self.wt / 'package-lock.json').write_text('{"lockfileVersion":3,"packages":{},"changed":true}')
        self.assertEqual(self.run_sandbox('deps', task).returncode, 0)
        self.assertEqual(len(self.installs()), 2)
        self.assertEqual(self.run_sandbox('deps', '--rebuild', task).returncode, 0)
        self.assertEqual(len(self.installs()), 3)
        self.assertTrue(any(c[:2] == ['volume', 'rm'] for c in self.get_docker_calls()))

    def test_start_auto_deps_workspace_mounts_and_network(self):
        task = self.node_task(workspace=True)
        res = self.run_sandbox('start', task)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(len(self.installs()), 1)
        install = self.installs()[0]
        self.assertEqual(install[install.index('--network') + 1], 'bridge')
        self.assertIn('--read-only', install)
        self.assertIn('ALL', install)
        self.assertIn('no-new-privileges', install)
        self.assertEqual(install[install.index('--user') + 1], '1000:1000')
        self.assertIn(str(self.wt.resolve()) + ':/source:ro', install)
        self.assertIn('npm ci', ' '.join(install))
        task_run = next(c for c in self.get_docker_calls() if 'workspace.role=sandbox' in c)
        self.assertEqual(task_run[task_run.index('--network') + 1], 'none')
        self.assertIn('dst=/work/node_modules', ' '.join(task_run))
        self.assertIn('dst=/work/packages/a/node_modules', ' '.join(task_run))
        self.assertIn('volume-subpath=deps/work/packages/a/node_modules', ' '.join(task_run))
        self.assertIn('readonly', ' '.join(task_run))
        for path in ('node_modules', 'packages/a/node_modules'):
            mount = next(arg for arg in task_run if f'dst=/work/{path},' in arg)
            self.assertIn(',readonly,', mount)
            for cache in ('.vite-temp', '.vite', '.cache', '.vitest', '.vitest-cache'):
                self.assertIn(
                    f'/work/{path}/{cache}:rw,size=256m,uid=1000,gid=1000,mode=0700',
                    [task_run[i + 1] for i, arg in enumerate(task_run) if arg == '--tmpfs'],
                )
        for call in self.get_docker_calls():
            if call[:1] == ['run'] and '--network' in call:
                mode = call[call.index('--network') + 1]
                self.assertEqual(mode, 'bridge' if 'workspace.role=deps' in call else 'none')

    def test_failure_masked_logged_no_task_container_or_cached_volume(self):
        task = self.node_task()
        res = self.run_sandbox('start', task, env={'FAIL_DEPS': '1'})
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('installation', res.stderr.lower())
        self.assertNotIn('super-secret-token-12345', res.stdout + res.stderr)
        self.assertFalse(any('workspace.role=sandbox' in c for c in self.get_docker_calls()))
        logs = list((self.root / '.workspace/orchestration/tasks' / task / 'sandbox').glob('deps-*.log'))
        self.assertTrue(logs)
        self.assertNotIn('super-secret-token-12345', logs[0].read_text())
        self.assertIn('***', logs[0].read_text())
        self.assertEqual(self.run_sandbox('deps', task).returncode, 0)
        self.assertEqual(len(self.installs()), 2)

    def test_cache_tmpfs_uses_configured_container_identity(self):
        task = self.node_task(workspace=True)
        res = self.run_sandbox('start', task, env={'WORKSPACE_SANDBOX_USER': '1234:5678'})
        self.assertEqual(res.returncode, 0, res.stderr)
        run = next(c for c in self.get_docker_calls() if 'workspace.role=sandbox' in c)
        mounts = [run[i + 1] for i, arg in enumerate(run) if arg == '--tmpfs']
        # 5 caches x 2 node_modules + /tmp + 5 dossiers de travail en mémoire (sandbox.WRITABLE_DIRS['node'])
        self.assertEqual(len(mounts), 16)
        for name in ('.workspace', 'dist', 'coverage', 'test-results', 'playwright-report'):
            self.assertIn(f'/work/{name}:rw,exec,size=256m,mode=1777', mounts)
        for path in ('node_modules', 'packages/a/node_modules'):
            for cache in ('.vite-temp', '.vite', '.cache', '.vitest', '.vitest-cache'):
                self.assertIn(f'/work/{path}/{cache}:rw,size=256m,uid=1234,gid=5678,mode=0700', mounts)

    def test_no_lock_no_volume(self):
        task = self.prepare_task(profile='node')
        res = self.run_sandbox('deps', task)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('aucun', res.stdout.lower())
        self.assertFalse(any(c[:1] == ['volume'] for c in self.get_docker_calls()))

    def test_python_venv_install_and_mount(self):
        (self.root / 'requirements.txt').write_text('six==1.17.0\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'requirements')
        task = self.prepare_task(profile='python')
        res = self.run_sandbox('start', task)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(len(self.installs()), 1)
        self.assertIn('python -m venv /deps/.venv', ' '.join(self.installs()[0]))
        run = next(c for c in self.get_docker_calls() if 'workspace.role=sandbox' in c)
        self.assertIn('dst=/work/.venv', ' '.join(run))
        self.assertIn('VIRTUAL_ENV=/deps/.venv', run)

    def python_task(self):
        (self.root / '.gitignore').write_text('.workspace/\ntasks/\n__pycache__/\n.venv/\n')
        (self.root / 'uv.lock').write_text('version = 1\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'lock')
        task = self.prepare_task(profile='python')
        return task, workdir.task_worktree(self.root, task)

    def test_python_mount_point_exists_in_read_only_clone(self):
        # E14: `.venv` is ignored by Git, so absent from the clone, and Docker cannot create it under a read-only /work.
        task, wt = self.python_task()
        self.assertFalse((wt / '.venv').exists())
        res = self.run_sandbox('start', task)
        self.assertEqual(res.returncode, 0, res.stderr)
        run = next(c for c in self.get_docker_calls() if 'workspace.role=sandbox' in c)
        self.assertIn(f'{wt.resolve()}:/work:ro', run)
        mounts = [arg for arg in run if arg.startswith('type=volume,') and ',dst=/work/' in arg]
        self.assertTrue(mounts)
        for mount in mounts:
            relative = mount.split(',dst=/work/')[1].split(',')[0]
            self.assertTrue((wt / relative).is_dir(), relative)
        self.assertEqual(list((wt / '.venv').iterdir()), [])
        status = subprocess.run(['git', 'status', '--porcelain'], cwd=wt, capture_output=True, text=True)
        self.assertEqual(status.stdout, '')

    def test_python_mount_point_refuses_symlink_and_file(self):
        task, wt = self.python_task()
        (wt / '.venv').symlink_to(self.root)
        res = self.run_sandbox('start', task)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('.venv', res.stderr)
        (wt / '.venv').unlink()
        (wt / '.venv').write_text('x')
        res = self.run_sandbox('start', task)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('.venv', res.stderr)
        self.assertFalse(any('workspace.role=sandbox' in c for c in self.get_docker_calls()))

    def test_image_changes_fingerprint(self):
        task = self.node_task()
        self.assertEqual(self.run_sandbox('deps', task).returncode, 0)
        self.assertEqual(self.run_sandbox('deps', task, env={'WORKSPACE_IMAGE': 'node:24-bookworm'}).returncode, 0)
        self.assertEqual(len(self.installs()), 2)

    def test_install_script_excludes_host_modules_and_prepares_workspace_dirs(self):
        task = self.node_task(workspace=True)
        (self.wt / 'node_modules').mkdir()
        (self.wt / 'node_modules/mac-native.node').write_text('Mach-O')
        (self.wt / '.env').write_text('PRIVATE=secret')
        self.assertEqual(self.run_sandbox('deps', task).returncode, 0)
        script = self.installs()[0][-1]
        destination = self.root.parent / 'linux-volume'
        destination.mkdir()
        temp = self.root.parent / 'install-tmp'
        temp.mkdir()
        npm = self.bin_dir / 'npm'
        npm.write_text(
            '#!/bin/sh\n[ "$1" = ci ] || exit 3\n[ ! -e node_modules/mac-native.node ] || exit 4\n[ ! -e .git ] || exit 5\n[ ! -e .env ] || exit 6\nmkdir -p node_modules\necho Linux > node_modules/binding.node\n'
        )
        npm.chmod(0o755)
        script = (
            script.replace('/tmp/source.tar', shlex.quote(str(temp / 'archive.tar')))
            .replace('/source', shlex.quote(str(self.wt)))
            .replace('/deps', shlex.quote(str(destination)))
        )
        proc = subprocess.run(['sh', '-ec', script], env=self.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((destination / 'work/node_modules/binding.node').read_text().strip(), 'Linux')
        self.assertTrue((destination / 'work/packages/a/node_modules').is_dir())
        for path in ('node_modules', 'packages/a/node_modules'):
            for cache in ('.vite-temp', '.vite', '.cache', '.vitest', '.vitest-cache'):
                directory = destination / 'work' / path / cache
                self.assertTrue(directory.is_dir(), str(directory))
                self.assertEqual(list(directory.iterdir()), [])
        self.assertEqual((self.wt / 'node_modules/mac-native.node').read_text(), 'Mach-O')

    def test_workspace_traversal_and_symlink_lock_refused(self):
        task = self.node_task()
        for bad in ('../escape', '/absolute'):
            (self.wt / 'package-lock.json').write_text(json.dumps({'packages': {bad: {}}}))
            res = self.run_sandbox('start', task)
            self.assertNotEqual(res.returncode, 0)
            self.assertFalse(any('workspace.role=sandbox' in c for c in self.get_docker_calls()))
        (self.wt / 'package-lock.json').unlink()
        (self.wt / 'package-lock.json').symlink_to(self.root / 'package-lock.json')
        self.assertNotEqual(self.run_sandbox('deps', task).returncode, 0)

    def test_python_all_manifests_and_profile_affect_fingerprint(self):
        task = self.prepare_task(profile='python')
        wt = workdir.task_worktree(self.root, task)
        for name in ('requirements.txt', 'requirements-dev.txt', 'uv.lock', 'poetry.lock', 'pyproject.toml'):
            (wt / name).write_text('fixture')
            self.assertEqual(self.run_sandbox('deps', task).returncode, 0)
        self.assertEqual(len(self.installs()), 5)
        (wt / 'package.json').write_text('{"name":"fixture"}')
        (wt / 'package-lock.json').write_text('{"lockfileVersion":3,"packages":{}}')
        (self.root / 'project.profile').write_text('node')
        self.assertEqual(self.run_sandbox('deps', task).returncode, 0)
        self.assertEqual(len(self.installs()), 6)


del _SandboxTests
