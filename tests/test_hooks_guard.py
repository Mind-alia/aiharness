import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'template/scripts/lib/hooks_guard.py'
SETTINGS = (ROOT / 'template/.claude/settings.json', ROOT / '.claude/settings.json')
BLOCKED = 2
ALLOWED = 0
STRIPPED_ENV = ('WORKSPACE_TASK_ID', 'CLAUDE_PROJECT_DIR')
# Processus placés entre le test et le crochet ; `exit $?` empêche le shell de se remplacer par la commande.
VIA_SHELL = ['sh', '-c', '"$@"; exit $?', 'sh']
VIA_PYTHON = [sys.executable, '-c', 'import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))']


def run_hook(mode, project, payload=None, raw=None, extra_env=None, via=()):  # noqa: PLR0913
    """Lance le crochet en sous-processus ; renvoie (code de sortie, sortie d'erreur)."""
    env = {key: value for key, value in os.environ.items() if key not in STRIPPED_ENV}
    if project is not None:
        env['CLAUDE_PROJECT_DIR'] = str(project)
    env.update(extra_env or {})
    data = raw if raw is not None else json.dumps(payload)
    done = subprocess.run(
        list(via) + [sys.executable, str(SCRIPT), mode],
        input=data,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
    )
    return done.returncode, done.stdout, done.stderr


def dead_pid():
    proc = subprocess.Popen([sys.executable, '-c', 'pass'])
    proc.wait()
    return proc.pid


class HooksGuardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Chemin non résolu (sous macOS, /var est un lien vers /private/var) : le crochet doit le normaliser.
        self.root = Path(tmp.name) / 'project'
        self.root.mkdir()

    def pre_tool(self, tool, tool_input, extra_env=None, via=()):
        payload = {'tool_name': tool, 'tool_input': tool_input, 'cwd': str(self.root)}
        return run_hook('pre-tool', self.root, payload, extra_env=extra_env, via=via)

    def bash(self, command, extra_env=None):
        return self.pre_tool('Bash', {'command': command}, extra_env)

    def stop(self, payload=None, extra_env=None, via=()):
        return run_hook('stop', self.root, payload or {'stop_hook_active': False}, extra_env=extra_env, via=via)

    def hold_lock(self, pid, session='session-test'):
        lock = self.root / '.workspace/orchestrator.json'
        lock.parent.mkdir(exist_ok=True)
        lock.write_text(json.dumps({'session': session, 'model': 'test', 'started_at': '2026-10-19T00:00:00Z', 'pid': pid}))

    def live_non_ancestor(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(self.reap, proc)
        return proc.pid

    def reap(self, proc):
        proc.kill()
        proc.wait()


class EditToolTests(HooksGuardTests):
    def test_write_into_scripts_is_refused_with_one_line_citing_agents(self):
        code, out, err = self.pre_tool('Write', {'file_path': 'scripts/x.sh', 'content': ''})
        self.assertEqual(code, BLOCKED)
        self.assertEqual(out, '')
        self.assertEqual(
            err.splitlines(),
            ['Refusé : scripts/x.sh est un chemin protégé du circuit (scripts/, project.profile, .workspace/) ; voir AGENTS.md.'],
        )

    def test_write_outside_protected_paths_is_silent_and_allowed(self):
        code, out, err = self.pre_tool('Write', {'file_path': 'apps/web/x.ts', 'content': ''})
        self.assertEqual((code, out, err), (ALLOWED, '', ''))

    def test_protected_targets_are_refused_for_every_edit_tool(self):
        cases = [
            ('Edit', {'file_path': 'project.profile'}),
            ('MultiEdit', {'file_path': '.workspace/tasks/a.md'}),
            ('NotebookEdit', {'notebook_path': '.workspace/x.ipynb'}),
            ('Write', {'file_path': 'scripts/lib/a.py'}),
        ]
        for tool, tool_input in cases:
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertEqual(self.pre_tool(tool, tool_input)[0], BLOCKED)

    def test_absolute_path_inside_project_is_normalised_and_refused(self):
        code, _, err = self.pre_tool('Edit', {'file_path': str(self.root / 'scripts/x.sh')})
        self.assertEqual(code, BLOCKED)
        self.assertIn('scripts/x.sh est un chemin protégé', err)

    def test_neighbours_and_outside_paths_are_not_protected(self):
        cases = [
            '/tmp/elsewhere/scripts/x.sh',
            '../scripts/x.sh',
            'apps/scripts/x.sh',
            'scripts-old/x.sh',
            './scripts/../apps/x.ts',
            'project.profile.bak',
        ]
        for path in cases:
            with self.subTest(path=path):
                self.assertEqual(self.pre_tool('Write', {'file_path': path})[0], ALLOWED)

    def test_other_tools_are_allowed_even_on_protected_paths(self):
        self.assertEqual(self.pre_tool('Read', {'file_path': 'scripts/gate.sh'})[0], ALLOWED)


class BashTests(HooksGuardTests):
    def test_redirections_into_protected_paths_are_refused(self):
        for command in (
            'echo x > scripts/a.sh',
            'echo x >> .workspace/log',
            'echo x >| project.profile',
            'echo x &> scripts/a.sh',
            'cmd 2>scripts/err',
            'ls && echo x>>.workspace/l',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], BLOCKED)

    def test_write_commands_on_protected_arguments_are_refused(self):
        for command in (
            'rm -rf .workspace',
            'mv a scripts/b',
            'touch project.profile',
            'chmod +x scripts/x',
            'chown user scripts/x',
            'truncate -s 0 scripts/x',
            'mkdir -p .workspace/x',
            'rmdir scripts/x',
            'unlink scripts/x',
            'tee scripts/x',
            'tee -a scripts/x',
            'FOO=1 rm -rf scripts/x',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], BLOCKED)

    def test_copy_commands_are_refused_only_when_the_last_argument_is_protected(self):
        for command in (
            'cp /tmp/a scripts/lib/a.py',
            'rsync -a src/ scripts/',
            'install -m 644 a scripts/b',
            'ln -s a scripts/b',
            'cp a scripts/x 2>/dev/null',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], BLOCKED)

    def test_reading_or_copying_out_of_scripts_is_allowed(self):
        for command in (
            'cat scripts/gate.sh',
            'cp scripts/gate.sh /tmp/g',
            'cp scripts/gate.sh apps/g',
            'bash scripts/run-in-sandbox.sh -- python3 -m unittest tests.test_x',
            'sed "s/a/b/" scripts/x',
            'dd if=scripts/x of=/tmp/y',
            'echo x > /dev/null',
            'echo "a;b" > apps/x',
            'cat < scripts/x > apps/out',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], ALLOWED)

    def test_in_place_editors_and_dd_are_refused_on_protected_targets(self):
        for command in (
            "sed -i 's/a/b/' scripts/x",
            "perl -pi -e 's/a/b/' .workspace/x",
            "sed -i.bak 's/a/b/' scripts/x",
            "sed --in-place 's/a/b/' scripts/x",
            'dd if=a of=scripts/x',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], BLOCKED)

    def test_every_simple_command_of_a_chain_is_checked(self):
        for command in (
            'ls ; rm -rf .workspace',
            'ls || rm scripts/x',
            'true | tee scripts/x',
            'ls\nrm -rf scripts',
            'cd x; echo ok && touch scripts/y',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], BLOCKED)

    def test_command_prefixes_are_looked_through(self):
        for command in (
            'command cp a.sh scripts/a.sh',
            'builtin command rm scripts/x',
            'exec rm scripts/x',
            'nohup rm scripts/x',
            'time rm scripts/x',
            'env rm scripts/x',
            'env FOO=1 rm scripts/x',
            'env -i PATH=/bin touch .workspace/y',
            'env -u FOO rm scripts/x',
            'sudo rm scripts/x',
            'sudo -n tee project.profile',
            'sudo -u root rm scripts/x',
            'sudo -g adm rm scripts/x',
            'doas rm scripts/x',
            'nice rm scripts/d',
            'nice -n 5 rm scripts/d',
            'xargs rm scripts/e < list',
            'xargs -n 1 rm scripts/e',
            'command -p chmod +x scripts/f.sh',
            'timeout 5 rm scripts/i',
            'timeout -s KILL 5 rm scripts/i',
            'timeout -k 2 5 rm scripts/i',
            'stdbuf -oL tee scripts/j',
            'sudo -- rm scripts/x',
            '/usr/bin/env /bin/rm scripts/x',
            'FOO=1 nohup nice -n 3 sudo -u x env A=1 timeout 9 rm scripts/x',
        ):
            with self.subTest(command=command):
                code, _, err = self.bash(command, {'WORKSPACE_TASK_ID': 'T1'})
                self.assertEqual(code, BLOCKED)
                self.assertIn('chemin protégé', err)

    def test_shell_dash_c_scripts_are_analysed(self):
        for command in (
            'sh -c "mv x scripts/h"',
            'bash -c "echo hi > .workspace/x"',
            'zsh -c "rm scripts/x"',
            'dash -c "touch project.profile"',
            'echo ok && sh -c "mv x scripts/h"',
            "bash -lc 'rm scripts/x'",
            'bash -o pipefail -c "rm scripts/x"',
            "sudo bash -c 'cp a scripts/b'",
            'sh -c "sh -c \\"rm scripts/x\\""',
            'sh -c "ls; env rm scripts/x"',
        ):
            with self.subTest(command=command):
                code, _, err = self.bash(command, {'WORKSPACE_TASK_ID': 'T1'})
                self.assertEqual(code, BLOCKED)
                self.assertIn('chemin protégé', err)

    def test_shell_nesting_is_bounded(self):
        command = 'rm scripts/x'
        for _ in range(5):
            command = 'sh -c ' + json.dumps(command)
        self.assertEqual(self.bash(command, {'WORKSPACE_TASK_ID': 'T1'})[0], BLOCKED)
        self.assertEqual(self.bash('sh -c ' + json.dumps(command), {'WORKSPACE_TASK_ID': 'T1'})[0], ALLOWED)

    def test_harmless_prefixed_commands_stay_allowed(self):
        for command in (
            'command -v git',
            'command -v rm',
            'env',
            'env FOO=1 python3 -m unittest',
            'nohup sleep 1',
            'time ls scripts',
            'bash -c "cat scripts/gate.sh"',
            'sh -c "echo hi > out.txt"',
            'nice -n 5 make test',
            'xargs cat < list',
            'timeout 5 ls',
            'sudo ls scripts',
            'bash scripts/run-in-sandbox.sh -- python3 -m unittest tests.test_x',
            'bash -c',
            'sh script.sh',
            'timeout 5',
            'sudo -n',
            'env -i',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command, {'WORKSPACE_TASK_ID': 'T1'})[0], ALLOWED)

    def test_direct_writes_stay_refused_with_the_task_id_set(self):
        for command in ('rm scripts/x', 'echo x > scripts/y'):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command, {'WORKSPACE_TASK_ID': 'T1'})[0], BLOCKED)

    def test_refusal_names_the_first_protected_path_relative_to_the_root(self):
        code, _, err = self.bash('rm -rf ./.workspace/tasks')
        self.assertEqual(code, BLOCKED)
        self.assertIn('Refusé : .workspace/tasks est', err)

    def test_punctuation_inside_a_word_does_not_split_it(self):
        for command in ('A=x:y rm scripts/x', 'rm scripts/a@b:c', 'echo 50%+1 > scripts/x'):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], BLOCKED)

    def test_a_hash_inside_a_word_does_not_start_a_comment(self):
        for command in ('echo a#b ; rm scripts/x', 'ls x#y && touch project.profile', 'echo a#b > scripts/x'):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], BLOCKED)

    def test_unparseable_command_is_not_a_write(self):
        self.assertEqual(self.bash('echo "unclosed > scripts/x')[0], ALLOWED)

    def test_other_tools_bash_payload_without_command_is_allowed(self):
        self.assertEqual(self.pre_tool('Bash', {'description': 'no command'})[0], ALLOWED)


