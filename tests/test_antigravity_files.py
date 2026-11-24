"""Tests unitaires de template/scripts/lib/antigravity_files.py (aucun accès réseau)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / 'template/scripts/lib'
sys.path.insert(0, str(LIB))

import antigravity_files  # noqa: E402


class AntigravityFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name).resolve() / 'work'
        self.work.mkdir()
        self.outbox = self.work / antigravity_files.OUTBOX

    def put(self, rel, text='x'):
        path = self.outbox / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_outbox_location(self):
        self.assertEqual(antigravity_files.OUTBOX, '.workspace/antigravity/out')

    def test_active_only_for_antigravity_in_write_mode(self):
        self.assertTrue(antigravity_files.active({'agent': 'antigravity', 'mode': 'write'}))
        self.assertFalse(antigravity_files.active({'agent': 'antigravity', 'mode': 'read'}))
        self.assertFalse(antigravity_files.active({'agent': 'codex', 'mode': 'write'}))
        self.assertFalse(antigravity_files.active({'agent': 'claude-haiku', 'mode': 'write'}))

    def test_reset_outbox_creates_it_and_empties_it(self):
        outbox = antigravity_files.reset_outbox(self.work)
        self.assertEqual(outbox, self.outbox)
        self.assertEqual(list(outbox.iterdir()), [])
        self.put('src/old.py')
        antigravity_files.reset_outbox(self.work)
        self.assertTrue(self.outbox.is_dir())
        self.assertEqual(list(self.outbox.iterdir()), [])

    def test_reset_outbox_does_not_follow_links(self):
        elsewhere = self.work.parent / 'elsewhere'
        elsewhere.mkdir()
        (elsewhere / 'keep.txt').write_text('keep')
        self.outbox.parent.mkdir(parents=True)
        self.outbox.symlink_to(elsewhere, target_is_directory=True)
        antigravity_files.reset_outbox(self.work)
        self.assertFalse(self.outbox.is_symlink())
        self.assertEqual((elsewhere / 'keep.txt').read_text(), 'keep')

    def test_prompt_section_lists_scope_files_and_forbids_commands(self):
        (self.work / 'src').mkdir()
        (self.work / 'src/app.py').write_text('a')
        (self.work / 'other.txt').write_text('b')
        task = {'agent': 'antigravity', 'mode': 'write', 'allowed': ['src/**']}
        text = antigravity_files.prompt_section(task, self.work)
        self.assertIn('.workspace/antigravity/out', text)
        self.assertIn('- src/app.py', text)
        self.assertNotIn('other.txt', text)
        for forbidden in ('run-in-sandbox', 'ask.sh', 'scripts/'):
            self.assertNotIn(forbidden, text)

    def test_prompt_section_lists_at_most_200_files(self):
        for k in range(250):
            (self.work / ('f%03d.txt' % k)).write_text('x')
        task = {'agent': 'antigravity', 'mode': 'write', 'allowed': ['*.txt']}
        text = antigravity_files.prompt_section(task, self.work)
        self.assertEqual(text.count('- f'), 200)

    def test_apply_missing_outbox(self):
        self.assertEqual(antigravity_files.apply_outbox(self.work, ['**']), ([], []))

    def test_apply_in_scope_file_creates_parents_and_replaces_bytes(self):
        (self.work / 'src').mkdir()
        (self.work / 'src/app.py').write_text('old')
        self.put('src/app.py', 'new')
        self.put('src/sub/extra.py', 'extra')
        applied, refused = antigravity_files.apply_outbox(self.work, ['src/app.py', 'src/sub/*.py'])
        self.assertEqual(applied, ['src/app.py', 'src/sub/extra.py'])
        self.assertEqual(refused, [])
        self.assertEqual((self.work / 'src/app.py').read_text(), 'new')
        self.assertEqual((self.work / 'src/sub/extra.py').read_text(), 'extra')

    def test_apply_out_of_scope_file_is_refused_and_absent(self):
        self.put('src/app.py')
        self.put('outside.txt')
        applied, refused = antigravity_files.apply_outbox(self.work, ['src/app.py'])
        self.assertEqual(applied, ['src/app.py'])
        self.assertEqual(refused, [{'path': 'outside.txt', 'reason': 'out_of_scope'}])
        self.assertFalse((self.work / 'outside.txt').exists())

    def test_apply_recursive_pattern(self):
        self.put('src/a/b/deep.py')
        self.put('lib/no.py')
        applied, refused = antigravity_files.apply_outbox(self.work, ['src/**'])
        self.assertEqual(applied, ['src/a/b/deep.py'])
        self.assertEqual(refused, [{'path': 'lib/no.py', 'reason': 'out_of_scope'}])

    def test_apply_refuses_links_without_following_them(self):
        secret = self.work.parent / 'secret.txt'
        secret.write_text('secret')
        self.put('ok.txt')
        (self.outbox / 'link.txt').symlink_to(secret)
        folder = self.work.parent / 'folder'
        folder.mkdir()
        (folder / 'inside.txt').write_text('inside')
        (self.outbox / 'dirlink').symlink_to(folder, target_is_directory=True)
        applied, refused = antigravity_files.apply_outbox(self.work, ['**'])
        self.assertEqual(applied, ['ok.txt'])
        self.assertEqual(refused, [{'path': 'dirlink', 'reason': 'unsafe'}, {'path': 'link.txt', 'reason': 'unsafe'}])
        self.assertFalse((self.work / 'link.txt').exists())
        self.assertFalse((self.work / 'dirlink').exists())

    def test_apply_refuses_a_target_behind_a_linked_component(self):
        elsewhere = self.work.parent / 'elsewhere'
        elsewhere.mkdir()
        (self.work / 'src').symlink_to(elsewhere, target_is_directory=True)
        (self.work / 'target.txt').symlink_to(elsewhere / 'target.txt')
        self.put('src/app.py')
        self.put('target.txt')
        applied, refused = antigravity_files.apply_outbox(self.work, ['**'])
        self.assertEqual(applied, [])
        self.assertEqual(refused, [{'path': 'src/app.py', 'reason': 'unsafe'}, {'path': 'target.txt', 'reason': 'unsafe'}])
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_apply_refuses_a_linked_outbox(self):
        elsewhere = self.work.parent / 'elsewhere'
        elsewhere.mkdir()
        (elsewhere / 'a.txt').write_text('a')
        self.outbox.parent.mkdir(parents=True)
        self.outbox.symlink_to(elsewhere, target_is_directory=True)
        applied, refused = antigravity_files.apply_outbox(self.work, ['**'])
        self.assertEqual(applied, [])
        self.assertEqual([item['reason'] for item in refused], ['unsafe'])
        self.assertFalse((self.work / 'a.txt').exists())

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'mkfifo indisponible')
    def test_apply_refuses_entries_that_are_not_regular_files(self):
        self.outbox.mkdir(parents=True)
        os.mkfifo(self.outbox / 'pipe')
        applied, refused = antigravity_files.apply_outbox(self.work, ['**'])
        self.assertEqual(applied, [])
        self.assertEqual(refused, [{'path': 'pipe', 'reason': 'unsafe'}])

    def test_apply_refused_sorted_by_path(self):
        self.put('b.txt')
        self.put('a.txt')
        applied, refused = antigravity_files.apply_outbox(self.work, [])
        self.assertEqual(applied, [])
        self.assertEqual([item['path'] for item in refused], ['a.txt', 'b.txt'])


if __name__ == '__main__':
    unittest.main()
