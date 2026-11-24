import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workspace', ROOT / 'scripts/workspace.py')
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


class StarterTests(unittest.TestCase):
    def test_new_project_has_no_ecc_or_bmad_and_named_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            project = workspace.create_project('demo-projet', folder)
            self.assertFalse((project / '.claude/agents/code-reviewer.md').exists())
            self.assertFalse((project / '_bmad/config.toml').exists())
            self.assertIn('demo-projet', (project / 'PROJECT.md').read_text())
            self.assertFalse((project / '.claude/settings.local.json').exists())
            self.assertTrue((project / '.git').is_dir())
            self.assertNotEqual(subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=project, capture_output=True).returncode, 0)

    def test_scripts_stay_lf_in_kit_template_and_new_project(self):
        with tempfile.TemporaryDirectory() as folder:
            project = workspace.create_project('demo', folder)
            for path in (ROOT / '.gitattributes', ROOT / 'template/.gitattributes', project / '.gitattributes'):
                self.assertIn('*.sh text eol=lf', path.read_text().splitlines())

    def test_new_project_excludes_internal_script_test_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            project = workspace.create_project('demo', folder)
            for name in ('__tests__', 'test-support'):
                self.assertTrue((ROOT / 'template/scripts' / name).is_dir())
                self.assertFalse((project / 'scripts' / name).exists())
            self.assertTrue((project / 'scripts/lib/task.py').is_file())

    @unittest.skipUnless(shutil.which('node') and shutil.which('npm'), 'Node and npm required')
    def test_fresh_node_project_runs_no_kit_tests(self):
        with tempfile.TemporaryDirectory() as folder:
            project = workspace.create_project('demo', folder, profile='node')
            (project / 'package.json').write_text(json.dumps({'scripts': {'test': 'node --test'}}))
            result = subprocess.run(['npm', 'test'], cwd=project, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stdout, r'(?:# |ℹ )tests 0\b')

    def test_profiles_are_saved_in_versionable_file(self):
        for profile in ('docs', 'node', 'docker', 'python'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as folder:
                project = workspace.create_project('demo', folder, profile=profile)
                self.assertEqual((project / 'project.profile').read_text(), profile + '\n')
        with tempfile.TemporaryDirectory() as folder:
            project = workspace.create_project('demo', folder)
            self.assertEqual((project / 'project.profile').read_text(), 'docs\n')
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                workspace.create_project('demo', folder, profile='unknown')
            self.assertFalse((Path(folder) / 'demo').exists())

    def test_new_cli_accepts_profile_and_rejects_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [str(ROOT / 'workspace'), 'new', 'demo', '--parent', folder, '--profile', 'node'], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((Path(folder) / 'demo/project.profile').read_text(), 'node\n')
            self.assertTrue((Path(folder) / 'demo/.git').is_dir())
            result = subprocess.run(
                [str(ROOT / 'workspace'), 'new', 'bad', '--parent', folder, '--profile', 'unknown'], capture_output=True, text=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(folder) / 'bad').exists())

    def test_fresh_cli_projects_pass_real_docs_check_for_every_profile(self):
        for profile in ('docs', 'node', 'docker', 'python'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as folder:
                result = subprocess.run(
                    [str(ROOT / 'workspace'), 'new', 'fresh', '--parent', folder, '--profile', profile], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                project = Path(folder) / 'fresh'
                result = subprocess.run(['/bin/sh', 'scripts/docs-check.sh'], cwd=project, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('Last updated: ' + date.today().isoformat(), (project / 'docs/STATUS.md').read_text())

    def test_existing_project_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            project = workspace.create_project('demo', folder)
            marker = project / 'my-work.txt'
            marker.write_text('conserver')
            with self.assertRaises(ValueError):
                workspace.create_project('demo', folder)
            self.assertEqual(marker.read_text(), 'conserver')

    def test_invalid_names_cannot_escape_projects_folder(self):
        for name in ['../secret', '/tmp/project', '', 'a/b', 'a\\b', '..']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                workspace.create_project(name)

    def test_missing_console_build_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'package.json').write_text('{}')
            with patch.dict(os.environ, {'WORKSPACE_CONSOLE_ROOT': folder}):
                with self.assertRaisesRegex(ValueError, 'workspace setup'):
                    workspace.open_project(folder)


class ConsoleRootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'kit'
        (self.root / '.workspace').mkdir(parents=True)
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('WORKSPACE_CONSOLE_ROOT', None)
        patcher = patch.object(workspace, 'ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def console(self, path):
        path.mkdir(parents=True, exist_ok=True)
        (path / 'package.json').write_text('{}')
        return path

    def test_environment_variable_comes_first(self):
        self.console(self.base / 'workspace-console')
        (self.root / '.workspace/console-root').write_text('/nonexistent\n')
        env_dir = self.console(self.base / 'from-env')
        os.environ['WORKSPACE_CONSOLE_ROOT'] = str(env_dir)
        self.assertEqual(workspace.console_root(), env_dir)

    def test_pointer_file_absolute_and_relative_then_sibling_folder(self):
        sibling = self.console(self.base / 'workspace-console')
        self.assertEqual(workspace.console_root(), sibling)
        pointer = self.root / '.workspace/console-root'
        absolute = self.console(self.base / 'elsewhere')
        pointer.write_text(f'{absolute}\nignored\n')
        self.assertEqual(workspace.console_root(), absolute)
        relative = self.console(self.root / 'nested/console')
        pointer.write_text('nested/console\n')
        self.assertEqual(workspace.console_root(), relative)

    def test_missing_console_exits_with_path_tried_for_every_source(self):
        with self.assertRaises(SystemExit) as raised:
            workspace.console_root()
        self.assertIn(str(self.base / 'workspace-console'), str(raised.exception))
        self.assertIn('WORKSPACE_CONSOLE_ROOT', str(raised.exception))
        (self.base / 'no-package').mkdir()
        for value in ('', str(self.base / 'no-package'), str(self.base / 'absent')):
            with self.subTest(value=value):
                os.environ['WORKSPACE_CONSOLE_ROOT'] = value
                with self.assertRaisesRegex(SystemExit, 'console introuvable'):
                    workspace.console_root()

    def test_console_path_command_prints_path_or_exits_2(self):
        script = ROOT / 'scripts/workspace.py'
        env = {k: v for k, v in os.environ.items() if k != 'WORKSPACE_CONSOLE_ROOT'}
        found = self.console(self.base / 'found')
        result = subprocess.run(
            [sys.executable, str(script), 'console-path'],
            capture_output=True,
            text=True,
            env={**env, 'WORKSPACE_CONSOLE_ROOT': str(found)},
        )
        self.assertEqual((result.returncode, result.stdout.strip()), (0, str(found)))
        result = subprocess.run(
            [sys.executable, str(script), 'console-path'],
            capture_output=True,
            text=True,
            env={**env, 'WORKSPACE_CONSOLE_ROOT': str(self.base / 'absent')},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('console introuvable', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


class DoctorTests(unittest.TestCase):
    def test_doctor_runs_without_the_retired_integrations_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            with (
                patch.object(workspace, 'ROOT', Path(folder)),
                patch.object(workspace.shutil, 'which', return_value='/fake/tool'),
                patch.object(workspace.subprocess, 'check_output', return_value='v24.0.0'),
                redirect_stdout(output),
            ):
                self.assertTrue(workspace.doctor())
            self.assertNotIn('integrations.lock.json', output.getvalue())

    def test_retired_commands_are_gone(self):
        for command in ('plugins', 'sources', 'office', 'sync-console'):
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/workspace.py'), command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, command)
            self.assertIn('invalid choice', result.stderr)


if __name__ == '__main__':
    unittest.main()
