import copy
import json
import os
import re
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

BRIEF = '''# Brief

## Contexte
Une petite boutique en ligne.

## Ce que je veux
Un truc pour que les clients voient leur panier.

## Contraintes
Rien de cher.

## Délai et budget
Vite, pas trop cher.
'''

ANSWERS = '1. Oui, la somme du panier.\n2. Le panier reste en mémoire.\n'

CADRAGE = dict(
    questions=[dict(question='Quelle devise ?', choices=['EUR', 'USD']), dict(question='Quels produits ?', choices=None)],
    criteria=['Quand le panier contient 2 et 3, alors le total vaut 5.', 'quand le panier est vide, alors le total vaut 0.'],
    estimate=dict(duration_minutes=45, tokens=400000, executor='claude-haiku'),
    plan=['Écrire add.', 'Brancher le panier.'],
    verify='python3 -m unittest tests.test_sum',
)

TWELVE = 'class Cases:\n' + ''.join('    def test_case_%d(self):\n        pass\n' % i for i in range(12))
PLAN = dict(
    tasks=[
        dict(suffix='', allowed=['src/sum_mod.py'], **{'class': 'small'}, verify='python3 -m unittest', body='# Besoin\nSomme stricte.')
    ],
    acceptance=[dict(task_suffix='', path='tests/test_sum.py', content='from src.sum_mod import add\nassert add(2, 3) == 5\n' + TWELVE)],
    acceptance_verify={'': 'python3 tests/test_sum.py'},
)