class OrchestratorExceptionTests(HooksGuardTests):
    def test_session_holding_the_lock_may_write_protected_paths(self):
        self.hold_lock(os.getpid())
        self.assertEqual(self.pre_tool('Write', {'file_path': 'scripts/x.sh'})[0], ALLOWED)
        self.assertEqual(self.bash('echo x > .workspace/tasks/a.md')[0], ALLOWED)

    def test_lock_holder_with_task_id_set_is_refused(self):
        self.hold_lock(os.getpid())
        code, _, err = self.pre_tool('Write', {'file_path': 'scripts/x.sh'}, extra_env={'WORKSPACE_TASK_ID': 'T13a'})
        self.assertEqual(code, BLOCKED)
        self.assertIn('AGENTS.md', err)

    def test_lock_with_dead_pid_does_not_grant_the_exception(self):
        self.hold_lock(dead_pid())
        self.assertEqual(self.pre_tool('Write', {'file_path': 'scripts/x.sh'})[0], BLOCKED)

    def test_live_process_that_is_not_an_ancestor_does_not_grant_the_exception(self):
        self.hold_lock(self.live_non_ancestor())
        self.assertEqual(self.pre_tool('Write', {'file_path': 'scripts/x.sh'})[0], BLOCKED)

    def test_unreadable_or_malformed_lock_does_not_grant_the_exception(self):
        lock = self.root / '.workspace/orchestrator.json'
        lock.parent.mkdir()
        for content in ('not json', '[1, 2]', json.dumps({'pid': 'abc'}), json.dumps({'pid': True})):
            with self.subTest(content=content):
                lock.write_text(content)
                self.assertEqual(self.pre_tool('Write', {'file_path': 'scripts/x.sh'})[0], BLOCKED)

    def test_hook_launched_through_a_shell_keeps_the_exception(self):
        self.hold_lock(os.getpid())
        code, _, err = self.pre_tool('Write', {'file_path': '.workspace/tasks/a.md'}, via=VIA_SHELL)
        self.assertEqual((code, err), (ALLOWED, ''))

    def test_hook_launched_by_a_non_shell_child_of_the_holder_gets_no_exception(self):
        self.hold_lock(os.getpid())
        code, _, err = self.pre_tool('Write', {'file_path': 'scripts/x.sh'}, via=VIA_PYTHON)
        self.assertEqual(code, BLOCKED)
        self.assertIn('scripts/x.sh', err)

    def test_absent_lock_refuses(self):
        self.assertEqual(self.pre_tool('Write', {'file_path': 'scripts/x.sh'})[0], BLOCKED)


