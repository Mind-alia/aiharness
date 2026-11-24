import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Keep fixtures in the repository: system temporary directories may be sandboxed.
TEST_TMP = ROOT / '.workspace/tmp-tests'
TEST_TMP.mkdir(parents=True, exist_ok=True)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fake_bin = self.root / 'fake-bin'
        fake_bin.mkdir()
        for name in ('codex', 'agy', 'claude'):
            executable = fake_bin / name
            executable.write_text('#!/bin/sh\nexit 99\n')
            executable.chmod(0o755)
        self.env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ['PATH'])
        shutil.copytree(ROOT / 'template/scripts', self.root / 'scripts')
        self.git('init', '-q')
        self.write('.gitignore', '.workspace/\n')
        self.write('task.md', '---\nid: scope-test\nagent: codex\nallowed:\n  - src/**\n  - single/*.txt\n---\nTask\n')
        self.write('src/a.txt', 'original')
        self.write('outside.txt', 'original')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args])

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)

    def run_script(self, name, *args):
        return subprocess.run([str(self.root / 'scripts' / name), *args], cwd=self.root, env=self.env, text=True, capture_output=True)

    def baseline(self):
        result = self.run_script('scope-baseline.sh', 'task.md')
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.root / '.workspace/run/delegate/scope-test/baseline.json'

    def check(self, expected, *args):
        result = self.run_script('check-scope.sh', 'task.md', *args)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_allowed_file(self):
        self.baseline()
        self.write('src/a.txt', 'changed')
        self.check(0)

    def test_forbidden_file_is_not_modified_by_check(self):
        self.baseline()
        self.write('outside.txt', 'changed')
        self.assertIn('hors périmètre : outside.txt', self.check(1).stdout)
        self.assertEqual((self.root / 'outside.txt').read_text(), 'changed')

    def test_new_files_and_recursive_patterns(self):
        self.baseline()
        self.write('src/deep/new.txt', 'new')
        self.write('src/root.txt', 'new')
        self.check(0)
        self.write('new file.txt', 'new')
        self.assertIn('hors périmètre : new file.txt', self.check(1).stdout)

    def test_star_does_not_cross_directories(self):
        self.baseline()
        self.write('single/a.txt', 'new')
        self.check(0)
        self.write('single/deep/a.txt', 'new')
        self.check(1)

    def test_deleted_file(self):
        self.baseline()
        (self.root / 'src/a.txt').unlink()
        self.check(0)
        (self.root / 'outside.txt').unlink()
        self.check(1)

    def test_preexisting_change_untouched_then_touched(self):
        self.write('outside.txt', 'before')
        baseline = self.baseline()
        self.assertEqual(json.loads(baseline.read_text())['version'], 1)
        self.assertIn(hashlib.sha256(b'before').hexdigest(), baseline.read_text())
        self.check(0, '--baseline', str(baseline))
        self.write('outside.txt', 'after')
        self.check(1)

    def test_preexisting_change_reverted_is_reported(self):
        self.write('outside.txt', 'before')
        self.baseline()
        self.write('outside.txt', 'original')
        self.check(1)

    def test_preexisting_untracked_and_deleted_files(self):
        self.write('untracked.txt', 'before')
        (self.root / 'outside.txt').unlink()
        self.baseline()
        self.check(0)
        self.write('outside.txt', 'restored')
        self.check(1)

    def test_rename_checks_both_paths(self):
        self.baseline()
        self.git('mv', 'outside.txt', 'src/moved.txt')
        self.assertIn('outside.txt', self.check(1).stdout)

    def test_unignored_baseline_does_not_report_itself(self):
        self.write('.gitignore', '')
        self.git('add', '.gitignore')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'unignore runtime')
        self.baseline()
        self.check(0)

    def test_staged_change_and_unusual_filename(self):
        self.baseline()
        self.write('strange\nname.txt', 'new')
        self.git('add', 'strange\nname.txt')
        self.check(1)

    def test_missing_baseline_fails(self):
        self.check(2)


if __name__ == '__main__':
    unittest.main()
