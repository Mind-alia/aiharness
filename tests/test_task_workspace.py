from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel, sous-processus ou Docker simulé ; suite complète seulement')

ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = ROOT / '.workspace/tmp-tests'
TEST_TMP.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import workdir  # noqa: E402


class TaskWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.temp.cleanup)
        # Les clones de tâche vont hors du dépôt et du projet, jamais dans le vrai ~/.workspace-ia.
        self.work_dir = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.work_dir.cleanup)
        patcher = mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': os.path.realpath(self.work_dir.name)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = (Path(self.temp.name) / 'project with spaces').resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.scripts_dir = self.root / 'scripts'
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.lib_dir = self.scripts_dir / 'lib'
        self.lib_dir.mkdir(parents=True, exist_ok=True)

        lib_source = ROOT / 'template/scripts/lib'
        if lib_source.exists():
            for lib_file in lib_source.glob('*.py'):
                shutil.copy2(lib_file, self.lib_dir / lib_file.name)

        source_cli = ROOT / 'template/scripts/task-workspace.sh'
        if source_cli.exists():
            shutil.copy2(source_cli, self.scripts_dir / 'task-workspace.sh')
            (self.scripts_dir / 'task-workspace.sh').chmod(0o755)

        self.git('init', '-q')
        (self.root / '.gitignore').write_text('.workspace/\ntasks/\n')
        (self.root / 'README.md').write_text('# Test project\n')
        (self.root / 'src').mkdir()
        (self.root / 'src/app.py').write_text('print("initial")\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial commit')
        self.base_commit = self.git('rev-parse', 'HEAD').strip()

    def clone(self, task_id='task-1', root=None):
        """Dossier de travail (clone sans historique) d'une tâche du projet."""
        return workdir.task_worktree(root or self.root, task_id)

    def adopt(self, task_id='task-1'):
        """Le projet récupère la branche du clone, comme le font merge et close."""
        ref = 'refs/heads/workspace-task/' + task_id
        self.git('fetch', '-q', self.clone(task_id).as_uri(), ref + ':' + ref)

    def git(self, *args, cwd=None):
        cmd = [
            'git',
            '-C',
            str(cwd or self.root),
            '-c',
            'user.name=Test',
            '-c',
            'user.email=test@example.invalid',
            '-c',
            'core.hooksPath=',
            *args,
        ]
        return subprocess.check_output(cmd, text=True)

    def write_task(self, rel_path='tasks/task-1.md', task_id='task-1', agent='codex', allowed=None, body='Consigne test'):
        if allowed is None:
            allowed = ['src/**']
        task_file = self.root / rel_path
        task_file.parent.mkdir(parents=True, exist_ok=True)
        allowed_lines = '\n'.join(f'  - {p}' for p in allowed)
        content = f"---\nid: {task_id}\nagent: {agent}\nallowed:\n{allowed_lines}\n---\n{body}\n"
        task_file.write_text(content, encoding='utf-8')
        return task_file

    def run_cli(self, *args, cwd=None, env=None):
        target = self.scripts_dir / 'task-workspace.sh'
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        return subprocess.run(['bash', str(target), *args], cwd=str(cwd or self.root), capture_output=True, text=True, env=run_env)

    def prepare_close_task(self, commit=False):
        self.write_task()
        result = self.run_cli('prepare', 'tasks/task-1.md')
        self.assertEqual(result.returncode, 0, result.stderr)
        worktree = self.clone()
        if commit:
            (worktree / 'src/app.py').write_text('changed\n')
            self.git('add', 'src/app.py', cwd=worktree)
            self.git('commit', '-qm', 'task change', cwd=worktree)
        return worktree

    def close_state(self):
        return json.loads((self.root / '.workspace/orchestration/tasks/task-1/state.json').read_text())

    def prepare_merge_task(self, verify='test -f src/app.py', isolation='none'):
        for name in ('verify-task.sh', 'sandbox.sh'):
            shutil.copy2(ROOT / 'template/scripts' / name, self.scripts_dir / name)
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('add', 'scripts')
        self.git('commit', '-qm', 'verification tooling')
        task = self.write_task(body='# feat: nouvelle fonction\nSuite')
        task.write_text(
            task.read_text().replace(
                'agent: codex',
                'agent: codex\nverify: ' + verify + '\nverify_profile: off' + ('' if isolation is None else '\nisolation: ' + isolation),
            )
        )
        # Ces tests visent la fusion, pas le refus précoce d'une image node incomplète (tests/test_full_image.py).
        result = self.run_cli('prepare', str(task), '--allow-slim-image')
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.clone()

    def test_tooling_installs_breaker(self):
        result = subprocess.run(['python3', str(ROOT / 'scripts/workspace.py'), 'tooling', str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.scripts_dir / 'breaker.sh').is_file())
        self.assertTrue((self.lib_dir / 'breaker.py').is_file())

    def test_merge_reads_promoted_breaker_even_when_verification_skipped(self):
        worktree = self.prepare_merge_task()
        hidden = self.root / '.workspace/acceptance/task-1'
        fixture = hidden / 'breaker/src/__breaker__/test_extreme.py'
        fixture.parent.mkdir(parents=True)
        fixture.write_text('import unittest\nclass Extreme(unittest.TestCase):\n def test_extreme(self): self.assertEqual(1, 2)\n')
        (hidden / 'extra.txt').write_text('src/__breaker__/test_extreme.py\n')
        (worktree / 'src/app.py').write_text('changed\n')
        before = self.git('rev-parse', 'HEAD')
        result = self.run_cli('merge', 'task-1', '--no-verify-again')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('acceptance_failed', result.stderr)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertFalse((worktree / 'src/__breaker__').exists())
        self.assertNotIn('__breaker__', self.git('ls-files', cwd=worktree))

    def test_merge_acceptance_combined_failure(self):
        worktree = self.prepare_merge_task()
        hidden = self.root / '.workspace/acceptance/task-1'
        hidden.mkdir(parents=True)
        (hidden / 'hidden.sh').write_text('test ! -f target.txt\n')
        task = self.root / 'tasks/task-1.md'
        # The registered task is the merge contract.
        task.write_text(task.read_text().replace('agent: codex', 'agent: codex\nacceptance: [hidden.sh]\nacceptance_verify: sh hidden.sh'))
        (worktree / 'src/app.py').write_text('changed\n')
        (self.root / 'target.txt').write_text('target\n')
        self.git('add', 'target.txt')
        self.git('commit', '-qm', 'target change')
        result = self.run_cli('merge', 'task-1', '--no-verify-again')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('acceptance_failed', result.stderr)
        self.assertFalse((worktree / 'hidden.sh').exists())
        self.assertNotIn('hidden.sh', self.git('ls-files', cwd=worktree).splitlines())

    def test_merge_acceptance_green_not_committed(self):
        worktree = self.prepare_merge_task()
        hidden = self.root / '.workspace/acceptance/task-1'
        hidden.mkdir(parents=True)
        (hidden / 'hidden.sh').write_text('test -f src/app.py\n')
        task = self.root / 'tasks/task-1.md'
        task.write_text(task.read_text().replace('agent: codex', 'agent: codex\nacceptance: [hidden.sh]\nacceptance_verify: sh hidden.sh'))
        (worktree / 'src/app.py').write_text('changed\n')
        result = self.run_cli('merge', 'task-1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((worktree / 'hidden.sh').exists())
        self.assertNotIn('hidden.sh', self.git('ls-tree', '-r', '--name-only', 'HEAD').splitlines())

    def test_merge_nominal(self):
        worktree = self.prepare_merge_task()
        (worktree / 'src/app.py').write_text('changed\n')
        self.git('add', '.', cwd=worktree)
        self.git('commit', '-qm', 'feat: task', cwd=worktree)
        result = self.run_cli('merge', 'task-1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'src/app.py').read_text(), 'changed\n')
        self.assertEqual(self.git('log', '-1', '--format=%s').strip(), 'merge: task task-1')
        self.assertEqual(len(self.git('rev-list', '--parents', '-n', '1', 'HEAD').split()), 3)
        self.assertFalse(worktree.exists())
        self.assertEqual(self.close_state()['status'], 'merged')
        self.assertNotIn('refs/heads/workspace-task/task-1', self.git('show-ref'))
        self.write_task('tasks/next.md', 'next')
        self.assertEqual(self.run_cli('prepare', 'tasks/next.md').returncode, 0)

    def integration_task(self, command=None, profile=None):
        if command is not None:
            (self.scripts_dir / 'gate.sh').write_text(command + '\n')
        if profile is not None:
            (self.root / 'project.profile').write_text(profile)
        self.git('add', '.')
        self.git('commit', '--allow-empty', '-qm', 'integration configuration')
        worktree = self.prepare_merge_task()
        (worktree / 'src/app.py').write_text('changed\n')
        return worktree

    def assert_integration_cleaned(self):
        path = self.root / '.workspace/integration/task-1'
        self.assertFalse(path.exists())
        self.assertNotIn(str(path), self.git('worktree', 'list', '--porcelain'))

    def test_integration_passes_exact_commit(self):
        self.integration_task('git rev-parse HEAD > "$CHECKED_COMMIT"; test -f src/app.py')
        checked = Path(self.temp.name) / 'checked'
        result = self.run_cli('merge', 'task-1', env={'CHECKED_COMMIT': str(checked)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('rev-parse', 'HEAD'), checked.read_text())
        self.assertEqual(self.close_state()['integration']['status'], 'passed')
        self.assert_integration_cleaned()

    def test_integration_failure_preserves_target(self):
        self.integration_task('for i in $(seq 1 45); do echo line-$i; done; echo API_TOKEN=ghp_private123; exit 42')  # gitleaks:allow
        before = self.git('rev-parse', 'HEAD')
        result = self.run_cli('merge', 'task-1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        state = self.close_state()
        self.assertEqual(state['status'], 'integration_failed')
        self.assertEqual(state['integration']['exit'], 42)
        self.assertEqual(len(state['integration']['output'].splitlines()), 40)
        self.assertNotIn('ghp_private123', json.dumps(state))
        self.assert_integration_cleaned()
        self.assertEqual(self.run_cli('status', 'task-1').returncode, 0)
        self.write_task('tasks/next.md', 'next')
        self.assertNotEqual(self.run_cli('prepare', 'tasks/next.md').returncode, 0)
        retry = self.run_cli('merge', 'task-1', '--no-integration')
        self.assertEqual(retry.returncode, 0, retry.stderr)

    def test_integration_none(self):
        self.integration_task()
        result = self.run_cli('merge', 'task-1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("aucune porte d'intégration configurée", result.stdout)
        self.assertEqual(self.close_state()['integration']['status'], 'none')

    def test_integration_skipped(self):
        self.integration_task('exit 42')
        result = self.run_cli('merge', 'task-1', '--no-integration')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.close_state()['integration']['status'], 'skipped')
        self.assert_integration_cleaned()

    def test_integration_target_moves(self):
        self.integration_task('git -C "$TARGET_ROOT" commit --allow-empty -qm "target advance"')
        result = self.run_cli('merge', 'task-1', env={'TARGET_ROOT': str(self.root)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('la cible a changé', result.stderr)
        self.assertEqual(self.git('log', '-1', '--format=%s').strip(), 'target advance')
        self.assert_integration_cleaned()

    def test_integration_config_priority_with_standard_profile(self):
        (self.root / '.workspace-integration').write_text('test -f src/app.py\n')
        self.integration_task('exit 42', 'node\n')
        task = self.root / 'tasks/task-1.md'
        task.write_text(task.read_text().replace('verify_profile: off', 'verify_profile: on\nverify_lint: off'))
        result = self.run_cli('merge', 'task-1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'project.profile').read_text(), 'node\n')
        self.assertEqual(self.close_state()['integration']['command'], 'test -f src/app.py')

    def test_integration_rejects_untracked_files(self):
        self.integration_task("echo 'ok = True' > helper.py; python3 -c 'import helper; assert helper.ok'")
        before = self.git('rev-parse', 'HEAD')
        result = self.run_cli('merge', 'task-1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('a modifié le résultat fusionné', result.stderr)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertFalse((self.root / 'helper.py').exists())
        self.assert_integration_cleaned()

    def test_integration_exchange_uses_merged_worktree(self):
        self.integration_task('test -f src/app.py')
        task = self.root / 'tasks/task-1.md'
        task.write_text(task.read_text().replace('isolation: none', 'isolation: exchange'))
        sandbox = self.scripts_dir / 'sandbox.sh'
        sandbox.write_text("""#!/bin/sh
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$1" = exec ]; then
    wt=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["worktree"])' \
        "$root/.workspace/orchestration/tasks/$2/state.json")
    shift 3
    case "$wt" in /*) ;; *) wt="$root/$wt" ;; esac
    cd "$wt" || exit 1
    exec "$@"
fi
""")
        self.git('add', 'scripts/sandbox.sh')
        self.git('commit', '-qm', 'sandbox test double')
        result = self.run_cli('merge', 'task-1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.close_state()['integration']['status'], 'passed')
        self.assertEqual(self.close_state()['worktree'], str(self.clone()))
        self.assert_integration_cleaned()

    def test_integration_timeout(self):
        self.integration_task('sleep 30')
        result = self.run_cli('merge', 'task-1', env={'WORKSPACE_INTEGRATION_TIMEOUT': '0.1'})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.close_state()['integration']['exit'], 124)
        self.assert_integration_cleaned()

    def test_merge_commits_dirty_worktree(self):
        worktree = self.prepare_merge_task(verify='test -f src/new.py')
        (worktree / 'src/new.py').write_text('new\n')
        (worktree / 'src/app.py').unlink()
        result = self.run_cli('merge', 'task-1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('log', '-1', '--format=%s', 'HEAD^2').strip(), 'feat: nouvelle fonction')
        self.assertTrue((self.root / 'src/new.py').is_file())
        self.assertFalse((self.root / 'src/app.py').exists())

    def test_merge_message_and_skip_verification(self):
        worktree = self.prepare_merge_task(verify='exit 42')
        (worktree / 'src/app.py').write_text('changed\n')
        result = self.run_cli('merge', 'task-1', '--no-verify-again', '--message', 'fix: message choisi')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('log', '-1', '--format=%s', 'HEAD^2').strip(), 'fix: message choisi')

    def test_merge_refuses_outside_scope(self):
        worktree = self.prepare_merge_task()
        (worktree / 'README.md').write_text('outside\n')
        before = self.git('rev-parse', 'HEAD')
        result = self.run_cli('merge', 'task-1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('périmètre', result.stderr)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=worktree), before)

    def test_merge_verification_failure_preserves_main(self):
        worktree = self.prepare_merge_task(verify='exit 42')
        (worktree / 'src/app.py').write_text('changed\n')
        before = self.git('rev-parse', 'HEAD')
        result = self.run_cli('merge', 'task-1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Vérification', result.stderr)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertTrue(worktree.exists())

    def test_merge_verifies_combined_version(self):
        worktree = self.prepare_merge_task(verify='test -f src/task.py && test ! -f target.txt')
        (worktree / 'src/task.py').write_text('task\n')
        (self.root / 'target.txt').write_text('target\n')
        self.git('add', 'target.txt')
        self.git('commit', '-qm', 'target advance')
        before = self.git('rev-parse', 'HEAD')
        result = self.run_cli('merge', 'task-1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Vérification', result.stderr)
        self.assertNotIn('Fichiers hors périmètre', result.stderr)
        self.assertTrue((worktree / 'target.txt').is_file())
        self.assertTrue((worktree / 'src/task.py').is_file())
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)

    def test_merge_target_files_do_not_violate_scope(self):
        worktree = self.prepare_merge_task(verify='test -f target.txt && test -f src/task.py')
        (worktree / 'src/task.py').write_text('task\n')
        (self.root / 'target.txt').write_text('outside task scope\n')
        self.git('add', 'target.txt')
        self.git('commit', '-qm', 'target advance')
        result = self.run_cli('merge', 'task-1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Fichiers hors périmètre', result.stderr)
        self.assertTrue((self.root / 'src/task.py').is_file())
        self.assertTrue((self.root / 'target.txt').is_file())

    def test_merge_retry_after_failed_verification_keeps_scope(self):
        flag = self.root.parent / (self.root.name + '-verify-ok')
        worktree = self.prepare_merge_task(verify='test -f "$VERIFY_FLAG"')
        (worktree / 'src/task.py').write_text('task\n')
        (self.root / 'target.txt').write_text('outside task scope\n')
        self.git('add', 'target.txt')
        self.git('commit', '-qm', 'target advance')
        env = {'VERIFY_FLAG': str(flag)}
        first = self.run_cli('merge', 'task-1', env=env)
        self.assertNotEqual(first.returncode, 0)
        self.assertTrue((worktree / 'target.txt').is_file())
        flag.write_text('ok\n')
        self.addCleanup(flag.unlink)
        second = self.run_cli('merge', 'task-1', env=env)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn('Fichiers hors périmètre', second.stderr)
        self.assertTrue((self.root / 'src/task.py').is_file())

    def test_merge_refuses_target_changed_during_verification(self):
        worktree = self.prepare_merge_task(verify='git -C "$TARGET_ROOT" commit --allow-empty -qm "target advance"')
        (worktree / 'src/task.py').write_text('task\n')
        before = self.git('rev-parse', 'HEAD')
        result = self.run_cli('merge', 'task-1', env={'TARGET_ROOT': str(self.root)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('la cible a changé pendant la vérification, relancer merge', result.stderr)
        self.assertNotEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.git('log', '-1', '--format=%s').strip(), 'target advance')
        self.assertFalse((self.root / 'src/task.py').exists())
        self.assertTrue(worktree.exists())

    def test_merge_conflict_aborts_and_records_files(self):
        worktree = self.prepare_merge_task(verify='exit 42')
        (worktree / 'src/app.py').write_text('task\n')
        (self.root / 'src/app.py').write_text('main\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'main change')
        before = self.git('rev-parse', 'HEAD')
        result = self.run_cli('merge', 'task-1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertFalse((self.root / '.git/MERGE_HEAD').exists())
        self.assertIn('src/app.py', result.stderr)
        self.assertEqual(self.git('status', '--porcelain', cwd=worktree), '')
        merge_head = worktree / self.git('rev-parse', '--git-path', 'MERGE_HEAD', cwd=worktree).strip()
        self.assertFalse(merge_head.exists())
        self.assertEqual(self.close_state()['status'], 'merge_conflict')
        self.assertEqual(self.close_state()['conflict_files'], ['src/app.py'])
        self.assertEqual(self.run_cli('status', 'task-1').returncode, 0)
        self.write_task('tasks/next.md', 'next')
        self.assertNotEqual(self.run_cli('prepare', 'tasks/next.md').returncode, 0)

    def test_merge_accepts_ignored_build_outputs_of_verification(self):
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        with (self.root / '.gitignore').open('a') as stream:
            stream.write('build/\n')
        self.git('add', '.gitignore')
        self.git('commit', '-qm', 'ignore build outputs')
        worktree = self.prepare_merge_task(verify='mkdir -p build && touch build/out && test -f src/app.py')
        (worktree / 'src/app.py').write_text('changed\n')
        result = self.run_cli('merge', 'task-1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'src/app.py').read_text(), 'changed\n')

    def test_merge_uses_profile_isolation_when_header_absent(self):
        # delegate.sh ran the task in a container because of the profile; merge must too.
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.root / 'project.profile').write_text('node\n')
        self.git('add', 'project.profile')
        self.git('commit', '-qm', 'profile')
        self.run_exchange_merge(True, isolation=None)

    def run_exchange_merge(self, succeeds, isolation='exchange'):
        worktree = self.prepare_merge_task(verify='test -f src/exchange.py' if succeeds else 'exit 42', isolation=isolation)
        (worktree / 'src/exchange.py').write_text('container check\n')
        fake_bin = Path(self.temp.name) / 'bin'
        fake_bin.mkdir()
        log = fake_bin / 'calls.jsonl'
        docker = fake_bin / 'docker'
        docker.write_text(
            '#!/usr/bin/env python3\n'
            + r"""import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['DOCKER_LOG']).open('a') as stream:
    stream.write(json.dumps(args) + '\n')
if args[0] == 'inspect':
    print('true')
elif args[0] == 'exec':
    sys.exit(subprocess.call(args[2:], cwd=os.environ['TASK_WORKTREE']))
"""
        )
        docker.chmod(0o755)
        before = self.git('rev-parse', 'HEAD')
        result = self.run_cli(
            'merge',
            'task-1',
            env={'PATH': str(fake_bin) + os.pathsep + os.environ['PATH'], 'DOCKER_LOG': str(log), 'TASK_WORKTREE': str(worktree)},
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertTrue(any(call[0] == 'inspect' for call in calls))
        self.assertTrue(any(call[0] == 'exec' for call in calls))
        self.assertEqual(
            calls[-1],
            ['rm', '-f', 'workspace-sandbox-' + hashlib.sha256(str(self.root.resolve()).encode('utf-8')).hexdigest()[:10] + '-' + 'task-1'],
        )
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((self.root / 'src/exchange.py').exists())
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.git('rev-parse', 'HEAD'), before)
            self.assertTrue(worktree.exists())

    def test_merge_exchange_verifies_worktree(self):
        self.run_exchange_merge(True)

    def test_merge_exchange_stops_after_failure(self):
        self.run_exchange_merge(False)

    def test_merge_refuses_closed_unknown_and_dirty_main(self):
        self.prepare_merge_task()
        self.assertNotEqual(self.run_cli('merge', 'missing').returncode, 0)
        (self.root / 'untracked').write_text('keep')
        self.assertNotEqual(self.run_cli('merge', 'task-1').returncode, 0)
        (self.root / 'untracked').unlink()
        self.assertEqual(self.run_cli('close', 'task-1', '--abandoned').returncode, 0)
        before = self.close_state()
        self.assertNotEqual(self.run_cli('merge', 'task-1').returncode, 0)
        self.assertEqual(self.close_state(), before)

    def test_close_merged_and_release_scope(self):
        worktree = self.prepare_close_task(commit=True)
        self.adopt()
        self.git('merge', '--no-ff', '-qm', 'merge task', 'workspace-task/task-1')
        result = self.run_cli('close', 'task-1', '--merged')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(worktree.exists())
        self.assertNotIn('refs/heads/workspace-task/task-1', self.git('show-ref'))
        self.assertEqual(self.close_state()['status'], 'merged')
        self.assertTrue(self.close_state()['closed_at'])
        self.write_task('tasks/next.md', 'next')
        result = self.run_cli('prepare', 'tasks/next.md')
        self.assertEqual(result.returncode, 0, result.stderr)
        listing = self.run_cli('status')
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertEqual([s['task_id'] for s in json.loads(listing.stdout)['closed']], ['task-1'])

    def test_close_merged_refuses_unmerged(self):
        worktree = self.prepare_close_task(commit=True)
        result = self.run_cli('close', 'task-1', '--merged')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('non entièrement fusionnée', result.stderr)
        self.assertTrue(worktree.exists())
        self.assertEqual(self.close_state()['status'], 'prepared')

    def test_close_abandoned_keeps_branch_and_is_idempotent(self):
        worktree = self.prepare_close_task(commit=True)
        result = self.run_cli('close', 'task-1', '--abandoned')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(worktree.exists())
        self.git('rev-parse', '--verify', 'refs/heads/workspace-task/task-1')
        before = self.close_state()
        self.assertEqual(before['status'], 'abandoned')
        result = self.run_cli('close', 'task-1', '--merged')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('déjà', result.stdout)
        self.assertEqual(self.close_state(), before)

    def registry_snapshot(self):
        refs = self.git('for-each-ref', '--format=%(refname) %(objectname)')
        files = sorted(
            str(p.relative_to(self.root))
            for p in (self.root / '.workspace').rglob('*')
            if p.is_file() and 'tmp-tests' not in p.parts and p.name != 'events.jsonl'
        )
        return refs, files, self.close_state()

    def test_retry_after_abandon_archives_previous_attempt(self):
        worktree = self.prepare_close_task(commit=True)
        old_tip = self.git('rev-parse', 'HEAD', cwd=worktree).strip()
        run_dir = self.root / '.workspace/run/delegate/task-1'
        run_dir.mkdir(parents=True)
        (run_dir / 'log.txt').write_text('first attempt\n')
        self.assertEqual(self.run_cli('close', 'task-1', '--abandoned').returncode, 0)
        self.write_task(body='Consigne modifiée')
        result = self.run_cli('prepare', 'tasks/task-1.md', '--retry')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.close_state()
        self.assertEqual(state['status'], 'prepared')
        self.assertEqual(state['retry_of'], 'task-1-tentative1')
        self.assertEqual(state['task_sha256'], hashlib.sha256((self.root / 'tasks/task-1.md').read_bytes()).hexdigest())
        self.assertEqual(state['base_commit'], self.base_commit)
        self.assertTrue(worktree.exists())
        archive = self.root / '.workspace/orchestration/archive/task-1-tentative1'
        archived = json.loads((archive / 'state.json').read_text())
        self.assertEqual(archived['status'], 'abandoned')
        self.assertEqual(archived['archived_branch'], 'archive/workspace-task/task-1-tentative1')
        self.assertRegex(archived['archived_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(archived['archived_at'][-1], 'Z')
        self.assertEqual((archive / 'run/log.txt').read_text(), 'first attempt\n')
        self.assertFalse(run_dir.exists())
        self.assertEqual(stat.S_IMODE(archive.parent.stat().st_mode), 0o700)
        self.assertEqual(self.git('rev-parse', 'refs/heads/archive/workspace-task/task-1-tentative1').strip(), old_tip)
        self.assertEqual(self.git('rev-parse', 'refs/heads/workspace-task/task-1').strip(), self.base_commit)
        events = [json.loads(line) for line in (self.root / '.workspace/events.jsonl').read_text().splitlines()]
        prepared = [e for e in events if e['event'] == 'prepared']
        self.assertNotIn('retry', prepared[0]['detail'])
        self.assertEqual(prepared[1]['detail']['retry'], 1)

    def test_retry_twice_numbers_attempts(self):
        self.prepare_close_task()
        for attempt in (1, 2):
            self.assertEqual(self.run_cli('close', 'task-1', '--abandoned').returncode, 0)
            result = self.run_cli('prepare', '--retry', 'tasks/task-1.md')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.close_state()['retry_of'], f'task-1-tentative{attempt}')
        archive_dir = self.root / '.workspace/orchestration/archive'
        self.assertEqual(sorted(p.name for p in archive_dir.iterdir()), ['task-1-tentative1', 'task-1-tentative2'])
        self.git('rev-parse', '--verify', 'refs/heads/archive/workspace-task/task-1-tentative1')
        self.git('rev-parse', '--verify', 'refs/heads/archive/workspace-task/task-1-tentative2')

    def test_retry_when_branch_was_deleted_by_hand(self):
        self.prepare_close_task()
        self.assertEqual(self.run_cli('close', 'task-1', '--abandoned').returncode, 0)
        self.git('branch', '-D', 'workspace-task/task-1')
        result = self.run_cli('prepare', 'tasks/task-1.md', '--retry')
        self.assertEqual(result.returncode, 0, result.stderr)
        archived = json.loads((self.root / '.workspace/orchestration/archive/task-1-tentative1/state.json').read_text())
        self.assertIsNone(archived['archived_branch'])

    def test_retry_refused_on_prepared_task_changes_nothing(self):
        self.prepare_close_task()
        before = self.registry_snapshot()
        result = self.run_cli('prepare', 'tasks/task-1.md', '--retry')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--retry', result.stderr)
        self.assertIn('prepared', result.stderr)
        self.assertEqual(self.registry_snapshot(), before)
        self.assertFalse((self.root / '.workspace/orchestration/archive').exists())

    def test_retry_refused_on_unknown_task(self):
        self.write_task()
        result = self.run_cli('prepare', 'tasks/task-1.md', '--retry')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--retry', result.stderr)
        self.assertIn('aucune tâche', result.stderr)
        self.assertFalse(self.clone().exists())

    def test_prepare_without_retry_still_refuses_abandoned_id(self):
        self.prepare_close_task()
        self.assertEqual(self.run_cli('close', 'task-1', '--abandoned').returncode, 0)
        before = self.registry_snapshot()
        result = self.run_cli('prepare', 'tasks/task-1.md')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('déjà', result.stderr)
        self.assertEqual(self.registry_snapshot(), before)

    def test_retry_refused_when_archive_target_exists(self):
        self.prepare_close_task()
        self.assertEqual(self.run_cli('close', 'task-1', '--abandoned').returncode, 0)
        self.git('branch', 'archive/workspace-task/task-1-tentative1')
        before = self.registry_snapshot()
        result = self.run_cli('prepare', 'tasks/task-1.md', '--retry')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.registry_snapshot(), before)
        self.git('rev-parse', '--verify', 'refs/heads/workspace-task/task-1')

    def test_retry_refused_on_symlinked_archive_dir(self):
        self.prepare_close_task()
        self.assertEqual(self.run_cli('close', 'task-1', '--abandoned').returncode, 0)
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        (self.root / '.workspace/orchestration/archive').symlink_to(elsewhere)
        result = self.run_cli('prepare', 'tasks/task-1.md', '--retry')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertEqual(self.close_state()['status'], 'abandoned')

    def test_retry_still_refuses_overlap_with_other_active_task(self):
        self.prepare_close_task()
        self.assertEqual(self.run_cli('close', 'task-1', '--abandoned').returncode, 0)
        self.write_task(rel_path='tasks/task-2.md', task_id='task-2', allowed=['src/**'])
        self.assertEqual(self.run_cli('prepare', 'tasks/task-2.md').returncode, 0)
        before = self.registry_snapshot()
        result = self.run_cli('prepare', 'tasks/task-1.md', '--retry')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('task-2', result.stderr)
        self.assertEqual(self.registry_snapshot(), before)
        self.git('rev-parse', '--verify', 'refs/heads/workspace-task/task-1')

    def test_close_dirty_refused_then_force_logged(self):
        worktree = self.prepare_close_task()
        (worktree / 'untracked').write_text('save me')
        result = self.run_cli('close', 'task-1', '--abandoned')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(worktree.exists())
        result = self.run_cli('close', 'task-1', '--abandoned', '--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.close_state()['close_force'])

    def test_close_keep_branch(self):
        self.prepare_close_task()
        result = self.run_cli('close', 'task-1', '--merged', '--keep-branch')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.git('rev-parse', '--verify', 'refs/heads/workspace-task/task-1')

    def test_close_refuses_corrupt_paths_and_symlinks(self):
        worktree = self.prepare_close_task()
        state_file = self.root / '.workspace/orchestration/tasks/task-1/state.json'
        original = state_file.read_text()
        state = json.loads(original)
        state['worktree'] = '../escape'
        state_file.write_text(json.dumps(state))
        self.assertNotEqual(self.run_cli('close', 'task-1', '--abandoned').returncode, 0)
        state_file.write_text(original)
        moved = worktree.with_name('moved')
        worktree.rename(moved)
        worktree.symlink_to(moved, target_is_directory=True)
        self.assertNotEqual(self.run_cli('close', 'task-1', '--abandoned', '--force').returncode, 0)
        self.assertTrue(moved.exists())
        worktree.unlink()
        moved.rename(worktree)
        state_file.write_text('{ invalid json')
        result = self.run_cli('close', 'task-1', '--abandoned')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('corrompu', result.stderr)
        self.assertTrue(worktree.exists())

    def make_arm(self):
        """Linked worktree of the project used as root, like a campaign arm."""
        for name in ('verify-task.sh', 'sandbox.sh'):
            shutil.copy2(ROOT / 'template/scripts' / name, self.scripts_dir / name)
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('add', 'scripts')
        self.git('commit', '-qm', 'verification tooling')
        arm = self.root / '.workspace/campaign/arm'
        self.git('worktree', 'add', '-q', '-b', 'arm-branch', str(arm))
        self.assertTrue((arm / '.git').is_file())
        return arm

    def run_arm(self, arm, *args):
        return subprocess.run(['bash', str(arm / 'scripts/task-workspace.sh'), *args], cwd=str(arm), capture_output=True, text=True)

    def prepare_arm_task(self, arm):
        task = arm / 'tasks/task-1.md'
        task.parent.mkdir(parents=True, exist_ok=True)
        task.write_text(
            "---\nid: task-1\nagent: codex\nallowed:\n  - src/**\n"
            "verify: test -f src/app.py\nverify_profile: off\nisolation: none\n"
            "---\n# feat: nouvelle fonction\nSuite\n",
            encoding='utf-8',
        )
        result = self.run_arm(arm, 'prepare', str(task))
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.clone('task-1', arm)

    def point_git_file_to_other_repository(self, worktree):
        other = Path(self.temp.name) / 'other repository'
        other.mkdir()
        self.git('init', '-q', cwd=other)
        (other / 'file.txt').write_text('other\n')
        self.git('add', '.', cwd=other)
        self.git('commit', '-qm', 'other initial', cwd=other)
        foreign = Path(self.temp.name) / 'foreign worktree'
        self.git('worktree', 'add', '-q', '-b', 'foreign', str(foreign), cwd=other)
        # The task clone owns a real .git folder: a git file pointing elsewhere is not that clone.
        shutil.rmtree(worktree / '.git')
        shutil.copy2(foreign / '.git', worktree / '.git')

    def test_prepare_and_merge_when_root_is_linked_worktree(self):
        arm = self.make_arm()
        main_head = self.git('rev-parse', 'HEAD').strip()
        main_branch = self.git('symbolic-ref', 'HEAD').strip()
        worktree = self.prepare_arm_task(arm)
        self.assertEqual(worktree, self.clone('task-1', arm))
        self.assertNotIn(arm, worktree.parents)
        self.assertNotIn(self.root, worktree.parents)
        (worktree / 'src/app.py').write_text('changed\n')
        self.git('add', '.', cwd=worktree)
        self.git('commit', '-qm', 'feat: task', cwd=worktree)
        result = self.run_arm(arm, 'merge', 'task-1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((arm / 'src/app.py').read_text(), 'changed\n')
        self.assertEqual(self.git('log', '-1', '--format=%s', cwd=arm).strip(), 'merge: task task-1')
        self.assertFalse(worktree.exists())
        state = json.loads((arm / '.workspace/orchestration/tasks/task-1/state.json').read_text())
        self.assertEqual(state['status'], 'merged')
        self.assertNotIn('refs/heads/workspace-task/task-1', self.git('show-ref'))
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), main_head)
        self.assertEqual(self.git('symbolic-ref', 'HEAD').strip(), main_branch)
        self.assertEqual((self.root / 'src/app.py').read_text(), 'print("initial")\n')

    def test_close_abandoned_when_root_is_linked_worktree(self):
        arm = self.make_arm()
        worktree = self.prepare_arm_task(arm)
        result = self.run_arm(arm, 'close', 'task-1', '--abandoned')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(worktree.exists())
        self.git('rev-parse', '--verify', 'refs/heads/workspace-task/task-1')

    def test_close_refuses_foreign_git_metadata(self):
        worktree = self.prepare_close_task()
        self.point_git_file_to_other_repository(worktree)
        result = self.run_cli('close', 'task-1', '--abandoned', '--force')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(worktree.exists())
        self.assertEqual(self.close_state()['status'], 'prepared')

    def test_close_refuses_foreign_git_metadata_when_root_is_linked_worktree(self):
        arm = self.make_arm()
        worktree = self.prepare_arm_task(arm)
        self.point_git_file_to_other_repository(worktree)
        result = self.run_arm(arm, 'close', 'task-1', '--abandoned', '--force')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(worktree.exists())
        state = json.loads((arm / '.workspace/orchestration/tasks/task-1/state.json').read_text())
        self.assertEqual(state['status'], 'prepared')

    def test_prepare_nominal_creates_branch_worktree_state(self):
        self.write_task('tasks/task-1.md', 'task-1', 'codex', ['src/**'], body='Consigne secrète demo')
        res = self.run_cli('prepare', 'tasks/task-1.md')
        self.assertEqual(res.returncode, 0, res.stderr)

        data = json.loads(res.stdout)
        self.assertEqual(data.get('schema_version'), 1)
        self.assertEqual(data.get('task_id'), 'task-1')
        self.assertEqual(data.get('agent'), 'codex')
        self.assertEqual(data.get('status'), 'prepared')
        self.assertEqual(data.get('branch'), 'workspace-task/task-1')
        self.assertEqual(data.get('worktree'), str(self.clone()))
        self.assertEqual(data.get('base_commit'), self.base_commit)
        self.assertEqual(data.get('allowed'), ['src/**'])
        self.assertEqual(data.get('source_task'), 'tasks/task-1.md')
        self.assertTrue(isinstance(data.get('created_at'), str))
        self.assertTrue(isinstance(data.get('task_sha256'), str))
        self.assertNotIn('Consigne secrète demo', res.stdout)

        branch_ref = self.git('rev-parse', '--verify', 'refs/heads/workspace-task/task-1').strip()
        self.assertEqual(branch_ref, self.base_commit)

        worktree_dir = self.clone()
        self.assertTrue(worktree_dir.is_dir())
        self.assertTrue((worktree_dir / '.git').is_dir())
        self.assertFalse((self.root / '.workspace/worktrees').exists())

        state_file = self.root / '.workspace/orchestration/tasks/task-1/state.json'
        self.assertTrue(state_file.is_file())
        saved_data = json.loads(state_file.read_text(encoding='utf-8'))
        self.assertEqual(saved_data, data)

    def busy_history(self):
        """Dizaines de commits, autres branches de tâche, branches archive/ et étiquette dans le projet."""
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        for index in range(12):
            (self.root / 'src/history.txt').write_text(f'{index}\n')
            self.git('add', 'src/history.txt')
            self.git('commit', '-qm', f'merge: task earlier-{index}')
        for branch in ('workspace-task/other', 'archive/workspace-task/old-tentative1', 'feature/x'):
            self.git('branch', branch, 'HEAD~5')
        self.git('tag', 'v1', 'HEAD~3')
        return self.git('rev-parse', 'HEAD~1').strip()

    def test_prepare_creates_a_history_free_clone_outside_the_project(self):
        parent_of_base = self.busy_history()
        self.write_task()
        result = self.run_cli('prepare', 'tasks/task-1.md')
        self.assertEqual(result.returncode, 0, result.stderr)
        clone = self.clone()
        self.assertNotIn(self.root, clone.parents)
        self.assertEqual(json.loads(result.stdout)['worktree'], str(clone))
        self.assertEqual(self.git('rev-list', '--all', '--count', cwd=clone).strip(), '1')
        self.assertEqual(self.git('for-each-ref', '--format=%(refname)', cwd=clone).split(), ['refs/heads/workspace-task/task-1'])
        self.assertEqual(self.git('symbolic-ref', 'HEAD', cwd=clone).strip(), 'refs/heads/workspace-task/task-1')
        self.assertEqual(self.git('remote', cwd=clone).strip(), '')
        self.assertFalse((clone / '.git/objects/info/alternates').exists())
        self.assertTrue((clone / '.git/shallow').is_file())
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=clone).strip(), self.git('rev-parse', 'HEAD').strip())
        self.assertNotEqual(subprocess.run(['git', '-C', str(clone), 'cat-file', '-e', parent_of_base], capture_output=True).returncode, 0)
        self.assertEqual(stat.S_IMODE(clone.stat().st_mode), 0o700)
        self.assertFalse((self.root / '.workspace/worktrees').exists())
        self.assertEqual(self.git('log', '--all', '--format=%s', cwd=clone).splitlines(), ['merge: task earlier-11'])
        self.assertEqual(self.git('config', '--local', 'core.hooksPath', cwd=clone).strip(), '/dev/null')
        self.assertEqual(self.git('config', '--local', 'core.fsmonitor', cwd=clone).strip(), 'false')
        for path in clone.rglob('*'):
            self.assertFalse(path.is_symlink(), path)

    def test_prepare_refuses_relative_or_inner_work_dir_and_creates_nothing(self):
        self.write_task()
        for value in ('relative/dir', str(self.root / 'inside')):
            with self.subTest(value=value):
                result = self.run_cli('prepare', 'tasks/task-1.md', env={'WORKSPACE_WORK_DIR': value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('WORKSPACE_WORK_DIR', result.stderr)
                self.assertFalse((self.root / 'inside').exists())
                self.assertFalse((self.root / 'relative').exists())
                self.assertFalse((self.root / '.workspace/orchestration/tasks/task-1').exists())
                self.assertNotEqual(self.git_status('rev-parse', '--verify', 'refs/heads/workspace-task/task-1'), 0)

    def git_status(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], capture_output=True).returncode

    def test_two_projects_with_one_task_name_use_distinct_clones(self):
        self.write_task()
        other = Path(self.temp.name) / 'elsewhere' / self.root.name
        shutil.copytree(self.root, other, symlinks=True)
        self.assertEqual(self.run_cli('prepare', 'tasks/task-1.md').returncode, 0)
        result = subprocess.run(
            ['bash', str(other / 'scripts/task-workspace.sh'), 'prepare', 'tasks/task-1.md'], cwd=str(other), capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.clone(), self.clone(root=other))
        self.assertTrue(self.clone().is_dir())
        self.assertTrue(self.clone(root=other).is_dir())

    def test_close_abandoned_brings_the_clone_commits_home(self):
        worktree = self.prepare_close_task(commit=True)
        committed = self.git('rev-parse', 'HEAD', cwd=worktree).strip()
        self.assertEqual(self.git('rev-parse', 'refs/heads/workspace-task/task-1').strip(), self.base_commit)
        result = self.run_cli('close', 'task-1', '--abandoned')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(worktree.exists())
        self.assertEqual(self.git('rev-parse', 'refs/heads/workspace-task/task-1').strip(), committed)
        self.assertEqual(self.git('log', '-1', '--format=%s', 'refs/heads/workspace-task/task-1').strip(), 'task change')

    def test_close_refuses_clone_on_another_branch_or_diverged_from_the_project(self):
        worktree = self.prepare_close_task(commit=True)
        self.git('checkout', '-q', '-b', 'elsewhere', cwd=worktree)
        result = self.run_cli('close', 'task-1', '--abandoned', '--force')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(worktree.exists())
        self.git('checkout', '-q', 'workspace-task/task-1', cwd=worktree)
        # The project branch moved to a commit the clone does not contain: no fast-forward, nothing is lost.
        (self.root / 'other.txt').write_text('other\n')
        self.git('add', 'other.txt')
        self.git('commit', '-qm', 'unrelated')
        self.git('branch', '-f', 'workspace-task/task-1', 'HEAD')
        result = self.run_cli('close', 'task-1', '--abandoned', '--force')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('avance rapide', result.stderr)
        self.assertTrue(worktree.exists())
        self.assertEqual(self.close_state()['status'], 'prepared')

    def test_retry_refused_while_the_old_clone_exists(self):
        self.prepare_close_task()
        self.assertEqual(self.run_cli('close', 'task-1', '--abandoned').returncode, 0)
        self.clone().mkdir(parents=True)
        before = self.registry_snapshot()
        result = self.run_cli('prepare', 'tasks/task-1.md', '--retry')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('existe encore', result.stderr)
        self.assertEqual(self.registry_snapshot(), before)

    def test_merge_brings_the_clone_work_to_the_project(self):
        worktree = self.prepare_merge_task()
        (worktree / 'src/app.py').write_text('changed\n')
        self.git('add', '.', cwd=worktree)
        self.git('commit', '-qm', 'feat: validated in the clone', cwd=worktree)
        self.assertEqual(self.run_cli('merge', 'task-1').returncode, 0)
        self.assertEqual(self.git('log', '-1', '--format=%s').strip(), 'merge: task task-1')
        self.assertEqual(self.git('log', '-1', '--format=%s', 'HEAD^2').strip(), 'feat: validated in the clone')
        self.assertFalse(worktree.exists())

    def test_status_nominal_is_idempotent_and_read_only(self):
        self.write_task('tasks/task-1.md', 'task-1', 'codex', ['src/**'])
        prep_res = self.run_cli('prepare', 'tasks/task-1.md')
        self.assertEqual(prep_res.returncode, 0, prep_res.stderr)
        prep_data = json.loads(prep_res.stdout)

        status_res = self.run_cli('status', 'task-1')
        self.assertEqual(status_res.returncode, 0, status_res.stderr)
        status_data = json.loads(status_res.stdout)
        self.assertEqual(status_data, prep_data)

        # Status does not mutate or touch state
        status_res2 = self.run_cli('status', 'task-1')
        self.assertEqual(status_res2.returncode, 0)
        self.assertEqual(json.loads(status_res2.stdout), prep_data)

    def test_status_unknown_task_fails(self):
        res = self.run_cli('status', 'unknown-task-99')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['inconnue', 'introuvable', 'aucun', 'erreur', 'trouvée']), res.stderr)

    def test_file_divergence_worktree_isolated_from_main(self):
        self.write_task('tasks/task-1.md', 'task-1', 'codex', ['src/**'])
        self.assertEqual(self.run_cli('prepare', 'tasks/task-1.md').returncode, 0)

        worktree_dir = self.clone()
        new_file = worktree_dir / 'src/new_feature.py'
        new_file.write_text('def feature(): pass\n')
        self.git('add', 'src/new_feature.py', cwd=worktree_dir)
        self.git('commit', '-qm', 'feature in worktree', cwd=worktree_dir)

        # Main working tree must not have the new file and HEAD must not move
        self.assertFalse((self.root / 'src/new_feature.py').exists())
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.base_commit)
        self.assertEqual(self.git('status', '--porcelain').strip(), '')

    def test_root_with_spaces_and_caller_in_subdirectory(self):
        # self.root is already 'project with spaces'
        sub = self.root / 'nested/sub/dir'
        sub.mkdir(parents=True)
        self.write_task('tasks/nested-task.md', 'nested-task', 'claude-haiku', ['src/**'])

        res = self.run_cli('prepare', '../../../tasks/nested-task.md', cwd=sub)
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data.get('source_task'), 'tasks/nested-task.md')
        self.assertTrue(self.clone('nested-task').is_dir())

    def test_explicit_base_commit(self):
        (self.root / 'second.txt').write_text('second\n')
        self.git('add', 'second.txt')
        self.git('commit', '-qm', 'second commit')
        head_commit = self.git('rev-parse', 'HEAD').strip()
        self.assertNotEqual(head_commit, self.base_commit)

        self.write_task('tasks/base-task.md', 'base-task', 'codex', ['src/**'])
        res = self.run_cli('prepare', 'tasks/base-task.md', '--base', self.base_commit)
        self.assertEqual(res.returncode, 0, res.stderr)

        data = json.loads(res.stdout)
        self.assertEqual(data.get('base_commit'), self.base_commit)
        branch_ref = self.git('rev-parse', 'refs/heads/workspace-task/base-task').strip()
        self.assertEqual(branch_ref, self.base_commit)

    def test_refuse_unborn_repository_no_head(self):
        unborn_dir = Path(self.temp.name) / 'unborn repo'
        unborn_dir.mkdir()
        subprocess.check_call(['git', 'init', '-q', str(unborn_dir)])
        shutil.copytree(self.scripts_dir, unborn_dir / 'scripts')

        task = unborn_dir / 'task.md'
        task.write_text('---\nid: unborn-task\nagent: codex\nallowed: [src/**]\n---\nTask\n')

        res = subprocess.run(
            ['bash', str(unborn_dir / 'scripts/task-workspace.sh'), 'prepare', 'task.md'],
            cwd=str(unborn_dir),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['head', 'commit', 'aucun', 'vide', 'initial']), res.stderr)
        self.assertFalse((unborn_dir / '.workspace').exists())

    def test_refuse_dirty_working_tree_preserves_work(self):
        (self.root / 'README.md').write_text('# Modified uncommitted dirty\n')
        self.write_task('tasks/dirty-task.md', 'dirty-task', 'codex', ['src/**'])

        res = self.run_cli('prepare', 'tasks/dirty-task.md')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['propre', 'sale', 'dirty', 'non valid', 'modifi']), res.stderr)

        # Ensure dirty file was neither lost nor committed
        self.assertEqual((self.root / 'README.md').read_text(), '# Modified uncommitted dirty\n')
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.base_commit)
        self.assertFalse(self.clone('dirty-task').exists())

    def test_refuse_invalid_base_ref(self):
        self.write_task('tasks/bad-base.md', 'bad-base', 'codex', ['src/**'])
        res = self.run_cli('prepare', 'tasks/bad-base.md', '--base', 'nonexistent-ref-00000000')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['base', 'ref', 'référence', 'invalide', 'introuvable']), res.stderr)
        self.assertFalse(self.clone('bad-base').exists())

    def test_refuse_invalid_task_id(self):
        for bad_id in ['../escape', 'task/sub', 'TASK_ID', 'task with space', '']:
            with self.subTest(bad_id=bad_id):
                task = self.root / 'bad.md'
                task.write_text(f"---\nid: \"{bad_id}\"\nagent: codex\nallowed: [src/**]\n---\nBody\n")
                res = self.run_cli('prepare', 'bad.md')
                self.assertNotEqual(res.returncode, 0)
                self.assertTrue(any(w in res.stderr.lower() for w in ['id', 'identifiant', 'invalide']), res.stderr)

    def test_refuse_task_file_outside_project_or_external_symlink(self):
        outside_dir = Path(self.temp.name) / 'outside'
        outside_dir.mkdir()
        outside_task = outside_dir / 'ext-task.md'
        outside_task.write_text('---\nid: ext-task\nagent: codex\nallowed: [src/**]\n---\nTask\n')

        res = self.run_cli('prepare', str(outside_task))
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['hors', 'racine', 'projet', 'symlink', 'symbolique', 'externe']), res.stderr)

        symlink_task = self.root / 'sym-task.md'
        symlink_task.symlink_to(outside_task)
        res_sym = self.run_cli('prepare', 'sym-task.md')
        self.assertNotEqual(res_sym.returncode, 0)
        self.assertTrue(
            any(w in res_sym.stderr.lower() for w in ['hors', 'racine', 'projet', 'symlink', 'symbolique', 'externe']), res_sym.stderr
        )

    def test_refuse_allowed_path_traversal_or_absolute(self):
        for bad_allowed in ['/etc/passwd', '../outside.txt', 'src/../../secret.txt']:
            with self.subTest(bad_allowed=bad_allowed):
                self.write_task('tasks/bad-allowed.md', 'bad-allowed', 'codex', [bad_allowed])
                res = self.run_cli('prepare', 'tasks/bad-allowed.md')
                self.assertNotEqual(res.returncode, 0)
                self.assertTrue(
                    any(w in res.stderr.lower() for w in ['allowed', 'périmètre', 'invalide', 'hors', 'absolu', 'travers']), res.stderr
                )

    def test_refuse_sensitive_paths_in_allowed(self):
        for sensitive in ['.git/**', '.workspace/**', 'scripts/gate.sh', '.env', 'secrets/key']:
            with self.subTest(sensitive=sensitive):
                self.write_task('tasks/sensitive.md', 'sensitive-task', 'codex', [sensitive])
                res = self.run_cli('prepare', 'tasks/sensitive.md')
                self.assertNotEqual(res.returncode, 0)
                self.assertTrue(any(w in res.stderr.lower() for w in ['sensible', 'interdit', 'protégé', 'refus', 'périmètre']), res.stderr)

    def test_refuse_external_symlink_in_orchestration_or_worktrees(self):
        outside = Path(self.temp.name) / 'outside_dest'
        outside.mkdir()
        worktrees_dir = self.root / '.workspace/worktrees'
        worktrees_dir.parent.mkdir(parents=True, exist_ok=True)
        worktrees_dir.symlink_to(outside, target_is_directory=True)

        self.write_task('tasks/sym-test.md', 'sym-test', 'codex', ['src/**'])
        res = self.run_cli('prepare', 'tasks/sym-test.md')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['symbolique', 'symlink', 'interdit', 'sortant', 'externe']), res.stderr)

    def test_refuse_duplicate_task_id(self):
        self.write_task('tasks/dup.md', 'dup-task', 'codex', ['src/**'])
        res1 = self.run_cli('prepare', 'tasks/dup.md')
        self.assertEqual(res1.returncode, 0, res1.stderr)

        res2 = self.run_cli('prepare', 'tasks/dup.md')
        self.assertNotEqual(res2.returncode, 0)
        self.assertTrue(any(w in res2.stderr.lower() for w in ['déjà', 'existant', 'enregistrée', 'existe']), res2.stderr)

    def test_preexisting_branch_not_deleted_on_collision(self):
        self.git('branch', 'workspace-task/pre-branch')
        self.write_task('tasks/colliding.md', 'pre-branch', 'codex', ['src/**'])

        res = self.run_cli('prepare', 'tasks/colliding.md')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['branche', 'existe', 'déjà', 'collision']), res.stderr)

        # Pre-existing branch must not be removed
        branch_exists = self.git('rev-parse', '--verify', 'refs/heads/workspace-task/pre-branch').strip()
        self.assertEqual(branch_exists, self.base_commit)

    def test_conflict_detection_identical_paths(self):
        self.write_task('tasks/task-a.md', 'task-a', 'codex', ['src/service.py'])
        self.assertEqual(self.run_cli('prepare', 'tasks/task-a.md').returncode, 0)

        self.write_task('tasks/task-b.md', 'task-b', 'claude-haiku', ['src/service.py'])
        res = self.run_cli('prepare', 'tasks/task-b.md')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['conflit', 'chevauchement', 'réservation', 'occupé', 'réservé']), res.stderr)
        self.assertFalse(self.clone('task-b').exists())

    def test_conflict_detection_parent_glob_vs_child(self):
        self.write_task('tasks/task-parent.md', 'task-parent', 'codex', ['src/**'])
        self.assertEqual(self.run_cli('prepare', 'tasks/task-parent.md').returncode, 0)

        self.write_task('tasks/task-child.md', 'task-child', 'claude-sonnet', ['src/utils/calc.py'])
        res = self.run_cli('prepare', 'tasks/task-child.md')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['conflit', 'chevauchement', 'réservation', 'occupé', 'réservé']), res.stderr)

    def test_conflict_conservative_ambiguous_overlap(self):
        self.write_task('tasks/task-glob.md', 'task-glob', 'codex', ['src/*.py'])
        self.assertEqual(self.run_cli('prepare', 'tasks/task-glob.md').returncode, 0)

        self.write_task('tasks/task-spec.md', 'task-spec', 'claude-haiku', ['src/app.py'])
        res = self.run_cli('prepare', 'tasks/task-spec.md')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['conflit', 'chevauchement', 'réservation', 'occupé', 'réservé']), res.stderr)

    def test_disjoint_tasks_allowed(self):
        self.write_task('tasks/web.md', 'task-web', 'codex', ['apps/web/**'])
        self.write_task('tasks/api.md', 'task-api', 'claude-sonnet', ['apps/api/**'])

        res1 = self.run_cli('prepare', 'tasks/web.md')
        self.assertEqual(res1.returncode, 0, res1.stderr)

        res2 = self.run_cli('prepare', 'tasks/api.md')
        self.assertEqual(res2.returncode, 0, res2.stderr)

        self.assertTrue(self.clone('task-web').is_dir())
        self.assertTrue(self.clone('task-api').is_dir())

    def test_atomic_state_permissions_mode_0600_and_0700(self):
        self.write_task('tasks/perm.md', 'task-perm', 'codex', ['src/**'])
        res = self.run_cli('prepare', 'tasks/perm.md')
        self.assertEqual(res.returncode, 0, res.stderr)

        state_path = self.root / '.workspace/orchestration/tasks/task-perm/state.json'
        self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)

        task_dir = state_path.parent
        self.assertEqual(stat.S_IMODE(task_dir.stat().st_mode), 0o700)

        worktree_dir = self.clone('task-perm')
        self.assertEqual(stat.S_IMODE(worktree_dir.stat().st_mode), 0o700)

    def test_git_hooks_disabled_during_mutations(self):
        hooks_dir = self.root / '.git/hooks'
        hooks_dir.mkdir(parents=True, exist_ok=True)
        failing_hook = hooks_dir / 'post-checkout'
        failing_hook.write_text('#!/bin/sh\nexit 1\n')
        failing_hook.chmod(0o755)

        self.write_task('tasks/hook-test.md', 'hook-test', 'codex', ['src/**'])
        res = self.run_cli('prepare', 'tasks/hook-test.md')
        # Prepare must succeed because core.hooksPath is bypassed
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_prevalidation_worktree_path_already_taken(self):
        # A pre-existing regular file at target worktree path proves prevalidation before Git mutation
        target_wt = self.clone('fail-wt')
        target_wt.parent.mkdir(parents=True, exist_ok=True)
        target_wt.write_text('blocking regular file\n')

        self.write_task('tasks/fail-wt.md', 'fail-wt', 'codex', ['src/**'])
        res = self.run_cli('prepare', 'tasks/fail-wt.md')
        self.assertNotEqual(res.returncode, 0)

        # Prevalidation aborts before creating the branch or state
        res_branch = subprocess.run(
            ['git', '-C', str(self.root), 'rev-parse', '--verify', 'refs/heads/workspace-task/fail-wt'], capture_output=True, text=True
        )
        self.assertNotEqual(res_branch.returncode, 0)
        self.assertFalse((self.root / '.workspace/orchestration/tasks/fail-wt/state.json').exists())

    def test_rollback_after_branch_creation_on_clone_failure(self):
        # Genuine rollback test: branch is created, but clone fails via wrapper
        fake_bin = self.root / 'fake-git-bin'
        fake_bin.mkdir(parents=True, exist_ok=True)
        fake_git = fake_bin / 'git'
        real_git = shutil.which('git')
        fake_git.write_text(f'''#!/bin/sh
case "$*" in
    *clone*sim-fail*)
        echo "fatal: simulated failure for clone" >&2
        exit 1
        ;;
esac
exec "{real_git}" "$@"
''')
        fake_git.chmod(0o755)

        env = {'PATH': f"{fake_bin}:{os.environ.get('PATH', '')}"}
        self.write_task('tasks/sim-fail.md', 'sim-fail', 'codex', ['src/**'])
        res = self.run_cli('prepare', 'tasks/sim-fail.md', env=env)
        self.assertNotEqual(res.returncode, 0)

        # Branch created during attempt must be rolled back
        res_branch = subprocess.run(
            ['git', '-C', str(self.root), 'rev-parse', '--verify', 'refs/heads/workspace-task/sim-fail'], capture_output=True, text=True
        )
        self.assertNotEqual(res_branch.returncode, 0)
        self.assertFalse(self.clone('sim-fail').exists())
        self.assertFalse((self.root / '.workspace/orchestration/tasks/sim-fail').exists())

    def test_refuse_dangerous_base_option_flag(self):
        self.write_task('tasks/bad-base-flag.md', 'bad-base-flag', 'codex', ['src/**'])
        res = self.run_cli('prepare', 'tasks/bad-base-flag.md', '--base', '--upload-pack=evil')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['base', 'invalide', 'option', 'interdit']), res.stderr)

    def test_refuse_corrupt_registry_state_json(self):
        corrupt_dir = self.root / '.workspace/orchestration/tasks/corrupt-task'
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        (corrupt_dir / 'state.json').write_text('{ not valid json ...')

        self.write_task('tasks/new-task.md', 'new-task', 'codex', ['src/**'])
        res = self.run_cli('prepare', 'tasks/new-task.md')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['corrompu', 'état', 'registre', 'refus', 'prudent']), res.stderr)

    def test_status_refuses_traversal_and_symlinks_without_creating_dirs(self):
        fresh_repo = Path(self.temp.name) / 'fresh_repo'
        fresh_repo.mkdir()
        shutil.copytree(self.scripts_dir, fresh_repo / 'scripts')

        res = subprocess.run(
            ['bash', str(fresh_repo / 'scripts/task-workspace.sh'), 'status', '../escape'],
            cwd=str(fresh_repo),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse((fresh_repo / '.workspace').exists())

        orch_dir = self.root / '.workspace/orchestration/tasks'
        orch_dir.mkdir(parents=True, exist_ok=True)
        sym_task = orch_dir / 'sym-task'
        outside_state = Path(self.temp.name) / 'outside_state'
        outside_state.mkdir()
        sym_task.symlink_to(outside_state)

        res_sym = self.run_cli('status', 'sym-task')
        self.assertNotEqual(res_sym.returncode, 0)
        self.assertTrue(
            any(w in res_sym.stderr.lower() for w in ['symlink', 'symbolique', 'interdit', 'inconnue', 'erreur']), res_sym.stderr
        )

    def test_regression_mission_imports_under_the_declared_python(self):
        """Défaut 1, mis à jour le 26/10/2026 (T94, Python 3.14 décidé par le propriétaire) : mission.py et les blocs Python de
        task-workspace.sh se lisent et s'importent sous l'interpréteur déclaré par .python-version."""
        mission_src = ROOT / 'template/scripts/lib/mission.py'
        self.assertTrue(mission_src.exists())

        targets = [(mission_src, mission_src.read_text(encoding='utf-8'))]

        sh_path = ROOT / 'template/scripts/task-workspace.sh'
        if sh_path.exists():
            sh_content = sh_path.read_text(encoding='utf-8')
            py_blocks = re.findall(r"<<\s*['\"]?PY(?:THON)?['\"]?\s*\n(.*?)\nPY(?:THON)?\b", sh_content, re.DOTALL)
            py_blocks.extend(re.findall(r"python3\s+-c\s+['\"](.*?)['\"]", sh_content, re.DOTALL))
            for idx, block in enumerate(py_blocks):
                targets.append((sh_path.with_name(f"task-workspace-inline-{idx}.py"), block))

        for file_path, code in targets:
            ast.parse(code, filename=str(file_path))

        # 5. Import du module sous l'interpréteur courant sans TypeError sur les annotations
        spec = importlib.util.spec_from_file_location('mission_declared_python_test', mission_src)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # 6. Appel avec argument None sans conflit de type
        resolved = mod.resolve_base_commit(self.root, None, self.base_commit)
        self.assertEqual(resolved, self.base_commit)

        # (b) Import par l'interpréteur déclaré (.python-version), dans un sous-processus, s'il est installé
        declared = shutil.which('python3.14')
        if declared:
            cmd = [
                declared,
                '-c',
                'import sys; from pathlib import Path; p = Path(sys.argv[1]); sys.path.insert(0, str(p.parent)); import mission; print("OK")',
                str(mission_src),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Échec d'importation sous {declared} :\n{res.stderr}")

    def test_regression_refuse_symlink_lock_file(self):
        """Défaut 2 : le fichier .lock ne doit jamais être suivi s'il est un lien symbolique."""
        orch_dir = self.root / '.workspace/orchestration'
        orch_dir.mkdir(parents=True, exist_ok=True)
        lock_path = orch_dir / '.lock'

        # Cible externe qui ne doit jamais être modifiée ni suivie
        outside_target = Path(self.temp.name) / 'fake_lock_target'
        outside_target.write_text('untouched\n')
        lock_path.symlink_to(outside_target)

        self.write_task('tasks/lock-test.md', 'lock-test', 'codex', ['src/**'])
        res = self.run_cli('prepare', 'tasks/lock-test.md')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['symlink', 'symbolique', 'interdit', 'verrou', 'lock']), res.stderr)
        self.assertEqual(outside_target.read_text(), 'untouched\n')

    def test_regression_refuse_nested_secrets_in_allowed(self):
        """Défaut 3 : secrets protégés en sous-dossiers (.env* et secrets/ à toute profondeur)."""
        nested_sensitive = [
            'backend/.env',
            'services/secrets/**',
            'pkg/secrets/key',
            'apps/web/.env.production',
            'deep/certs/server.key',
        ]
        for idx, pattern in enumerate(nested_sensitive):
            with self.subTest(pattern=pattern):
                task_id = f"nested-sec-{idx}"
                self.write_task(f"tasks/{task_id}.md", task_id, 'codex', [pattern])
                res = self.run_cli('prepare', f"tasks/{task_id}.md")
                self.assertNotEqual(res.returncode, 0)
                self.assertTrue(any(w in res.stderr.lower() for w in ['sensible', 'interdit', 'protégé', 'refus', 'périmètre']), res.stderr)

    def test_regression_folder_scope_without_glob_overlaps_child(self):
        """Défaut 4 : dossier sans glob ('src/api') chevauche son enfant ('src/api/auth.py')."""
        # A: dossier sans glob réservé en premier
        self.write_task('tasks/api-dir.md', 'task-api-dir', 'codex', ['src/api'])
        res_dir = self.run_cli('prepare', 'tasks/api-dir.md')
        self.assertEqual(res_dir.returncode, 0, res_dir.stderr)

        # Enfant réservé ensuite : doit être refusé pour conflit de réservation
        self.write_task('tasks/api-child.md', 'task-api-child', 'claude-sonnet', ['src/api/auth.py'])
        res_child = self.run_cli('prepare', 'tasks/api-child.md')
        self.assertNotEqual(res_child.returncode, 0)
        self.assertTrue(
            any(w in res_child.stderr.lower() for w in ['conflit', 'chevauchement', 'réservation', 'occupé', 'réservé']), res_child.stderr
        )

        # B: enfant réservé en premier sur un autre sous-domaine
        self.write_task('tasks/ui-child.md', 'task-ui-child', 'codex', ['components/ui/button.py'])
        res_ui_c = self.run_cli('prepare', 'tasks/ui-child.md')
        self.assertEqual(res_ui_c.returncode, 0, res_ui_c.stderr)

        # Dossier parent sans glob réservé ensuite : doit être refusé
        self.write_task('tasks/ui-dir.md', 'task-ui-dir', 'claude-haiku', ['components/ui'])
        res_ui_d = self.run_cli('prepare', 'tasks/ui-dir.md')
        self.assertNotEqual(res_ui_d.returncode, 0)
        self.assertTrue(
            any(w in res_ui_d.stderr.lower() for w in ['conflit', 'chevauchement', 'réservation', 'occupé', 'réservé']), res_ui_d.stderr
        )

    def test_regression_corrupt_registry_schema_without_allowed_or_inconsistent_status(self):
        """Défaut 5 : état corrompu sans allowed ou statut incohérent refusé, ne contourne pas les réservations."""
        tasks_dir = self.root / '.workspace/orchestration/tasks'
        tasks_dir.mkdir(parents=True, exist_ok=True)

        # 1. État sans champ allowed
        bad_dir1 = tasks_dir / 'corrupt-no-allowed'
        bad_dir1.mkdir(parents=True, exist_ok=True)
        (bad_dir1 / 'state.json').write_text(
            json.dumps({"schema_version": 1, "task_id": "corrupt-no-allowed", "agent": "codex", "status": "prepared"})
        )

        self.write_task('tasks/task-try1.md', 'task-try1', 'codex', ['src/**'])
        res1 = self.run_cli('prepare', 'tasks/task-try1.md')
        self.assertNotEqual(res1.returncode, 0)
        self.assertTrue(
            any(w in res1.stderr.lower() for w in ['corrompu', 'invalide', 'état', 'registre', 'refus', 'prudent']), res1.stderr
        )

        # 2. État avec statut inconnu/incohérent
        shutil.rmtree(bad_dir1)
        bad_dir2 = tasks_dir / 'corrupt-bad-status'
        bad_dir2.mkdir(parents=True, exist_ok=True)
        (bad_dir2 / 'state.json').write_text(
            json.dumps(
                {"schema_version": 1, "task_id": "corrupt-bad-status", "agent": "codex", "status": "incoherent_xyz", "allowed": ["src/**"]}
            )
        )

        self.write_task('tasks/task-try2.md', 'task-try2', 'codex', ['src/**'])
        res2 = self.run_cli('prepare', 'tasks/task-try2.md')
        self.assertNotEqual(res2.returncode, 0)
        self.assertTrue(
            any(w in res2.stderr.lower() for w in ['corrompu', 'invalide', 'état', 'registre', 'refus', 'prudent', 'statut']), res2.stderr
        )

        # 3. Status sur la tâche au statut corrompu doit échouer
        res_st = self.run_cli('status', 'corrupt-bad-status')
        self.assertNotEqual(res_st.returncode, 0)
        self.assertTrue(any(w in res_st.stderr.lower() for w in ['corrompu', 'invalide', 'état', 'erreur']), res_st.stderr)

    def test_regression_rollback_conditional_branch_deletion_preserves_moved_branch(self):
        """Défaut 6 : suppression conditionnelle git update-ref -d ne supprime pas si la branche a bougé."""
        # Créer un commit distinct de la base, puis revenir sur la base : la tâche part de la base
        # et le faux git déplacera la branche vers ce commit (conservé par une étiquette).
        base_before = self.git('rev-parse', 'HEAD').strip()
        (self.root / 'extra.txt').write_text('extra\n')
        self.git('add', 'extra.txt')
        self.git('commit', '-qm', 'extra commit')
        advanced_commit = self.git('rev-parse', 'HEAD').strip()
        self.git('tag', 'keep-advanced', advanced_commit)
        self.git('reset', '-q', '--hard', base_before)

        # Wrapper git qui simule l'échec du clonage MAIS avance la branche avant
        # Hors du dépôt : un dossier non suivi rendrait l'arbre sale et prepare refuserait avant le scénario.
        fake_bin = self.root.parent / 'fake-git-rollback'
        fake_bin.mkdir(parents=True, exist_ok=True)
        log_file = fake_bin / 'git_calls.log'
        if log_file.exists():
            log_file.unlink()
        fake_git = fake_bin / 'git'
        real_git = shutil.which('git')
        fake_git.write_text(f'''#!/bin/sh
case "$*" in
    *update-ref*)
        echo "$@" >> "{log_file}"
        ;;
    *" clone "*sim-moved*)
        "{real_git}" -C "{self.root}" update-ref refs/heads/workspace-task/sim-moved "{advanced_commit}"
        echo "fatal: simulated failure for clone" >&2
        exit 1
        ;;
esac
exec "{real_git}" "$@"
''')
        fake_git.chmod(0o755)

        env = {'PATH': f"{fake_bin}:{os.environ.get('PATH', '')}"}
        self.write_task('tasks/sim-moved.md', 'sim-moved', 'codex', ['src/**'])
        res = self.run_cli('prepare', 'tasks/sim-moved.md', env=env)
        self.assertNotEqual(res.returncode, 0)

        # La branche ayant bougé vers advanced_commit, update-ref -d ne doit PAS l'avoir supprimée
        branch_ref = self.git('rev-parse', '--verify', 'refs/heads/workspace-task/sim-moved').strip()
        self.assertEqual(branch_ref, advanced_commit)

        # Vérification probante : la commande de nettoyage passe bien le commit attendu à update-ref -d
        self.assertTrue(log_file.exists(), "update-ref doit avoir été invoqué pendant le rollback")
        log_content = log_file.read_text(encoding='utf-8')
        self.assertIn('update-ref', log_content)
        self.assertIn('-d', log_content)
        self.assertIn('refs/heads/workspace-task/sim-moved', log_content)
        self.assertIn(self.base_commit, log_content)

    def test_regression_refuse_scripts_and_root_and_empty_sensitive_patterns(self):
        """Point 1 (HAUTE) : allowed: ['scripts'], ['scripts/'], racine (., ./, /) et motifs vides refusés."""
        # 1. Vérification unitaire directe de is_sensitive_pattern
        mission_src = ROOT / 'template/scripts/lib/mission.py'
        spec = importlib.util.spec_from_file_location('mission_sens_test', mission_src)
        self.assertIsNotNone(spec and spec.loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for pattern in ['scripts', 'scripts/', '.', './', '/', '', '   ']:
            self.assertTrue(mod.is_sensitive_pattern(pattern), f"is_sensitive_pattern('{pattern}') doit retourner True")
        self.assertFalse(mod.is_sensitive_pattern('src/**'))

        # 2. Vérification de rejet par prepare
        for pattern in ['scripts', 'scripts/', '.', './', '/', '']:
            with self.subTest(pattern=pattern):
                task_id = f"task-pat-{abs(hash(pattern)) % 10000}"
                self.write_task(f"tasks/{task_id}.md", task_id, 'codex', [pattern])
                res = self.run_cli('prepare', f"tasks/{task_id}.md")
                self.assertNotEqual(res.returncode, 0)
                self.assertTrue(
                    any(
                        w in res.stderr.lower()
                        for w in ['sensible', 'interdit', 'protégé', 'invalide', 'vide', 'racine', 'périmètre', 'absolu']
                    ),
                    f"Pattern '{pattern}' doit être refusé : {res.stderr}",
                )

    def test_regression_partial_clone_failure_cleaned_up_for_retry(self):
        """Point 2 (MOYENNE) : échec partiel de git clone nettoie le clone et permet une nouvelle tentative."""
        fake_bin = self.root.parent / 'fake-git-wt-fail'
        fake_bin.mkdir(parents=True, exist_ok=True)
        fake_git = fake_bin / 'git'
        real_git = shutil.which('git')
        target_wt = self.clone('task-wt-fail')
        fake_git.write_text(f'''#!/bin/sh
case "$*" in
    *" clone "*task-wt-fail*)
        mkdir -p "{target_wt}"
        echo "partial checkout" > "{target_wt}/partial.txt"
        echo "fatal: unable to checkout working tree (simulated failure)" >&2
        exit 1
        ;;
esac
exec "{real_git}" "$@"
''')
        fake_git.chmod(0o755)

        env = {'PATH': f"{fake_bin}:{os.environ.get('PATH', '')}"}
        self.write_task('tasks/task-wt-fail.md', 'task-wt-fail', 'codex', ['src/**'])

        res1 = self.run_cli('prepare', 'tasks/task-wt-fail.md', env=env)
        self.assertNotEqual(res1.returncode, 0)

        # Le dossier de clone partiel doit avoir été nettoyé
        self.assertFalse(target_wt.exists(), "Le dossier de clone orphelin doit être nettoyé après échec.")

        # Nouvelle tentative avec git normal : doit réussir sans collision
        res2 = self.run_cli('prepare', 'tasks/task-wt-fail.md')
        self.assertEqual(res2.returncode, 0, f"La nouvelle tentative doit réussir : {res2.stderr}")
        self.assertTrue(target_wt.is_dir())
        self.assertTrue((self.root / '.workspace/orchestration/tasks/task-wt-fail/state.json').is_file())

    def test_regression_refuse_symlink_task_file(self):
        """Point 3 (MOYENNE) : lien symbolique interne sur le fichier de tâche refusé avant toute résolution."""
        self.write_task('tasks/vrai.md', 'task-sym-real', 'codex', ['src/**'])
        sym_task = self.root / 'tasks/lien.md'
        sym_task.symlink_to('vrai.md')

        res = self.run_cli('prepare', 'tasks/lien.md')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(
            any(w in res.stderr.lower() for w in ['symlink', 'symbolique', 'interdit']),
            f"Stderr doit mentionner l'interdiction de symlink : {res.stderr}",
        )


if __name__ == '__main__':
    unittest.main()
