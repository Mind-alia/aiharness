"""Documentation check: local Markdown links and append-only docs/LOG.md."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'template/scripts/lib/docs_links.py'


class DocsLinksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def check(self):
        return subprocess.run([sys.executable, str(SCRIPT), str(self.root)], capture_output=True, text=True)

    def git(self, *args):
        subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=self.root, check=True, capture_output=True)

    def assertBroken(self, result, *lines):  # noqa: N802
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout.splitlines(), list(lines))

    def test_valid_links_pass(self):
        self.write('a.md', '[b](docs/b.md) [dossier](docs) [racine](/a.md#x) [q](docs/b.md?v=1#y)\n')
        self.write('docs/b.md', '[retour](../a.md)\n')
        result = self.check()
        self.assertEqual((result.returncode, result.stdout), (0, ''))

    def test_broken_link_is_reported_as_written(self):
        self.write('docs/a.md', 'voir [x](absent.md#ancre)\n')
        self.assertBroken(self.check(), 'DOCS FAIL: lien cassé docs/a.md -> absent.md#ancre')

    def test_root_relative_target_resolves_from_root(self):
        self.write('docs/a.md', '[x](/b.md)\n')
        self.assertBroken(self.check(), 'DOCS FAIL: lien cassé docs/a.md -> /b.md')

    def test_link_inside_code_block_is_ignored(self):
        self.write('a.md', '```\n[x](absent.md)\n```\n[y](absent.md)\n')
        self.assertBroken(self.check(), 'DOCS FAIL: lien cassé a.md -> absent.md')

    def test_urls_and_anchors_are_ignored(self):
        self.write('a.md', '[w](https://exemple.fr/x) [h](http://exemple.fr) [m](mailto:a@b.fr) [a](#section)\n')
        result = self.check()
        self.assertEqual((result.returncode, result.stdout), (0, ''))

    def test_excluded_folders_are_not_scanned(self):
        for folder in ('node_modules/p', '.git/x', '.workspace/t', 'docs/archive'):
            self.write(folder + '/a.md', '[x](absent.md)\n')
        result = self.check()
        self.assertEqual((result.returncode, result.stdout), (0, ''))

    @unittest.skipUnless(shutil.which('git'), 'git absent')
    def test_log_modified_in_the_middle_fails_and_extended_passes(self):
        self.write('docs/LOG.md', '# Journal\n\n- un\n- deux\n')
        self.git('init', '-q')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'log')
        self.write('docs/LOG.md', '# Journal\n\n- un\n- deux\n- trois\n')
        result = self.check()
        self.assertEqual((result.returncode, result.stdout), (0, ''))
        self.write('docs/LOG.md', '# Journal\n\n- UN\n- deux\n- trois\n')
        self.assertBroken(self.check(), "DOCS FAIL: docs/LOG.md n'est plus en ajout seul")

    @unittest.skipUnless(shutil.which('git'), 'git absent')
    def test_untracked_log_is_not_checked(self):
        self.git('init', '-q')
        self.write('docs/LOG.md', 'quelconque\n')
        result = self.check()
        self.assertEqual((result.returncode, result.stdout), (0, ''))


if __name__ == '__main__':
    unittest.main()
