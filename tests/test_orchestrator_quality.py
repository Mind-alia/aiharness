"""Échecs de qualité connus du programme d'orchestration (T99, T114, T118) : catégorie lue dans la sortie unittest, blocs
rendus tels quels, `ruff format` limité aux fichiers Python du clone, erreurs pyright filtrées aux fichiers de la tâche."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'orchestrator'))

import quality  # noqa: E402

LINE, DASH = '=' * 70, '-' * 70


def failure(name, message):
    return f'{LINE}\nFAIL: {name} (tests.test_code_quality.CodeQualityTests.{name})\n{DASH}\nAssertionError: 1 != 0 : {message}\n\n'


FORMAT = failure('test_ruff_format_is_clean', 'Would reformat: tools/a.py')
LINT = failure('test_ruff_lint_is_clean', 'tools/a.py:3:5: C901 `f` is too complex (11 > 10)')
TYPES = failure('test_type_errors_stay_within_budget', '195 not less than or equal to 193')
OTHER = f'{LINE}\nERROR: test_merge (tests.test_task_workspace.MergeTests.test_merge)\n{DASH}\nTraceback\n\n'
END = f'{DASH}\nRan 40 tests in 2.000s\n\nFAILED (failures=1)\n'


class CategoryTests(unittest.TestCase):
    def test_format_alone_is_format(self):
        self.assertEqual(quality.category('...\n' + FORMAT + END), quality.FORMAT)

    def test_lint_with_or_without_format_is_lint(self):
        self.assertEqual(quality.category(LINT + END), quality.LINT)
        self.assertEqual(quality.category(FORMAT + LINT + END), quality.LINT)

    def test_type_budget_without_lint_is_types(self):
        # gt2-run-states, 3/11 : 195 erreurs pyright pour un budget de 193, seul test rouge de la suite hôte.
        self.assertEqual(quality.category(TYPES + END), quality.TYPES)
        self.assertEqual(quality.category(FORMAT + TYPES + END), quality.TYPES)
        self.assertEqual(quality.category(TYPES + LINT + END), quality.LINT)
        self.assertIsNone(quality.category(TYPES + OTHER + END))

    def test_any_other_failure_or_no_failure_is_unknown(self):
        self.assertIsNone(quality.category(FORMAT + OTHER + END))
        self.assertIsNone(quality.category('sh: make: not found\n'))
        self.assertIsNone(quality.category(''))
        self.assertIsNone(quality.category(None))

    def test_failures_keep_the_tool_output_as_is_and_bounded(self):
        text = quality.failures('début du journal\n' + FORMAT + LINT + END)
        self.assertIn('Would reformat: tools/a.py', text)
        self.assertIn('C901 `f` is too complex (11 > 10)', text)
        self.assertNotIn('début du journal', text)
        self.assertTrue(quality.failures(LINT * 200, limit=500).endswith('[…]'))


class FormatTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(folder.cleanup)
        self.clone = Path(os.path.realpath(folder.name))
        (self.clone / 'src').mkdir()
        (self.clone / 'src' / 'a.py').write_text('x=1\n')
        (self.clone / 'src' / 'notes.md').write_text('texte\n')
        (self.clone / 'src' / 'lien.py').symlink_to(self.clone / 'src' / 'a.py')
        self.log = self.clone / 'verify.log'

    def test_only_python_files_inside_the_clone_are_kept(self):
        names = ['src/a.py', 'src/notes.md', 'src/lien.py', '../dehors.py', 'src/absent.py', None]
        self.assertEqual(quality.python_files(self.clone, names), ['src/a.py'])

    def test_format_runs_the_pinned_command_in_the_clone(self):
        fake = self.clone / 'fake-ruff'
        fake.write_text('#!/bin/sh\npwd > called.txt\necho "$@" >> called.txt\necho "1 file reformatted"\n')
        fake.chmod(0o755)
        os.environ[quality.RUFF_ENV] = str(fake)
        self.addCleanup(os.environ.pop, quality.RUFF_ENV, None)
        self.assertEqual(quality.format_files(self.clone, ['src/a.py'], self.log), 0)
        called = (self.clone / 'called.txt').read_text().splitlines()
        self.assertEqual(called, [str(self.clone), 'format --no-cache -- src/a.py'])
        self.assertIn('1 file reformatted', self.log.read_text())

    def test_missing_ruff_is_127(self):
        os.environ[quality.RUFF_ENV] = str(self.clone / 'absent-ruff')
        self.addCleanup(os.environ.pop, quality.RUFF_ENV, None)
        self.assertEqual(quality.format_files(self.clone, ['src/a.py'], self.log), quality.RUFF_MISSING)

    def test_default_command_is_the_pinned_version_of_the_quality_suite(self):
        text = (ROOT / 'tests' / 'test_code_quality.py').read_text(encoding='utf-8')
        version = quality.RUFF.split()[-1].split('@')[1]
        self.assertIn(f"RUFF_VERSION = '{version}'", text)

    def test_type_errors_keep_only_the_task_files(self):
        fake = self.clone / 'fake-pyright'
        diagnostics = [
            {
                'file': 'src/a.py',
                'severity': 'error',
                'message': 'Argument of type "str | None"\n  is not assignable',
                'rule': 'reportArgumentType',
                'range': {'start': {'line': 210, 'character': 4}},
            },
            {'file': 'src/a.py', 'severity': 'warning', 'message': 'unused', 'range': {'start': {'line': 1, 'character': 0}}},
            {'file': 'tools/other.py', 'severity': 'error', 'message': 'ancienne erreur du budget', 'range': {'start': {'line': 3}}},
        ]
        script = 'import json, os\nd = %r\nfor x in d:\n    x["file"] = os.path.join(os.getcwd(), x["file"])\n' % diagnostics
        fake.write_text('#!%s\n%sprint(json.dumps({"generalDiagnostics": d, "summary": {"errorCount": 2}}))\n' % (sys.executable, script))
        fake.chmod(0o755)
        os.environ[quality.PYRIGHT_ENV] = str(fake)
        self.addCleanup(os.environ.pop, quality.PYRIGHT_ENV, None)
        text = quality.type_errors(self.clone, ['src/a.py', 'src/notes.md'])
        self.assertEqual(text, 'src/a.py:211:5: error: Argument of type "str | None" is not assignable (reportArgumentType)')

    def test_missing_pyright_says_what_to_run(self):
        os.environ[quality.PYRIGHT_ENV] = str(self.clone / 'absent-pyright')
        self.addCleanup(os.environ.pop, quality.PYRIGHT_ENV, None)
        self.assertIn('make typecheck', quality.type_errors(self.clone, ['src/a.py']))

    def test_pinned_pyright_is_the_one_of_the_quality_suite(self):
        text = (ROOT / 'tests' / 'test_code_quality.py').read_text(encoding='utf-8')
        self.assertIn("PYRIGHT_VERSION = '1.1.402'", text)
        self.assertEqual(quality.PYRIGHT, 'pyright')

    def test_read_from_an_offset(self):
        self.log.write_text('ancien\nnouveau\n')
        self.assertEqual(quality.read_from(self.log, len('ancien\n')), 'nouveau\n')
        self.assertEqual(quality.size(self.clone / 'absent.log'), 0)
        self.assertEqual(quality.read_from(self.clone / 'absent.log', 0), '')


if __name__ == '__main__':
    unittest.main()
