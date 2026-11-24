"""Tests de template/scripts/lib/bench_rule.py : la porte exige un banc d'attaque vert et à jour."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / 'template/scripts/lib'
sys.path.insert(0, str(LIB))

import bench_rule  # noqa: E402

SANDBOX = 'template/scripts/lib/sandbox.py'
RUN_IN_SANDBOX = 'template/scripts/run-in-sandbox.sh'
DOCKER_FILE = 'template/docker/python-git.Dockerfile'
GIT_IDENTITY = {
    'GIT_AUTHOR_NAME': 'Test',
    'GIT_AUTHOR_EMAIL': 'test@example.invalid',
    'GIT_COMMITTER_NAME': 'Test',
    'GIT_COMMITTER_EMAIL': 'test@example.invalid',
}


class BenchRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'project with spaces'
        self.root.mkdir()
        self.env = dict(os.environ, **GIT_IDENTITY)
        self.git('init', '-q')
        self.write(SANDBOX, 'SANDBOX = 1\n')
        self.write(RUN_IN_SANDBOX, '#!/bin/sh\n')
        self.write('README.md', '# Projet\n')
        self.commit_all('initial')

    def git(self, *args):
        return subprocess.run(
            ['git', *args], cwd=str(self.root), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
        ).stdout.strip()

    def commit_all(self, message):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)

    def head(self):
        return self.git('rev-parse', 'HEAD')

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def write_record(self, **fields):
        payload = {'commit': self.head(), 'green': True, 'failles': []}
        payload.update(fields)
        self.write(bench_rule.RECORD, json.dumps(payload) + '\n')

    def refusal(self, reason):
        return f"banc d'attaque requis : {reason} ; lancer bash scripts/attack-bench.sh <tâche préparée>"

    def test_applies_only_when_a_watched_path_exists(self):
        self.assertFalse(bench_rule.applies(self.root / 'absent'))
        self.assertTrue(bench_rule.applies(self.root))

    def test_applies_is_false_when_no_watched_path_is_on_disk(self):
        for relative in (SANDBOX, RUN_IN_SANDBOX):
            (self.root / relative).unlink()
        shutil.rmtree(self.root / 'template/scripts')
        self.assertFalse(bench_rule.applies(self.root))

    def test_applies_requires_template_docker_to_be_a_directory(self):
        for relative in (SANDBOX, RUN_IN_SANDBOX):
            (self.root / relative).unlink()
        self.write('template/docker', 'pas un dossier\n')
        self.assertFalse(bench_rule.applies(self.root))
        (self.root / 'template/docker').unlink()
        self.write(DOCKER_FILE, 'FROM scratch\n')
        self.assertTrue(bench_rule.applies(self.root))

    def test_missing_record_is_red(self):
        ok, message = bench_rule.check(self.root)
        self.assertFalse(ok)
        self.assertEqual(message, self.refusal('aucun banc enregistré'))

    def test_record_symlink_is_unreadable(self):
        target = self.write('elsewhere.json', json.dumps({'commit': self.head(), 'green': True}))
        record = self.root / bench_rule.RECORD
        record.parent.mkdir(parents=True)
        record.symlink_to(target)
        ok, message = bench_rule.check(self.root)
        self.assertFalse(ok)
        self.assertEqual(message, self.refusal('enregistrement illisible'))

    def test_dangling_record_symlink_is_unreadable(self):
        record = self.root / bench_rule.RECORD
        record.parent.mkdir(parents=True)
        record.symlink_to(self.root / 'absent.json')
        ok, message = bench_rule.check(self.root)
        self.assertFalse(ok)
        self.assertEqual(message, self.refusal('enregistrement illisible'))

    def test_invalid_json_is_unreadable(self):
        self.write(bench_rule.RECORD, '{pas du json')
        ok, message = bench_rule.check(self.root)
        self.assertFalse(ok)
        self.assertEqual(message, self.refusal('enregistrement illisible'))

    def test_record_that_is_not_an_object_is_unreadable(self):
        self.write(bench_rule.RECORD, '["commit"]\n')
        ok, message = bench_rule.check(self.root)
        self.assertFalse(ok)
        self.assertEqual(message, self.refusal('enregistrement illisible'))

    def test_record_without_commit_is_unreadable(self):
        for commit in (None, '', '   ', 42, '--output=/tmp/x'):
            with self.subTest(commit=commit):
                self.write_record(commit=commit)
                ok, message = bench_rule.check(self.root)
                self.assertFalse(ok)
                self.assertEqual(message, self.refusal('enregistrement illisible'))

    def test_red_record_is_red(self):
        self.write_record(green=False, failles=['write-etc'])
        ok, message = bench_rule.check(self.root)
        self.assertFalse(ok)
        self.assertEqual(message, self.refusal('dernier banc rouge'))

    def test_green_must_be_true_not_merely_truthy(self):
        self.write_record(green=1)
        ok, message = bench_rule.check(self.root)
        self.assertFalse(ok)
        self.assertEqual(message, self.refusal('dernier banc rouge'))

    def test_green_record_on_current_commit_is_green(self):
        self.write_record()
        self.assertEqual(bench_rule.check(self.root), (True, ''))

    def test_unwatched_change_keeps_the_rule_green(self):
        self.write_record()
        self.write('README.md', '# Projet modifié\n')
        self.write('src/app.py', 'print("nouveau")\n')
        self.assertEqual(bench_rule.check(self.root), (True, ''))

    def test_uncommitted_sandbox_change_is_red_and_named(self):
        self.write_record()
        self.write(SANDBOX, 'SANDBOX = 2\n')
        ok, message = bench_rule.check(self.root)
        self.assertFalse(ok)
        self.assertEqual(message, self.refusal(f'modifié depuis le banc {self.head()[:7]} : {SANDBOX}'))

    def test_change_committed_after_the_bench_is_red(self):
        bench_commit = self.head()
        self.write_record(commit=bench_commit)
        self.write(SANDBOX, 'SANDBOX = 2\n')
        self.commit_all('sandbox')
        ok, message = bench_rule.check(self.root)
        self.assertFalse(ok)
        self.assertEqual(message, self.refusal(f'modifié depuis le banc {bench_commit[:7]} : {SANDBOX}'))

    def test_untracked_file_under_docker_is_red_and_named(self):
        self.write_record()
        self.write(DOCKER_FILE, 'FROM scratch\n')
        ok, message = bench_rule.check(self.root)
        self.assertFalse(ok)
        self.assertIn(f': {DOCKER_FILE}', message)

    def test_every_changed_watched_file_is_listed_sorted_and_comma_separated(self):
        self.write_record()
        self.write(RUN_IN_SANDBOX, '#!/bin/sh\necho modifié\n')
        self.write(SANDBOX, 'SANDBOX = 3\n')
        self.write(DOCKER_FILE, 'FROM scratch\n')
        ok, message = bench_rule.check(self.root)
        self.assertFalse(ok)
        listed = ', '.join(sorted([DOCKER_FILE, RUN_IN_SANDBOX, SANDBOX]))
        self.assertEqual(message, self.refusal(f'modifié depuis le banc {self.head()[:7]} : {listed}'))

    def test_unknown_commit_is_red(self):
        self.write_record(commit='0' * 40)
        ok, message = bench_rule.check(self.root)
        self.assertFalse(ok)
        self.assertEqual(message, self.refusal(f'commit du banc introuvable : {"0" * 40}'))

    def test_outside_a_git_repository_is_red(self):
        plain = Path(self.temp.name).resolve() / 'hors depot'
        plain.mkdir()
        record = plain / bench_rule.RECORD
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({'commit': self.head(), 'green': True}), encoding='utf-8')
        (plain / SANDBOX).parent.mkdir(parents=True)
        (plain / SANDBOX).write_text('SANDBOX = 1\n', encoding='utf-8')
        ok, message = bench_rule.check(plain)
        self.assertFalse(ok)
        self.assertIn('commit du banc introuvable', message)
        self.assertNotIn('\n', message)


if __name__ == '__main__':
    unittest.main()
