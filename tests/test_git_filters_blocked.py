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

import workdir  # noqa: E402

CLEAN_CONFIG = (
    '[core]\n\trepositoryformatversion = 0\n\thooksPath = /dev/null\n\tfsmonitor = false\n'
    '[user]\n\tname = Test\n\temail = test@example.invalid\n'
)
REFUSED_SECTIONS = (
    ('[Filter "X"]\n\tsmudge = x\n', 'filter.x.smudge'),
    ('[filter "x"]\n\tclean = x\n', 'filter.x.clean'),
    ('[filter "x"]\n\tprocess = x\n', 'filter.x.process'),
    ('[merge "m"]\n\tdriver = x\n', 'merge.m.driver'),
    ('[diff "d"]\n\ttextconv = x\n', 'diff.d.textconv'),
    ('[diff "d"]\n\tcommand = x\n', 'diff.d.command'),
    ('[diff]\n\texternal = x\n', 'diff.external'),
    ('[include]\n\tpath = x\n', 'include.path'),
    ('[includeIf "gitdir:/"]\n\tpath = x\n', 'includeif.gitdir:/.path'),
    ('[core]\n\tpager = x\n', 'core.pager'),
    ('[core]\n\teditor = x\n', 'core.editor'),
    ('[core]\n\tsshCommand = x\n', 'core.sshcommand'),
    ('[core]\n\tfsmonitor = true\n', 'core.fsmonitor'),
    ('[core]\n\thooksPath = .githooks\n', 'core.hookspath'),
    ('[core]\n\tattributesFile = x\n', 'core.attributesfile'),
    ('[credential]\n\thelper = x\n', 'credential.helper'),
    ('[credential "https://h"]\n\thelper = x\n', 'credential.https://h.helper'),
    ('[gpg]\n\tprogram = x\n', 'gpg.program'),
    ('[pager]\n\tlog = x\n', 'pager.log'),
    ('[sequence]\n\teditor = x\n', 'sequence.editor'),
    ('[uploadpack]\n\tpackObjectsHook = x\n', 'uploadpack.packobjectshook'),
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'core.hooksPath=/dev/null', '-C', str(cwd), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class CloneConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.temp.cleanup)
        self.clone = Path(os.path.realpath(self.temp.name)) / 'clone'
        (self.clone / '.git').mkdir(parents=True)
        self.config = self.clone / '.git' / 'config'
        self.config.write_text(CLEAN_CONFIG, encoding='utf-8')

    def test_hardening_is_exact(self):
        self.assertEqual(
            workdir.GIT_HARDENING, ['-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=/dev/null']
        )

    def test_clean_config_has_no_refused_key(self):
        self.assertEqual(workdir.unsafe_clone_config(self.clone), [])
        workdir.check_clone_config(self.clone)

    def test_each_refused_section_is_named_in_lowercase(self):
        for text, key in REFUSED_SECTIONS:
            with self.subTest(key=key):
                self.config.write_text(CLEAN_CONFIG + text, encoding='utf-8')
                self.assertEqual(workdir.unsafe_clone_config(self.clone), [key])

    def test_list_is_sorted_without_duplicates(self):
        self.config.write_text(CLEAN_CONFIG + '[filter "b"]\n\tsmudge = x\n\tsmudge = y\n[filter "a"]\n\tclean = x\n', encoding='utf-8')
        self.assertEqual(workdir.unsafe_clone_config(self.clone), ['filter.a.clean', 'filter.b.smudge'])

    def test_safe_values_are_exact(self):
        self.config.write_text('[core]\n\thooksPath = /dev/null/\n', encoding='utf-8')
        self.assertEqual(workdir.unsafe_clone_config(self.clone), ['core.hookspath'])

    def test_check_names_the_keys(self):
        self.config.write_text(CLEAN_CONFIG + '[filter "x"]\n\tsmudge = x\n', encoding='utf-8')
        with self.assertRaises(ValueError) as caught:
            workdir.check_clone_config(self.clone)
        self.assertEqual(str(caught.exception), 'configuration git du clone refusée : filter.x.smudge')

    def test_config_symlink_is_refused_without_reading_the_target(self):
        target = self.clone.parent / 'elsewhere'
        target.write_text(CLEAN_CONFIG, encoding='utf-8')
        self.config.unlink()
        self.config.symlink_to(target)
        self.assertEqual(workdir.unsafe_clone_config(self.clone), ['.git/config (lien symbolique)'])

    def test_git_dir_symlink_is_refused(self):
        moved = self.clone.parent / 'moved-git'
        (self.clone / '.git').rename(moved)
        (self.clone / '.git').symlink_to(moved)
        self.assertEqual(workdir.unsafe_clone_config(self.clone), ['.git (pas un dossier)'])

    def test_missing_config_is_refused(self):
        self.config.unlink()
        self.assertEqual(workdir.unsafe_clone_config(self.clone), ['.git/config (absent)'])

    def test_malformed_config_is_unreadable(self):
        self.config.write_text('[core\n\tbroken\n', encoding='utf-8')
        self.assertEqual(workdir.unsafe_clone_config(self.clone), ['.git/config (illisible)'])

    def test_included_file_is_never_followed(self):
        included = self.clone.parent / 'included.config'
        included.write_text('[filter "x"]\n\tsmudge = x\n', encoding='utf-8')
        self.config.write_text(CLEAN_CONFIG + '[include]\n\tpath = %s\n' % included, encoding='utf-8')
        self.assertEqual(workdir.unsafe_clone_config(self.clone), ['include.path'])