class StopTests(HooksGuardTests):
    def test_without_lock_stop_is_silent(self):
        self.assertEqual(self.stop(), (ALLOWED, '', ''))

    def test_lock_held_by_this_session_blocks_stop_with_session_name_and_release_hint(self):
        self.hold_lock(os.getpid(), session='orchestrateur-42')
        code, out, err = self.stop()
        self.assertEqual(code, BLOCKED)
        self.assertEqual(out, '')
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn('orchestrateur-42', err)
        self.assertIn('bash scripts/orchestrator.sh release', err)

    def test_stop_hook_already_active_never_blocks_twice(self):
        self.hold_lock(os.getpid())
        self.assertEqual(self.stop({'stop_hook_active': True})[0], ALLOWED)

    def test_dead_lock_holder_does_not_block_stop(self):
        self.hold_lock(dead_pid())
        self.assertEqual(self.stop()[0], ALLOWED)

    def test_stop_through_a_shell_still_reminds_the_holder(self):
        self.hold_lock(os.getpid())
        self.assertEqual(self.stop(via=VIA_SHELL)[0], BLOCKED)

    def test_stop_launched_by_a_non_shell_child_of_the_holder_is_silent(self):
        self.hold_lock(os.getpid())
        self.assertEqual(self.stop(via=VIA_PYTHON), (ALLOWED, '', ''))

    def test_lock_of_a_live_non_ancestor_does_not_block_stop(self):
        self.hold_lock(self.live_non_ancestor())
        self.assertEqual(self.stop()[0], ALLOWED)


class InputTests(HooksGuardTests):
    def test_invalid_json_is_silent_and_allowed(self):
        for mode in ('pre-tool', 'stop'):
            with self.subTest(mode=mode):
                self.assertEqual(run_hook(mode, self.root, raw='{pas du json'), (ALLOWED, '', ''))

    def test_json_that_is_not_an_object_is_silent_and_allowed(self):
        self.assertEqual(run_hook('pre-tool', self.root, raw='[1, 2]'), (ALLOWED, '', ''))

    def test_root_falls_back_to_cwd_of_the_payload_when_env_is_unset(self):
        payload = {'tool_name': 'Write', 'tool_input': {'file_path': 'scripts/x.sh'}, 'cwd': str(self.root)}
        code, _, err = run_hook('pre-tool', None, payload)
        self.assertEqual(code, BLOCKED)
        self.assertIn('scripts/x.sh', err)

    def test_unknown_mode_is_a_usage_error(self):
        code, _, _ = run_hook('nope', self.root, {})
        self.assertNotIn(code, (ALLOWED, BLOCKED))


class SettingsTests(unittest.TestCase):
    def test_template_and_project_settings_are_identical(self):
        first, second = (path.read_bytes() for path in SETTINGS)
        self.assertEqual(first, second)

    def test_settings_declare_the_pre_tool_and_stop_hooks(self):
        hooks = json.loads(SETTINGS[0].read_text(encoding='utf-8'))['hooks']
        pre_commands = [item['command'] for group in hooks['PreToolUse'] for item in group['hooks']]
        stop_commands = [item['command'] for group in hooks['Stop'] for item in group['hooks']]
        self.assertTrue(any(command.endswith('hooks_guard.py" pre-tool; }') for command in pre_commands))
        self.assertTrue(any(command.endswith('hooks_guard.py" stop; }') for command in stop_commands))
        # Both hooks run the interpreter resolved by scripts/python.sh (T94), which blocks on an older Python.
        for command in pre_commands + stop_commands:
            self.assertIn('. "$CLAUDE_PROJECT_DIR/scripts/python.sh" && "$WORKSPACE_PYTHON"', command)


if __name__ == '__main__':
    unittest.main()
