import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / 'template/scripts'
RELAYS = ('codex.sh', 'gemini.sh')
# Tools the relays call before the prerequisite check or in the happy path, reachable through a reduced PATH.
WRAPPED_TOOLS = ('dirname', 'mkdir', 'date', 'mktemp', 'mv', 'cat', 'tail')
BASH = shutil.which('bash')
PYTHON = sys.executable
IS_ROOT = hasattr(os, 'geteuid') and os.geteuid() == 0


def write_executable(path, body):
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class RelayCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / 'project'
        self.bin = Path(self.tmp.name) / 'bin'
        self.calls = Path(self.tmp.name) / 'calls.log'
        self.project.mkdir()
        self.bin.mkdir()
        (self.project / 'scripts/lib').mkdir(parents=True)
        for name in RELAYS:
            shutil.copy(TEMPLATE / name, self.project / 'scripts' / name)
            (self.project / 'scripts' / name).chmod(0o755)
        shutil.copy(TEMPLATE / 'lib/codex_profile.py', self.project / 'scripts/lib/codex_profile.py')
        self.prompt = self.project / 'prompt.md'
        self.prompt.write_text('consigne\n')
        self.agy = self.bin.parent / 'fake-agy'
        self.fake_tool(self.agy)
        self.fake_tool(self.bin / 'npx')
        self.link_tools()

    def fake_tool(self, path):
        write_executable(path, f'echo "{path.name} $*" >> "{self.calls}"\nexit 0')

    def link_tools(self):
        # Wrappers instead of symlinks: the repository must not contain links.
        for name in WRAPPED_TOOLS:
            real = shutil.which(name)
            write_executable(self.bin / name, f'exec {shlex.quote(real)} "$@"')
        write_executable(self.bin / 'python3', f'exec {shlex.quote(PYTHON)} "$@"')

    def remove_tool(self, name):
        (self.bin / name).unlink()

    def run_relay(self, name, *args, env_extra=None, path=None):
        env = {'PATH': path if path is not None else str(self.bin)}
        if name == 'gemini.sh':
            env['AGY_BIN'] = str(self.agy)
        env.update(env_extra or {})
        return subprocess.run(
            [BASH, str(self.project / 'scripts' / name), *args],
            cwd=str(self.project),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )

    def assert_single_line_missing(self, result, name, what):
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stderr, f'{name}: prérequis manquant : {what}\n')
        self.assertEqual(result.stdout, '')
        self.assertFalse((self.project / '.workspace').exists())
        self.assertFalse(self.calls.exists())


class RelayPrereqTests(RelayCase):
    def test_unknown_mode_is_bad_usage_even_without_python(self):
        self.remove_tool('python3')
        for name in RELAYS:
            with self.subTest(relay=name):
                result = self.run_relay(name, 'bogus', str(self.prompt), 'out.md')
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertFalse((self.project / '.workspace').exists())
                self.assertFalse(self.calls.exists())

    def test_missing_python_is_reported_before_anything_runs(self):
        self.remove_tool('python3')
        for name in RELAYS:
            with self.subTest(relay=name):
                result = self.run_relay(name, 'write', str(self.prompt), 'out.md')
                self.assert_single_line_missing(result, name, 'python3')

    def test_missing_python_is_named_first_when_other_prerequisites_are_missing(self):
        self.remove_tool('python3')
        self.remove_tool('npx')
        result = self.run_relay('codex.sh', 'write', str(self.prompt), 'out.md')
        self.assert_single_line_missing(result, 'codex.sh', 'python3')

    def test_missing_npx_is_reported_for_codex(self):
        self.remove_tool('npx')
        result = self.run_relay('codex.sh', 'review', str(self.prompt), 'out.md')
        self.assert_single_line_missing(result, 'codex.sh', 'npx')

    def test_missing_agy_binary_is_reported_with_its_path(self):
        self.agy.unlink()
        result = self.run_relay('gemini.sh', 'review', str(self.prompt), 'out.md')
        self.assert_single_line_missing(result, 'gemini.sh', f'binaire agy introuvable : {self.agy}')

    @unittest.skipIf(IS_ROOT, 'root can read files without the read permission')
    def test_unreadable_prompt_is_reported_as_unavailable_not_bad_usage(self):
        for name in RELAYS:
            with self.subTest(relay=name):
                self.prompt.chmod(0o000)
                self.addCleanup(self.prompt.chmod, 0o644)
                result = self.run_relay(name, 'review', str(self.prompt), 'out.md')
                self.assert_single_line_missing(result, name, f'fichier de consigne illisible : {self.prompt}')

    def test_all_prerequisites_present_reaches_the_relay_binary(self):
        cases = (('codex.sh', 'npx'), ('gemini.sh', 'fake-agy'))
        for name, binary in cases:
            with self.subTest(relay=name):
                if self.calls.exists():
                    self.calls.unlink()
                result = self.run_relay(name, 'review', str(self.prompt), 'out.md')
                self.assertNotEqual(result.returncode, 3, result.stderr)
                self.assertIn(f'{binary} ', self.calls.read_text())


