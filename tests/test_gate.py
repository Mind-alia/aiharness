import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'node requis')
class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project with spaces'
        self.scripts = self.root / 'scripts'
        self.scripts.mkdir(parents=True)
        for name in ('gate.sh', 'verify-task.sh', 'check-secret-tracing.sh'):
            source = ROOT / 'template/scripts' / name
            if source.exists():
                shutil.copy2(source, self.scripts / name)
        (self.root / 'scripts/lib').mkdir(exist_ok=True)
        for module in ('output.py', 'secrets.py'):
            shutil.copy2(ROOT / 'template/scripts/lib' / module, self.root / 'scripts/lib' / module)
        self.bin = Path(self.temp.name) / 'bin'
        self.bin.mkdir()
        (self.bin / 'python3').symlink_to(shutil.which('python3'))
        (self.bin / 'node').symlink_to(shutil.which('node'))
        (self.bin / 'true').symlink_to('/usr/bin/true')
        self.log = self.root / 'calls'
        self.env = dict(os.environ, PATH=str(self.bin), CALLS=str(self.log))
        self.fake('npm')
        make = self.bin / 'make'
        # Use real make: existing files satisfy undeclared goals, even with -n.
        real_make = shutil.which('make')
        make.write_text("#!/bin/sh\necho \"make $*\" >> \"$CALLS\"\nexec " + str(real_make) + ' "$@"\n')
        make.chmod(0o755)
        self.fake('pytest')
        self.fake('ruff')
        (self.scripts / 'docs-check.sh').write_text('#!/bin/sh\necho docs >> "$CALLS"\n')
        (self.root / 'task.md').write_text('---\nid: demo\n---\nverify: exit 99\n')

    def fake(self, name, code=0):
        target = self.bin / name
        target.write_text('#!/bin/sh\necho "' + name + ' $*" >> "$CALLS"\nexit ' + str(code) + '\n')
        target.chmod(0o755)

    def profile(self, profile):
        (self.root / 'project.profile').write_text(profile + '\n')
        (self.root / 'package.json').write_text(json.dumps({'scripts': {key: 'fake' for key in ('typecheck', 'lint', 'test', 'e2e')}}))
        (self.root / 'Makefile').write_text('.PHONY: check test\ncheck:\n\t@true\ntest:\n\t@true\n')

    def run_script(self, name='gate.sh', *args):
        return subprocess.run(['/bin/sh', str(self.scripts / name), *args], cwd=self.bin, env=self.env, capture_output=True, text=True)

    def test_fix4_direct_commands_mask_both_streams_and_preserve_code(self):
        self.profile('docs')
        (self.root / '.env').write_text('PASSWORD=fakecredential42\n')
        command = 'echo fakecredential42; echo fakecredential42 >&2; exit 17'
        (self.root / 'task.md').write_text('---\nverify: ' + command + '\n---\n')
        (self.scripts / 'docs-check.sh').write_text('#!/bin/sh\n' + command + '\n')
        for name, args, code in [('verify-task.sh', [str(self.root / 'task.md')], 17), ('gate.sh', ['--quick'], 17)]:
            with self.subTest(script=name):
                result = self.run_script(name, *args)
                self.assertEqual(result.returncode, code)
                self.assertFalse('fakecredential42' in result.stdout + result.stderr, 'unmasked fake secret')
                self.assertIn('***', result.stdout + result.stderr)

    def test_fix4_direct_large_streams_are_bounded_and_masked(self):
        self.profile('docs')
        (self.root / '.env').write_text('PASSWORD=fakecredential42\n')
        producer = self.root / 'produce.py'
        producer.write_text(
            "import sys\nfor stream in (sys.stdout, sys.stderr):\n stream.write('fakecredential42\\n' + 'x' * (3 * 1024 * 1024))\nsys.exit(19)\n"
        )
        command = 'python3 produce.py'
        (self.root / 'task.md').write_text('---\nverify: ' + command + '\n---\n')
        (self.scripts / 'docs-check.sh').write_text('#!/bin/sh\n' + command + '\n')
        for name, args in [('verify-task.sh', [str(self.root / 'task.md')]), ('gate.sh', ['--quick'])]:
            with self.subTest(script=name):
                result = self.run_script(name, *args)
                self.assertEqual(result.returncode, 19)
                self.assertFalse('fakecredential42' in result.stdout + result.stderr, 'unmasked fake secret')
                self.assertIn('[TRUNCATED]', result.stdout + result.stderr)
                self.assertLess(len(result.stdout + result.stderr), 4 * 1024 * 1024 + 1000)

    def test_each_full_profile(self):
        expected = {
            'node': ['npm run typecheck', 'npm run lint', 'npm run test', 'npm run e2e', 'docs'],
            'docker': ['make check', 'make test', 'docs'],
            'python': ['ruff check .', 'pytest ', 'docs'],
            'docs': ['docs'],
        }
        for profile, calls in expected.items():
            with self.subTest(profile=profile):
                self.profile(profile)
                self.log.unlink(missing_ok=True)
                result = self.run_script()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.log.read_text().splitlines(), calls)
                rows = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(len(rows), len(calls) + 1)
                self.assertIn({'secrets': True}, rows)
                self.assertTrue(all(value is True for row in rows for value in row.values()))

    def test_missing_checks_fail(self):
        for profile, missing in [('node', 'package.json'), ('docker', 'Makefile'), ('docs', 'scripts/docs-check.sh'), ('python', None)]:
            with self.subTest(profile=profile):
                self.profile(profile)
                if missing:
                    (self.root / missing).unlink()
                else:
                    (self.bin / 'ruff').unlink()
                result = self.run_script()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('non_configure', result.stdout)

    def test_missing_npm_script_and_make_target_fail(self):
        self.profile('node')
        (self.root / 'package.json').write_text('{"scripts":{"test":"fake"}}')
        self.assertIn('"typecheck": "non_configure"', self.run_script().stdout)
        self.profile('docker')
        (self.root / 'Makefile').write_text('other:\n')
        self.assertNotEqual(self.run_script().returncode, 0)

    def test_docker_requires_declared_phony_controls(self):
        for content in (
            'other:\n',
            '.PHONY: check test\nother:\n',
            'check:\n\t@true\ntest:\n\t@true\n',
            '.PHONY: test\ncheck:\n\t@true\ntest:\n\t@true\n',
        ):
            with self.subTest(makefile=content):
                self.profile('docker')
                (self.root / 'Makefile').write_text(content)
                (self.root / 'check').touch()
                (self.root / 'test').touch()
                self.log.unlink(missing_ok=True)
                result = self.run_script()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('"check": "non_configure"', result.stdout)
                self.assertNotIn('make check', self.log.read_text())

    def test_docker_test_goal_also_requires_declaration_and_phony(self):
        for content in ('.PHONY: check test\ncheck:\n', '.PHONY: check\ncheck:\ntest:\n'):
            with self.subTest(makefile=content):
                self.profile('docker')
                (self.root / 'Makefile').write_text(content)
                (self.root / 'test').touch()
                result = self.run_script()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('"test": "non_configure"', result.stdout)

    def test_docker_accepts_literal_continuations_and_shared_rules(self):
        self.profile('docker')
        (self.root / 'Makefile').write_text('.PHONY: check \\n test # controls\ncheck test: ; @true\n'.replace('\\n', '\\\n'))
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_docker_configuration_never_expands_shell(self):
        self.profile('docker')
        (self.root / 'Makefile').write_text('SIDE := $(shell echo unsafe > expanded)\nother:\n')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'expanded').exists())

    def test_unknown_and_missing_profile_refused(self):
        for profile in ('unknown', ''):
            self.profile(profile)
            self.assertNotEqual(self.run_script().returncode, 0)

    def test_failure_is_reported_and_gate_continues(self):
        self.profile('node')
        self.fake('npm', 7)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('"typecheck": false', result.stdout)
        self.assertIn('docs', self.log.read_text())

    def test_verify_override_runs_at_root_and_preserves_exit(self):
        self.profile('docs')
        task = self.root / 'task.md'
        task.write_text('---\nverify: \'test -f project.profile && exit 17\' # comment\n---\n')
        result = self.run_script('verify-task.sh', str(task))
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertFalse(self.log.exists())

    def test_verify_quick_profiles(self):
        for profile, calls in {'node': ['npm run lint'], 'docker': ['make check'], 'python': ['pytest '], 'docs': ['docs']}.items():
            with self.subTest(profile=profile):
                self.profile(profile)
                self.log.unlink(missing_ok=True)
                result = self.run_script('verify-task.sh', str(self.root / 'task.md'))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.log.read_text().splitlines() if self.log.exists() else [], calls)

    def verify_node(self, header='verify: npm run test', tsconfig=True):
        self.profile('node')
        self.fake('npx', 27)
        if tsconfig:
            (self.root / 'tsconfig.json').write_text('{}')
        shutil.copy2(ROOT / 'template/scripts/lib/task.py', self.scripts / 'lib/task.py')
        task = self.root / 'task.md'
        task.write_text('---\nid: demo\nagent: codex\nallowed: []\n' + header + '\n---\n')
        return self.run_script('verify-task.sh', str(task))

    def test_verify_success_then_types_failure(self):
        result = self.verify_node()
        self.assertEqual(result.returncode, 27, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['npm run test', 'npx tsc -b'])
        self.assertIn('tsc -b', result.stderr)

    def test_verify_profile_off(self):
        result = self.verify_node('verify: npm run test\nverify_profile: off\nverify_lint: on')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['npm run test'])

    def test_verify_without_tsconfig(self):
        result = self.verify_node(tsconfig=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['npm run test', 'npm run lint'])

    def test_verify_absent_runs_types_only(self):
        result = self.verify_node('')
        self.assertEqual(result.returncode, 27, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['npx tsc -b'])

    def test_verify_failure_stops_before_types(self):
        result = self.verify_node('verify: exit 18')
        self.assertEqual(result.returncode, 18, result.stderr)
        self.assertFalse(self.log.exists())

    def test_verify_lint_enabled_by_default_and_can_be_disabled(self):
        for shared_parser in (False, True):
            with self.subTest(shared_parser=shared_parser):
                self.verify_node('verify_profile: off')
                if not shared_parser:
                    (self.scripts / 'lib/task.py').unlink()
                self.log.unlink(missing_ok=True)
                self.fake('npx')
                self.fake('npm', 31)
                task = self.root / 'task.md'
                header = task.read_text().replace('verify_profile: off', '')
                for option in ('', 'verify_lint: on'):
                    with self.subTest(option=option):
                        task.write_text(header.replace('allowed: []', 'allowed: []\n' + option))
                        result = self.run_script('verify-task.sh', str(task))
                        self.assertEqual(result.returncode, 31, result.stderr)
                        self.assertEqual(self.log.read_text().splitlines(), ['npx tsc -b', 'npm run lint'])
                        self.log.unlink()
                task.write_text(header.replace('allowed: []', 'allowed: []\nverify_lint: off'))
                result = self.run_script('verify-task.sh', str(task))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.log.read_text().splitlines(), ['npx tsc -b'])

    def test_verify_missing_root_lint_script_is_skipped(self):
        for shared_parser in (False, True):
            with self.subTest(shared_parser=shared_parser):
                self.verify_node('verify_profile: off', tsconfig=False)
                if not shared_parser:
                    (self.scripts / 'lib/task.py').unlink()
                (self.root / 'package.json').write_text('{"scripts":{"test":"fake"}}')
                nested = self.root / 'nested'
                nested.mkdir(exist_ok=True)
                (nested / 'package.json').write_text('{"scripts":{"lint":"fake"}}')
                self.fake('npm', 31)
                task = self.root / 'task.md'
                task.write_text(task.read_text().replace('verify_profile: off', ''))
                result = self.run_script('verify-task.sh', str(task))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(self.log.exists())
                self.assertIn('lint', result.stderr)
                self.assertIn('sauté', result.stderr)

    def test_quick_failure_preserves_code(self):
        self.profile('python')
        self.fake('pytest', 23)
        self.assertEqual(self.run_script('verify-task.sh', str(self.root / 'task.md')).returncode, 23)

    def test_shared_parser_is_used_when_available(self):
        self.profile('docs')
        lib = self.scripts / 'lib'
        lib.mkdir(exist_ok=True)
        (lib / 'task.py').write_text("def read_task(path):\n    return {'verify': 'exit 19'}\n")
        self.assertEqual(self.run_script('verify-task.sh', str(self.root / 'task.md')).returncode, 19)
        (lib / 'task.py').write_text("def read_task(path):\n    raise ValueError('invalid task')\n")
        self.assertEqual(self.run_script('verify-task.sh', str(self.root / 'task.md')).returncode, 2)

    def test_integration_with_shared_parser(self):
        parser = ROOT / 'template/scripts/lib/task.py'
        if not parser.exists():
            self.skipTest('Shared parser is being implemented separately')
        (self.scripts / 'lib').mkdir(exist_ok=True)
        shutil.copy2(parser, self.scripts / 'lib/task.py')
        self.profile('docs')
        task = self.root / 'task.md'
        task.write_text('---\nid: demo\nagent: codex\nallowed: []\nverify: exit 13 # comment\n---\n')
        self.assertEqual(self.run_script('verify-task.sh', str(task)).returncode, 13)
        task.write_text('---\nid: demo\nagent: codex\nallowed: []\n---\n')
        self.assertEqual(self.run_script('verify-task.sh', str(task)).returncode, 0)

    def test_missing_tools_never_pass(self):
        for profile, tool in [('node', 'npm'), ('docker', 'make'), ('python', 'pytest')]:
            with self.subTest(tool=tool):
                self.profile(profile)
                executable = self.bin / tool
                hidden = self.bin / (tool + '.hidden')
                executable.rename(hidden)
                result = self.run_script()
                hidden.rename(executable)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('non_configure', result.stdout)

    def test_missing_task_refused(self):
        self.assertNotEqual(self.run_script('verify-task.sh', 'absent.md').returncode, 0)

    def test_secrets_control_rejects_tracing(self):
        self.profile('docs')
        (self.root / 'unsafe.sh').write_text('set -x\ncat secrets/password\n')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn({'secrets': False}, [json.loads(line) for line in result.stdout.splitlines()])


