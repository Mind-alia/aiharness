import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'template/scripts/lib/hooks_guard.py'
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import executor_guard  # noqa: E402

ALLOWED = 0
REFUSED = 2
SANDBOX = 'bash scripts/run-in-sandbox.sh'


class ExecutorGuardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / 'project'
        self.worktree = self.root / '.workspace/worktrees/demo'
        (self.worktree / 'src').mkdir(parents=True)
        (self.worktree / 'src/a.py').write_text('x = 1\n')
        (self.root / 'AGENTS.md').write_text('secret rules\n')
        self.outside = Path(tmp.name).resolve() / 'outside'
        self.outside.mkdir()
        (self.outside / 'key').write_text('k\n')

    def hook(self, tool, tool_input, raw=None, args=None):
        payload = raw if raw is not None else json.dumps({'tool_name': tool, 'tool_input': tool_input, 'cwd': str(self.worktree)})
        command = [sys.executable, str(SCRIPT), 'executor'] + (args if args is not None else [str(self.root), str(self.worktree), 'demo'])
        done = subprocess.run(command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        return done.returncode, done.stderr

    def journal(self):
        path = self.root / '.workspace/events.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def assert_allowed(self, tool, tool_input):
        code, stderr = self.hook(tool, tool_input)
        self.assertEqual((code, stderr), (ALLOWED, ''), '%s %r' % (tool, tool_input))

    def assert_refused(self, tool, tool_input, reason=None):
        code, stderr = self.hook(tool, tool_input)
        self.assertEqual(code, REFUSED, '%s %r' % (tool, tool_input))
        self.assertEqual(len(stderr.splitlines()), 1, stderr)
        self.assertIn(tool, stderr)
        self.assertIn('bash scripts/ask.sh "<question>"', stderr)
        if reason:
            self.assertIn(reason, stderr)

    # Critères de conformité : journal et message.
    def test_read_inside_worktree_is_allowed_and_leaves_no_event(self):
        self.assert_allowed('Read', {'file_path': str(self.worktree / 'src/a.py')})
        self.assertEqual(self.journal(), [])

    def test_read_of_the_main_repository_is_refused_and_journaled(self):
        target = str(self.root / 'AGENTS.md')
        self.assert_refused('Read', {'file_path': target}, 'hors du worktree')
        (event,) = self.journal()
        self.assertEqual((event['event'], event['task_id']), ('tool_refused', 'demo'))
        self.assertEqual(event['detail']['role'], 'executor')
        self.assertEqual(event['detail']['tool'], 'Read')
        self.assertEqual(event['detail']['target'], target)
        self.assertIn('worktree', event['detail']['reason'])

    def test_journal_target_is_bounded_to_200_characters(self):
        command = SANDBOX + ' -- echo ' + '$(x)' * 100
        self.assert_refused('Bash', {'command': command})
        self.assertEqual(self.journal()[0]['detail']['target'], command[:200])

    def test_unwritable_journal_does_not_change_the_decision(self):
        (self.root / '.workspace/events.jsonl').mkdir()
        code, stderr = self.hook('Read', {'file_path': str(self.root / 'AGENTS.md')})
        self.assertEqual(code, REFUSED)
        self.assertEqual(len(stderr.splitlines()), 1, stderr)

    # Règle 1.
    def test_unreadable_input_is_refused(self):
        for raw in (
            '',
            'pas du json',
            '[]',
            '{"tool_name": "Read"}',
            '{"tool_name": "Read", "tool_input": "x"}',
            '{"tool_name": "TodoWrite", "tool_input": []}',
            '{"tool_input": {}}',
        ):
            with self.subTest(raw=raw):
                self.assertEqual(self.hook(None, None, raw=raw)[0], REFUSED)

    def test_wrong_argument_count_is_refused(self):
        self.assertEqual(self.hook('Read', {}, args=[str(self.root)])[0], REFUSED)

    # Règle 2.
    def test_cut_tools_are_refused(self):
        for tool in ('WebFetch', 'WebSearch', 'Agent', 'Task', 'mcp__github__create_issue', 'mcp__x'):
            with self.subTest(tool=tool):
                self.assert_refused(tool, {'url': 'https://example.com'}, "outil coupé pour l'exécutant")

    # Règle 3.
    def test_single_sandbox_or_ask_command_is_allowed(self):
        for command in (
            SANDBOX + ' -- true',
            SANDBOX + ' -- python3 -m unittest tests.test_x',
            'bash scripts/ask.sh "où est la carte ?"',
            'bash scripts/ask.sh',
            SANDBOX + " -- sh -c 'echo $PATH; ls | wc -l > out'",
            SANDBOX + " -- sh -c 'echo `id`'",
            "bash 'scripts/run-in-sandbox.sh' -- true",
            SANDBOX + ' -- echo "a;b"',
        ):
            with self.subTest(command=command):
                self.assert_allowed('Bash', {'command': command})

    def test_other_commands_are_refused(self):
        for command in (
            'git log',
            'ls',
            'cd .. && ' + SANDBOX + ' -- true',
            SANDBOX + 'x -- true',
            'bash scripts/ask.shx x',
            'bash scripts/other.sh',
            'bash -c "' + SANDBOX + '"',
            'sh scripts/ask.sh x',
            'FOO=1 ' + SANDBOX + ' -- true',
            '/bin/bash scripts/ask.sh x',
            'sed -i s/a/b/ src/a.py',
            '',
            '   ',
        ):
            with self.subTest(command=command):
                self.assert_refused('Bash', {'command': command})

    def test_separators_redirections_and_newlines_are_refused(self):
        for tail in (
            '; id',
            ' && id',
            ' || id',
            ' | cat',
            ' & id',
            ' > /tmp/x',
            ' >> /tmp/x',
            ' < /etc/passwd',
            '\nid',
            '\r\nid',
            ' <(id)',
            ' (id)',
            ' 2>&1 2>&1',
            ' 2>&1 > x',
        ):
            with self.subTest(tail=tail):
                self.assert_refused('Bash', {'command': SANDBOX + ' -- true' + tail})

    def test_host_evaluated_expansions_are_refused(self):
        for command in (
            SANDBOX + ' -- echo $(cat ~/.ssh/id_rsa)',
            SANDBOX + ' -- echo `id`',
            SANDBOX + ' -- echo $HOME',
            SANDBOX + ' -- echo "$HOME"',
            SANDBOX + ' -- echo "`id`"',
            SANDBOX + " -- echo 'unclosed",
        ):
            with self.subTest(command=command):
                self.assert_refused('Bash', {'command': command})

    # Un `\` échappe le caractère suivant : `\$` et `\`` sont littéraux.
    def test_escaped_characters_are_literal_and_allowed(self):
        for command in (
            SANDBOX + ' -- grep -n "a\\|b" src/a.py',
            SANDBOX + ' -- grep -n a\\|b src/a.py',
            SANDBOX + ' -- echo \\$x',
            SANDBOX + ' -- echo "\\$x"',
            SANDBOX + ' -- echo \\`id\\`',
            SANDBOX + ' -- echo "\\`id\\`"',
            SANDBOX + ' -- echo a\\;b',
            SANDBOX + ' -- echo "a\\"b"',
            SANDBOX + ' -- echo \\\\',
            SANDBOX + ' -- echo "\\\\"',
            SANDBOX + ' -- echo \\"',
            SANDBOX + " -- echo \\'",
            SANDBOX + ' -- echo \\ x',
        ):
            with self.subTest(command=command):
                self.assert_allowed('Bash', {'command': command})

    def test_a_backslash_only_protects_the_next_character(self):
        for command in (
            SANDBOX + ' -- echo \\\\$x',
            SANDBOX + ' -- echo "\\\\$x"',
            SANDBOX + ' -- echo \\\\`id`',
            SANDBOX + ' -- echo \\$x $y',
            SANDBOX + ' -- echo \\$x ; id',
            SANDBOX + ' -- echo \\" ; id',
            SANDBOX + " -- echo \\'$x'",
            SANDBOX + ' -- echo \\$(id)',
            SANDBOX + ' -- echo x\\',
            SANDBOX + ' -- echo \\\\; id',
        ):
            with self.subTest(command=command):
                self.assert_refused('Bash', {'command': command})

    def test_an_escaped_newline_cannot_hide_an_operator(self):
        for command in (SANDBOX + ' -- true\\\n; id', SANDBOX + ' -- true \\\n$(id)', SANDBOX + ' -- true \\\n`id`'):
            with self.subTest(command=command):
                self.assert_refused('Bash', {'command': command})

    def test_bash_without_a_command_string_is_refused(self):
        self.assert_refused('Bash', {})
        self.assert_refused('Bash', {'command': 12})

    # Règle 4.
    def test_path_tools_stay_inside_the_worktree(self):
        inside = str(self.worktree / 'src')
        for tool, tool_input in (
            ('Read', {'file_path': 'src/a.py'}),
            ('Read', {'file_path': inside + '/a.py'}),
            ('LS', {'path': inside}),
            ('Grep', {'pattern': 'x'}),
            ('Grep', {'pattern': 'x', 'path': 'src'}),
            ('Glob', {'pattern': 'src/*.py'}),
            ('Glob', {'pattern': '**/*.py'}),
            ('Glob', {'pattern': inside + '/*.py'}),
            ('NotebookRead', {'notebook_path': 'n.ipynb'}),
            ('Write', {'file_path': 'src/b.py'}),
            ('Edit', {'file_path': inside + '/a.py'}),
            ('Read', {}),
        ):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assert_allowed(tool, tool_input)

    def test_paths_leaving_the_worktree_are_refused(self):
        for tool, tool_input in (
            ('Read', {'file_path': str(self.root / 'AGENTS.md')}),
            ('Read', {'file_path': '../../../AGENTS.md'}),
            ('Read', {'file_path': '/etc/passwd'}),
            ('Grep', {'pattern': 'x', 'path': str(self.root)}),
            ('LS', {'path': '..'}),
            ('Glob', {'pattern': str(self.root) + '/**'}),
            ('Glob', {'pattern': '../*'}),
            ('Glob', {'pattern': '*/../../x'}),
            ('Glob', {'pattern': '/etc/*', 'path': 'src'}),
            ('Write', {'file_path': str(self.outside / 'x')}),
            ('Edit', {'file_path': '../x'}),
            ('NotebookEdit', {'notebook_path': '/tmp/n.ipynb'}),
            ('Read', {'file_path': 12}),
            ('Read', {'file_path': '~/.ssh/id_rsa'}),
        ):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assert_refused(tool, tool_input)

    def test_a_link_leaving_the_worktree_is_followed_and_refused(self):
        os.symlink(self.outside, self.worktree / 'link')
        os.symlink(self.outside / 'key', self.worktree / 'src/key')
        for tool, tool_input in (
            ('Read', {'file_path': 'link/key'}),
            ('Read', {'file_path': 'src/key'}),
            ('Write', {'file_path': 'link/new'}),
            ('LS', {'path': 'link'}),
        ):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assert_refused(tool, tool_input, 'hors du worktree')

    def test_a_worktree_given_through_a_link_is_resolved(self):
        alias = self.root / 'alias'
        os.symlink(self.worktree, alias)
        code, _ = self.hook('Read', {'file_path': str(self.worktree / 'src/a.py')}, args=[str(self.root), str(alias), 'demo'])
        self.assertEqual(code, ALLOWED)

    # Règle 5.
    def test_writes_to_circuit_paths_are_refused_but_reads_are_allowed(self):
        for name in (
            'scripts/x.sh',
            'scripts',
            '.workspace/tasks/a.md',
            '.git/hooks/pre-commit',
            'project.profile',
            'Scripts/x.sh',
            'sub/.git/config',
            'src/../scripts/x.sh',
        ):
            for tool, key in (('Write', 'file_path'), ('Edit', 'file_path'), ('MultiEdit', 'file_path'), ('NotebookEdit', 'notebook_path')):
                with self.subTest(tool=tool, name=name):
                    self.assert_refused(tool, {key: name}, 'protégé')
        for name in ('scripts/x.sh', '.workspace/tasks/a.md', 'project.profile'):
            with self.subTest(read=name):
                self.assert_allowed('Read', {'file_path': name})

    def test_writes_under_claude_settings_are_refused_but_reads_are_allowed(self):
        for name in (
            '.claude/settings.json',
            '.claude/settings.local.json',
            '.claude',
            '.Claude/settings.json',
            '.claude/agents/x.md',
            'src/../.claude/settings.json',
        ):
            for tool, key in (('Write', 'file_path'), ('Edit', 'file_path'), ('MultiEdit', 'file_path'), ('NotebookEdit', 'notebook_path')):
                with self.subTest(tool=tool, name=name):
                    self.assert_refused(tool, {key: name}, 'protégé')
        for name in ('.claude/settings.json', '.claude'):
            with self.subTest(read=name):
                self.assert_allowed('Read', {'file_path': name})

    def test_lookalike_names_are_writable(self):
        for name in (
            'src/scripts/x.py',
            'scripts.md',
            'project.profile.md',
            '.github/ci.yml',
            'tests/x.py',
            '.claude.md',
            'claude/settings.json',
            'src/.claude-notes.md',
        ):
            with self.subTest(name=name):
                self.assert_allowed('Write', {'file_path': name})

    # Règle 6 : liste blanche d'outils.
    def test_tools_outside_the_allowlist_are_refused_and_journaled(self):
        for tool in (
            'SomethingNew',
            'TodoWrite',
            'Skill',
            'Artifact',
            'CronCreate',
            'SendMessage',
            'ToolSearch',
            'EnterWorktree',
            'ExitPlanMode',
            'BashOutput',
            'mcp__x__y',
            'WebFetch',
            'Agent',
            'Task',
        ):
            with self.subTest(tool=tool):
                self.assert_refused(tool, {'todos': []})
        self.assert_refused('SomethingNew', {}, 'hors liste blanche')
        event = self.journal()[-1]
        self.assertEqual((event['event'], event['detail']['tool']), ('tool_refused', 'SomethingNew'))

    def test_every_allowed_tool_is_allowed_with_a_valid_input(self):
        inputs = {
            'Bash': {'command': SANDBOX + ' -- true'},
            'Read': {'file_path': 'src/a.py'},
            'Edit': {'file_path': 'src/a.py'},
            'Write': {'file_path': 'src/b.py'},
            'Glob': {'pattern': '*.py'},
            'Grep': {'pattern': 'x'},
            'NotebookEdit': {'notebook_path': 'n.ipynb'},
            'MultiEdit': {'file_path': 'src/a.py'},
            'LS': {'path': 'src'},
            'NotebookRead': {'notebook_path': 'n.ipynb'},
        }
        self.assertEqual(sorted(inputs), sorted(executor_guard.ALLOWED_TOOLS))
        self.assertEqual(executor_guard.EXECUTOR_TOOLS, ('Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep', 'NotebookEdit'))
        for tool, tool_input in inputs.items():
            with self.subTest(tool=tool):
                self.assert_allowed(tool, tool_input)

    # Règle 3 : filtres d'affichage.
    def test_display_filters_after_the_sandbox_command_are_allowed(self):
        for command in (
            SANDBOX + ' -- python3 -m unittest tests.test_x 2>&1 | tail -n 30',
            SANDBOX + ' -- ls 2>&1',
            SANDBOX + ' -- ls | head -n 5',
            SANDBOX + ' -- ls | tail -40',
            SANDBOX + ' -- ls | head -3',
            SANDBOX + ' -- ls | tail -n 12345',
            SANDBOX + " -- sh -c 'grep -n a src/a.py | head -n 3'",
            SANDBOX + " -- sh -c 'grep -n a src/a.py | head -n 3' 2>&1 | tail -n 5",
            SANDBOX + ' -- ls 2>&1 | head -1 ',
            SANDBOX + ' -- echo "a b" | tail -n 1',
        ):
            with self.subTest(command=command):
                self.assert_allowed('Bash', {'command': command})

    def test_anything_else_after_the_sandbox_command_is_refused(self):
        for command in (
            SANDBOX + ' -- ls | tail -n 30 > /tmp/x',
            SANDBOX + ' -- ls | tail -f',
            SANDBOX + ' -- ls | tail -n 30 /etc/passwd',
            SANDBOX + ' -- ls | sh',
            SANDBOX + ' -- ls | tail -n 3 | head -n 1',
            SANDBOX + ' -- ls 2>&1 | tee x',
            SANDBOX + ' -- ls ; echo fini',
            SANDBOX + ' -- ls | head -c 100',
            SANDBOX + ' -- ls 2>/tmp/err',
            SANDBOX + ' -- ls | tail -n $(id -u)',
            SANDBOX + ' -- ls | tail -n 123456',
            SANDBOX + ' -- ls | tail -123456',
            SANDBOX + ' -- ls | tail -n',
            SANDBOX + ' -- ls | tail -n x',
            SANDBOX + ' -- ls | tail',
            SANDBOX + ' -- ls | tail -n 3 2>&1',
            SANDBOX + ' -- ls 2>&1 2>&1',
            SANDBOX + ' -- ls 2>&1 ; id',
            SANDBOX + ' -- ls 2>&1 && id',
            SANDBOX + ' -- ls | tail -n 3 && id',
            SANDBOX + ' -- ls | tail -n 3 ; id',
            SANDBOX + ' -- ls | tail -n 3\nid',
            SANDBOX + ' -- ls | tail -n 3 > x',
            SANDBOX + " -- echo 'x | tail -n 3",
            SANDBOX + ' -- echo $(id) | tail -n 3',
            SANDBOX + ' -- echo x\\ | tail -n 3',
            SANDBOX + ' -- ls |& tail -n 3',
            'bash scripts/ask.sh "q" | tail -n 3',
            'bash scripts/ask.sh "q" 2>&1',
            'ls | tail -n 3',
            'ls 2>&1',
            'git log | head -n 3',
            'bash scripts/other.sh -- ls | tail -n 3',
            'bash scripts/run-in-sandbox.sh | tail -n 3',
        ):
            with self.subTest(command=command):
                self.assert_refused('Bash', {'command': command})


if __name__ == '__main__':
    unittest.main()
