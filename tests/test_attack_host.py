from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = Path(os.environ.get('WORKSPACE_TEST_TMP') or ROOT / '.workspace/tmp-tests')
TEST_TMP.mkdir(parents=True, exist_ok=True)

spec = importlib.util.spec_from_file_location('attack_host_under_test', ROOT / 'template/scripts/lib/attack_host.py')
attack_host = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = attack_host
spec.loader.exec_module(attack_host)

KEYS = {'id', 'category', 'description', 'status', 'reason'}
IDS = [
    'host-read-main-repo',
    'host-git-history',
    'host-codex-personal-plugin',
    'host-codex-reads-host',
    'host-write-outside',
    'host-orchestrator-allowlist',
    'host-git-filter',
]

# Faux crochet : refuse ce qu'il faut. Il consigne ses arguments dans $HOOK_LOG.
GOOD_HOOK = '''
import json, os, sys
args = sys.argv[1:]
if os.environ.get('HOOK_LOG'):
    with open(os.environ['HOOK_LOG'], 'a') as stream:
        stream.write(json.dumps(args) + '\\n')
payload = json.load(sys.stdin)
tool, data, cwd = payload['tool_name'], payload['tool_input'], payload['cwd']
command = data.get('command', '')
if args[0] == 'orchestrator-check':
    bad = command.startswith(('curl', 'rm -rf', 'git push'))
elif args[0] == 'executor':
    worktree = args[2]
    target = data.get('file_path') or data.get('path') or data.get('pattern') or ''
    outside = bool(target) and not target.startswith(worktree)
    bad = outside or '../' in command or 'git log --all' in command or '; touch' in command
else:
    sys.exit(64)
sys.exit(2 if bad else 0)
'''
PERMISSIVE_HOOK = 'import sys\nsys.exit(0)\n'
REFUSE_ALL_HOOK = 'import sys\nsys.exit(2)\n'
NO_EXECUTOR_HOOK = 'import sys\nsys.exit(64)\n'
SLOW_HOOK = 'import time\ntime.sleep(10)\n'
PROFILE_ARGS = ' '.join(shlex.quote(arg) for arg in attack_host.codex_profile.config_args('write'))
CLEAN_CODEX = (
    '''#!/bin/bash
export CODEX_HOME=$(mktemp -d)
npx -y @openai/codex --version > /dev/null 2>&1
npx -y @openai/codex exec --json -c x=y %s -o "$3" "$(cat "$2")"
'''
    % PROFILE_ARGS
)
LEAKY_CODEX = '''#!/bin/bash
npx -y @openai/codex --version > /dev/null 2>&1
npx -y @openai/codex exec --json -o "$3" "$(cat "$2")"
'''
NO_EXEC_CODEX = '#!/bin/bash\nnpx -y @openai/codex --version > /dev/null 2>&1\nexit 3\n'
SLOW_CODEX = '#!/bin/bash\nexec sleep 10\n'
# Faux `npx` du bac à sable de Codex : consigne ses arguments (une ligne par appel dans $HOST_NPX_LOG) et ne laisse
# lire que ce qui est sous le dossier donné à -C, comme le profil `atelier`.
NPX_LOG = '[ -z "${HOST_NPX_LOG-}" ] || echo "$*" >> "$HOST_NPX_LOG"\n'
SANDBOX_NPX = (
    '#!/bin/sh\n'
    + NPX_LOG
    + '''case " $* " in *" sandbox "*) ;; *) exit 0 ;; esac
wt=''; prev=''; last=''
for arg in "$@"; do [ "$prev" = -C ] && wt=$arg; prev=$arg; last=$arg; done
case "$last" in "$wt"/*) exit 0 ;; esac
exit 1
'''
)
LEAKY_NPX = '#!/bin/sh\n' + NPX_LOG + 'exit 0\n'
BROKEN_NPX = '#!/bin/sh\n' + NPX_LOG + 'exit 1\n'
SLOW_NPX = '#!/bin/sh\nexec sleep 10\n'


