"""Adoption d'un dépôt étranger comme mission (Python 3.14)."""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/mission'))
import adopt  # noqa: E402

GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME='Test',
    GIT_AUTHOR_EMAIL='test@example.invalid',
    GIT_COMMITTER_NAME='Test',
    GIT_COMMITTER_EMAIL='test@example.invalid',
    GIT_CONFIG_GLOBAL=os.devnull,
    GIT_CONFIG_SYSTEM=os.devnull,
)


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo)] + list(args), env=GIT_ENV, check=True, capture_output=True).stdout.decode()


class AdoptTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(dir=os.environ.get('WORKSPACE_TEST_TMP'))).resolve()
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.missions = self.temp / 'missions'

    def make_source(self, files, name='source'):
        repo = self.temp / name
        repo.mkdir()
        git(repo, 'init', '-q', '-b', 'trunk')
        for relative, content in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content if isinstance(content, bytes) else content.encode())
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '-m', 'initial')
        return repo

    def run_adopt(self, source, directory=None, **kwargs):
        directory = directory or self.missions / 'm'
        output = io.StringIO()
        with redirect_stdout(output):
            record = adopt.adopt(str(source), directory=str(directory), **kwargs)
        return record, output.getvalue(), Path(directory)

    def snapshot(self, repo):
        return git(repo, 'rev-parse', 'HEAD'), git(repo, 'status', '--porcelain')


