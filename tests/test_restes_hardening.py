from __future__ import annotations

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

import executor_guard  # noqa: E402
import workdir  # noqa: E402

CLEAN_CONFIG = (
    '[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n\thooksPath = /dev/null\n'
    '\tfsmonitor = false\n[user]\n\tname = Test\n\temail = test@example.invalid\n'
)
REFUSED_LINES = (
    ('[core]\n\tworktree = /tmp/x\n', 'core.worktree'),
    ('[core]\n\tsparseCheckout = true\n', 'core.sparsecheckout'),
    ('[extensions]\n\tworktreeConfig = true\n', 'extensions.worktreeconfig'),
    ('[alias]\n\tst = status\n', 'alias.st'),
    ('[remote "origin"]\n\turl = x\n', 'remote.origin.url'),
)
SESSION_A = 'dd7c9422-1c89-4018-93a3-c86fb6345339'
SESSION_B = '0b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d'


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'core.hooksPath=/dev/null', '-C', str(cwd), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class AllowListTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.temp.cleanup)
        self.clone = Path(os.path.realpath(self.temp.name)) / 'clone'
        (self.clone / '.git').mkdir(parents=True)
        self.config = self.clone / '.git' / 'config'
        self.config.write_text(CLEAN_CONFIG, encoding='utf-8')

    def refused(self):
        return workdir.unsafe_clone_config(self.clone)

    def test_constants(self):
        self.assertFalse(hasattr(workdir, 'UNSAFE_CONFIG_KEYS'))
        self.assertEqual(workdir.EXTRA_CONFIG_SOURCES, ('config.worktree', 'commondir', 'worktrees'))
        self.assertEqual(
            workdir.SAFE_CONFIG_VALUES,
            {'core.fsmonitor': 'false', 'core.hookspath': '/dev/null', 'core.bare': 'false', 'core.repositoryformatversion': '0'},
        )

    def test_clean_config_is_accepted(self):
        self.assertEqual(self.refused(), [])

    def test_unknown_keys_are_refused(self):
        for text, key in REFUSED_LINES:
            with self.subTest(key=key):
                self.config.write_text(CLEAN_CONFIG + text, encoding='utf-8')
                self.assertEqual(self.refused(), [key])

    def test_safe_keys_with_other_values_are_refused(self):
        for old, new, key in (
            ('bare = false', 'bare = true', 'core.bare'),
            ('repositoryformatversion = 0', 'repositoryformatversion = 1', 'core.repositoryformatversion'),
        ):
            with self.subTest(key=key):
                self.config.write_text(CLEAN_CONFIG.replace(old, new), encoding='utf-8')
                self.assertEqual(self.refused(), [key])

    def test_extra_sources_are_refused(self):
        git_dir = self.clone / '.git'
        for name, make in (
            ('config.worktree', lambda p: p.write_text('')),
            ('commondir', lambda p: p.write_text('x')),
            ('worktrees', lambda p: p.mkdir()),
        ):
            with self.subTest(name=name):
                path = git_dir / name
                make(path)
                self.assertEqual(self.refused(), ['.git/%s (source de configuration refusée)' % name])
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()

    def test_extra_source_symlink_is_refused(self):
        (self.clone / '.git' / 'commondir').symlink_to(self.clone.parent)
        self.assertEqual(self.refused(), ['.git/commondir (source de configuration refusée)'])

    def test_git_file_is_not_a_directory(self):
        shutil.rmtree(str(self.clone / '.git'))
        (self.clone / '.git').write_text('gitdir: /elsewhere\n', encoding='utf-8')
        self.assertEqual(self.refused(), ['.git (pas un dossier)'])

    def test_missing_config(self):
        self.config.unlink()
        self.assertEqual(self.refused(), ['.git/config (absent)'])

    def test_config_symlink(self):
        target = self.clone.parent / 'elsewhere'
        target.write_text(CLEAN_CONFIG, encoding='utf-8')
        self.config.unlink()
        self.config.symlink_to(target)
        self.assertEqual(self.refused(), ['.git/config (lien symbolique)'])


