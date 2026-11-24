"""Modules a foreign project provides or declares (Python 3.14)."""

import ast
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import project_modules  # noqa: E402

SETUP_PY = """from setuptools import setup
requires = ['urllib3>=1.21.1,<1.27', 'certifi>=2017.4.17']
test_requirements = ['pytest-mock', 'pytest>=3']
setup(
    name='x',
    install_requires=requires,
    tests_require=test_requirements,
    extras_require={'socks': ['PySocks>=1.5.6'], 'empty': []},
    classifiers=['Not :: A :: Requirement'],
)
"""
SETUP_CFG = """[metadata]
name = x

[options]
install_requires =
    Werkzeug>=2.2
    Jinja2
tests_require = pytest

[options.extras_require]
async = asgiref>=3.2
"""


class ProjectModulesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=os.environ.get('WORKSPACE_TEST_TMP')))
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def test_no_declaration_file(self):
        self.assertEqual(project_modules.requirement_lines(self.root), [])

    def test_setup_py_lists_names_and_extras(self):
        self.write('setup.py', SETUP_PY)
        lines = project_modules.requirement_lines(self.root)
        self.assertEqual(sorted(lines), ['PySocks>=1.5.6', 'certifi>=2017.4.17', 'pytest-mock', 'pytest>=3', 'urllib3>=1.21.1,<1.27'])

    def test_setup_py_is_parsed_never_run(self):
        marker = self.root / 'ran'
        self.write('setup.py', "open(%r, 'w').close()\nsetup(install_requires=['idna'])\n" % str(marker))
        self.assertEqual(project_modules.requirement_lines(self.root), ['idna'])
        self.assertFalse(marker.exists())

    def test_broken_setup_files_give_nothing(self):
        self.write('setup.py', 'def (:\n')
        self.write('setup.cfg', 'no section header\n')
        self.assertEqual(project_modules.requirement_lines(self.root), [])

    def test_setup_cfg(self):
        self.write('setup.cfg', SETUP_CFG)
        lines = [line.strip() for line in project_modules.requirement_lines(self.root) if line.strip()]
        self.assertEqual(sorted(lines), ['Jinja2', 'Werkzeug>=2.2', 'asgiref>=3.2', 'pytest'])

    def test_requirements_folder(self):
        self.write('requirements/tests.in', '# tools\npytest\n-r base.in\n')
        self.write('requirements/tests.txt', 'pytest==7.2.2\n')
        self.write('requirements/notes.md', 'requests\n')
        self.assertEqual(project_modules.requirement_lines(self.root), ['pytest', 'pytest==7.2.2'])

    def test_tree_modules(self):
        self.write('src/flask/__init__.py', '')
        self.write('tests/test_apps/blueprintapp/__init__.py', '')
        self.write('tools/helper.py', '')
        self.write('docs/index.rst', '')
        self.write('.venv/lib/site/requests/__init__.py', '')
        self.write('.workspace/acceptance/t/ghost.py', '')
        self.write('top.py', '')
        names = project_modules.tree_modules(self.root)
        self.assertLessEqual({'flask', 'blueprintapp', 'helper', 'tools', 'top'}, names)
        self.assertFalse({'docs', 'requests', 'ghost', self.root.name} & names)

    def test_runner_modules(self):
        task = {'verify': 'PYTHONPATH=$PWD/src python3.11 -m pytest -q tests', 'acceptance_verify': 'python3 -m unittest tests.test_x'}
        self.assertEqual(project_modules.runner_modules(task), {'pytest', 'unittest'})
        self.assertEqual(project_modules.runner_modules({'verify': 'make test'}), set())
        self.assertEqual(project_modules.runner_modules({}), set())

    def test_conditional_modules(self):
        source = (
            'import json\n'
            'try:\n    import simplejson\nexcept ImportError:\n    simplejson = None\n'
            'def helper():\n    import always\n    if PY3:\n        import winreg\n    else:\n        import _winreg as winreg\n'
            'if WINDOWS:\n    from msvcrt import getch\n'
        )
        names = project_modules.conditional_modules(ast.parse(source))
        self.assertEqual(names, {'simplejson', 'winreg', '_winreg', 'msvcrt'})


if __name__ == '__main__':
    unittest.main()
