import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
from task import read_task

GIT = shutil.which('git')

DEMANDE = '''# Demande

## Besoin
<!-- exemple de commentaire -->
Une fonction de somme stricte.

## Ce qui ne doit pas casser
Les tests existants.

## Preuve de réussite
<!-- commentaire
sur plusieurs lignes -->
add(2, 3) renvoie 5 et add("a", 1) lève TypeError.

## Fichiers
src/sum_mod.py
'''


@unittest.skipUnless(GIT, 'git absent')
class PrepareDemandeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / 'template/scripts', self.root / 'scripts', ignore=shutil.ignore_patterns('__pycache__', '__tests__'))
        (self.root / '.gitignore').write_text('.workspace/\ncall.json\nbin/\n__pycache__/\n')
        (self.root / 'demande.md').write_text(DEMANDE)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('python3', 'bash'):
            (self.bin / name).symlink_to(shutil.which(name))
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + str(Path(GIT).parent))
        self.env.pop('WORKSPACE_AGENT_ROOT', None)
        twelve = 'class Cases:\n' + ''.join('    def test_case_%d(self):\n        pass\n' % i for i in range(12))
        self.plan = dict(
            tasks=[
                dict(
                    suffix='',
                    allowed=['src/sum_mod.py'],
                    **{'class': 'small'},
                    verify='python3 -m unittest',
                    body='# Besoin\nSomme stricte.',
                )
            ],
            acceptance=[
                dict(task_suffix='', path='tests/test_sum.py', content='from src.sum_mod import add\nassert add(2, 3) == 5\n' + twelve)
            ],
            acceptance_verify={'': 'python3 tests/test_sum.py'},
        )
        self.git('init', '-q')
        self.commit()

    def git(self, *args):
        return subprocess.run(
            ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )

    def commit(self):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'base')

    def fake(self, payload=None):
        payload = self.plan if payload is None else payload
        output = json.dumps(dict(result=json.dumps(payload), usage=dict(input_tokens=1, output_tokens=1)))
        script = '#!' + sys.executable + '\nimport sys, pathlib\n'
        script += 'pathlib.Path("call.json").write_text(__import__("json").dumps(dict(prompt=sys.stdin.read())))\n'
        script += 'print(' + repr(output) + ')\n'
        (self.bin / 'claude').write_text(script)
        (self.bin / 'claude').chmod(0o700)

    def run_prepare(self, demande='demande.md', task_id='sum', *extra):
        return subprocess.run(
            ['bash', 'scripts/prepare-task.sh', '--from-demande', demande, task_id, '--executor', 'claude-haiku', *extra],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def assert_nothing_written(self):
        self.assertFalse((self.root / '.workspace/proposals').exists())
        self.assertFalse((self.root / 'tasks').exists())
        self.assertFalse((self.root / '.workspace/acceptance').exists())

    def test_nominal(self):
        self.fake()
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], 'Proposition : .workspace/proposals/sum')
        self.assertIn('.workspace/tasks/', result.stdout.splitlines()[-1])
        base = self.root / '.workspace/proposals/sum'
        task = read_task(base / 'task.md')
        self.assertEqual((task['agent'], task['acceptance']), ('claude-haiku', ['tests/test_sum.py']))
        self.assertEqual((base / 'acceptance/tests/test_sum.py').read_text(), self.plan['acceptance'][0]['content'])
        self.assertTrue((base / 'selfcheck.txt').read_text().startswith('rouge sur la base (code '))
        self.assertEqual(base.stat().st_mode & 0o777, 0o700)
        self.assertEqual((base / 'acceptance').stat().st_mode & 0o777, 0o700)
        self.assertEqual((base / 'task.md').stat().st_mode & 0o777, 0o600)
        self.assertEqual((base / 'acceptance/tests/test_sum.py').stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root / 'tasks').exists())
        self.assertFalse((self.root / '.workspace/tasks').exists())
        self.assertFalse((self.root / '.workspace/acceptance').exists())
        self.assertFalse((self.root / 'tests').exists())
        self.assertEqual(self.git('status', '--porcelain').stdout, '')

    def test_prompt_carries_the_proof(self):
        self.fake()
        self.assertEqual(self.run_prepare().returncode, 0)
        prompt = json.loads((self.root / 'call.json').read_text())['prompt']
        self.assertIn('add(2, 3) renvoie 5', prompt)
        self.assertIn('Preuve de réussite', prompt)
        self.assertIn('fixe ce que les tests cachés doivent prouver', prompt)

    def test_two_tasks(self):
        original = copy.deepcopy(self.plan)
        self.plan = dict(tasks=[], acceptance=[], acceptance_verify={})
        for suffix in ('-1', '-2'):
            self.plan['tasks'].append(dict(original['tasks'][0], suffix=suffix))
            self.plan['acceptance'].append(dict(original['acceptance'][0], task_suffix=suffix))
            self.plan['acceptance_verify'][suffix] = original['acceptance_verify']['']
        self.fake()
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines()[:2], ['Proposition : .workspace/proposals/sum-1', 'Proposition : .workspace/proposals/sum-2']
        )
        self.assertTrue((self.root / '.workspace/proposals/sum-2/selfcheck.txt').is_file())

    def test_blank_template_refused_without_planner(self):
        shutil.copy(ROOT / 'template/docs/DEMANDE.md', self.root / 'blank.md')
        self.fake()
        result = self.run_prepare('blank.md')
        self.assertEqual(result.returncode, 2)
        self.assertIn('preuve de réussite', result.stderr)
        self.assertFalse((self.root / 'call.json').exists())
        self.assert_nothing_written()

    def test_proof_with_only_a_comment(self):
        (self.root / 'demande.md').write_text(DEMANDE.replace('add(2, 3) renvoie 5 et add("a", 1) lève TypeError.', ''))
        self.fake()
        result = self.run_prepare()
        self.assertEqual(result.returncode, 2)
        self.assertIn('preuve de réussite', result.stderr)
        self.assertFalse((self.root / 'call.json').exists())
        self.assert_nothing_written()

    def test_missing_need_and_case_insensitive_titles(self):
        (self.root / 'demande.md').write_text('## preuve de RÉUSSITE\nUn test.\n')
        self.fake()
        self.assertEqual(self.run_prepare().returncode, 2)
        self.assertFalse((self.root / 'call.json').exists())
        (self.root / 'demande.md').write_text('## BESOIN\nUne somme.\n\n## preuve de réussite\nUn test.\n')
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_full_gate_as_verify_refused(self):
        for verify in ('bash scripts/gate.sh', 'scripts/gate.sh', 'gate.sh', 'cd x && ./gate.sh'):
            with self.subTest(verify=verify):
                self.plan['tasks'][0]['verify'] = verify
                self.fake()
                result = self.run_prepare()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assert_nothing_written()

    def test_vacuous_test_gives_code_one(self):
        self.plan['acceptance'][0]['content'] = (
            'assert True\n' + 'class Cases:\n' + ''.join('    def test_case_%d(self):\n        pass\n' % i for i in range(12))
        )
        self.fake()
        result = self.run_prepare()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Proposition : .workspace/proposals/sum', result.stdout)
        self.assertTrue(
            (self.root / '.workspace/proposals/sum/selfcheck.txt').read_text().startswith('acceptance_vacuous : vert sur la base')
        )

    def test_existing_proposal_is_preserved(self):
        existing = self.root / '.workspace/proposals/sum'
        existing.mkdir(parents=True)
        (existing / 'task.md').write_text('original')
        self.fake()
        self.assertEqual(self.run_prepare().returncode, 2)
        self.assertEqual((existing / 'task.md').read_text(), 'original')
        self.assertEqual(sorted(path.name for path in existing.iterdir()), ['task.md'])

    def test_project_without_commit(self):
        shutil.rmtree(self.root / '.git')
        self.git('init', '-q')
        self.fake()
        result = self.run_prepare()
        self.assertEqual(result.returncode, 2)
        self.assert_nothing_written()

    def test_project_without_git(self):
        shutil.rmtree(self.root / '.git')
        self.fake()
        self.assertEqual(self.run_prepare().returncode, 2)
        self.assert_nothing_written()

    def test_absent_planner(self):
        self.assertEqual(self.run_prepare().returncode, 3)
        self.assert_nothing_written()

    def test_usual_mode_unchanged(self):
        self.fake()
        result = subprocess.run(
            ['bash', 'scripts/prepare-task.sh', 'demande.md', 'sum', '--executor', 'claude-haiku'],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'sum\n')
        self.assertTrue((self.root / 'tasks/sum.md').is_file())
        self.assertFalse((self.root / '.workspace/proposals').exists())


if __name__ == '__main__':
    unittest.main()