@unittest.skipUnless(GIT, 'git absent')
class PrepareBriefTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=os.environ.get('WORKSPACE_TEST_TMP'))).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        shutil.copytree(ROOT / 'template/scripts', self.root / 'scripts', ignore=shutil.ignore_patterns('__pycache__', '__tests__'))
        (self.root / '.gitignore').write_text('.workspace/\ncall*.json\nbin/\n__pycache__/\n')
        (self.root / 'brief.md').write_text(BRIEF)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('python3', 'bash'):
            (self.bin / name).symlink_to(shutil.which(name))
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + str(Path(GIT).parent))
        self.env.pop('WORKSPACE_AGENT_ROOT', None)
        self.cadrage = copy.deepcopy(CADRAGE)
        self.mission = self.root / '.workspace/missions/boutique'
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

    def fake(self, cadrage=None, plan=None):
        """A claude that answers the cadrage prompt and the fiche prompt differently and records each prompt."""
        answers = dict(cadrage=self.cadrage if cadrage is None else cadrage, plan=PLAN if plan is None else plan)
        script = '#!' + sys.executable + '\nimport json, pathlib, sys\n'
        script += 'prompt = sys.stdin.read()\n'
        script += 'kind = "cadrage" if "bureau d\'études" in prompt else "plan"\n'
        script += 'pathlib.Path("call-" + kind + ".json").write_text(json.dumps(dict(prompt=prompt)))\n'
        script += 'answers = json.loads(' + repr(json.dumps(answers)) + ')\n'
        script += 'print(json.dumps(dict(result=json.dumps(answers[kind]), usage=dict(input_tokens=1, output_tokens=1))))\n'
        (self.bin / 'claude').write_text(script)
        (self.bin / 'claude').chmod(0o700)

    def prompt(self, kind):
        return json.loads((self.root / ('call-' + kind + '.json')).read_text())['prompt']

    def run_prepare(self, *extra, brief='brief.md', name='boutique'):
        return subprocess.run(
            ['bash', 'scripts/prepare-task.sh', '--from-brief', brief, name, *extra],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def answer(self, text=ANSWERS):
        self.mission.mkdir(parents=True, exist_ok=True)
        (self.mission / 'reponses.md').write_text(text)

    def run_second(self, *extra):
        return self.run_prepare('--with-answers', '--executor', 'claude-haiku', *extra)

    def frame(self):
        self.fake()
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def assert_no_mission(self):
        self.assertFalse((self.root / '.workspace/missions').exists())

    def assert_no_proposal(self):
        self.assertFalse((self.root / '.workspace/proposals').exists())
        self.assertFalse((self.root / '.workspace/tasks').exists())
        self.assertFalse((self.root / 'tasks').exists())

    def test_template_has_the_four_sections(self):
        text = (ROOT / 'template/docs/BRIEF.md').read_text(encoding='utf-8')
        self.assertTrue(text.startswith('# Brief\n'))
        self.assertEqual(
            [line for line in text.splitlines() if line.startswith('## ')],
            ['## Contexte', '## Ce que je veux', '## Contraintes', '## Délai et budget'],
        )
        for line in text.splitlines():
            if line.startswith('## '):
                following = text.splitlines()[text.splitlines().index(line) + 1]
                self.assertTrue(following.startswith('<!--') and following.endswith('-->'), line)

    def test_step_one_writes_the_cadrage_only(self):
        result = self.frame()
        self.assertEqual(result.stdout.strip(), '.workspace/missions/boutique/cadrage.md')
        self.assertEqual(sorted(path.name for path in self.mission.iterdir()), ['cadrage.json', 'cadrage.md'])
        self.assertEqual(json.loads((self.mission / 'cadrage.json').read_text()), self.cadrage)
        self.assertEqual(self.mission.stat().st_mode & 0o777, 0o700)
        for name in ('cadrage.json', 'cadrage.md'):
            self.assertEqual((self.mission / name).stat().st_mode & 0o777, 0o600)
        self.assert_no_proposal()
        self.assertFalse((self.root / '.workspace/acceptance').exists())
        self.assertFalse((self.root / 'call-plan.json').exists())
        self.assertEqual(self.git('status', '--porcelain').stdout, '')

    def test_cadrage_markdown_shape(self):
        self.frame()
        text = (self.mission / 'cadrage.md').read_text(encoding='utf-8')
        lines = text.splitlines()
        self.assertEqual(lines[0], '# Cadrage — boutique')
        self.assertIn('Rien n\'est construit', text)
        self.assertIn('reponses.md', lines[2])
        self.assertIn('--with-answers', lines[2])
        self.assertEqual(
            [line for line in lines if line.startswith('## ')],
            ['## Questions au client', '## Critères d\'acceptation proposés', '## Estimation', '## Plan'],
        )
        for expected in (
            '1. Quelle devise ? (choix : EUR / USD)',
            '2. Quels produits ?',
            '- Quand le panier contient 2 et 3, alors le total vaut 5.',
            '- quand le panier est vide, alors le total vaut 0.',
            '- Durée : 45 min',
            '- Tokens : 400000',
            '- Exécutant pressenti : claude-haiku',
            '- Vérification proposée : `python3 -m unittest tests.test_sum`',
            '1. Écrire add.',
            '2. Brancher le panier.',
        ):
            self.assertIn(expected, lines)
        self.assertNotIn('2. Quels produits ? (choix', text)

    def test_prompt_rules(self):
        self.frame()
        prompt = self.prompt('cadrage')
        self.assertIn(BRIEF, prompt)
        for expected in (
            '"questions"',
            '"criteria"',
            '"estimate"',
            '"plan"',
            '"verify"',
            'Quand …, alors …',
            'Cinq questions au plus',
            'scripts/gate.sh',
            'Ne rien construire',
            'claude-opus',
        ):
            self.assertIn(expected, prompt)

    def test_executor_is_optional_in_step_one_only(self):
        self.frame()
        shutil.rmtree(self.root / '.workspace/missions')
        result = self.run_prepare('--executor', 'codex')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.answer()
        result = self.run_prepare('--with-answers')
        self.assertEqual(result.returncode, 2)
        self.assert_no_proposal()
        result = subprocess.run(
            ['bash', 'scripts/prepare-task.sh', 'brief.md', 'boutique'], cwd=self.root, env=self.env, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 2)

    def test_invalid_cadrages_write_nothing(self):
        def with_(**changes):
            return dict(copy.deepcopy(CADRAGE), **changes)

        def estimate(**changes):
            return with_(estimate=dict(CADRAGE['estimate'], **changes))

        question = dict(question='Quoi ?', choices=None)
        extra = with_()
        extra['extra'] = 1
        missing = with_()
        del missing['plan']
        cases = {
            'six questions': with_(questions=[question] * 6),
            'question vide': with_(questions=[dict(question=' ', choices=None)]),
            'question sans choices': with_(questions=[dict(question='Quoi ?')]),
            'question champ en trop': with_(questions=[dict(question='Quoi ?', choices=None, extra=1)]),
            'choices vide': with_(questions=[dict(question='Quoi ?', choices=[])]),
            'choices non chaîne': with_(questions=[dict(question='Quoi ?', choices=['a', 3])]),
            'choices chaîne': with_(questions=[dict(question='Quoi ?', choices='a')]),
            'critère sans Quand': with_(criteria=['Le prix est juste.']),
            'critère sans alors': with_(criteria=['Quand le prix baisse, il est juste.']),
            'critère multiligne': with_(criteria=['Quand a, alors b\nQuand c, alors d']),
            'aucun critère': with_(criteria=[]),
            'onze critères': with_(criteria=['Quand a, alors b'] * 11),
            'durée booléenne': estimate(duration_minutes=True),
            'tokens nuls': estimate(tokens=0),
            'tokens flottants': estimate(tokens=1.5),
            'durée texte': estimate(duration_minutes='45'),
            'exécutant inconnu': estimate(executor='gpt'),
            'estimate en trop': with_(estimate=dict(CADRAGE['estimate'], extra=1)),
            'plan vide': with_(plan=[]),
            'neuf étapes': with_(plan=['étape'] * 9),
            'étape vide': with_(plan=['']),
            'verify gate': with_(verify='bash scripts/gate.sh'),
            'verify gate relatif': with_(verify='cd x && ./gate.sh'),
            'verify vide': with_(verify=''),
            'champ en trop': extra,
            'champ manquant': missing,
            'liste': [CADRAGE],
        }
        for label, cadrage in cases.items():
            with self.subTest(label):
                self.fake(cadrage)
                result = self.run_prepare()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assert_no_mission()

    def test_limits_are_accepted(self):
        self.cadrage['questions'] = [dict(question='Q' + str(i), choices=['a']) for i in range(5)]
        self.cadrage['criteria'] = ['Quand a, alors b'] * 10
        self.cadrage['plan'] = ['étape'] * 8
        self.frame()
        self.assertEqual((self.mission / 'cadrage.md').read_text().count('\n1. '), 2)
        self.cadrage['questions'] = []
        shutil.rmtree(self.root / '.workspace/missions')
        self.frame()

    def test_existing_cadrage_is_never_rewritten(self):
        self.mission.mkdir(parents=True)
        (self.mission / 'cadrage.md').write_text('original')
        self.fake()
        result = self.run_prepare()
        self.assertEqual(result.returncode, 2)
        self.assertEqual((self.mission / 'cadrage.md').read_text(), 'original')
        self.assertEqual(sorted(path.name for path in self.mission.iterdir()), ['cadrage.md'])
        self.assertFalse((self.root / 'call-cadrage.json').exists())

    def test_second_run_of_step_one_is_refused(self):
        self.frame()
        before = (self.mission / 'cadrage.md').read_text()
        self.fake(dict(CADRAGE, plan=['Autre.']))
        self.assertEqual(self.run_prepare().returncode, 2)
        self.assertEqual((self.mission / 'cadrage.md').read_text(), before)

    def test_brief_empty_too_long_or_missing(self):
        self.fake()
        (self.root / 'brief.md').write_text(' \n')
        self.assertEqual(self.run_prepare().returncode, 2)
        (self.root / 'brief.md').write_text('a' * 65537)
        self.assertEqual(self.run_prepare().returncode, 2)
        self.assertEqual(self.run_prepare(brief='absent.md').returncode, 2)
        self.assert_no_mission()
        self.assertFalse((self.root / 'call-cadrage.json').exists())

    def test_invalid_name(self):
        self.fake()
        for name in ('Boutique', 'a/b', '..', 'x' * 101):
            with self.subTest(name=name):
                self.assertEqual(self.run_prepare(name=name).returncode, 2)
        self.assert_no_mission()

    def test_absent_planner(self):
        result = self.run_prepare()
        self.assertEqual(result.returncode, 3)
        self.assert_no_mission()

    def test_with_answers_without_from_brief(self):
        self.fake()
        result = subprocess.run(
            ['bash', 'scripts/prepare-task.sh', 'brief.md', 'boutique', '--with-answers', '--executor', 'claude-haiku'],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / 'call-plan.json').exists())
        self.assert_no_mission()

    def test_answers_missing_before_step_two(self):
        self.frame()
        result = self.run_second()
        self.assertEqual(result.returncode, 2)
        self.assertIn('reponses.md', result.stderr)
        self.assertFalse((self.root / 'call-plan.json').exists())
        self.assert_no_proposal()

    def test_answers_empty(self):
        self.frame()
        self.answer(' \n\n')
        result = self.run_second()
        self.assertEqual(result.returncode, 2)
        self.assertIn('reponses.md', result.stderr)
        self.assertFalse((self.root / 'call-plan.json').exists())
        self.assert_no_proposal()

    def test_step_two_without_cadrage_or_with_a_broken_one(self):
        self.answer()
        self.fake()
        self.assertEqual(self.run_second().returncode, 2)
        (self.mission / 'cadrage.json').write_text('{pas du json')
        self.assertEqual(self.run_second().returncode, 2)
        (self.mission / 'cadrage.json').write_text(json.dumps(dict(self.cadrage, verify='bash scripts/gate.sh')))
        self.assertEqual(self.run_second().returncode, 2)
        (self.mission / 'cadrage.json').write_text(json.dumps(dict(self.cadrage, questions=[{}] * 6)))
        self.assertEqual(self.run_second().returncode, 2)
        self.assertFalse((self.root / 'call-plan.json').exists())
        self.assert_no_proposal()

    def test_step_two_writes_proposals(self):
        self.frame()
        self.answer()
        result = self.run_second()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], 'Proposition : .workspace/proposals/boutique')
        base = self.root / '.workspace/proposals/boutique'
        task = read_task(base / 'task.md')
        self.assertEqual((task['agent'], task['acceptance']), ('claude-haiku', ['tests/test_sum.py']))
        self.assertTrue((base / 'selfcheck.txt').read_text().startswith('rouge sur la base (code '))
        self.assertFalse((self.root / '.workspace/tasks').exists())
        self.assertFalse((self.root / 'tasks').exists())
        self.assertEqual(self.git('status', '--porcelain').stdout, '')

    def test_step_two_prompt_is_a_demande(self):
        self.frame()
        self.answer()
        self.assertEqual(self.run_second().returncode, 0)
        prompt = self.prompt('plan')
        demande = prompt[prompt.index('DEMANDE :\n') + len('DEMANDE :\n') :]
        self.assertIn('fixe ce que les tests cachés doivent prouver', prompt)
        sections = re.findall(r'^## (.*)$', demande, re.M)
        self.assertEqual(sections[-3:], ['Ce qui ne doit pas casser', 'Preuve de réussite', 'Fichiers'])
        self.assertTrue(demande.startswith('# Demande\n\n## Besoin\n' + BRIEF.strip()))
        self.assertIn(ANSWERS, demande)
        self.assertLess(demande.index(BRIEF.strip()), demande.index(ANSWERS.strip()))
        self.assertIn('## Ce qui ne doit pas casser\nLes tests existants.\n', demande)
        proof = demande[demande.index('## Preuve de réussite\n') : demande.index('## Fichiers')]
        self.assertEqual(proof.splitlines()[1:3], ['- ' + criterion for criterion in CADRAGE['criteria']])
        self.assertTrue(demande.rstrip().endswith('## Fichiers\nNon précisé.'))

    def test_step_two_vacuous_test_gives_code_one(self):
        self.frame()
        self.answer()
        plan = copy.deepcopy(PLAN)
        plan['acceptance'][0]['content'] = 'assert True\n' + TWELVE
        self.fake(plan=plan)
        result = self.run_second()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(
            (self.root / '.workspace/proposals/boutique/selfcheck.txt').read_text().startswith('acceptance_vacuous : vert sur la base')
        )

    def test_step_two_existing_proposal_is_preserved(self):
        self.frame()
        self.answer()
        existing = self.root / '.workspace/proposals/boutique'
        existing.mkdir(parents=True)
        (existing / 'task.md').write_text('original')
        self.assertEqual(self.run_second().returncode, 2)
        self.assertEqual((existing / 'task.md').read_text(), 'original')

    def test_step_two_refuses_the_full_gate_in_the_plan(self):
        self.frame()
        self.answer()
        plan = copy.deepcopy(PLAN)
        plan['tasks'][0]['verify'] = 'bash scripts/gate.sh'
        self.fake(plan=plan)
        self.assertEqual(self.run_second().returncode, 2)
        self.assert_no_proposal()

    def test_step_two_absent_planner(self):
        self.frame()
        self.answer()
        (self.bin / 'claude').unlink()
        self.assertEqual(self.run_second().returncode, 3)
        self.assert_no_proposal()

    def test_existing_modes_unchanged(self):
        (self.root / 'demande.md').write_text('## Besoin\nUne somme.\n\n## Preuve de réussite\nadd(2, 3) renvoie 5.\n')
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
        self.assertFalse((self.root / '.workspace/missions').exists())
        result = subprocess.run(
            ['bash', 'scripts/prepare-task.sh', '--from-demande', 'demande.md', 'sum2', '--executor', 'claude-haiku'],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / '.workspace/proposals/sum2/task.md').is_file())
        self.assertFalse((self.root / '.workspace/missions').exists())


if __name__ == '__main__':
    unittest.main()
