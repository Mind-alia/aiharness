import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CAMPAIGN = Path(__file__).resolve().parents[1] / 'tools/campaign'
sys.path.insert(0, str(CAMPAIGN))
import etalons

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
HEADER = '''---
id: %(id)s
agent: codex
allowed: [calc.py, "tests/**"]
acceptance: [eval/test_eval.py]
acceptance_verify: python3 -m unittest discover -s eval
---
# Fiche
'''
STATE = 'tasks/%s.md'


class HarvestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'Demo_Project'
        self.out = self.base / 'jeu'
        self.project.mkdir()
        self.git('init', '-q', '-b', 'initial')
        (self.project / 'calc.py').write_text('def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a + b\n')
        (self.project / 'other.py').write_text('VALUE = 1\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        self.commit = self.git('rev-parse', 'HEAD')
        self.good = self.make_diff('+', '-', tests=True)
        self.code_only = self.make_diff('+', '-')
        self.partial = self.make_diff('+', '+')
        self.tests_only = self.make_diff(None, None, tests=True)

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

    def make_diff(self, add, sub, tests=False):
        if add is not None:
            (self.project / 'calc.py').write_text('def add(a, b):\n    return a %s b\n\n\ndef sub(a, b):\n    return a %s b\n' % (add, sub))
        if tests:
            (self.project / 'tests').mkdir()
            (self.project / 'tests/test_extra.py').write_text(
                'import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n'
            )
            self.git('add', '-N', 'tests/test_extra.py')
        diff = self.git('diff') + '\n'
        self.git('reset', '-q', '--hard')
        self.git('clean', '-qfd')
        return diff

    def add_task(self, task_id, status='merged', outcome=None, diff=None, created='2026-10-10T10:00:00Z', fiche=None, hidden=True):  # noqa: PLR0913
        """Preuves d'une délégation : état, fiche, tests cachés, diff et issue."""
        state = self.project / '.workspace/orchestration/tasks' / task_id
        run = self.project / '.workspace/run/delegate' / task_id
        state.mkdir(parents=True)
        run.mkdir(parents=True)
        (state / 'state.json').write_text(
            json.dumps(
                dict(
                    task_id=task_id, agent='codex', status=status, created_at=created, base_commit=self.commit, source_task=STATE % task_id
                )
            )
        )
        (self.project / 'tasks').mkdir(exist_ok=True)
        (self.project / (STATE % task_id)).write_text(HEADER % dict(id=task_id) if fiche is None else fiche)
        if hidden:
            acceptance = self.project / '.workspace/acceptance' / task_id / 'eval'
            acceptance.mkdir(parents=True)
            (acceptance / 'test_eval.py').write_text(EVALUATION)
        if outcome:
            (run / 'status.json').write_text(json.dumps(dict(outcome=outcome)))
        if diff is not None:
            (run / 'diff.txt').write_text(diff)

    def standard(self):
        self.add_task('calc-a', diff=self.good)
        self.add_task('calc-b', diff=self.code_only)
        self.add_task('calc-c', status='abandoned', outcome='acceptance_failed', diff=self.partial)

    def harvest(self, *arguments, project=True):
        args = list(arguments)
        if project:
            args = ['--harvest', str(self.project)] + ([] if '--out' in args else ['--out', str(self.out)]) + args
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = etalons.main(args)
        return code, out.getvalue(), err.getvalue()

    def manifest(self):
        return json.loads((self.out / 'etalons.json').read_text())

    def tree(self):
        return sorted(str(p.relative_to(self.base)) for p in self.out.rglob('*')) if self.out.exists() else []

    def test_harvest_builds_expected_etalons(self):
        self.standard()
        self.add_task('calc-d', status='abandoned', outcome='failed', diff=self.partial)
        (self.project / '.workspace/orchestration/tasks/vide').mkdir()
        code, out, err = self.harvest()
        self.assertEqual(code, 0, err)
        self.assertEqual(
            out.splitlines(),
            [
                'calc-a : retenu',
                'calc-b : retenu',
                'calc-c : retenu',
                'calc-d : écarté : ni fusionnée ni en échec connu',
                'étalons ajoutés : 6 (2 bons, 4 mauvais)',
            ],
        )
        items = {item['id']: item for item in self.manifest()['etalons']}
        label = 'demo-project-'
        self.assertEqual(
            [i['id'] for i in self.manifest()['etalons']],
            [
                label + n
                for n in ('calc-a-bon', 'calc-a-sans-code', 'calc-a-hors-perimetre', 'calc-b-bon', 'calc-b-hors-perimetre', 'calc-c-echec')
            ],
        )
        self.assertEqual(self.manifest().keys(), {'etalons'})
        for item in items.values():
            self.assertEqual(set(item), {'id', 'diff', 'base', 'expected', 'kind', 'source', 'config', 'task'})
            self.assertEqual(item['base'], self.commit)
            self.assertEqual(item['config'], 'demo-project.config.json')
            self.assertTrue(item['source'].strip())
        bon = items[label + 'calc-a-bon']
        self.assertEqual((bon['expected'], bon['kind'], bon['task']), ('accept', 'bon', 'calc-a'))
        self.assertEqual((self.out / bon['diff']).read_text(), self.good)
        sans = items[label + 'calc-a-sans-code']
        self.assertEqual((sans['expected'], sans['kind']), ('reject', 'sans implémentation'))
        self.assertEqual((self.out / sans['diff']).read_text(), self.tests_only)
        outside = items[label + 'calc-b-hors-perimetre']
        self.assertEqual((outside['expected'], outside['kind']), ('reject', 'hors périmètre'))
        text = (self.out / outside['diff']).read_text()
        self.assertTrue(text.startswith(self.code_only))
        self.assertTrue(text.endswith('+++ b/scripts/etalon-hors-perimetre.sh\n@@ -0,0 +1 @@\n+exit 0\n'))
        echec = items[label + 'calc-c-echec']
        self.assertEqual((echec['expected'], echec['kind']), ('reject', 'échec réel'))
        self.assertEqual((self.out / echec['diff']).read_text(), self.partial)
        self.assertFalse(list(self.out.rglob('test_eval.py')), 'les tests cachés ne sont jamais copiés')

    def test_private_config_points_at_the_hidden_tests_where_they_are(self):
        self.standard()
        self.assertEqual(self.harvest('--setup', 'make deps')[0], 0)
        config = json.loads((self.out / 'demo-project.config.json').read_text())
        self.assertEqual(config['project'], str(self.project))
        self.assertEqual(config['setup'], 'make deps')
        self.assertEqual([t['id'] for t in config['tasks']], ['calc-a', 'calc-b', 'calc-c'])
        self.assertEqual(
            config['tasks'][0],
            dict(
                id='calc-a',
                allowed=['calc.py', 'tests/**'],
                evaluation_target='eval',
                evaluation_files=[str(self.project / '.workspace/acceptance/calc-a/eval/test_eval.py')],
                evaluation_command='python3 -m unittest discover -s eval',
            ),
        )

    def test_replay_of_the_harvested_manifest_is_clean_and_report_counts_the_set(self):
        self.standard()
        self.assertEqual(self.harvest()[0], 0)
        report = self.base / 'rapport.md'
        done = subprocess.run(
            [sys.executable, str(CAMPAIGN / 'campaign.py'), 'etalons', str(self.out / 'etalons.json'), '--write', str(report)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(os.environ, **IDENTITY),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 8)
        self.assertEqual(lines[-2:], ['faux rejets : 0/2', 'fausses acceptations : 0/4'])
        text = report.read_text(encoding='utf-8')
        self.assertIn('- Tâche : calc-a, calc-b, calc-c', text.splitlines())
        self.assertIn('- Configuration : demo-project.config.json', text.splitlines())
        self.assertIn('- Jeu : 6 étalons (2 bons, 4 mauvais), 3 tâches', text.splitlines())
        self.assertEqual(text.splitlines()[-3], 'taille du jeu : 6 étalons, 3 tâches')
        self.assertEqual(text.splitlines()[-2:], lines[-2:])

    def test_second_identical_harvest_is_refused_and_changes_nothing(self):
        self.standard()
        self.assertEqual(self.harvest()[0], 0)
        before = {p: p.read_bytes() for p in self.out.rglob('*') if p.is_file()}
        code, out, err = self.harvest()
        self.assertEqual(code, 64)
        self.assertIn('demo-project-calc-a-bon', err)
        self.assertEqual({p: p.read_bytes() for p in self.out.rglob('*') if p.is_file()}, before)

    def test_second_harvest_with_another_label_appends_and_keeps_existing_keys(self):
        self.standard()
        self.assertEqual(self.harvest()[0], 0)
        first = self.manifest()
        data = dict(first, extra='gardé')
        (self.out / 'etalons.json').write_text(json.dumps(data))
        code, out, err = self.harvest('--label', 'autre')
        self.assertEqual(code, 0, err)
        second = self.manifest()
        self.assertEqual(second['extra'], 'gardé')
        self.assertEqual(second['etalons'][:6], first['etalons'])
        self.assertEqual(len(second['etalons']), 12)
        self.assertEqual(second['etalons'][6]['id'], 'autre-calc-a-bon')
        self.assertEqual(second['etalons'][6]['config'], 'autre.config.json')
        self.assertTrue((self.out / 'autre.config.json').is_file())

    def test_existing_config_keeps_its_tasks_and_replaces_the_same_one(self):
        self.standard()
        self.out.mkdir()
        (self.out / 'demo-project.config.json').write_text(
            json.dumps(dict(project='/ailleurs', setup='npm ci', tasks=[dict(id='ancienne', x=1), dict(id='calc-a', x=2)]))
        )
        self.assertEqual(self.harvest('--only', 'calc-a')[0], 0)
        config = json.loads((self.out / 'demo-project.config.json').read_text())
        self.assertEqual([t['id'] for t in config['tasks']], ['ancienne', 'calc-a'])
        self.assertNotIn('x', config['tasks'][1])
        self.assertEqual((config['project'], config['setup']), (str(self.project), 'npm ci'))

    def test_configs_folder_is_separate_and_manifest_path_is_relative(self):
        self.standard()
        configs = self.base / 'prive'
        self.assertEqual(self.harvest('--configs', str(configs))[0], 0)
        self.assertTrue((configs / 'demo-project.config.json').is_file())
        self.assertFalse((self.out / 'demo-project.config.json').exists())
        self.assertEqual(self.manifest()['etalons'][0]['config'], '../prive/demo-project.config.json')
        manifest = etalons.load_manifest(self.out / 'etalons.json')
        self.assertEqual(manifest['etalons'][0]['config'], str(configs / 'demo-project.config.json'))

    def test_only_and_since_filter_the_tasks(self):
        self.add_task('calc-a', diff=self.good, created='2026-10-01T00:00:00Z')
        self.add_task('calc-b', diff=self.code_only, created='2026-10-12T00:00:00Z')
        self.add_task('calc-c', diff=self.code_only, created='2026-10-15T09:00:00Z')
        code, out, err = self.harvest('--since', '2026-10-12', '--only', 'calc-b,calc-a')
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines(), ['calc-b : retenu', 'étalons ajoutés : 2 (1 bons, 1 mauvais)'])
        self.assertEqual({i['task'] for i in self.manifest()['etalons']}, {'calc-b'})

    def test_discard_reasons(self):
        task = HEADER % dict(id='x')
        cases = {
            'fiche illisible': dict(fiche='pas de fiche'),
            'sans acceptance': dict(
                fiche=task.replace('acceptance: [eval/test_eval.py]\n', '').replace(
                    'acceptance_verify: python3 -m unittest discover -s eval\n', ''
                )
            ),
            'plusieurs dossiers': dict(fiche=task.replace('[eval/test_eval.py]', '[eval/test_eval.py, autre/b.py]')),
            'test caché absent': dict(hidden=False),
            'diff absent': dict(diff=None),
            'sans diff --git': dict(diff='rien\n'),
            'masqué': dict(diff=self.code_only + '+***\n'),
        }
        for label, changes in cases.items():
            with self.subTest(label):
                shutil.rmtree(str(self.project / '.workspace'), ignore_errors=True)
                self.add_task('calc-x', **dict(dict(diff=self.code_only), **changes))
                code, out, err = self.harvest('--out', str(self.base / ('jeu-' + label.replace(' ', '-'))))
                self.assertEqual(code, 1, err)
                self.assertRegex(out.splitlines()[0], r'^calc-x : écarté : .+')
                self.assertEqual(out.splitlines()[-1], 'étalons ajoutés : 0 (0 bons, 0 mauvais)')
        self.assertFalse(list(self.base.glob('jeu-*')))

    def test_diff_with_only_code_or_only_tests_has_no_sans_code_etalon(self):
        self.add_task('calc-a', diff=self.code_only)
        self.add_task('calc-b', diff=self.tests_only)
        self.assertEqual(self.harvest()[0], 0)
        ids = [i['id'] for i in self.manifest()['etalons']]
        self.assertFalse([i for i in ids if i.endswith('sans-code')])
        self.assertEqual(len(ids), 4)

    def test_scope_covering_everything_gives_no_out_of_scope_etalon(self):
        fiche = HEADER % dict(id='calc-a')
        for allowed in ('["*"]', '[]'):
            with self.subTest(allowed):
                shutil.rmtree(str(self.project / '.workspace'), ignore_errors=True)
                shutil.rmtree(str(self.out), ignore_errors=True)
                self.add_task('calc-a', diff=self.code_only, fiche=fiche.replace('[calc.py, "tests/**"]', allowed))
                self.assertEqual(self.harvest()[0], 0)
                self.assertEqual([i['id'] for i in self.manifest()['etalons']], ['demo-project-calc-a-bon'])

    def test_bad_usage_exits_64_and_writes_nothing(self):
        self.standard()
        out = str(self.out)
        cases = [
            ['--harvest'],
            ['--harvest', str(self.project)],
            ['--out', out],
            ['--harvest', str(self.project), '--out'],
            ['--harvest', str(self.project), '--out', '--label', 'x'],
            ['--harvest', str(self.project), '--out', out, '--out', out],
            ['--harvest', str(self.project), '--out', out, '--inconnue', 'x'],
            ['--harvest', str(self.project), '--out', out, 'manifeste.json'],
            ['--harvest', str(self.project), '--out', out, '--since', 'hier'],
            ['--harvest', str(self.project), '--out', out, '--label', 'Mal Nommé'],
            ['--harvest', str(self.project), '--out', out, '--only', ','],
        ]
        for arguments in cases:
            with self.subTest(arguments):
                code, stdout, stderr = self.harvest(*arguments, project=False)
                self.assertEqual(code, 64)
                self.assertIn('usage', stderr)
                self.assertEqual(stdout, '')
        self.assertFalse(self.out.exists())

    def test_project_without_orchestration_tasks_exits_64(self):
        code, out, err = self.harvest()
        self.assertEqual(code, 64)
        self.assertTrue(err.strip())
        self.assertFalse(self.out.exists())

    def test_unreadable_existing_manifest_exits_64_and_writes_nothing(self):
        self.standard()
        self.out.mkdir()
        (self.out / 'etalons.json').write_text('{')
        self.assertEqual(self.harvest()[0], 64)
        (self.out / 'etalons.json').write_text('{"etalons": 3}')
        self.assertEqual(self.harvest()[0], 64)
        self.assertEqual(self.tree(), [os.path.relpath(str(self.out / 'etalons.json'), str(self.base))])

    def test_cli_goes_through_campaign_py(self):
        self.standard()
        done = subprocess.run(
            [
                sys.executable,
                str(CAMPAIGN / 'campaign.py'),
                'etalons',
                '--harvest',
                str(self.project),
                '--out',
                str(self.out),
                '--only',
                'calc-c',
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines()[-1], 'étalons ajoutés : 1 (0 bons, 1 mauvais)')


class MultiTaskManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name).resolve()
        self.configs = {}
        for name, allowed in (('un', ['a.py']), ('deux', ['b.py'])):
            (self.work / (name + '.json')).write_text(
                json.dumps(
                    dict(
                        project='.',
                        tasks=[
                            dict(
                                id='tache-' + name,
                                allowed=allowed,
                                evaluation_files=[],
                                evaluation_target='eval',
                                evaluation_command='true',
                            )
                        ],
                    )
                )
            )
        (self.work / 'd.diff').write_text('diff --git a/a.py b/a.py\n')

    def item(self, name, **extra):
        return dict(dict(id=name, diff='d.diff', base='abc', expected='reject', kind='k', source='s'), **extra)

    def write(self, data):
        (self.work / 'etalons.json').write_text(json.dumps(data))
        return self.work / 'etalons.json'

    def test_mixed_manifest_takes_defaults_when_an_etalon_has_none(self):
        manifest = etalons.load_manifest(
            self.write(
                dict(
                    config='un.json',
                    task='tache-un',
                    etalons=[self.item('a'), self.item('b', config='deux.json', task='tache-deux'), self.item('c', task='tache-un')],
                )
            )
        )
        pairs = [(i['config'], i['task'], i['written_config']) for i in manifest['etalons']]
        self.assertEqual(
            pairs,
            [
                (str(self.work / 'un.json'), 'tache-un', 'un.json'),
                (str(self.work / 'deux.json'), 'tache-deux', 'deux.json'),
                (str(self.work / 'un.json'), 'tache-un', 'un.json'),
            ],
        )
        self.assertEqual(manifest['config'], str(self.work / 'un.json'))

    def test_manifest_without_defaults_needs_config_and_task_on_each_etalon(self):
        complete = self.item('a', config='un.json', task='tache-un')
        manifest = etalons.load_manifest(self.write(dict(etalons=[complete])))
        self.assertNotIn('config', manifest)
        for label, data in (
            ('config manquante', self.item('a', task='tache-un')),
            ('task manquante', self.item('a', config='un.json')),
            ('task vide', self.item('a', config='un.json', task='')),
        ):
            with self.subTest(label), self.assertRaises(ValueError):
                etalons.load_manifest(self.write(dict(etalons=[data])))

    def test_unknown_keys_are_refused_at_both_levels(self):
        with self.assertRaises(ValueError):
            etalons.load_manifest(self.write(dict(etalons=[self.item('a', config='un.json', task='t', autre=1)])))
        with self.assertRaises(ValueError):
            etalons.load_manifest(self.write(dict(config='un.json', task='t', autre=1, etalons=[self.item('a')])))
        with self.assertRaises(ValueError):
            etalons.load_manifest(self.write(dict(config='un.json', task='t')))

    def test_replay_reads_the_scope_of_each_etalon_task(self):
        manifest = etalons.load_manifest(
            self.write(
                dict(etalons=[self.item('a', config='un.json', task='tache-un'), self.item('b', config='deux.json', task='tache-deux')])
            )
        )
        original = etalons.evaluate_diff
        etalons.evaluate_diff = lambda *args: (1, 1, 0, '')
        self.addCleanup(setattr, etalons, 'evaluate_diff', original)
        rows = etalons.replay(manifest)
        self.assertEqual([(r['task'], r['out_of_scope']) for r in rows], [('tache-un', []), ('tache-deux', ['a.py'])])
        text = etalons.report(manifest, rows)
        self.assertIn('- Tâche : tache-deux, tache-un', text.splitlines())
        self.assertIn('- Configuration : deux.json, un.json', text.splitlines())
        self.assertIn('- Jeu : 2 étalons (0 bons, 2 mauvais), 2 tâches', text.splitlines())
        self.assertEqual(text.splitlines()[-3:], ['taille du jeu : 2 étalons, 2 tâches', 'faux rejets : 0/0', 'fausses acceptations : 1/2'])
        self.assertTrue(re.search(r'(?m)^\| a \|', text))

    def test_unknown_task_of_one_etalon_stops_the_replay(self):
        manifest = etalons.load_manifest(
            self.write(dict(config='un.json', task='tache-un', etalons=[self.item('a'), self.item('b', task='inconnue')]))
        )
        with self.assertRaises(etalons.Refused) as caught:
            etalons.replay(manifest)
        self.assertEqual(caught.exception.code, 64)


if __name__ == '__main__':
    unittest.main()