class CodexHomeTests(RelayCase):
    """codex.sh gives Codex a reduced CODEX_HOME holding only the credentials, and no network."""

    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp.name) / 'home'
        self.codex_dir = self.home / '.codex'
        self.codex_dir.mkdir(parents=True)
        (self.codex_dir / 'auth.json').write_text('{"token": "t"}\n')
        for name, content in (('config.toml', 'plugins = true\n'), ('AGENTS.md', 'superpowers\n')):
            (self.codex_dir / name).write_text(content)
        (self.codex_dir / 'skills').mkdir()
        (self.codex_dir / 'skills/s.md').write_text('skill\n')
        self.seen = Path(self.tmp.name) / 'seen.json'
        # The fake npx records, at `exec` time, what Codex would find in its home.
        write_executable(
            self.bin / 'npx',
            (
                'case " $* " in *" exec "*) ;; *) exit 0 ;; esac\n'
                f'{shlex.quote(PYTHON)} - "$@" <<\'PY\'\n'
                'import json, os, sys\n'
                'home = os.environ.get("CODEX_HOME", "")\n'
                'mode = oct(os.stat(home).st_mode & 0o777) if home else ""\n'
                'auth = os.path.join(home, "auth.json")\n'
                f'open({str(self.seen)!r}, "w").write(json.dumps({{"home": home, "mode": mode, "args": sys.argv[1:],\n'
                '    "files": sorted(os.listdir(home)) if home else [],\n'
                '    "auth_mode": oct(os.stat(auth).st_mode & 0o777) if os.path.exists(auth) else None,\n'
                '    "auth": open(auth).read() if os.path.exists(auth) else None}))\n'
                'PY\n'
                'out=""; while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done\n'
                '[ -z "$out" ] || echo done > "$out"'
            ),
        )

    def run_codex(self, **env_extra):
        env = {'HOME': str(self.home), **env_extra}
        return self.run_relay('codex.sh', 'write', str(self.prompt), 'out.md', env_extra=env)

    def test_exec_sees_only_the_credentials_in_a_private_home_under_the_project(self):
        for name in ('cp', 'chmod', 'rm', 'ls'):
            self.assertFalse((self.bin / name).exists())
        result = self.run_codex()
        self.assertEqual(result.returncode, 0, result.stderr)
        seen = json.loads(self.seen.read_text())
        homes = self.project / '.workspace/run/codex-home'
        self.assertEqual(Path(seen['home']).parent, homes)
        self.assertEqual(seen['files'], ['auth.json'])
        self.assertEqual(seen['mode'], '0o700')
        self.assertEqual(seen['auth_mode'], '0o600')
        self.assertEqual(seen['auth'], '{"token": "t"}\n')
        self.assertEqual(sorted(path.name for path in self.codex_dir.iterdir()), ['AGENTS.md', 'auth.json', 'config.toml', 'skills'])

    def test_exec_has_no_network_and_keeps_the_user_config_ignored_and_the_worktree_target(self):
        worktree = self.project / 'wt'
        worktree.mkdir()
        result = self.run_codex(WORKSPACE_AGENT_ROOT=str(worktree))
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(self.seen.read_text())['args']
        self.assertIn('--ignore-user-config', args)
        self.assertEqual(args[args.index('-c') + 1], 'sandbox_workspace_write.network_access=false')
        self.assertEqual(args[args.index('-C') + 1], str(worktree))

    def profile_args(self, mode):
        table = '{":minimal"="read",":project_roots"="%s",":tmpdir"="write"}' % mode
        return ['-c', 'default_permissions="atelier"', '-c', 'permissions.atelier.filesystem=' + table]

    def test_exec_gets_the_permission_profile_right_after_the_network_setting(self):
        for relay_mode, project_access in (('write', 'write'), ('review', 'read')):
            with self.subTest(mode=relay_mode):
                result = self.run_relay('codex.sh', relay_mode, str(self.prompt), 'out.md', env_extra={'HOME': str(self.home)})
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads(self.seen.read_text())['args']
                network = args.index('sandbox_workspace_write.network_access=false')
                self.assertEqual(args[network + 1 : network + 5], self.profile_args(project_access))
                for flag in ('--sandbox', '-s', '--full-auto', '--dangerously-bypass-approvals-and-sandbox'):
                    self.assertNotIn(flag, args)
                self.assertIn('--ignore-user-config', args)

    def test_the_profile_is_read_from_the_folder_next_to_codex_sh_whatever_the_caller_directory(self):
        elsewhere = Path(self.tmp.name) / 'elsewhere'
        elsewhere.mkdir()
        env = {'PATH': str(self.bin), 'HOME': str(self.home)}
        result = subprocess.run(
            [BASH, str(self.project / 'scripts/codex.sh'), 'write', str(self.prompt), str(self.project / 'out.md')],
            cwd=str(elsewhere),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.seen.read_text())['args'].count('default_permissions="atelier"'), 1)

    def test_a_failing_profile_stops_the_relay_before_codex_starts(self):
        self.fake_tool(self.bin / 'npx')
        (self.project / 'scripts/lib/codex_profile.py').write_text('import sys\nsys.exit(1)\n')
        result = self.run_relay('codex.sh', 'write', str(self.prompt), 'out.md', env_extra={'HOME': str(self.home)})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.seen.exists())
        self.assertFalse((self.project / '.workspace/run/codex-home').exists())

    def test_a_missing_profile_stops_the_relay_before_codex_starts(self):
        self.fake_tool(self.bin / 'npx')
        (self.project / 'scripts/lib/codex_profile.py').unlink()
        result = self.run_relay('codex.sh', 'review', str(self.prompt), 'out.md', env_extra={'HOME': str(self.home)})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_the_credentials_copy_is_removed_afterwards_and_each_run_has_its_own_home(self):
        self.assertEqual(self.run_codex().returncode, 0)
        first = Path(json.loads(self.seen.read_text())['home'])
        self.assertEqual(self.run_codex().returncode, 0)
        second = Path(json.loads(self.seen.read_text())['home'])
        self.assertNotEqual(first, second)
        for home in (first, second):
            self.assertTrue(home.is_dir())
            self.assertEqual(list(home.iterdir()), [])

    def test_codex_home_variable_selects_the_source_and_a_missing_source_copies_nothing(self):
        other = Path(self.tmp.name) / 'other'
        other.mkdir()
        (other / 'auth.json').write_text('other\n')
        self.assertEqual(self.run_codex(CODEX_HOME=str(other)).returncode, 0)
        self.assertEqual(json.loads(self.seen.read_text())['auth'], 'other\n')
        (other / 'auth.json').unlink()
        self.assertEqual(self.run_codex(CODEX_HOME=str(other)).returncode, 0)
        seen = json.loads(self.seen.read_text())
        self.assertEqual(seen['files'], [])
        self.assertEqual(sorted(path.name for path in other.iterdir()), [])

    def test_failure_of_exec_still_removes_the_credentials(self):
        write_executable(self.bin / 'npx', 'case " $* " in *" exec "*) exit 1 ;; esac\nexit 0')
        result = self.run_codex()
        self.assertEqual(result.returncode, 2, result.stderr)
        (home,) = (self.project / '.workspace/run/codex-home').iterdir()
        self.assertEqual(list(home.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