class KitGateTests(unittest.TestCase):
    """Kit self-checks; no node needed, so they run in the sandbox too."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'kit'
        self.scripts = self.root / 'scripts'
        self.scripts.mkdir(parents=True)
        for name in ('gate.sh', 'check-secret-tracing.sh'):
            shutil.copy2(ROOT / 'template/scripts' / name, self.scripts / name)
        (self.scripts / 'lib').mkdir()
        for module in ('output.py', 'secrets.py'):
            shutil.copy2(ROOT / 'template/scripts/lib' / module, self.scripts / 'lib' / module)
        self.bin = Path(self.temp.name) / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls'
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'], CALLS=str(self.log))
        self.fake('ruff')
        (self.scripts / 'docs-check.sh').write_text('#!/bin/sh\necho docs >> "$CALLS"\n')
        (self.root / 'project.profile').write_text('python\n')

    def fake(self, name):
        target = self.bin / name
        target.write_text('#!/bin/sh\necho "' + name + ' $*" >> "$CALLS"\n')
        target.chmod(0o755)

    def run_script(self, name, *args):
        return subprocess.run(['/bin/sh', str(self.scripts / name), *args], cwd=self.bin, env=self.env, capture_output=True, text=True)

    def kit_docs(self, agents='docs-check: kit\n'):
        shutil.copy2(ROOT / 'template/scripts/docs-check.sh', self.scripts / 'docs-check.sh')
        (self.root / 'AGENTS.md').write_text(agents)
        (self.root / 'docs').mkdir(exist_ok=True)
        for name in ('LOG.md', 'AMELIORATIONS.md', 'QUESTIONS.md'):
            (self.root / 'docs' / name).write_text('# Doc\n')

    def test_docs_check_kit_marker_passes_with_required_documents(self):
        self.kit_docs()
        result = self.run_script('docs-check.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, 'docs: OK\n')

    def test_docs_check_kit_missing_log_fails(self):
        self.kit_docs()
        (self.root / 'docs/LOG.md').unlink()
        result = self.run_script('docs-check.sh')
        self.assertEqual(result.returncode, 1)
        self.assertIn('DOCS FAIL: docs/LOG.md missing', result.stdout)

    def test_docs_check_kit_long_rules_file_fails(self):
        self.kit_docs('docs-check: kit\n' + 'regle\n' * 80)
        result = self.run_script('docs-check.sh')
        self.assertEqual(result.returncode, 1)
        self.assertIn('DOCS FAIL: AGENTS.md longer than 80 lines', result.stdout)

    def test_python_profile_uses_repo_test_runner_when_present(self):
        (self.root / 'tests').mkdir()
        (self.root / 'tests/run.sh').write_text('echo "$1" >> "$CALLS"\n')
        for quick, calls in ((True, ['fast']), (False, ['ruff check .', 'full', 'docs'])):
            with self.subTest(quick=quick):
                self.log.unlink(missing_ok=True)
                result = self.run_script('gate.sh', *(['--quick'] if quick else []))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.log.read_text().splitlines(), calls)
                rows = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertIn({'unittest': True}, rows)
                self.assertNotIn({'pytest': True}, rows)