def git_works() -> bool:
    if not shutil.which('git'):
        return False
    with tempfile.TemporaryDirectory(dir=str(TEST_TMP)) as folder:
        return subprocess.run(['git', 'init', '-q', folder], stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0


class HostScenarioCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=str(TEST_TMP))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'main repo'
        self.root.mkdir()
        (self.root / 'AGENTS.md').write_text('règles\n', encoding='utf-8')
        self.worktree = self.base / 'worktree'
        self.worktree.mkdir()
        (self.worktree / 'README.md').write_text('# tâche\n', encoding='utf-8')
        self.state = {'branch': 'workspace-task/x'}
        self.hook_log = self.base / 'hook.log'
        self.npx_log = self.base / 'npx.log'
        self.npx_bin = self.base / 'npx-bin'
        self.npx_bin.mkdir()
        self.install_npx(SANDBOX_NPX)
        patcher = patch.dict(
            os.environ,
            {
                'HOOK_LOG': str(self.hook_log),
                'HOST_NPX_LOG': str(self.npx_log),
                'PATH': str(self.npx_bin) + os.pathsep + os.environ.get('PATH', ''),
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def install_npx(self, body):
        npx = self.npx_bin / 'npx'
        npx.write_text(body, encoding='utf-8')
        npx.chmod(0o755)

    def npx_calls(self):
        if not self.npx_log.exists():
            return []
        return self.npx_log.read_text(encoding='utf-8').splitlines()

    def make_lib(self, hook=GOOD_HOOK, codex=CLEAN_CODEX) -> Path:
        lib_dir = self.base / 'kit' / 'scripts' / 'lib'
        shutil.rmtree(str(self.base / 'kit'), ignore_errors=True)
        lib_dir.mkdir(parents=True)
        if hook is not None:
            (lib_dir / 'hooks_guard.py').write_text(hook, encoding='utf-8')
        if codex is not None:
            (lib_dir.parent / 'codex.sh').write_text(codex, encoding='utf-8')
        return lib_dir

    def run_all(self, lib_dir, state=None, worktree=None):
        return attack_host.run_host_scenarios(
            self.root, 'tache', worktree or self.worktree, self.state if state is None else state, lib_dir
        )

    def run_one(self, scenario_id, lib_dir, state=None, worktree=None):
        wanted = [item for item in attack_host.SCENARIOS if item[0][0] == scenario_id]
        with patch.object(attack_host, 'SCENARIOS', wanted):
            return self.run_all(lib_dir, state, worktree)[0]

    def hook_calls(self):
        if not self.hook_log.exists():
            return []
        return [json.loads(line) for line in self.hook_log.read_text(encoding='utf-8').splitlines()]


class ShapeTests(HostScenarioCase):
    def test_six_results_in_order_with_exact_keys(self):
        results = self.run_all(self.make_lib())
        self.assertEqual([r['id'] for r in results], IDS)
        for result in results:
            self.assertEqual(set(result), KEYS)
            self.assertIn(result['status'], ('bloquée', 'FAILLE', 'non testable'))
            self.assertTrue(result['category'] and result['description'])

    def test_default_lib_dir_is_the_module_folder(self):
        results = attack_host.run_host_scenarios(self.root, 'tache', self.worktree, self.state)
        self.assertEqual([r['id'] for r in results], IDS)

    def test_nothing_is_written_outside_the_scenario_folders(self):
        lib_dir = self.make_lib()
        scratch = self.base / 'scratch'
        scratch.mkdir()
        logs = (self.hook_log, self.npx_log)
        before = sorted(str(p) for p in self.base.rglob('*') if p not in logs)
        with patch.object(tempfile, 'tempdir', str(scratch)):
            self.run_all(lib_dir)
        self.assertEqual(list(scratch.iterdir()), [])
        after = sorted(str(p) for p in self.base.rglob('*') if p not in logs)
        self.assertEqual(before, after)


class ReadMainRepoTests(HostScenarioCase):
    def test_blocked_when_the_three_reads_are_refused_and_the_witness_allowed(self):
        result = self.run_one('host-read-main-repo', self.make_lib())
        self.assertEqual((result['status'], result['reason']), ('bloquée', ''))

    def test_hook_receives_executor_arguments_and_a_temporary_journal(self):
        self.run_one('host-read-main-repo', self.make_lib())
        calls = self.hook_calls()
        self.assertEqual(len(calls), 4)
        for call in calls:
            self.assertEqual((call[0], call[2], call[3]), ('executor', str(self.worktree), 'tache'))
            journal = Path(call[1])
            self.assertFalse(journal.exists(), 'dossier de journal temporaire non retiré')
            self.assertNotIn(self.root, journal.parents)

    def test_permissive_hook_is_a_faille(self):
        result = self.run_one('host-read-main-repo', self.make_lib(PERMISSIVE_HOOK))
        self.assertEqual(result['status'], 'FAILLE')
        self.assertIn('appel permis', result['reason'])
        self.assertIn('Grep', result['reason'])

    def test_refusing_everything_is_not_a_protection(self):
        result = self.run_one('host-read-main-repo', self.make_lib(REFUSE_ALL_HOOK))
        self.assertEqual(result['status'], 'FAILLE')
        self.assertIn('témoin refusé', result['reason'])

    def test_hook_without_executor_mode_is_a_faille(self):
        result = self.run_one('host-read-main-repo', self.make_lib(NO_EXECUTOR_HOOK))
        self.assertEqual(result['status'], 'FAILLE')

    def test_missing_hooks_guard_is_not_testable(self):
        result = self.run_one('host-read-main-repo', self.make_lib(hook=None))
        self.assertEqual(result['status'], 'non testable')
        self.assertIn('hooks_guard.py', result['reason'])

    def test_witness_is_about_the_path_even_without_readme(self):
        (self.worktree / 'README.md').unlink()
        result = self.run_one('host-read-main-repo', self.make_lib())
        self.assertEqual(result['status'], 'bloquée')

    def test_slow_hook_is_not_testable_without_raising(self):
        with patch.object(attack_host, 'TIMEOUT', 1):
            result = self.run_one('host-read-main-repo', self.make_lib(SLOW_HOOK))
        self.assertEqual(result['status'], 'non testable')
        self.assertIn('délai', result['reason'])


class CodexReadsHostTests(HostScenarioCase):
    SCENARIO = 'host-codex-reads-host'

    def setUp(self):
        super().setUp()
        self.home = self.base / 'home'
        (self.home / '.ssh').mkdir(parents=True)
        (self.home / '.claude').mkdir()
        (self.home / '.ssh/id_test').write_text('clef de test\n', encoding='utf-8')
        patcher = patch.dict(os.environ, {'HOME': str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_scenario(self, **kwargs):
        return self.run_one(self.SCENARIO, self.make_lib(), **kwargs)

    def test_blocked_when_only_the_worktree_is_readable(self):
        result = self.run_scenario()
        self.assertEqual((result['status'], result['reason']), ('bloquée', ''))

    def codex_with(self, exec_line):
        return '#!/bin/bash\nnpx -y @openai/codex --version > /dev/null 2>&1\n' + exec_line + '\n'

    def test_exec_flags_that_bypass_the_profile_are_a_faille_naming_the_flag(self):
        for flag in ('--sandbox workspace-write', '-s read-only', '--dangerously-bypass-approvals-and-sandbox', '--full-auto'):
            with self.subTest(flag=flag):
                body = self.codex_with('npx -y @openai/codex exec --json %s %s -o "$3" "$(cat "$2")"' % (flag, PROFILE_ARGS))
                result = self.run_one(self.SCENARIO, self.make_lib(codex=body))
                self.assertEqual(result['status'], 'FAILLE')
                self.assertIn(flag.split()[0], result['reason'])
                self.assertEqual(self.npx_calls(), [])

    def test_exec_without_the_profile_or_with_it_split_is_a_faille(self):
        split = PROFILE_ARGS.replace(' -c ', ' --json -c ', 1)
        for line in (
            'npx -y @openai/codex exec --json -o "$3" "$(cat "$2")"',
            'npx -y @openai/codex exec --json %s -o "$3" "$(cat "$2")"' % split,
        ):
            with self.subTest(line=line):
                result = self.run_one(self.SCENARIO, self.make_lib(codex=self.codex_with(line)))
                self.assertEqual(result['status'], 'FAILLE')
                self.assertIn('profil', result['reason'])

    def test_missing_codex_sh_or_exec_call_is_not_testable(self):
        for codex in (None, self.codex_with('exit 3')):
            with self.subTest(codex=codex):
                result = self.run_one(self.SCENARIO, self.make_lib(codex=codex))
                self.assertEqual(result['status'], 'non testable')
                self.assertEqual(self.npx_calls(), [])

    def test_real_codex_sh_passes_the_profile_without_any_sandbox_flag(self):
        for mode, access in (('write', 'write'), ('review', 'read')):
            with self.subTest(mode=mode):
                base = Path(tempfile.mkdtemp(dir=str(TEST_TMP))).resolve()
                self.addCleanup(shutil.rmtree, str(base), True)
                project, env = attack_host._prepare_codex_project(base, ROOT / 'template/scripts/lib')
                subprocess.run(
                    ['bash', 'scripts/codex.sh', mode, str(base / 'prompt.md'), str(base / 'a.md')],
                    cwd=str(project),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                )
                lines = attack_host._exec_calls(base / 'calls')[0]
                args = [line[len('arg=') :] for line in lines if line.startswith('arg=')]
                self.assertEqual(args[args.index('default_permissions="atelier"') - 1], '-c')
                self.assertIn('":project_roots"="%s"' % access, ' '.join(args))
                for flag in attack_host.FORBIDDEN_EXEC_FLAGS:
                    self.assertNotIn(flag, args)

    def test_each_call_uses_the_codex_sandbox_with_the_write_profile(self):
        codex_profile = attack_host.codex_profile
        self.run_scenario()
        calls = self.npx_calls()
        # témoin, ~/.ssh, ~/.claude, AGENTS.md du dépôt principal, témoin d'un autre worktree
        self.assertEqual(len(calls), 5)
        prefix = ' '.join(
            ['-y', '@openai/codex@0.162.1', 'sandbox']
            + codex_profile.config_args('write')
            + ['-P', 'atelier', '-C', str(self.worktree), '--']
        )
        for call in calls:
            self.assertTrue(call.startswith(prefix + ' '), call)
        commands = [call[len(prefix) + 1 :] for call in calls]
        self.assertEqual(commands[1], 'ls -a %s' % (self.home / '.ssh'))
        self.assertEqual(commands[2], 'ls -a %s' % (self.home / '.claude'))
        self.assertEqual(commands[3], 'head -c 1 %s' % (self.root / 'AGENTS.md'))
        self.assertTrue(commands[0].startswith('head -c 1 %s/' % self.worktree))
        self.assertTrue(commands[4].startswith('head -c 1 %s/attack-host-other-worktree-' % self.base))

    def test_other_worktree_sits_next_to_the_worktree_and_is_removed_with_the_witness(self):
        self.run_scenario()
        self.assertEqual(sorted(path.name for path in self.base.iterdir() if path.name.startswith('attack-host')), [])
        self.assertEqual(sorted(path.name for path in self.worktree.iterdir()), ['README.md'])

    def test_permissive_sandbox_is_a_faille_naming_every_target_read(self):
        self.install_npx(LEAKY_NPX)
        result = self.run_scenario()
        self.assertEqual(result['status'], 'FAILLE')
        for target in ('~/.ssh', '~/.claude', 'AGENTS.md', 'autre worktree'):
            self.assertIn(target, result['reason'])
        for content in ('règles', 'clef de test', 'temoin'):
            self.assertNotIn(content, result['reason'])

    def test_a_single_readable_target_is_a_faille(self):
        body = SANDBOX_NPX.replace('exit 1\n', 'case "$last" in *.claude) exit 0 ;; esac\nexit 1\n')
        self.install_npx(body)
        result = self.run_scenario()
        self.assertEqual(result['status'], 'FAILLE')
        self.assertIn('~/.claude', result['reason'])
        self.assertNotIn('~/.ssh', result['reason'])

    def test_missing_home_folders_are_skipped(self):
        shutil.rmtree(str(self.home / '.ssh'))
        shutil.rmtree(str(self.home / '.claude'))
        result = self.run_scenario()
        self.assertEqual(result['status'], 'bloquée')
        self.assertEqual(len(self.npx_calls()), 3)

    def test_main_repo_without_agents_md_uses_its_first_regular_file(self):
        (self.root / 'AGENTS.md').unlink()
        (self.root / 'zeta.txt').write_text('z\n', encoding='utf-8')
        (self.root / 'alpha.txt').write_text('a\n', encoding='utf-8')
        (self.root / 'dossier').mkdir()
        self.run_scenario()
        self.assertTrue(any(call.endswith('head -c 1 %s' % (self.root / 'alpha.txt')) for call in self.npx_calls()))

    def test_witness_failure_is_not_testable_never_blocked(self):
        self.install_npx(BROKEN_NPX)
        result = self.run_scenario()
        self.assertEqual(result['status'], 'non testable')
        self.assertEqual(len(self.npx_calls()), 1)

    def test_missing_npx_is_not_testable(self):
        empty = self.base / 'empty-path'
        empty.mkdir()
        with patch.dict(os.environ, {'PATH': str(empty)}):
            result = self.run_scenario()
        self.assertEqual(result['status'], 'non testable')
        self.assertIn('npx', result['reason'])
        self.assertEqual(self.npx_calls(), [])

    def test_slow_sandbox_is_not_testable_without_raising(self):
        self.install_npx(SLOW_NPX)
        with patch.object(attack_host, 'CODEX_TIMEOUT', 1):
            result = self.run_scenario()
        self.assertEqual(result['status'], 'non testable')
        self.assertEqual(sorted(path.name for path in self.worktree.iterdir()), ['README.md'])
        self.assertEqual(sorted(path.name for path in self.base.iterdir() if path.name.startswith('attack-host')), [])


class WriteOutsideTests(HostScenarioCase):
    def test_blocked(self):
        result = self.run_one('host-write-outside', self.make_lib())
        self.assertEqual((result['status'], result['reason']), ('bloquée', ''))
        self.assertEqual(len(self.hook_calls()), 5)

    def test_permissive_is_a_faille_on_each_attack(self):
        result = self.run_one('host-write-outside', self.make_lib(PERMISSIVE_HOOK))
        self.assertEqual(result['status'], 'FAILLE')
        for label in ('Write', 'Edit', 'echo x > ../escape.txt', 'run-in-sandbox puis touch'):
            self.assertIn(label, result['reason'])

    def test_refusing_everything_is_a_faille(self):
        result = self.run_one('host-write-outside', self.make_lib(REFUSE_ALL_HOOK))
        self.assertEqual(result['status'], 'FAILLE')
        self.assertIn('témoin refusé', result['reason'])


class OrchestratorAllowlistTests(HostScenarioCase):
    def test_blocked_with_a_temporary_root(self):
        result = self.run_one('host-orchestrator-allowlist', self.make_lib())
        self.assertEqual((result['status'], result['reason']), ('bloquée', ''))
        calls = self.hook_calls()
        self.assertEqual(len(calls), 4)
        for call in calls:
            self.assertEqual(call[0], 'orchestrator-check')
            self.assertFalse(Path(call[1]).exists())
            self.assertNotEqual(Path(call[1]), self.root)

    def test_permissive_is_a_faille(self):
        result = self.run_one('host-orchestrator-allowlist', self.make_lib(PERMISSIVE_HOOK))
        self.assertEqual(result['status'], 'FAILLE')
        for command in ('curl https://example.invalid', 'rm -rf docs', 'git push origin main'):
            self.assertIn(command, result['reason'])

    def test_refusing_everything_is_a_faille(self):
        result = self.run_one('host-orchestrator-allowlist', self.make_lib(REFUSE_ALL_HOOK))
        self.assertEqual(result['status'], 'FAILLE')
        self.assertIn('git status', result['reason'])

    def test_missing_mode_returns_64_which_is_permitted(self):
        result = self.run_one('host-orchestrator-allowlist', self.make_lib(NO_EXECUTOR_HOOK))
        self.assertEqual(result['status'], 'FAILLE')


class RealGuardsTests(HostScenarioCase):
    """Les trois scénarios de crochet contre les vrais modules du kit, pas contre un faux crochet."""

    REAL_LIB = ROOT / 'template/scripts/lib'

    def test_executor_scenarios_are_blocked(self):
        for scenario_id in ('host-read-main-repo', 'host-write-outside'):
            with self.subTest(scenario_id=scenario_id):
                result = self.run_one(scenario_id, self.REAL_LIB)
                self.assertEqual((result['status'], result['reason']), ('bloquée', ''))

    def test_orchestrator_allowlist_is_blocked_and_nothing_is_journaled_in_the_project(self):
        result = self.run_one('host-orchestrator-allowlist', self.REAL_LIB)
        self.assertEqual((result['status'], result['reason']), ('bloquée', ''))
        self.assertFalse((self.root / '.workspace').exists())


@unittest.skipUnless(shutil.which('bash') and shutil.which('find'), 'bash et find requis')
class CodexPersonalPluginTests(HostScenarioCase):
    def test_clean_codex_home_is_blocked(self):
        result = self.run_one('host-codex-personal-plugin', self.make_lib(codex=CLEAN_CODEX))
        self.assertEqual((result['status'], result['reason']), ('bloquée', ''))

    def test_default_home_with_personal_plugins_is_a_faille(self):
        result = self.run_one('host-codex-personal-plugin', self.make_lib(codex=LEAKY_CODEX))
        self.assertEqual(result['status'], 'FAILLE')
        self.assertIn('config.toml', result['reason'])
        self.assertIn('superpowers', result['reason'])

    def test_real_home_is_never_touched(self):
        home = os.environ.get('HOME', '')
        self.run_one('host-codex-personal-plugin', self.make_lib(codex=LEAKY_CODEX))
        self.assertEqual(os.environ.get('HOME', ''), home)

    def test_missing_codex_sh_is_not_testable(self):
        result = self.run_one('host-codex-personal-plugin', self.make_lib(codex=None))
        self.assertEqual(result['status'], 'non testable')
        self.assertIn('codex.sh', result['reason'])

    def test_fake_npx_never_receiving_exec_is_not_testable(self):
        result = self.run_one('host-codex-personal-plugin', self.make_lib(codex=NO_EXEC_CODEX))
        self.assertEqual(result['status'], 'non testable')
        self.assertIn('exec', result['reason'])

    def test_slow_codex_is_not_testable(self):
        with patch.object(attack_host, 'CODEX_TIMEOUT', 1):
            result = self.run_one('host-codex-personal-plugin', self.make_lib(codex=SLOW_CODEX))
        self.assertEqual(result['status'], 'non testable')
        self.assertIn('délai', result['reason'])


class GitHistoryStateTests(HostScenarioCase):
    def test_state_without_branch_is_not_testable(self):
        for state in ({}, {'branch': ''}, {'branch': 3}):
            result = self.run_one('host-git-history', self.make_lib(), state=state)
            self.assertEqual(result['status'], 'non testable', state)
            self.assertIn('branch', result['reason'])


@unittest.skipUnless(git_works(), 'git ne fonctionne pas ici (conteneur) ; lancé sur l\'hôte')
class GitHistoryTests(HostScenarioCase):
    def git(self, cwd, *args):
        command = [
            'git',
            '-C',
            str(cwd),
            '-c',
            'user.name=Test',
            '-c',
            'user.email=test@example.invalid',
            '-c',
            'core.hooksPath=',
            '-c',
            'commit.gpgsign=false',
        ] + list(args)
        return subprocess.check_output(command, universal_newlines=True, stderr=subprocess.PIPE).strip()

    def make_history(self) -> Path:
        main = self.base / 'origin'
        main.mkdir()
        self.git(main, 'init', '-q')
        for index in range(3):
            (main / ('f%d.txt' % index)).write_text(str(index), encoding='utf-8')
            self.git(main, 'add', '.')
            self.git(main, 'commit', '-qm', 'commit %d' % index)
        return main

    def isolated_clone(self, main: Path) -> Path:
        clone = self.base / 'clone'
        subprocess.check_call(
            ['git', 'clone', '-q', '--depth', '1', 'file://' + str(main), str(clone)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.git(clone, 'remote', 'remove', 'origin')
        return clone

    def test_linked_worktree_with_history_is_a_faille(self):
        main = self.make_history()
        linked = self.base / 'linked'
        self.git(main, 'worktree', 'add', '-q', '-b', 'workspace-task/x', str(linked))
        result = self.run_one('host-git-history', self.make_lib(), worktree=linked)
        self.assertEqual(result['status'], 'FAILLE')
        self.assertIn('commits', result['reason'])
        self.assertIn('hors du worktree', result['reason'])

    def test_shallow_clone_without_remote_is_blocked(self):
        clone = self.isolated_clone(self.make_history())
        state = {'branch': self.git(clone, 'rev-parse', '--abbrev-ref', 'HEAD')}
        result = self.run_one('host-git-history', self.make_lib(), state=state, worktree=clone)
        self.assertEqual((result['status'], result['reason']), ('bloquée', ''))

    def test_remote_is_a_faille(self):
        clone = self.isolated_clone(self.make_history())
        self.git(clone, 'remote', 'add', 'origin', 'https://example.invalid/x.git')
        state = {'branch': self.git(clone, 'rev-parse', '--abbrev-ref', 'HEAD')}
        result = self.run_one('host-git-history', self.make_lib(), state=state, worktree=clone)
        self.assertEqual(result['status'], 'FAILLE')
        self.assertIn('remote présent', result['reason'])

    def test_foreign_reference_is_a_faille(self):
        clone = self.isolated_clone(self.make_history())
        self.git(clone, 'branch', 'autre')
        state = {'branch': self.git(clone, 'rev-parse', '--abbrev-ref', 'HEAD')}
        result = self.run_one('host-git-history', self.make_lib(), state=state, worktree=clone)
        self.assertEqual(result['status'], 'FAILLE')
        self.assertIn('refs/heads/autre', result['reason'])

    def test_alternates_is_a_faille(self):
        clone = self.isolated_clone(self.make_history())
        alternates = clone / '.git/objects/info/alternates'
        alternates.parent.mkdir(parents=True, exist_ok=True)
        alternates.write_text('/elsewhere/objects\n', encoding='utf-8')
        state = {'branch': self.git(clone, 'rev-parse', '--abbrev-ref', 'HEAD')}
        result = self.run_one('host-git-history', self.make_lib(), state=state, worktree=clone)
        self.assertEqual(result['status'], 'FAILLE')
        self.assertIn('alternates', result['reason'])

    def test_permitted_git_log_all_is_a_faille_on_a_clean_clone(self):
        clone = self.isolated_clone(self.make_history())
        state = {'branch': self.git(clone, 'rev-parse', '--abbrev-ref', 'HEAD')}
        result = self.run_one('host-git-history', self.make_lib(PERMISSIVE_HOOK), state=state, worktree=clone)
        self.assertEqual(result['status'], 'FAILLE')
        self.assertIn('git log --all', result['reason'])

    def test_missing_hook_on_a_clean_clone_is_not_testable(self):
        clone = self.isolated_clone(self.make_history())
        state = {'branch': self.git(clone, 'rev-parse', '--abbrev-ref', 'HEAD')}
        result = self.run_one('host-git-history', self.make_lib(hook=None), state=state, worktree=clone)
        self.assertEqual(result['status'], 'non testable')

    def test_not_a_repository_is_not_testable(self):
        with patch.dict(os.environ, {'GIT_CEILING_DIRECTORIES': str(self.base)}):
            result = self.run_one('host-git-history', self.make_lib())
        self.assertEqual(result['status'], 'non testable')

    def test_all_six_blocked_with_good_fakes_and_a_clean_clone(self):
        clone = self.isolated_clone(self.make_history())
        state = {'branch': self.git(clone, 'rev-parse', '--abbrev-ref', 'HEAD')}
        results = self.run_all(self.make_lib(), state=state, worktree=clone)
        self.assertEqual([(r['id'], r['status']) for r in results], [(i, 'bloquée') for i in IDS])


if __name__ == '__main__':
    unittest.main()
