import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / 'template/scripts/lib'
sys.path.insert(0, str(LIB))
import plan_check  # noqa: E402

BODY = """
## Besoin
Corriger.

## Interface imposée
- f()

## Règles numérotées
1. Une.
2. Deux.
3. Trois.
4. Quatre.

## Critères de conformité
- ok
"""


ANCHORS = 'from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n'


def hidden_source(count=12, extra=''):
    tests = ''.join('    def test_%d(self):\n        pass\n' % i for i in range(count))
    return 'import unittest\n%s\n\nclass T(unittest.TestCase):\n%s' % (extra, tests)


class PlanCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get('WORKSPACE_TEST_TMP'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = Path(self.tmp)
        for name in ('template/scripts/lib/a.py', 'tests/test_a.py'):
            self.write(name, '')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def fiche(  # noqa: PLR0913
        self,
        header_extra='',
        allowed=('template/scripts/lib/a.py', 'tests/test_a.py'),
        body=BODY,
        agent='claude-haiku',
        hidden=None,
        acceptance=True,
    ):
        lines = ['---', 'id: t1', 'agent: ' + agent, 'allowed:'] + ['  - ' + a for a in allowed]
        if acceptance:
            lines += ['acceptance:', '  - tests/test_hidden.py', 'acceptance_verify: python3 -m unittest tests.test_hidden']
        lines += ['verify_profile: off', header_extra, '---']
        if acceptance:
            self.write('.workspace/acceptance/t1/tests/test_hidden.py', hidden if hidden is not None else hidden_source())
        return self.write('.workspace/tasks/t1.md', '\n'.join(lines) + body)

    def codes(self, result, key='defects'):
        return [d['code'] for d in result[key]]

    def test_good_small_sheet(self):
        result = plan_check.check(self.root, self.fiche())
        self.assertEqual(result['verdict'], 'bon', result)
        self.assertEqual((result['class'], result['class_source']), ('small', 'calculée'))
        self.assertEqual(result['hidden_tests'], 12)
        self.assertEqual(result['estimate_seconds'], 240)

    def test_json_keys(self):
        result = plan_check.check(self.root, self.fiche())
        self.assertEqual(
            set(result), {'verdict', 'defects', 'warnings', 'class', 'class_source', 'executor', 'hidden_tests', 'estimate_seconds'}
        )

    def test_no_header(self):
        path = self.write('.workspace/tasks/x.md', 'rien\n')
        result = plan_check.check(self.root, path)
        self.assertEqual(self.codes(result), ['header'])
        self.assertEqual((result['class'], result['hidden_tests']), ('large', 0))
        self.assertEqual(result['verdict'], 'à reprendre')

    def test_unknown_agent_is_header(self):
        result = plan_check.check(self.root, self.fiche(agent='inconnu-xyz'))
        self.assertEqual(self.codes(result), ['header'])

    def test_field_defects(self):
        for extra, code in (('class: huge', 'class_invalid'), ('attempts: 3', 'attempts')):
            self.assertIn(code, self.codes(plan_check.check(self.root, self.fiche(extra))))
        self.write('AGENTS.md', '# x\n\ndocs-check: kit\n')
        path = self.fiche()
        path.write_text(path.read_text().replace('verify_profile: off', 'verify_profile: on'))
        self.assertIn('verify_profile', self.codes(plan_check.check(self.root, path)))

    def test_allowed_defects(self):
        for entry, code in (
            ('template/*.py', 'wide_pattern'),
            ('.', 'wide_pattern'),
            ('scripts/x.sh', 'protected_path'),
            ('.workspace/x', 'protected_path'),
            ('project.profile', 'protected_path'),
        ):
            result = plan_check.check(self.root, self.fiche(allowed=('tests/test_a.py', entry)))
            self.assertIn(code, self.codes(result), entry)

    def test_secret_name(self):
        result = plan_check.check(self.root, self.fiche(body=BODY + '\nAPI_TOKEN=abc\n'))
        self.assertIn('secret_name', self.codes(result))

    def test_class_computation(self):
        task = {'allowed': ['tests/test_a.py'], 'body': BODY}
        self.assertEqual(plan_check.classify(self.root, task)[0], 'small')
        task['allowed'] = ['new/one.py']
        self.assertEqual(plan_check.classify(self.root, task), ('medium', ['1 fichiers nouveaux']))
        task['allowed'] = ['n/a.py', 'n/b.py', 'n/c.py', 'n/d.md', 'tests/test_new.py']
        self.assertEqual(plan_check.classify(self.root, task)[0], 'large')
        task = {'allowed': ['tests/test_a.py'], 'body': BODY.replace('## Critères', '## Autre')}
        self.assertEqual(plan_check.classify(self.root, task), ('large', ['brief flou']))
        many = BODY.replace('4. Quatre.', '\n'.join('%d. r' % i for i in range(4, 9)))
        task = {'allowed': ['tests/test_a.py'], 'body': many}
        self.assertEqual(plan_check.classify(self.root, task), ('medium', ['8 règles']))
        task = {'allowed': ['tests/test_a.py'] + ['template/scripts/lib/a.py'] * 4, 'body': BODY}
        self.assertEqual(plan_check.classify(self.root, task)[0], 'medium')

    def test_header_class_wins(self):
        result = plan_check.check(self.root, self.fiche('class: medium', agent='claude-sonnet'))
        self.assertEqual((result['class'], result['class_source']), ('medium', 'en-tête'))
        self.assertEqual(result['estimate_seconds'], 1800)

    def test_large_needs_opus(self):
        result = plan_check.check(self.root, self.fiche('class: large'))
        self.assertIn('large_not_opus', self.codes(result))
        result = plan_check.check(self.root, self.fiche('class: large', agent='claude-opus'))
        self.assertNotIn('large_not_opus', self.codes(result))

    def test_executor_class_is_warning(self):
        result = plan_check.check(self.root, self.fiche(agent='claude-sonnet'))
        self.assertEqual(result['verdict'], 'bon')
        self.assertEqual(self.codes(result, 'warnings'), ['executor_class'])

    def test_hidden_missing_file_and_none(self):
        path = self.fiche()
        (self.root / '.workspace/acceptance/t1/tests/test_hidden.py').unlink()
        self.assertIn('hidden_missing', self.codes(plan_check.check(self.root, path)))
        result = plan_check.check(self.root, self.fiche(acceptance=False))
        self.assertEqual(self.codes(result), ['hidden_missing'])

    def test_hidden_count_and_min(self):
        result = plan_check.check(self.root, self.fiche(hidden=hidden_source(4)))
        self.assertIn('hidden_count', self.codes(result))
        extra = 'acceptance_min: 4\nacceptance_min_reason: deux défauts d\'une ligne'
        result = plan_check.check(self.root, self.fiche(extra, hidden=hidden_source(4)))
        self.assertNotIn('hidden_count', self.codes(result))
        self.assertEqual(result['verdict'], 'bon', result)

    def pytest_fiche(self, hidden, verify='python3 -m pytest -q --pyargs tests.test_hidden'):
        path = self.fiche(hidden=hidden)
        path.write_text(path.read_text().replace('python3 -m unittest tests.test_hidden', verify))
        return path

    def test_pytest_module_level_and_async_tests_counted(self):
        # E14: pytest tests at module level and `async def` tests counted for zero.
        hidden = ''.join('def test_m%d():\n    pass\n' % i for i in range(5))
        hidden += ''.join('async def test_a%d():\n    pass\n' % i for i in range(4))
        hidden += 'class TestX:\n' + ''.join('    async def test_c%d(self):\n        pass\n' % i for i in range(3))
        hidden += 'def helper_test_x():\n    pass\n    def test_nested():\n        pass\n'
        result = plan_check.check(self.root, self.pytest_fiche(hidden))
        self.assertEqual(result['hidden_tests'], 13)
        self.assertNotIn('hidden_count', self.codes(result))
        self.assertEqual(result['verdict'], 'bon', result)
        # unittest does not run module-level functions: only the methods count.
        result = plan_check.check(self.root, self.fiche(hidden=hidden))
        self.assertEqual(result['hidden_tests'], 4)
        self.assertIn('hidden_count', self.codes(result))

    def test_hidden_import_declared_package(self):
        # E14: third-party packages declared by the project were reported outside the scope.
        src = hidden_source(extra='import pytest\nfrom fastapi.testclient import TestClient\nfrom sqlalchemy import select\n')
        self.assertEqual(len(self.outside('import pytest\nfrom fastapi.testclient import TestClient\nfrom sqlalchemy import select\n')), 3)
        sources = {
            'uv.lock': 'version = 1\n\n[[package]]\nname = "pytest"\n\n[[package]]\nname = "fastapi"\n\n[[package]]\nname = "SQLAlchemy"\n',
            'pyproject.toml': '[project]\nname = "x"\ndependencies = ["fastapi>=0.115", "sqlalchemy[asyncio]>=2.0.36"]\n'
            '[dependency-groups]\ndev = ["pytest>=8.3"]\n',
            'requirements-dev.txt': '# tools\n-r base.txt\npytest==8.3\nfastapi\nSQLAlchemy[asyncio]>=2\n',
        }
        for name, text in sources.items():
            declared = self.write(name, text)
            result = plan_check.check(self.root, self.fiche(hidden=src))
            self.assertNotIn('hidden_outside_scope', self.codes(result), name)
            self.assertEqual(result['verdict'], 'bon', name)
            messages = self.outside('import pytest\nimport requests\n')
            self.assertEqual(len(messages), 1, name)
            self.assertIn('requests', messages[0])
            declared.unlink()
        self.write('uv.lock', 'not = [toml')
        self.assertEqual(len(self.outside('import pytest\n')), 1)

    def test_hidden_import_of_a_foreign_project(self):
        # E18: a public repository declares and lays out its modules elsewhere than the kit's own projects do.
        extra = (
            'import flask\nimport blueprintapp\nimport urllib3.util\nimport winreg\n'
            'if OLD:\n    import _winreg\ntry:\n    import simplejson\nexcept ImportError:\n    pass\n'
        )
        self.assertEqual(len(self.outside(extra)), 3)
        self.write('src/flask/__init__.py', '')
        self.write('tests/test_apps/blueprintapp/__init__.py', '')
        self.write('setup.py', "requires = ['urllib3>=1.21.1,<1.27']\nsetup(install_requires=requires)\n")
        self.assertEqual(self.outside(extra), [])
        messages = self.outside(extra + 'import ghost\nimport pytest\n')
        self.assertEqual(sorted(m.rsplit(' ', 1)[-1] for m in messages), ['ghost', 'pytest'])

    def test_hidden_import_of_the_runner_named_by_the_sheet(self):
        self.assertEqual(len(self.outside('import pytest\n')), 1)
        path = self.fiche(hidden=hidden_source(extra='import pytest\nimport nose\n'))
        path.write_text(
            path.read_text().replace('python3 -m unittest tests.test_hidden', 'python3.11 -m pytest -q --pyargs tests.test_hidden')
        )
        result = plan_check.check(self.root, path)
        messages = [d['message'] for d in result['defects'] if d['code'] == 'hidden_outside_scope']
        self.assertEqual(len(messages), 1)
        self.assertIn('nose', messages[0])

    def test_hidden_borrows_even_under_a_condition(self):
        src = hidden_source(extra='try:\n    from tests.test_a import Fixture\nexcept ImportError:\n    Fixture = None\n')
        self.assertIn('hidden_borrows', self.codes(plan_check.check(self.root, self.fiche(hidden=src))))

    def test_acceptance_min_invalid(self):
        for extra in (
            'acceptance_min: 4',
            'acceptance_min: 20\nacceptance_min_reason: x',
            'acceptance_min: beaucoup\nacceptance_min_reason: x',
        ):
            result = plan_check.check(self.root, self.fiche(extra, hidden=hidden_source(4)))
            self.assertIn('acceptance_min', self.codes(result), extra)
            self.assertIn('hidden_count', self.codes(result), extra)

    def test_acceptance_verify_module(self):
        path = self.fiche()
        path.write_text(path.read_text().replace('tests.test_hidden', 'tests.test_other'))
        self.assertIn('acceptance_verify', self.codes(plan_check.check(self.root, path)))

    def test_hidden_outside_scope_path(self):
        src = hidden_source(extra=ANCHORS + "PATH = ROOT / 'template/scripts/lib/absent.py'\nOK = ROOT / 'template/scripts/lib/a.py'\n")
        result = plan_check.check(self.root, self.fiche(hidden=src))
        self.assertEqual(result['verdict'], 'à reprendre')
        messages = [d['message'] for d in result['defects'] if d['code'] == 'hidden_outside_scope']
        self.assertEqual(len(messages), 1)
        self.assertIn('absent.py', messages[0])

    def outside(self, extra):
        result = plan_check.check(self.root, self.fiche(hidden=hidden_source(extra=extra)))
        return [d['message'] for d in result['defects'] if d['code'] == 'hidden_outside_scope']

    def test_anchored_paths_only(self):
        self.assertEqual(
            len(self.outside(ANCHORS + "LIB = ROOT / 'template/scripts/lib'\nX = str(LIB / 'absent.py')\nY = LIB / 'a.py'\n")), 1
        )
        self.assertEqual(len(self.outside("import os\nHERE = Path(__file__).parent\nZ = HERE.parent / 'docs/absent.md'\n")), 1)
        self.assertEqual(
            len(self.outside("import os\nHERE = os.path.dirname(os.path.abspath(__file__))\nZ = HERE.parent / 'docs/absent.md'\n")), 1
        )
        self.assertEqual(
            len(self.outside("import os\nHERE = os.path.dirname(os.path.abspath(__file__))\nZ = os.fspath(HERE / 'absent.py')\n")), 1
        )

    def test_dirname_wrapped_in_path(self):
        for expr in (
            'Path(os.path.dirname(__file__))',
            'Path(os.path.dirname(os.path.abspath(__file__)))',
            'Path(os.path.dirname(os.path.realpath(__file__)))',
            'Path(os.path.dirname(Path(__file__).resolve()))',
        ):
            extra = "import os\nHERE = %s\nZ = HERE / 'absent.py'\nW = HERE.parent / 'ok_dir/absent.py'\n" % expr
            self.assertEqual(len(self.outside(extra)), 2, expr)

    def test_unanchored_literals_ignored(self):
        extra = (
            ANCHORS + "A = self.root / 'src/a.py'\nB = tmp / 'files/code.py'\n"
            "C = 'template/scripts/lib/absent.py'\nD = ROOT / '..' / 'x.py'\n"
            "E = Path(__file__) / 'x/y.py'\n"
        )
        self.assertEqual(self.outside(extra), [])

    def test_hidden_path_in_allowed_is_fine(self):
        src = hidden_source(extra=ANCHORS + "PATH = ROOT / 'template/scripts/lib/new.py'\n")
        allowed = ('template/scripts/lib/new.py', 'tests/test_a.py')
        result = plan_check.check(self.root, self.fiche(hidden=src, allowed=allowed, agent='claude-sonnet'))
        self.assertNotIn('hidden_outside_scope', self.codes(result))

    def test_hidden_import_outside_scope(self):
        src = hidden_source(extra='import unknown_mod\nimport json\nimport a\n')
        result = plan_check.check(self.root, self.fiche(hidden=src))
        messages = [d['message'] for d in result['defects'] if d['code'] == 'hidden_outside_scope']
        self.assertEqual(len(messages), 1)
        self.assertIn('unknown_mod', messages[0])
        src = hidden_source(extra="import importlib\nM = importlib.import_module('ghost')\n")
        result = plan_check.check(self.root, self.fiche(hidden=src))
        self.assertIn('hidden_outside_scope', self.codes(result))

    def test_hidden_import_new_module_in_allowed(self):
        src = hidden_source(extra='import new_module\n')
        allowed = ('template/scripts/lib/new_module.py', 'tests/test_a.py')
        result = plan_check.check(self.root, self.fiche(hidden=src, allowed=allowed, agent='claude-sonnet'))
        self.assertNotIn('hidden_outside_scope', self.codes(result))

    def test_hidden_borrows(self):
        forms = (
            'from tests.test_a import Fixture',
            'import tests.test_a',
            'from tests import test_a',
            "import importlib\nX = importlib.import_module('tests.test_a')",
            'import test_a',
        )
        for form in forms:
            result = plan_check.check(self.root, self.fiche(hidden=hidden_source(extra=form + '\n')))
            self.assertIn('hidden_borrows', self.codes(result), form)
        allowed = ('template/scripts/lib/a.py',)
        result = plan_check.check(self.root, self.fiche(hidden=hidden_source(extra=forms[0] + '\n'), allowed=allowed))
        self.assertNotIn('hidden_borrows', self.codes(result))

    def fake_claude(self, payload, code=0):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir(exist_ok=True)
        log = self.root / 'claude.log'
        script = bin_dir / 'claude'
        script.write_text(
            '#!/bin/sh\necho "$@" >> %s\ncat >/dev/null\ncat <<\'EOF\'\n%s\nEOF\nexit %d\n' % (log, payload, code), encoding='utf-8'
        )
        script.chmod(0o755)
        old = os.environ['PATH']
        os.environ['PATH'] = str(bin_dir) + os.pathsep + old
        self.addCleanup(os.environ.__setitem__, 'PATH', old)
        return log

    @staticmethod
    def wrap(answer):
        return json.dumps({'result': 'Voici : ' + json.dumps(answer) + ' fin'})

    def test_model_not_called_by_default(self):
        log = self.fake_claude(self.wrap({'realisable': True}))
        plan_check.check(self.root, self.fiche())
        self.assertFalse(log.exists())

    def test_model_defects(self):
        log = self.fake_claude(self.wrap({'realisable': False, 'contradictions': ['a', 'b'], 'classe': 'medium'}))
        result = plan_check.check(self.root, self.fiche(), with_model=True)
        self.assertEqual(self.codes(result), ['model_unfeasible', 'model_contradiction', 'model_contradiction', 'model_class'])
        call = log.read_text()
        self.assertIn('--model sonnet', call)
        self.assertIn('--strict-mcp-config', call)

    def test_model_good(self):
        self.fake_claude(self.wrap({'realisable': True, 'contradictions': [], 'classe': 'small'}))
        result = plan_check.check(self.root, self.fiche(), with_model=True)
        self.assertEqual(result['verdict'], 'bon', result)

    def test_model_unavailable_is_warning(self):
        for payload, code in (('pas du json', 0), (self.wrap({'realisable': False}), 3)):
            self.fake_claude(payload, code)
            result = plan_check.check(self.root, self.fiche(), with_model=True)
            self.assertEqual(result['verdict'], 'bon')
            self.assertEqual(self.codes(result, 'warnings'), ['model_unavailable'])

    def test_check_plan_uses_opus(self):
        log = self.fake_claude(self.wrap({'realisable': False, 'contradictions': ['x']}))
        plan = self.write('plan.md', '# Plan\n')
        result = plan_check.check_plan(self.root, plan)
        self.assertEqual(set(result), {'verdict', 'defects', 'warnings'})
        self.assertEqual(self.codes(result), ['model_unfeasible', 'model_contradiction'])
        self.assertIn('--model opus', log.read_text())

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(LIB / 'plan_check.py'), str(self.root)] + list(args), capture_output=True, text=True)

    def test_cli_text_and_json(self):
        path = str(self.fiche())
        done = self.run_cli(path)
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[0], 'classe : small (calculée)')
        self.assertEqual(lines[-1], 'verdict : bon')
        self.assertIn('durée estimée : 4 min', lines)
        self.assertIn('tests cachés : 12', lines)
        data = json.loads(self.run_cli(path, '--json').stdout)
        self.assertEqual(data['verdict'], 'bon')

    def test_cli_defect_exit_1(self):
        path = str(self.fiche(hidden=hidden_source(extra=ANCHORS + "P = ROOT / 'template/scripts/lib/absent.py'\n")))
        done = self.run_cli(path)
        self.assertEqual(done.returncode, 1)
        self.assertIn('défaut [hidden_outside_scope]', done.stdout)
        self.assertTrue(done.stdout.endswith('verdict : à reprendre (1 défauts)\n'))

    def test_cli_no_header(self):
        done = self.run_cli(str(self.write('x.md', 'vide\n')))
        self.assertEqual(done.returncode, 1)
        self.assertIn('verdict : à reprendre (1 défauts)', done.stdout)

    def test_cli_usage_errors(self):
        self.assertEqual(self.run_cli().returncode, 64)
        self.assertEqual(self.run_cli('--inconnu').returncode, 64)
        self.assertEqual(self.run_cli(str(self.root / 'absent.md')).returncode, 64)
        self.assertEqual(self.run_cli('--plan', str(self.root / 'absent.md')).returncode, 64)
        self.assertEqual(self.run_cli(str(self.fiche()), '--inconnu').returncode, 64)

    def test_cli_plan(self):
        self.fake_claude(self.wrap({'realisable': True, 'contradictions': []}))
        plan = self.write('plan.md', '# Plan\n')
        env = dict(os.environ)
        done = subprocess.run(
            [sys.executable, str(LIB / 'plan_check.py'), str(self.root), '--plan', str(plan)], capture_output=True, text=True, env=env
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), 'verdict : bon')

    def test_shell_wrapper(self):
        script = REPO / 'template/scripts/plan-check.sh'
        done = subprocess.run(['sh', str(script)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 64)
        done = subprocess.run(['sh', str(script), '--inconnu'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 64)


if __name__ == '__main__':
    unittest.main()
