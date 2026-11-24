import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'template/scripts/lib/codex_profile.py'

spec = importlib.util.spec_from_file_location('codex_profile_under_test', SCRIPT)
codex_profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codex_profile)


def table(project_access):
    return '{":minimal"="read",":project_roots"="%s",":tmpdir"="write"}' % project_access


class ConfigArgsTests(unittest.TestCase):
    def test_profile_name(self):
        self.assertEqual(codex_profile.PROFILE, 'atelier')

    def test_write_mode_lets_codex_write_in_the_project(self):
        self.assertEqual(
            codex_profile.config_args('write'),
            [
                '-c',
                'default_permissions="atelier"',
                '-c',
                'permissions.atelier.filesystem=' + table('write'),
            ],
        )

    def test_review_mode_only_reads_the_project(self):
        self.assertEqual(
            codex_profile.config_args('review'),
            [
                '-c',
                'default_permissions="atelier"',
                '-c',
                'permissions.atelier.filesystem=' + table('read'),
            ],
        )

    def test_the_host_is_not_readable_beyond_the_minimum(self):
        for mode in ('write', 'review'):
            filesystem = codex_profile.config_args(mode)[3]
            self.assertEqual(filesystem.count('='), 4)
            for allowed in (':minimal', ':project_roots', ':tmpdir'):
                self.assertIn('"%s"=' % allowed, filesystem)
            self.assertNotIn(':root', filesystem.replace(':project_roots', ''))
            self.assertNotIn('home', filesystem)

    def test_any_other_mode_is_refused(self):
        for mode in ('', 'other', 'WRITE', 'read-only', None):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    codex_profile.config_args(mode)


class CommandLineTests(unittest.TestCase):
    def run_script(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], stdin=subprocess.DEVNULL, capture_output=True, text=True)

    def test_prints_one_argument_per_line(self):
        for mode in ('write', 'review'):
            with self.subTest(mode=mode):
                result = self.run_script(mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), codex_profile.config_args(mode))
                self.assertTrue(result.stdout.endswith('\n'))
                self.assertEqual(result.stderr, '')

    def test_invalid_usage_is_64_with_nothing_on_stdout(self):
        for args in ((), ('other',), ('write', 'review'), ('',)):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 64)
                self.assertEqual(result.stdout, '')
                self.assertIn('Usage', result.stderr)


if __name__ == '__main__':
    unittest.main()
