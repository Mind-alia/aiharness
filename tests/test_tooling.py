"""Installation du circuit dans des dépôts existants (Python 3.14)."""

import io
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_workspace import ROOT, workspace


class ToolingTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.workspace/tmp-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)

    def install(self, **kwargs):
        output = io.StringIO()
        with redirect_stdout(output):
            workspace.install_tooling(self.project, **kwargs)
        return output.getvalue()

    def test_empty_project_and_repeat(self):
        output = self.install()
        for name in workspace.CIRCUIT_SCRIPTS:
            target = self.project / 'scripts' / name
            self.assertEqual(target.read_bytes(), (ROOT / 'template/scripts' / name).read_bytes())
            self.assertTrue(target.stat().st_mode & 0o111)
        for source in (ROOT / 'template/scripts/lib').glob('*.py'):
            self.assertEqual((self.project / 'scripts/lib' / source.name).read_bytes(), source.read_bytes())
        self.assertTrue((self.project / 'docs/BRIEF-AGENTS.md').exists())
        self.assertEqual((self.project / 'team.json').read_bytes(), (ROOT / 'template/team.json').read_bytes())
        self.assertIn('Ajoutés', output)
        self.assertIn('Identiques', self.install())
        self.assertFalse((self.project / 'scripts/gate.sh').exists())

    def test_tooling_ships_the_container_recipes_and_prints_the_build_commands(self):
        output = self.install()
        for source in (ROOT / 'template/docker').glob('*.Dockerfile'):
            self.assertEqual((self.project / 'docker' / source.name).read_bytes(), source.read_bytes())
        self.assertIn('docker build -t workspace-node-git:24-bookworm -f docker/node-git.Dockerfile docker', output)
        self.assertTrue((self.project / 'scripts/weekly-report.sh').is_file())
        self.assertTrue((self.project / 'scripts/statusline.sh').is_file())

    def test_statusline_install_writes_only_the_given_settings_file(self):
        settings = self.project / 'claude/settings.json'
        done = subprocess.run([str(ROOT / 'workspace'), 'statusline-install', '--settings', str(settings)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('ajouté', done.stdout)
        command = __import__('json').loads(settings.read_text())['statusLine']['command']
        self.assertTrue(command.startswith('sh ') and command.endswith('scripts/statusline.sh'), command)
        again = subprocess.run([str(ROOT / 'workspace'), 'statusline-install', '--settings', str(settings)], capture_output=True, text=True)
        self.assertIn('identique', again.stdout)

    def test_tooling_generates_the_team_subagents_and_keeps_them_on_repeat(self):
        output = self.install()
        agents = sorted(p.name for p in (self.project / '.claude/agents').glob('*.md'))
        self.assertEqual(agents, ['atlas.md', 'bric.md', 'crash.md', 'fiole.md', 'garde.md', 'loupe.md', 'plume.md'])
        self.assertIn('Sous-agents : 7 écrits', output)
        self.assertIn('Sous-agents : 0 écrits, 7 identiques, 0 retirés', self.install())
        done = subprocess.run([str(ROOT / 'workspace'), 'team-agents', str(self.project)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('7 identiques', done.stdout)

    def test_project_files_and_documents_are_preserved_even_with_force(self):
        for name in [
            'scripts/gate.sh',
            'scripts/docs-check.sh',
            'scripts/codex.sh',
            'scripts/gemini.sh',
            'AGENTS.md',
            'CLAUDE.md',
            'docs/BRIEF-AGENTS.md',
            'docs/PIEGES.md',
            'docs/STATUS.md',
            'team.json',
        ]:
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('propre au projet')
        output = self.install(force_circuit=True)
        for name in [
            'scripts/gate.sh',
            'scripts/docs-check.sh',
            'scripts/codex.sh',
            'scripts/gemini.sh',
            'AGENTS.md',
            'CLAUDE.md',
            'docs/BRIEF-AGENTS.md',
            'docs/PIEGES.md',
            'docs/STATUS.md',
            'team.json',
        ]:
            self.assertEqual((self.project / name).read_text(), 'propre au projet')
            self.assertIn(name, output)

    def test_conflicts_refuse_before_any_write_then_force(self):
        target = self.project / 'scripts/ask.sh'
        target.parent.mkdir()
        target.write_text('custom')
        with self.assertRaisesRegex(ValueError, 'scripts/ask.sh'):
            self.install()
        self.assertFalse((self.project / 'project.profile').exists())
        self.assertFalse((self.project / 'scripts/delegate.sh').exists())
        self.install(force_circuit=True)
        self.assertEqual(target.read_bytes(), (ROOT / 'template/scripts/ask.sh').read_bytes())

    def test_dry_run_cli_writes_nothing(self):
        before = sorted(str(p.relative_to(self.project)) for p in self.project.rglob('*'))
        result = subprocess.run(
            [str(ROOT / 'workspace'), 'tooling', str(self.project), '--dry-run', '--profile', 'node'], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Ajoutés', result.stdout)
        self.assertEqual(before, sorted(str(p.relative_to(self.project)) for p in self.project.rglob('*')))

    def test_profile_detection_and_existing_profile(self):
        for markers, expected in [
            (['package.json'], 'node'),
            (['pyproject.toml'], 'python'),
            (['requirements.txt'], 'python'),
            (['Makefile', 'Dockerfile'], 'docker'),
            ([], 'docs'),
        ]:
            with self.subTest(expected=expected):
                for marker in markers:
                    (self.project / marker).touch()
                self.install()
                self.assertEqual((self.project / 'project.profile').read_text(), expected + '\n')
                self.install(profile='node')
                self.assertEqual((self.project / 'project.profile').read_text(), expected + '\n')
                (self.project / 'project.profile').unlink()
                for marker in markers:
                    (self.project / marker).unlink()

    def test_python_package_with_a_package_json_is_python(self):
        # E18: sphinx ships a package.json for its JavaScript tests; the mission asked for the Node image.
        (self.project / 'package.json').write_text('{"scripts": {"test": "karma start"}}')
        self.assertEqual(workspace.detect_profile(self.project), 'node')
        (self.project / 'pyproject.toml').write_text('[tool.ruff]\nline-length = 100\n')
        self.assertEqual(workspace.detect_profile(self.project), 'node')
        (self.project / 'pyproject.toml').write_text('[build-system]\nrequires = ["flit_core"]\n\n[project]\nname = "x"\n')
        self.assertEqual(workspace.detect_profile(self.project), 'python')
        (self.project / 'package.json').unlink()
        self.assertEqual(workspace.detect_profile(self.project), 'python')

    def test_c_profile_needs_a_makefile_and_a_c_file_outside_hidden_folders(self):
        self.assertEqual(workspace.detect_profile(self.project), 'docs')
        (self.project / 'Makefile').touch()
        (self.project / '.workspace/worktrees/x').mkdir(parents=True)
        (self.project / '.workspace/worktrees/x/main.c').touch()
        self.assertEqual(workspace.detect_profile(self.project), 'docs')
        (self.project / 'src').mkdir()
        (self.project / 'src/main.c').touch()
        self.assertEqual(workspace.detect_profile(self.project), 'c')
        (self.project / 'Makefile').unlink()
        self.assertEqual(workspace.detect_profile(self.project), 'docs')

    def test_gitignore_append_without_duplicates(self):
        path = self.project / '.gitignore'
        path.write_text('custom\n.workspace/')
        self.install()
        self.assertEqual(path.read_text(), 'custom\n.workspace/\n__pycache__/\n')
        self.install()
        self.assertEqual(path.read_text().count('.workspace/'), 1)
        self.assertEqual(path.read_text().count('__pycache__/'), 1)

    def test_gitattributes_keeps_scripts_lf_without_duplicates(self):
        path = self.project / '.gitattributes'
        path.write_text('*.png binary\n*.sh text eol=lf')
        self.install()
        text = path.read_text()
        self.assertTrue(text.startswith('*.png binary\n*.sh text eol=lf\n'))
        self.assertEqual(text.count('*.sh text eol=lf'), 1)
        self.assertIn('*.py text eol=lf\n', text)
        self.install()
        self.assertEqual(path.read_text(), text)

    def test_refuse_non_repository_kit_and_nested_kit_directory(self):
        with self.assertRaisesRegex(ValueError, 'kit'):
            workspace.install_tooling(ROOT)
        nested = self.project / 'nested'
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, 'Git'):
            workspace.install_tooling(nested)
        shutil.rmtree(self.project / '.git')
        with self.assertRaisesRegex(ValueError, 'Git'):
            self.install()

    def test_symlink_parent_cannot_write_outside_project(self):
        (self.project / 'scripts').symlink_to(ROOT / 'template/scripts', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symbolique'):
            self.install(force_circuit=True)
