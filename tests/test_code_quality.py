"""T93: the code rules are enforced by the suite itself, wherever the pinned tools are available.

ruff (lint and format) must be clean; pyright keeps an error budget that may only go down (T76 and later work
lower it; never raise TYPE_ERROR_BUDGET). The interpreter must be the one declared in .python-version (T94).
Tools are pinned in the Makefile; a missing tool skips its test (the only allowed skip, AGENTS.md).
"""

import json
import re
import shutil
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUFF_VERSION = '0.17.2'
PYRIGHT_VERSION = '1.1.402'
# pyright errors in template/scripts/lib and tools: 212 on 26 October 2026, 193 after the split of T14 phase A. Only ever lower it.
TYPE_ERROR_BUDGET = 193


def ruff_command():
    """The pinned ruff: `ruff` on PATH when it is that version (the python-git image has it, T98), else uvx offline."""
    if shutil.which('ruff') is not None:
        try:
            done = subprocess.run(['ruff', '--version'], capture_output=True, text=True, timeout=60, cwd=ROOT)
        except (OSError, subprocess.SubprocessError):
            done = None
        if done is not None and done.returncode == 0 and done.stdout.split()[-1:] == [RUFF_VERSION]:
            return ['ruff']
    return ['uvx', '--offline', f'ruff@{RUFF_VERSION}']


RUFF = ruff_command()


def ruff_available():
    if RUFF == ['ruff']:
        return True
    if shutil.which('uvx') is None:
        return False
    try:
        done = subprocess.run(RUFF + ['--version'], capture_output=True, text=True, timeout=60, cwd=ROOT)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def pyright_available():
    if shutil.which('pyright') is None:
        return False
    try:
        done = subprocess.run(['pyright', '--version'], capture_output=True, text=True, timeout=120, cwd=ROOT)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and PYRIGHT_VERSION in done.stdout


class CodeQualityTests(unittest.TestCase):
    def test_interpreter_matches_requires_python(self):
        declared = re.fullmatch(r'(\d+)\.(\d+)\s*', (ROOT / '.python-version').read_text(encoding='utf-8'))
        self.assertIsNotNone(declared)
        self.assertGreaterEqual(
            sys.version_info[:2],
            (int(declared.group(1)), int(declared.group(2))),
            'run the suite with the interpreter of .python-version (make test uses python3.14)',
        )

    @unittest.skipUnless(ruff_available(), 'ruff 0.17.2 not available offline (uvx)')
    def test_ruff_lint_is_clean(self):
        done = subprocess.run(RUFF + ['check', '--no-cache', '--output-format', 'concise', '.'], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(done.returncode, 0, done.stdout[-4000:] + done.stderr[-2000:])

    @unittest.skipUnless(ruff_available(), 'ruff 0.17.2 not available offline (uvx)')
    def test_ruff_format_is_clean(self):
        done = subprocess.run(RUFF + ['format', '--no-cache', '--check', '.'], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(done.returncode, 0, done.stdout[-4000:] + done.stderr[-2000:])

    @unittest.skipUnless(pyright_available(), 'pyright %s not installed' % PYRIGHT_VERSION)
    def test_type_errors_stay_within_budget(self):
        done = subprocess.run(['pyright', '--outputjson'], capture_output=True, text=True, cwd=ROOT)
        errors = json.loads(done.stdout)['summary']['errorCount']
        self.assertLessEqual(
            errors, TYPE_ERROR_BUDGET, 'new type errors: fix them, never raise TYPE_ERROR_BUDGET (make typecheck lists them)'
        )


class ToolPinTests(unittest.TestCase):
    def test_sandbox_image_and_makefile_pin_the_same_ruff(self):
        dockerfile = (ROOT / 'template' / 'docker' / 'python-git.Dockerfile').read_text(encoding='utf-8')
        self.assertIn(f'ruff=={RUFF_VERSION}', dockerfile)
        self.assertIn('COPY --from=ruff /usr/local/bin/ruff /usr/local/bin/ruff', dockerfile)
        self.assertIn(f'ruff@{RUFF_VERSION}', (ROOT / 'Makefile').read_text(encoding='utf-8'))

    def test_sandbox_image_and_makefile_pin_the_same_pyright(self):
        # T116 : sans pyright dans l'image, un budget de types dépassé n'apparaissait que sur l'hôte (3/11, gt2-run-states).
        dockerfile = (ROOT / 'template' / 'docker' / 'python-git.Dockerfile').read_text(encoding='utf-8')
        self.assertIn(f'pyright/-/pyright-{PYRIGHT_VERSION}.tgz', dockerfile)
        self.assertIn(f"grep -qx 'pyright {PYRIGHT_VERSION}'", dockerfile)
        self.assertRegex(dockerfile, r"hexdigest\(\) == '[0-9a-f]{64}'")
        self.assertIn(f'version {PYRIGHT_VERSION}', (ROOT / 'Makefile').read_text(encoding='utf-8'))

    def test_a_ruff_on_path_is_used_only_at_the_pinned_version(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None) as folder:
            fake = Path(folder) / 'ruff'
            for version, expected in ((RUFF_VERSION, ['ruff']), ('0.1.0', ['uvx', '--offline', f'ruff@{RUFF_VERSION}'])):
                fake.write_text(f'#!/bin/sh\necho "ruff {version}"\n')
                fake.chmod(0o755)
                with unittest.mock.patch.dict(os.environ, {'PATH': folder + os.pathsep + os.environ.get('PATH', '')}):
                    self.assertEqual(ruff_command(), expected)


if __name__ == '__main__':
    unittest.main()
