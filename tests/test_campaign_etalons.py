import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CAMPAIGN = Path(__file__).resolve().parents[1] / 'tools/campaign'
sys.path.insert(0, str(CAMPAIGN))
import etalons
from evaluation import Refused

IDENTITY = dict(
    GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.org', GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.org'
)
EVALUATION = '''import unittest
import calc


class Evaluation(unittest.TestCase):
    def test_add(self):
        self.assertEqual(calc.add(2, 3), 5)

    def test_sub(self):
        self.assertEqual(calc.sub(5, 3), 2)
'''
HEADER = 'diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n'


class DiffPathsTests(unittest.TestCase):
    def test_paths_sorted_unique_without_prefixes_or_dev_null(self):
        data = (
            b'diff --git a/z.py b/z.py\n--- a/z.py\n+++ b/z.py\n@@ -1 +1 @@\n-a\n+b\n'
            b'diff --git a/a.py b/a.py\nnew file mode 100644\n--- /dev/null\n+++ b/a.py\n@@ -0,0 +1 @@\n+x\n'
            b'diff --git a/old.py b/new.py\nrename from old.py\nrename to new.py\n'
        )
        self.assertEqual(etalons.diff_paths(data), ['a.py', 'new.py', 'old.py', 'z.py'])

    def test_deleted_file_and_empty_input(self):
        data = b'diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n'
        self.assertEqual(etalons.diff_paths(data), ['gone.py'])
        self.assertEqual(etalons.diff_paths(b''), [])

    def test_path_with_space_and_quoted_name(self):
        data = b'diff --git a/my file.py b/my file.py\n--- a/my file.py\n+++ b/my file.py\n'
        self.assertEqual(etalons.diff_paths(data), ['my file.py'])
        quoted = b'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
        self.assertEqual(etalons.diff_paths(quoted), ['café.py'])


class InScopeTests(unittest.TestCase):
    def test_empty_or_missing_allowed_covers_everything(self):
        self.assertTrue(etalons.in_scope('any/file.py', []))
        self.assertTrue(etalons.in_scope('any/file.py', None))

    def test_recursive_prefix_pattern(self):
        self.assertTrue(etalons.in_scope('src/a/b.py', ['src/**']))
        self.assertFalse(etalons.in_scope('src', ['src/**']))
        self.assertFalse(etalons.in_scope('srcs/b.py', ['src/**']))

    def test_other_patterns_use_fnmatchcase(self):
        self.assertTrue(etalons.in_scope('calc.py', ['calc.py']))
        self.assertTrue(etalons.in_scope('lib/x.py', ['lib/*.py']))
        self.assertFalse(etalons.in_scope('Calc.py', ['calc.py']))
        self.assertFalse(etalons.in_scope('other.py', ['calc.py', 'lib/*.py']))


class EtalonsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'project'
        self.work = self.base / 'etalons'
        for directory in (self.project, self.work / 'evaluation', self.work / 'diffs'):
            directory.mkdir(parents=True)
        self.git('init', '-q', '-b', 'initial')
        (self.project / 'calc.py').write_text('def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a + b\n')
        (self.project / 'other.py').write_text('VALUE = 1\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        self.commit = self.git('rev-parse', 'HEAD')
        (self.work / 'evaluation/test_eval.py').write_text(EVALUATION)
        task = dict(
            id='calc',
            request='demande.md',
            evaluation_files=['evaluation/test_eval.py'],
            evaluation_target='eval',
            allowed=['calc.py'],
            evaluation_command=['python3', '-m', 'unittest', 'discover', '-s', 'eval'],
        )
        (self.work / 'demande.md').write_text('Corriger calc.py.')
        self.write_json('config.json', dict(project='../project', start_commit=self.commit, arms=['opus-seul'], tasks=[task]))
        self.etalons = []

    def git(self, *args):
        done = subprocess.run(
            ['git'] + list(args),
            cwd=str(self.project),
            text=True,
            env=dict(os.environ, **IDENTITY),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(done.returncode, 0, done.stdout)
        return done.stdout.strip()

    def write_json(self, name, data):
        (self.work / name).write_text(json.dumps(data))

    def make_diff(self, name, add='+', sub='-', other=None, new_test=False):
        (self.project / 'calc.py').write_text('def add(a, b):\n    return a %s b\n\n\ndef sub(a, b):\n    return a %s b\n' % (add, sub))
        if other:
            (self.project / 'other.py').write_text(other)
        if new_test:
            (self.project / 'eval').mkdir()
            (self.project / 'eval/test_eval.py').write_text(
                'import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n'
            )
            self.git('add', '-N', 'eval/test_eval.py')
        (self.work / 'diffs' / (name + '.diff')).write_text(self.git('diff') + '\n')
        self.git('reset', '-q', '--hard')
        self.git('clean', '-qfd')

    def add(self, name, expected, kind='test', diff=None, base=None, source='fabriqué'):  # noqa: PLR0913
        self.etalons.append(
            dict(id=name, diff=diff or 'diffs/%s.diff' % name, base=base or self.commit, expected=expected, kind=kind, source=source)
        )

    def manifest(self, **changes):
        data = dict(config='config.json', task='calc', etalons=self.etalons)
        data.update(changes)
        self.write_json('etalons.json', data)
        return self.work / 'etalons.json'

    def standard(self):
        self.make_diff('bon', '+', '-')
        self.make_diff('partiel', '+', '+')
        self.make_diff('hors-perimetre', '+', '-', other='VALUE = 2\n')
        self.make_diff('tricheur', '+', '-', new_test=True)
        self.add('bon', 'accept', 'bon travail')
        self.add('partiel', 'reject', 'partiel')
        self.add('hors-perimetre', 'reject', 'hors périmètre')
        self.add('tricheur', 'reject', 'tricheur')

    def cli(self, *arguments, script=None):
        script = script or CAMPAIGN / 'etalons.py'
        command = [sys.executable, str(script)] + (['etalons'] if script.name == 'campaign.py' else [])
        return subprocess.run(
            command + list(arguments), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ, **IDENTITY)
        )

    def rows(self):
        return {row['id']: row for row in etalons.replay(etalons.load_manifest(self.manifest()))}

    def test_replay_classifies_good_partial_out_of_scope_and_cheating_etalons(self):
        self.standard()
        rows = self.rows()
        self.assertEqual(list(rows), ['bon', 'partiel', 'hors-perimetre', 'tricheur'])
        self.assertEqual((rows['bon']['observed'], rows['bon']['passed'], rows['bon']['total'], rows['bon']['code']), ('accept', 2, 2, 0))
        self.assertEqual((rows['partiel']['observed'], rows['partiel']['passed'], rows['partiel']['total']), ('reject', 1, 2))
        self.assertEqual(rows['hors-perimetre']['observed'], 'reject')
        self.assertEqual((rows['hors-perimetre']['passed'], rows['hors-perimetre']['total']), (2, 2))
        self.assertEqual(rows['hors-perimetre']['out_of_scope'], ['other.py'])
        self.assertEqual(rows['tricheur']['observed'], 'reject')
        self.assertIn('écraser', rows['tricheur']['reason'])
        self.assertEqual(rows['bon']['out_of_scope'], [])
        self.assertEqual(rows['bon']['reason'], '')
        self.assertTrue(all(row['verdict'] == 'ok' for row in rows.values()))
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_false_reject_and_false_acceptance_are_counted(self):
        self.standard()
        self.etalons[0]['expected'] = 'reject'
        self.etalons[1]['expected'] = 'accept'
        rows = self.rows()
        self.assertEqual(rows['bon']['verdict'], 'fausse_acceptation')
        self.assertEqual(rows['partiel']['verdict'], 'faux_rejet')
        self.assertEqual(rows['hors-perimetre']['verdict'], 'ok')

    def test_masked_and_unapplicable_diffs_are_rejected_with_reason(self):
        self.make_diff('bon')
        (self.work / 'diffs/masque.diff').write_text(HEADER + '@@ -1 +1 @@\n-a\n+***\n')
        (self.work / 'diffs/casse.diff').write_text(HEADER + '@@ -40,1 +40,1 @@\n-nope\n+nope2\n')
        self.add('masque', 'reject')
        self.add('casse', 'reject')
        rows = self.rows()
        for name, text in (('masque', 'masqué'), ('casse', 'ne s’applique pas')):
            self.assertEqual(rows[name]['observed'], 'reject')
            self.assertIn(text, rows[name]['reason'])
            self.assertIsNone(rows[name]['passed'])
            self.assertIsNone(rows[name]['code'])
            self.assertEqual(rows[name]['verdict'], 'ok')

    def test_task_without_allowed_has_no_scope_limit(self):
        self.standard()
        config = json.loads((self.work / 'config.json').read_text())
        del config['tasks'][0]['allowed']
        self.write_json('config.json', config)
        rows = self.rows()
        self.assertEqual(rows['hors-perimetre']['observed'], 'accept')
        self.assertEqual(rows['hors-perimetre']['verdict'], 'fausse_acceptation')

    def test_unusable_commit_or_configuration_stops_everything(self):
        self.standard()
        self.etalons[1]['base'] = '0' * 40
        with self.assertRaises(Refused) as caught:
            etalons.replay(etalons.load_manifest(self.manifest()))
        self.assertEqual(caught.exception.code, 64)
        self.etalons[1]['base'] = self.commit
        with self.assertRaises(Refused) as caught:
            etalons.replay(etalons.load_manifest(self.manifest(task='inconnue')))
        self.assertEqual(caught.exception.code, 64)

    def test_cli_prints_one_line_per_etalon_then_two_rates(self):
        self.standard()
        done = self.cli(str(self.manifest()))
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[0], 'bon | bon travail | attendu accept | obtenu accept | 2/2 | périmètre ok | ok')
        self.assertEqual(lines[1], 'partiel | partiel | attendu reject | obtenu reject | 1/2 | périmètre ok | ok')
        self.assertEqual(
            lines[2], 'hors-perimetre | hors périmètre | attendu reject | obtenu reject | 2/2 | périmètre hors : other.py | ok'
        )
        self.assertTrue(lines[3].startswith('tricheur | tricheur | attendu reject | obtenu reject | ?/? | '))
        self.assertEqual(lines[-2:], ['faux rejets : 0/1', 'fausses acceptations : 0/3'])
        self.assertEqual(len(lines), 6)
        self.assertIn('Ran 2 tests', done.stderr)
        self.assertNotIn('Ran 2 tests', done.stdout)

    def test_cli_exit_code_is_one_when_a_rate_is_not_zero(self):
        self.standard()
        self.etalons[1]['expected'] = 'accept'
        done = self.cli(str(self.manifest()))
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(done.stdout.splitlines()[-2:], ['faux rejets : 1/2', 'fausses acceptations : 0/2'])
        self.assertIn('faux_rejet', done.stdout)

    def test_campaign_py_dispatches_the_etalons_command(self):
        self.standard()
        done = self.cli(str(self.manifest()), script=CAMPAIGN / 'campaign.py')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines()[-2:], ['faux rejets : 0/1', 'fausses acceptations : 0/3'])
        missing = self.cli(str(self.work / 'absent.json'), script=CAMPAIGN / 'campaign.py')
        self.assertEqual(missing.returncode, 64)
        usage = subprocess.run([sys.executable, str(CAMPAIGN / 'campaign.py')], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(usage.returncode, 64)
        self.assertIn('usage', usage.stderr)

    def test_write_produces_markdown_report_even_when_a_rate_is_not_zero(self):
        self.standard()
        self.etalons[0]['expected'] = 'reject'
        report = self.base / 'rapport.md'
        done = self.cli(str(self.manifest()), '--write', str(report))
        self.assertEqual(done.returncode, 1, done.stderr)
        text = report.read_text(encoding='utf-8')
        lines = text.splitlines()
        self.assertEqual(lines[0], "# Taux d'erreur du vérificateur")
        self.assertRegex(text, r'(?m)^- Date : \d{4}-\d{2}-\d{2}$')
        self.assertRegex(text, r'(?m)^- Commit du kit : (?:[0-9a-f]{4,40}|inconnu)$')
        self.assertIn('- Tâche : calc', lines)
        self.assertIn('- Configuration : config.json', lines)
        self.assertEqual(len([line for line in lines if line.startswith('| ') and 'tricheur' in line]), 1)
        self.assertEqual(len([line for line in lines if re.match(r'\| (bon|partiel|hors-perimetre|tricheur) \|', line)]), 4)
        self.assertEqual(lines[-2:], done.stdout.splitlines()[-2:])
        self.assertEqual(lines[-2:], ['faux rejets : 0/0', 'fausses acceptations : 1/4'])

    def test_invalid_manifests_exit_64(self):
        self.standard()
        cases = [
            ('expected maybe', dict(etalons=[dict(self.etalons[0], expected='maybe')])),
            ('duplicate id', dict(etalons=[self.etalons[0], self.etalons[0]])),
            ('empty list', dict(etalons=[])),
            ('bad id', dict(etalons=[dict(self.etalons[0], id='Bon_1')])),
            ('missing key', dict(etalons=[{k: v for k, v in self.etalons[0].items() if k != 'base'}])),
            ('extra key', dict(etalons=[dict(self.etalons[0], extra=1)])),
            ('kind not a string', dict(etalons=[dict(self.etalons[0], kind=3)])),
            ('base looks like an option', dict(etalons=[dict(self.etalons[0], base='--help')])),
            ('no task', dict(task='')),
        ]
        for label, changes in cases:
            with self.subTest(label):
                path = self.manifest(**changes)
                with self.assertRaises(ValueError):
                    etalons.load_manifest(path)
                done = self.cli(str(path))
                self.assertEqual(done.returncode, 64, done.stderr)
                self.assertEqual(done.stdout, '')
                self.assertTrue(done.stderr.strip())
        (self.work / 'broken.json').write_text('{')
        self.assertEqual(self.cli(str(self.work / 'broken.json')).returncode, 64)

    def test_bad_usage_exits_64(self):
        for arguments in (('a.json', 'b.json'), ('--write',), ('--other',), ('--write', 'x.md', '--write', 'y.md')):
            with self.subTest(arguments):
                done = self.cli(*arguments)
                self.assertEqual(done.returncode, 64)
                self.assertIn('usage', done.stderr)

    def test_load_manifest_resolves_paths_and_keeps_absolute_ones(self):
        self.standard()
        absolute = str(self.work / 'diffs/bon.diff')
        self.etalons[0]['diff'] = absolute
        manifest = etalons.load_manifest(self.manifest())
        self.assertEqual(manifest['config'], str(self.work / 'config.json'))
        self.assertEqual(manifest['etalons'][0]['diff'], absolute)
        self.assertEqual(manifest['etalons'][1]['diff'], str(self.work / 'diffs/partiel.diff'))

    def test_default_manifest_sits_two_folders_above_campaign_py(self):
        self.assertEqual(etalons.DEFAULT_MANIFEST, CAMPAIGN.parents[1] / 'tools/campaign/etalons/etalons.json')


if __name__ == '__main__':
    unittest.main()
