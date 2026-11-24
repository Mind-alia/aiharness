import importlib
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
FAKE_MAKE = """#!/bin/sh
echo make "$@" >> "$CALLS"
if [ -n "${MAKE_FAIL+x}" ] && [ "$MAKE_FAIL" = "$*" ]; then
    exit 3
fi
exit 0
"""
FAKE_NORMINETTE = """#!/bin/sh
echo norminette "$@" >> "$CALLS"
if [ -n "${NORM_FAIL+x}" ]; then
    echo "Error: TAB_CHAR (line: 3, col: 1): tabulation"
    exit 1
fi
exit 0
"""


class ProfileCTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(dir=os.environ.get('WORKSPACE_TEST_TMP'))).resolve()
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.root = self.temp / 'project'
        self.scripts = self.root / 'scripts'
        (self.scripts / 'lib').mkdir(parents=True)
        for name in ('gate.sh', 'check-secret-tracing.sh'):
            shutil.copy2(ROOT / 'template/scripts' / name, self.scripts / name)
        for module in ('output.py', 'secrets.py'):
            shutil.copy2(ROOT / 'template/scripts/lib' / module, self.scripts / 'lib' / module)
        (self.scripts / 'docs-check.sh').write_text('#!/bin/sh\necho docs >> "$CALLS"\n')
        (self.root / 'project.profile').write_text('c\n')
        (self.root / 'Makefile').write_text('.PHONY: test\ntest:\n\t@true\n')
        (self.root / 'main.c').write_text('int main(void) { return 0; }\n')
        self.bin = self.temp / 'bin'
        self.bin.mkdir()
        (self.bin / 'python3').symlink_to(shutil.which('python3'))
        self.fake_executable('make', FAKE_MAKE)
        self.fake_executable('norminette', FAKE_NORMINETTE)
        self.log = self.temp / 'calls'
        self.env = dict(os.environ, PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}', CALLS=str(self.log))
        for key in ('MAKE_FAIL', 'NORM_FAIL'):
            self.env.pop(key, None)

    def fake_executable(self, name, text):
        target = self.bin / name
        target.write_text(text)
        target.chmod(0o755)

    def run_gate(self, *args):
        return subprocess.run(
            ['/bin/sh', str(self.scripts / 'gate.sh'), *args], cwd=self.temp, env=self.env, capture_output=True, text=True
        )

    def rows(self, result):
        return [json.loads(line) for line in result.stdout.splitlines()]

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_quick_runs_make_then_norminette_on_sorted_sources(self):
        (self.root / 'src').mkdir()
        (self.root / 'src/b.c').write_text('\n')
        (self.root / 'src/a.h').write_text('\n')
        result = self.run_gate('--quick')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rows(result), [{'secrets': True}, {'make': True}, {'norminette': True}])
        self.assertEqual(self.calls(), ['make', 'norminette main.c src/a.h src/b.c'])

    def test_full_adds_make_test_and_docs(self):
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([next(iter(row)) for row in self.rows(result)], ['secrets', 'make', 'test', 'norminette', 'docs'])
        self.assertEqual(self.calls(), ['make', 'make test', 'norminette main.c', 'docs'])

    def test_missing_makefile_is_non_configure(self):
        (self.root / 'Makefile').unlink()
        result = self.run_gate('--quick')
        self.assertEqual(result.returncode, 2)
        self.assertIn({'make': 'non_configure'}, self.rows(result))
        self.assertNotIn('norminette', self.calls())

    def test_makefile_without_declared_test_target_adds_no_test_line(self):
        for content in ('.PHONY: all\nall:\n\t@true\n', 'test:\n\t@true\n'):
            with self.subTest(makefile=content):
                (self.root / 'Makefile').write_text(content)
                self.log.unlink(missing_ok=True)
                result = self.run_gate()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('test', [next(iter(row)) for row in self.rows(result)])
                self.assertNotIn('make test', self.calls())

    def test_make_failure_stops_quick_before_norminette(self):
        self.env['MAKE_FAIL'] = ''
        result = self.run_gate('--quick')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn({'make': False}, self.rows(result))
        self.assertNotIn('norminette', self.calls())

    def test_make_test_failure_is_red_in_full_mode(self):
        self.env['MAKE_FAIL'] = 'test'
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn({'test': False}, self.rows(result))

    def test_absent_norminette_is_reported_without_failure(self):
        self.env['PATH'] = str(self.bin)
        (self.bin / 'norminette').unlink(missing_ok=True)
        result = self.run_gate('--quick')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('norminette', result.stderr)
        self.assertEqual(self.rows(result), [{'secrets': True}, {'make': True}])

    def test_norminette_error_line_reaches_error_output(self):
        self.env['NORM_FAIL'] = '1'
        result = self.run_gate('--quick')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('(line: 3, col: 1)', result.stderr)
        self.assertIn({'norminette': False}, self.rows(result))

    def test_no_c_sources_means_no_norminette_line(self):
        (self.root / 'main.c').unlink()
        result = self.run_gate('--quick')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rows(result), [{'secrets': True}, {'make': True}])
        self.assertNotIn('norminette', self.calls())

    def test_hidden_and_worktree_sources_are_never_checked(self):
        for hidden in ('.workspace/worktrees/task/bad.c', '.git/hook.c', '.hidden.h', '.cache/x/y.c'):
            path = self.root / hidden
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('\n')
        result = self.run_gate('--quick')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), ['make', 'norminette main.c'])


class SandboxCImageTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
        self.addCleanup(sys.path.remove, str(ROOT / 'template/scripts/lib'))
        self.sandbox = importlib.import_module('sandbox')
        self.addCleanup(sys.modules.pop, 'sandbox', None)

    def select(self, env):
        with mock.patch.dict(os.environ, env, clear=True):
            return self.sandbox.select_image('c', None)

    def test_c_profile_defaults_to_c_norm_image(self):
        self.assertEqual(self.select({}), 'workspace-c-norm:bookworm')
        self.assertEqual(self.sandbox.DEFAULT_IMAGE_C, 'workspace-c-norm:bookworm')

    def test_c_profile_uses_c_override_then_global_override(self):
        self.assertEqual(self.select({'WORKSPACE_IMAGE_C': 'my-c:1'}), 'my-c:1')
        self.assertEqual(self.select({'WORKSPACE_IMAGE_C': 'my-c:1', 'WORKSPACE_IMAGE': 'any:2'}), 'any:2')

    def test_other_profiles_are_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.sandbox.select_image('python', None), self.sandbox.DEFAULT_IMAGE_PYTHON)


if __name__ == '__main__':
    unittest.main()