class AdoptTests(AdoptTestCase):
    def test_nominal_python_project(self):
        source = self.make_source({'pytest.ini': '[pytest]\n', 'lib.py': 'x = 1\n', 'pyproject.toml': ''})
        before = self.snapshot(source)
        record, output, mission = self.run_adopt(source, name='demo')
        self.assertEqual(self.snapshot(source), before)
        self.assertEqual(
            sorted(record),
            sorted(
                ['name', 'source', 'repo', 'profile', 'verify', 'default_branch', 'base_commit', 'branch', 'tooling_commit', 'adopted_at']
            ),
        )
        text = (mission / 'mission.json').read_text(encoding='utf-8')
        self.assertEqual(json.loads(text), record)
        self.assertEqual(text, json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
        self.assertRegex(record['adopted_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(
            (record['name'], record['repo'], record['profile'], record['verify']), ('demo', 'repo', 'python', 'python3 -m pytest')
        )
        self.assertEqual((record['default_branch'], record['branch']), ('trunk', 'mission/demo'))
        repo = mission / 'repo'
        self.assertEqual(git(repo, 'rev-parse', 'trunk').strip(), record['base_commit'])
        self.assertEqual(git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip(), 'mission/demo')
        self.assertEqual(git(repo, 'rev-parse', 'HEAD').strip(), record['tooling_commit'])
        self.assertEqual(git(repo, 'rev-parse', 'HEAD~1').strip(), record['base_commit'])
        self.assertEqual(
            git(repo, 'log', '-1', '--format=%s%n%an <%ae>', 'HEAD').splitlines(),
            ['chore(workspace): circuit installé pour la mission demo', 'Workspace IA <workspace-ia@example.invalid>'],
        )
        self.assertEqual(git(repo, 'status', '--porcelain', '--untracked-files=all'), '')
        self.assertEqual(git(repo, 'diff', '--name-only', '--diff-filter=MDRT', record['base_commit'], 'HEAD'), '')
        self.assertTrue((repo / 'scripts/delegate.sh').is_file())
        self.assertEqual((repo / 'project.profile').read_text().strip(), 'python')
        self.assertEqual(output.splitlines(), ['Mission : demo', 'Profil : python', 'Porte : python3 -m pytest', 'Branche : mission/demo'])

    def test_workspace_directory_is_ignored(self):
        _, _, mission = self.run_adopt(self.make_source({'a.txt': 'a\n'}))
        repo = mission / 'repo'
        (repo / '.workspace').mkdir()
        (repo / '.workspace/x').write_text('x')
        self.assertEqual(git(repo, 'status', '--porcelain', '--untracked-files=all'), '')

    def test_source_without_gitignore_gets_added_files(self):
        source = self.make_source({'README.md': 'hi\n'})
        record, output, mission = self.run_adopt(source)
        repo = mission / 'repo'
        self.assertIn('.gitignore', git(repo, 'ls-files').splitlines())
        self.assertIn('.gitattributes', git(repo, 'ls-files').splitlines())
        self.assertEqual(record['profile'], 'docs')
        self.assertIsNone(record['verify'])
        self.assertIn('Porte : aucune', output.splitlines())

    def test_crlf_files_of_the_adopted_repository_are_left_alone(self):
        # E18: sphinx and pylint track CRLF Python files; `*.py text eol=lf` rewrote them and the adoption refused itself.
        crlf = b'x = 1\r\ny = 2\r\n'
        for name, extra in (('plain', {}), ('attrs', {'.gitattributes': b'*.png binary\n'})):
            source = self.make_source({'pkg/win.py': crlf, 'tool.sh': b'echo hi\r\n', 'data.json': b'{}\r\n', **extra}, name=name)
            record, _, mission = self.run_adopt(source, directory=self.missions / name)
            repo = mission / 'repo'
            self.assertEqual((repo / 'pkg/win.py').read_bytes(), crlf, name)
            self.assertEqual(git(repo, 'diff', '--name-only', record['base_commit'], 'HEAD', '--', 'pkg', 'tool.sh', 'data.json'), '', name)
            self.assertEqual(git(repo, 'status', '--porcelain', '--untracked-files=all'), '', name)
            self.assertIn('eol: lf', git(repo, 'check-attr', 'eol', '--', 'scripts/gate.sh'), name)
            self.assertIn('eol: lf', git(repo, 'check-attr', 'eol', '--', 'scripts/lib/task.py'), name)
            self.assertIn('eol: unspecified', git(repo, 'check-attr', 'eol', '--', 'pkg/win.py'), name)
            self.assertIn('eol: unspecified', git(repo, 'check-attr', 'eol', '--', 'tool.sh'), name)

    def test_tracked_gitignore_and_gitattributes_stay_byte_identical(self):
        ignore = b'build/\r\nnode_modules'
        attributes = b'*.png binary'
        source = self.make_source({'.gitignore': ignore, '.gitattributes': attributes, 'a.txt': 'a\n'})
        record, _, mission = self.run_adopt(source)
        repo = mission / 'repo'
        self.assertEqual((repo / '.gitignore').read_bytes(), ignore)
        self.assertEqual((repo / '.gitattributes').read_bytes(), attributes)
        exclude = Path(git(repo, 'rev-parse', '--git-path', 'info/exclude').strip())
        exclude = exclude if exclude.is_absolute() else repo / exclude
        self.assertEqual(sorted(set(exclude.read_text().split()) & {'.workspace/', '__pycache__/'}), ['.workspace/', '__pycache__/'])
        attributes_file = Path(git(repo, 'rev-parse', '--git-path', 'info/attributes').strip())
        attributes_file = attributes_file if attributes_file.is_absolute() else repo / attributes_file
        self.assertIn('scripts/**/*.sh text eol=lf', attributes_file.read_text().splitlines())
        self.assertEqual(git(repo, 'diff', '--name-only', '--diff-filter=MDRT', record['base_commit'], 'HEAD'), '')
        self.assertEqual(git(repo, 'status', '--porcelain', '--untracked-files=all'), '')
        (repo / '.workspace').mkdir()
        (repo / '.workspace/x').write_text('x')
        self.assertEqual(git(repo, 'status', '--porcelain', '--untracked-files=all'), '')

    def test_c_project(self):
        source = self.make_source({'Makefile': 'all:\n\tcc main.c\n', 'main.c': 'int main(void){return 0;}\n'})
        record, _, _ = self.run_adopt(source)
        self.assertEqual(record['profile'], 'c')
        self.assertIsNone(record['verify'])

    def test_makefile_test_rule_and_node_profile(self):
        source = self.make_source({'package.json': '{"name": "x"}', 'Makefile': 'test:\n\techo ok\n'})
        record, _, _ = self.run_adopt(source)
        self.assertEqual((record['profile'], record['verify']), ('node', 'make test'))

    def test_default_name_derivation(self):
        source = self.make_source({'a.txt': 'a\n'}, name='My_Lib.git')
        record, _, _ = self.run_adopt(source, directory=self.missions / 'd')
        self.assertEqual(record['name'], 'my-lib')
        self.assertEqual(record['branch'], 'mission/my-lib')

    def test_source_is_resolved_without_modification_of_origin(self):
        source = self.make_source({'a.txt': 'a\n'})
        git(source, 'checkout', '-q', '-b', 'feature')
        record, _, mission = self.run_adopt(source)
        self.assertEqual(record['default_branch'], 'feature')
        self.assertEqual(git(source, 'rev-parse', '--abbrev-ref', 'HEAD').strip(), 'feature')
        self.assertEqual(git(mission / 'repo', 'rev-parse', 'feature').strip(), record['base_commit'])


class FailureTests(AdoptTestCase):
    def test_nonexistent_source_leaves_nothing(self):
        mission = self.missions / 'm'
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_adopt(self.temp / 'absent', directory=mission, name='x')
        self.assertFalse(mission.exists())

    def test_existing_empty_directory_is_emptied_not_removed(self):
        mission = self.missions / 'm'
        mission.mkdir(parents=True)
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_adopt(self.temp / 'absent', directory=mission, name='x')
        self.assertTrue(mission.is_dir())
        self.assertEqual(list(mission.iterdir()), [])

    def test_existing_empty_directory_is_used(self):
        mission = self.missions / 'm'
        mission.mkdir(parents=True)
        record, _, _ = self.run_adopt(self.make_source({'a.txt': 'a\n'}), directory=mission)
        self.assertTrue((mission / 'mission.json').is_file())
        self.assertEqual(record['repo'], 'repo')

    def test_occupied_directory_is_refused_untouched(self):
        mission = self.missions / 'm'
        mission.mkdir(parents=True)
        (mission / 'keep.txt').write_text('keep')
        with self.assertRaises(ValueError):
            self.run_adopt(self.make_source({'a.txt': 'a\n'}), directory=mission)
        self.assertEqual([path.name for path in mission.iterdir()], ['keep.txt'])
        self.assertEqual((mission / 'keep.txt').read_text(), 'keep')

    def test_directory_that_is_a_file_is_refused(self):
        target = self.temp / 'file'
        target.write_text('x')
        with self.assertRaises(ValueError):
            self.run_adopt(self.make_source({'a.txt': 'a\n'}), directory=target)
        self.assertEqual(target.read_text(), 'x')

    def test_invalid_names_write_nothing(self):
        source = self.make_source({'a.txt': 'a\n'})
        for bad in ('Bad/Name', '-lead', '', 'x' * 64, 'Upper'):
            mission = self.missions / 'm'
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.run_adopt(source, directory=mission, name=bad)
            self.assertFalse(mission.exists())

    def test_source_starting_with_dash_is_refused(self):
        mission = self.missions / 'm'
        with self.assertRaises(ValueError):
            self.run_adopt('--upload-pack=touch x', directory=mission, name='x')
        self.assertFalse(mission.exists())

    def test_circuit_conflict_removes_everything(self):
        source = self.make_source({'scripts/delegate.sh': '#!/bin/sh\necho mine\n'})
        mission = self.missions / 'm'
        with self.assertRaises(ValueError):
            self.run_adopt(source, directory=mission)
        self.assertFalse(mission.exists())
        self.assertEqual(git(source, 'status', '--porcelain'), '')

    def test_repository_without_commit_is_refused(self):
        empty = self.temp / 'empty'
        empty.mkdir()
        git(empty, 'init', '-q')
        mission = self.missions / 'm'
        with self.assertRaises(ValueError):
            self.run_adopt(empty, directory=mission, name='x')
        self.assertFalse(mission.exists())


class DetectGateTests(AdoptTestCase):
    def gate(self, files):
        repo = self.temp / 'gate'
        shutil.rmtree(repo, True)
        for relative, content in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        repo.mkdir(exist_ok=True)
        return adopt.detect_gate(repo)

    def test_makefile_rules(self):
        self.assertEqual(self.gate({'Makefile': 'test:\n\ttrue\n'}), 'make test')
        self.assertEqual(self.gate({'Makefile': 'all:\n\ttrue\ntest : all\n\ttrue\n'}), 'make test')
        self.assertEqual(self.gate({'GNUmakefile': 'test:\n\ttrue\n'}), 'make test')
        self.assertIsNone(self.gate({'Makefile': 'test := 1\n'}))
        self.assertIsNone(self.gate({'Makefile': 'test ?= 1\n'}))
        self.assertIsNone(self.gate({'Makefile': '\ttest:\n# test:\nall:\n'}))

    def test_first_makefile_only(self):
        self.assertIsNone(self.gate({'GNUmakefile': 'all:\n', 'Makefile': 'test:\n'}))

    def test_makefile_wins_over_package_json(self):
        self.assertEqual(self.gate({'Makefile': 'test:\n', 'package.json': '{"scripts": {"test": "jest"}}'}), 'make test')

    def test_package_json(self):
        self.assertEqual(self.gate({'package.json': '{"scripts": {"test": "jest"}}'}), 'npm test')
        self.assertIsNone(self.gate({'package.json': json.dumps({'scripts': {'test': 'echo "Error: no test specified" && exit 1'}})}))
        self.assertIsNone(self.gate({'package.json': '{"scripts": {"test": "  "}}'}))
        self.assertIsNone(self.gate({'package.json': '{"scripts": {"test": 3}}'}))
        self.assertIsNone(self.gate({'package.json': '{"scripts": []}'}))
        self.assertIsNone(self.gate({'package.json': '{broken'}))

    def test_default_npm_script_falls_through_to_pytest(self):
        self.assertEqual(
            self.gate({'package.json': '{"scripts": {"test": "echo no test specified"}}', 'pytest.ini': ''}), 'python3 -m pytest'
        )

    def test_makefile_without_test_rule_but_pytest_ini(self):
        self.assertEqual(self.gate({'Makefile': 'all:\n\ttrue\n', 'pytest.ini': ''}), 'python3 -m pytest')

    def test_pytest_markers(self):
        pytest = 'python3 -m pytest'
        self.assertEqual(self.gate({'pyproject.toml': '[tool.pytest.ini_options]\n'}), pytest)
        self.assertEqual(self.gate({'setup.cfg': '[tool:pytest]\n'}), pytest)
        self.assertEqual(self.gate({'tox.ini': '[pytest]\n'}), pytest)
        self.assertEqual(self.gate({'requirements-dev.txt': 'pytest>=7\n'}), pytest)
        self.assertEqual(self.gate({'tests/test_a.py': ''}), pytest)
        self.assertEqual(self.gate({'test/test_a.py': ''}), pytest)

    def test_no_gate(self):
        self.assertIsNone(self.gate({'README.md': 'x'}))
        self.assertIsNone(self.gate({'tests/helper.py': '', 'tests/sub/test_a.py': ''}))
        self.assertIsNone(self.gate({'requirements.txt': 'requests\n'}))
        self.assertIsNone(self.gate({'pyproject.toml': '[project]\n'}))

    def test_unreadable_files_count_as_absent(self):
        repo = self.temp / 'gate'
        repo.mkdir()
        (repo / 'Makefile').write_bytes(b'\xff\xfe test:\n')
        (repo / 'package.json').write_bytes(b'\xff\xfe')
        self.assertIsNone(adopt.detect_gate(repo))


class NameTests(unittest.TestCase):
    def test_derive_name(self):
        for source, expected in (
            ('/tmp/My_Lib.git', 'my-lib'),
            ('https://host/user/Repo.Name.git/', 'repo-name'),
            ('git@host:user/a__b', 'a-b'),
            ('host:solo.git', 'solo'),
            ('/x/--y--', 'y'),
        ):
            with self.subTest(source=source):
                self.assertEqual(adopt.derive_name(source), expected)
        self.assertTrue(re.fullmatch(adopt.NAME_PATTERN, adopt.derive_name('/tmp/ok')))


if __name__ == '__main__':
    unittest.main()