@unittest.skipUnless(shutil.which('git'), 'git absent')
class MergeRefusedTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get('WORKSPACE_TESTS') == 'fast':
            self.skipTest('module lent : Git réel et sous-processus ; suite complète seulement')
        tmp = ROOT / '.workspace/tmp-tests'
        tmp.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(tmp))
        self.addCleanup(self.temp.cleanup)
        self.work_dir = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.work_dir.cleanup)
        patcher = mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': os.path.realpath(self.work_dir.name)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = (Path(self.temp.name) / 'project').resolve()
        scripts = self.root / 'scripts'
        (scripts / 'lib').mkdir(parents=True)
        for source in (ROOT / 'template/scripts/lib').glob('*.py'):
            shutil.copy2(str(source), str(scripts / 'lib' / source.name))
        for name in ('task-workspace.sh', 'verify-task.sh', 'sandbox.sh'):
            shutil.copy2(str(ROOT / 'template/scripts' / name), str(scripts / name))
        self.cli = scripts / 'task-workspace.sh'
        _git(self.root, 'init', '-q')
        (self.root / '.gitignore').write_text('.workspace/\ntasks/\n')
        (self.root / 'src').mkdir()
        (self.root / 'src/app.py').write_text('print("initial")\n')
        _git(self.root, 'add', '.')
        _git(self.root, 'commit', '-qm', 'initial commit')

    def run_cli(self, *args):
        return subprocess.run(['bash', str(self.cli), *args], cwd=str(self.root), capture_output=True, text=True)

    def test_merge_of_a_clone_with_a_filter_is_refused(self):
        task = self.root / 'tasks/task-1.md'
        task.parent.mkdir()
        task.write_text(
            '---\nid: task-1\nagent: codex\nallowed:\n  - src/**\nverify: test -f src/app.py\n'
            'verify_profile: off\nisolation: none\n---\n# feat: x\nSuite\n',
            encoding='utf-8',
        )
        prepared = self.run_cli('prepare', str(task), '--allow-slim-image')
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        clone = workdir.task_worktree(self.root, 'task-1')
        self.assertEqual(workdir.unsafe_clone_config(clone), [])
        (clone / 'src/app.py').write_text('changed\n')
        _git(clone, 'add', 'src/app.py')
        _git(clone, 'commit', '-qm', 'task change')
        with (clone / '.git' / 'config').open('a', encoding='utf-8') as stream:
            stream.write('[filter "attack"]\n\tsmudge = touch %s\n' % (self.root / 'ran.txt'))
        head = subprocess.run(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout
        result = self.run_cli('merge', 'task-1', '--no-verify-again')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('task-workspace.sh: configuration git du clone refusée : filter.attack.smudge', result.stderr)
        self.assertEqual(subprocess.run(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout, head)
        self.assertFalse((self.root / 'ran.txt').exists())
        self.assertFalse((self.root / 'src/app.py').read_text() == 'changed\n')


if __name__ == '__main__':
    unittest.main()