@unittest.skipUnless(shutil.which('git'), 'git absent')
class PreparedCloneTests(unittest.TestCase):
    def test_clone_prepared_by_the_circuit_is_accepted(self):
        if os.environ.get('WORKSPACE_TESTS') == 'fast':
            self.skipTest('module lent : Git réel et sous-processus ; suite complète seulement')
        tmp = ROOT / '.workspace/tmp-tests'
        tmp.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=str(tmp))
        self.addCleanup(temp.cleanup)
        work_dir = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(work_dir.cleanup)
        patcher = mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': os.path.realpath(work_dir.name)})
        patcher.start()
        self.addCleanup(patcher.stop)
        root = (Path(temp.name) / 'project').resolve()
        scripts = root / 'scripts'
        (scripts / 'lib').mkdir(parents=True)
        for source in (ROOT / 'template/scripts/lib').glob('*.py'):
            shutil.copy2(str(source), str(scripts / 'lib' / source.name))
        for name in ('task-workspace.sh', 'verify-task.sh', 'sandbox.sh'):
            shutil.copy2(str(ROOT / 'template/scripts' / name), str(scripts / name))
        _git(root, 'init', '-q')
        (root / '.gitignore').write_text('.workspace/\ntasks/\n')
        (root / 'src').mkdir()
        (root / 'src/app.py').write_text('print("initial")\n')
        _git(root, 'add', '.')
        _git(root, 'commit', '-qm', 'initial commit')
        task = root / 'tasks/task-1.md'
        task.parent.mkdir()
        task.write_text(
            '---\nid: task-1\nagent: codex\nallowed:\n  - src/**\nverify: test -f src/app.py\n'
            'verify_profile: off\nisolation: none\n---\n# feat: x\nSuite\n',
            encoding='utf-8',
        )
        prepared = subprocess.run(
            ['bash', str(scripts / 'task-workspace.sh'), 'prepare', str(task), '--allow-slim-image'],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertEqual(workdir.unsafe_clone_config(workdir.task_worktree(root, 'task-1')), [])


class SessionBoundReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.temp.cleanup)
        base = Path(os.path.realpath(self.temp.name))
        self.worktree = base / 'wt'
        self.worktree.mkdir()
        self.config = base / 'claude-config'
        patcher = mock.patch.dict(os.environ, {executor_guard.CONFIG_DIR_VARIABLE: str(self.config)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = Path(executor_guard.session_output_roots(str(self.worktree))[1])
        for session in (SESSION_A, SESSION_B):
            path = self.root / session / 'tool-results' / 'x'
            path.parent.mkdir(parents=True)
            path.write_text('out\n')
        self.target = self.root / SESSION_A / 'tool-results' / 'x'

    def decide(self, **extra):
        payload = {'tool_name': 'Read', 'tool_input': {'file_path': str(self.target)}}
        payload.update(extra)
        return executor_guard.decide(payload, str(self.worktree))

    def test_own_session_is_allowed(self):
        self.assertIsNone(self.decide(session_id=SESSION_A))

    def test_other_absent_empty_or_invalid_session_is_refused(self):
        for session in (SESSION_B, '', '../x', None, 5, SESSION_A.upper(), SESSION_A + '\n'):
            with self.subTest(session=session):
                self.assertIn('hors du worktree', self.decide(session_id=session))
        self.assertIn('hors du worktree', self.decide())

    def test_direct_call(self):
        wt = str(self.worktree)
        self.assertTrue(executor_guard.is_session_output(str(self.target), wt, SESSION_A))
        self.assertFalse(executor_guard.is_session_output(str(self.target), wt, SESSION_B))
        self.assertFalse(executor_guard.is_session_output(str(self.target), wt))


if __name__ == '__main__':
    unittest.main()
