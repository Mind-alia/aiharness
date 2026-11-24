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

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel et sous-processus ; suite complète seulement')

ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = ROOT / '.workspace/tmp-tests'

# Records every call outside the project (files inside it would count as agent changes).
# FAKE_PLAN is a JSON list; entry n-1 drives call n: {"stdout", "stderr", "code"}.
FAKE_AGENT = '''#!/usr/bin/env python3
import json, os, pathlib, sys
record = pathlib.Path(os.environ['RECORD_DIR'])
name = pathlib.Path(sys.argv[0]).name
if '--version' in sys.argv:
    sys.exit(0)
count = record / (name + '-count')
n = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(n))
(record / ('%s-args-%d.json' % (name, n))).write_text(json.dumps(sys.argv[1:]))
(record / ('%s-stdin-%d.txt' % (name, n))).write_text(sys.stdin.read())
plan = json.loads(os.environ.get('FAKE_PLAN', '[]'))
step = plan[n - 1] if n <= len(plan) else {}
if name == 'npx':
    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text('relay answer')
elif name == 'agy':
    print(json.dumps({'status': 'SUCCESS', 'response': 'relay answer'}))
else:
    sys.stdout.write(step.get('stdout', json.dumps({'result': 'answer'})))
    sys.stderr.write(step.get('stderr', ''))
    sys.exit(step.get('code', 0))
'''

# Fails the first verification only, so exactly one retry follows.
VERIFY_ONCE = '#!/bin/sh\nif [ ! -f "$RECORD_DIR/verified" ]; then touch "$RECORD_DIR/verified"; echo FIX THIS FAILURE; exit 1; fi\n'


def load(name):
    lib = str(ROOT / 'template/scripts/lib')
    spec = importlib.util.spec_from_file_location(name, lib + '/%s.py' % name)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, lib)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(lib)
    return module


class DelegateCostTests(unittest.TestCase):
    def setUp(self):
        TEST_TMP.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.root = base / 'project'
        self.record = base / 'record'
        self.record.mkdir()
        (self.root / 'scripts/lib').mkdir(parents=True)
        (self.root / 'docs').mkdir()
        (self.root / 'bin').mkdir()
        for script in (ROOT / 'template/scripts').glob('*.sh'):
            shutil.copy2(script, self.root / 'scripts' / script.name)
        for lib_file in (ROOT / 'template/scripts/lib').glob('*.py'):
            shutil.copy2(lib_file, self.root / 'scripts/lib' / lib_file.name)
        shutil.copy2(ROOT / 'template/.gitignore', self.root / '.gitignore')
        (self.root / 'tracked.txt').write_text('before\n')
        self.git('init', '-q')
        self.git('add', 'tracked.txt')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
        for name in ('claude', 'npx', 'agy'):
            self.executable('bin/' + name, FAKE_AGENT)
        self.env = dict(
            os.environ,
            PATH=str(self.root / 'bin') + os.pathsep + os.environ['PATH'],
            DELEGATE_RETRY_DELAY='0',
            AGY_BIN=str(self.root / 'bin/agy'),
            RECORD_DIR=str(self.record),
            FAKE_PLAN='[]',
        )
        self.task = self.root / 'task.md'

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True)

    def executable(self, name, content):
        path = self.root / name
        path.write_text(content)
        path.chmod(0o755)

    def write_task(self, agent='claude-haiku', mode='write', extra=''):
        self.task.write_text(
            '---\nid: demo\nagent: %s\nmode: %s\nallowed: [tracked.txt]\n'
            'verify: echo targeted\nattempts: 2\n%s---\nTASK BODY\n' % (agent, mode, extra)
        )

    def run_delegate(self, plan=None):
        env = dict(self.env, FAKE_PLAN=json.dumps(plan or []))
        return subprocess.run(
            ['bash', str(self.root / 'scripts/delegate.sh'), str(self.task)],
            cwd=self.root / 'docs',
            env=env,
            text=True,
            capture_output=True,
        )

    def status(self):
        return json.loads((self.root / '.workspace/run/delegate/demo/status.json').read_text())

    def args(self, n, name='claude'):
        return json.loads((self.record / ('%s-args-%d.json' % (name, n))).read_text())

    def stdin(self, n, name='claude'):
        return (self.record / ('%s-stdin-%d.txt' % (name, n))).read_text()

    def calls(self, name='claude'):
        path = self.record / (name + '-count')
        return int(path.read_text()) if path.exists() else 0

    def events(self, kind):
        lines = (self.root / '.workspace/events.jsonl').read_text().splitlines()
        return [e['detail'] for e in map(json.loads, lines) if e['event'] == kind]

    def failing_first_verification(self):
        self.executable('scripts/verify-task.sh', VERIFY_ONCE)

    # Règle 2 : arguments de Claude.
    def test_default_and_custom_max_turns_with_exact_argument_order(self):
        self.write_task()
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertEqual(
            self.args(1), ['-p', '--output-format', 'json', '--model', 'haiku', '--permission-mode', 'acceptEdits', '--max-turns', '40']
        )
        self.write_task('claude-sonnet', 'review', 'max_turns: 7\n')
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertEqual(
            self.args(2), ['-p', '--output-format', 'json', '--model', 'sonnet', '--permission-mode', 'plan', '--max-turns', '7']
        )

    def test_max_turns_precedes_allowed_tools_and_resume(self):
        claude_args = load('delegate_run').claude_args
        task = {'agent': 'claude-opus', 'mode': 'write', 'max_turns': 9}
        self.assertEqual(
            claude_args(task, 'exchange', 'sess_1'),
            [
                'claude',
                '-p',
                '--output-format',
                'json',
                '--model',
                'opus',
                '--permission-mode',
                'acceptEdits',
                '--max-turns',
                '9',
                '--allowedTools',
                'Bash(bash scripts/run-in-sandbox.sh:*)',
                '--resume',
                'sess_1',
            ],
        )
        self.assertEqual(claude_args(dict(task, max_turns=None, mode='review'), 'exchange')[-2:], ['--max-turns', '40'])

    def test_guard_adds_the_executor_hook_and_cut_tools(self):
        module = load('delegate_run')
        task = {'id': 'demo', 'agent': 'claude-opus', 'mode': 'write', 'max_turns': 9}
        root, worktree = '/abs/project', '/abs/project/.workspace/worktrees/demo'
        args = module.claude_args(task, 'exchange', guard=(root, worktree))
        self.assertEqual(
            args[:9], ['claude', '-p', '--output-format', 'json', '--model', 'opus', '--permission-mode', 'acceptEdits', '--max-turns']
        )
        self.assertEqual(
            args[9:15],
            [
                '9',
                '--allowedTools',
                'Bash(bash scripts/run-in-sandbox.sh:*),Bash(bash scripts/ask.sh:*)',
                '--disallowedTools',
                'WebFetch,WebSearch,Agent',
                '--settings',
            ],
        )
        settings = json.loads(args[15])
        (entry,) = settings['hooks']['PreToolUse']
        self.assertEqual(entry['matcher'], '*')
        (hook,) = entry['hooks']
        self.assertEqual(hook['type'], 'command')
        self.assertEqual(
            shlex.split(hook['command']), [sys.executable, root + '/scripts/lib/hooks_guard.py', 'executor', root, worktree, 'demo']
        )
        self.assertEqual(
            args[16:],
            [
                '--setting-sources',
                'project,local',
                '--strict-mcp-config',
                '--mcp-config',
                '{"mcpServers": {}}',
                '--tools',
                'Bash,Read,Edit,Write,Glob,Grep,NotebookEdit',
            ],
        )
        self.assertEqual(json.loads(args[20]), {'mcpServers': {}})
        self.assertEqual(len(args), 23)
        resumed = module.claude_args(task, 'exchange', 'sess_1', guard=(root, worktree))
        self.assertEqual(resumed[16:], args[16:] + ['--resume', 'sess_1'])

    def test_guard_review_mode_also_cuts_personal_settings_and_extra_tools(self):
        task = {'id': 'demo', 'agent': 'claude-sonnet', 'mode': 'review'}
        args = load('delegate_run').claude_args(task, 'exchange', guard=('/r', '/r/w'))
        self.assertEqual(
            args[-7:],
            [
                '--setting-sources',
                'project,local',
                '--strict-mcp-config',
                '--mcp-config',
                '{"mcpServers": {}}',
                '--tools',
                'Bash,Read,Edit,Write,Glob,Grep,NotebookEdit',
            ],
        )

    def test_guard_in_review_mode_keeps_the_hook_without_allowed_tools(self):
        task = {'id': 'demo', 'agent': 'claude-sonnet', 'mode': 'review'}
        args = load('delegate_run').claude_args(task, 'exchange', guard=('/r', '/r/w'))
        self.assertNotIn('--allowedTools', args)
        self.assertEqual(args[args.index('--disallowedTools') + 1], 'WebFetch,WebSearch,Agent')
        self.assertIn('--settings', args)

    def test_guard_quotes_paths_with_spaces(self):
        task = {'id': 'demo', 'agent': 'claude-haiku', 'mode': 'write'}
        args = load('delegate_run').claude_args(task, 'exchange', guard=('/a b/p', '/a b/p/w'))
        command = json.loads(args[args.index('--settings') + 1])['hooks']['PreToolUse'][0]['hooks'][0]['command']
        self.assertEqual(shlex.split(command)[1:3], ['/a b/p/scripts/lib/hooks_guard.py', 'executor'])

    def test_workshop_section_states_the_four_ideas_and_both_commands(self):
        section = load('delegate_run').workshop_section('/abs/wt')
        self.assertLess(len(section.splitlines()), 10)
        for text in (
            'Ton atelier est ce worktree et son conteneur, rien d\'autre.',
            '`bash scripts/run-in-sandbox.sh -- <commande>`',
            '`bash scripts/ask.sh "<question>"`',
            'continue ce qui ne dépend pas de la réponse',
            'ne se réessaie pas sous une autre forme',
            '/abs/wt',
        ):
            self.assertIn(text, section)

    def test_workshop_section_points_to_read_tools_and_to_pipes_in_the_container(self):
        section = load('delegate_run').workshop_section('/abs/wt')
        self.assertLess(len(section.splitlines()), 10)
        for text in (
            'Read, Grep et Glob',
            'jamais avec `grep` ou `cat` en Bash',
            "`bash scripts/run-in-sandbox.sh -- sh -c '<commande> | <filtre>'`",
        ):
            self.assertIn(text, section)

    def test_workshop_section_admits_tail_and_head_after_the_sandbox_command(self):
        section = load('delegate_run').workshop_section('/abs/wt')
        self.assertLess(len(section.splitlines()), 10)
        for text in ('2>&1 | tail -n N', '| head -n N', 'run-in-sandbox.sh'):
            self.assertIn(text, section)

    # Règle 3 et 4 : reprise de session.
    def test_verification_failure_resumes_session_with_only_the_feedback(self):
        self.write_task()
        self.failing_first_verification()
        plan = [
            {'stdout': json.dumps({'result': 'first', 'session_id': 'sess-1'})},
            {'stdout': json.dumps({'result': 'second', 'session_id': 'sess-2'})},
        ]
        self.assertEqual(self.run_delegate(plan).returncode, 0)
        self.assertNotIn('--resume', self.args(1))
        self.assertEqual(self.args(2)[-2:], ['--resume', 'sess-1'])
        self.assertTrue(self.stdin(2).startswith('Échec de vérification, corriger :\n'))
        self.assertIn('FIX THIS FAILURE', self.stdin(2))
        self.assertNotIn('TASK BODY', self.stdin(2))
        self.assertIn('TASK BODY', self.stdin(1))
        prompt = (self.root / '.workspace/run/delegate/demo/prompt.md').read_text()
        self.assertIn('TASK BODY', prompt)
        self.assertIn('Échec de vérification, corriger :\nFIX THIS FAILURE', prompt)
        self.assertEqual([e['resumed'] for e in self.events('attempt_started')], [False, True])

    def test_acceptance_failure_resumes_with_the_acceptance_feedback(self):
        self.write_task()
        hidden = self.root / '.workspace/acceptance/demo'
        hidden.mkdir(parents=True)
        (hidden / 'check.txt').write_text('x')
        self.task.write_text(
            self.task.read_text().replace(
                'attempts: 2\n',
                'attempts: 2\nacceptance: [check.txt]\n'
                'acceptance_verify: test -f "$RECORD_DIR/accepted" || { touch "$RECORD_DIR/accepted"; echo "FAIL: hidden"; exit 1; }\n',
            )
        )
        plan = [
            {'stdout': json.dumps({'result': 'a', 'session_id': 'sess-1'})},
            {'stdout': json.dumps({'result': 'b', 'session_id': 'sess-2'})},
        ]
        result = self.run_delegate(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.args(2)[-2:], ['--resume', 'sess-1'])
        self.assertTrue(self.stdin(2).startswith('Échec d’acceptation, corriger :\n'))
        self.assertNotIn('TASK BODY', self.stdin(2))
        self.assertEqual(self.status()['acceptance_attempts'], 2)

    def test_transient_error_resumes_with_the_interruption_sentence(self):
        self.write_task()
        plan = [
            {'stdout': json.dumps({'session_id': 'sess-1'}), 'stderr': '503 UNAVAILABLE network', 'code': 1},
            {'stdout': json.dumps({'result': 'done', 'session_id': 'sess-1'})},
        ]
        self.assertEqual(self.run_delegate(plan).returncode, 0)
        self.assertEqual(self.args(2)[-2:], ['--resume', 'sess-1'])
        self.assertEqual(self.stdin(2), "La tentative précédente s'est interrompue ; reprends la tâche là où elle en était.")

    def test_no_usable_session_keeps_the_full_prompt_and_no_resume(self):
        outputs = {
            'no session id': json.dumps({'result': 'x'}),
            'not json': 'plain prose',
            'empty id': json.dumps({'result': 'x', 'session_id': ''}),
            'forbidden characters': json.dumps({'result': 'x', 'session_id': 'a b;c'}),
            'not a string': json.dumps({'result': 'x', 'session_id': 12}),
        }
        for label, output in outputs.items():
            with self.subTest(label):
                shutil.rmtree(self.record)
                self.record.mkdir()
                self.write_task()
                self.failing_first_verification()
                self.assertEqual(self.run_delegate([{'stdout': output}, {'stdout': output}]).returncode, 0)
                self.assertEqual(self.calls(), 2)
                self.assertNotIn('--resume', self.args(2))
                self.assertIn('TASK BODY', self.stdin(2))
                self.assertIn('FIX THIS FAILURE', self.stdin(2))
                self.assertEqual([e['resumed'] for e in self.events('attempt_started')][-2:], [False, False])

    def test_only_the_previous_attempt_session_is_resumed(self):
        self.write_task()
        self.task.write_text(self.task.read_text().replace('attempts: 2', 'attempts: 3'))
        self.executable('scripts/verify-task.sh', '#!/bin/sh\nexit 1\n')
        plan = [
            {'stdout': json.dumps({'result': 'a', 'session_id': 'sess-1'})},
            {'stdout': json.dumps({'result': 'b'})},
            {'stdout': json.dumps({'result': 'c', 'session_id': 'sess-3'})},
        ]
        self.assertNotEqual(self.run_delegate(plan).returncode, 0)
        self.assertEqual(self.args(2)[-2:], ['--resume', 'sess-1'])
        self.assertNotIn('--resume', self.args(3))
        self.assertIn('TASK BODY', self.stdin(3))

    def test_secret_in_the_followup_stops_before_resuming(self):
        self.write_task()
        self.executable('scripts/verify-task.sh', '#!/bin/sh\necho "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"\nexit 1\n')
        plan = [{'stdout': json.dumps({'result': 'a', 'session_id': 'sess-1'})}]
        self.assertNotEqual(self.run_delegate(plan).returncode, 0)
        self.assertEqual(self.status()['outcome'], 'secret_in_prompt')
        self.assertEqual(self.calls(), 1)

    # Règle 4 et 5 : plafond de tours.
    def test_max_turns_stop_is_verified_and_a_failure_resumes(self):
        self.write_task()
        self.failing_first_verification()
        plan = [
            {'stdout': json.dumps({'subtype': 'error_max_turns', 'session_id': 'sess-1', 'is_error': False}), 'code': 1},
            {'stdout': json.dumps({'result': 'fixed', 'session_id': 'sess-1'})},
        ]
        result = self.run_delegate(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['outcome'], 'done')
        self.assertEqual(self.status()['verify'], {'command': 'echo targeted', 'exit': 0})
        self.assertEqual(self.args(2)[-2:], ['--resume', 'sess-1'])
        self.assertEqual([e['max_turns_reached'] for e in self.events('attempt_finished')], [True, False])
        self.assertEqual(self.events('attempt_finished')[0]['code'], 1)

    def test_max_turns_stop_with_passing_verification_is_done(self):
        self.write_task()
        plan = [{'stdout': json.dumps({'subtype': 'error_max_turns', 'session_id': 'sess-1'}), 'code': 1}]
        self.assertEqual(self.run_delegate(plan).returncode, 0)
        self.assertEqual(self.status()['outcome'], 'done')
        self.assertEqual(self.calls(), 1)

    def test_other_error_subtypes_are_not_a_turn_limit(self):
        self.write_task()
        plan = [{'stdout': json.dumps({'subtype': 'error_during_execution', 'session_id': 'sess-1'}), 'code': 1}]
        self.assertNotEqual(self.run_delegate(plan).returncode, 0)
        self.assertEqual(self.status()['outcome'], 'failed')
        self.assertEqual([e['max_turns_reached'] for e in self.events('attempt_finished')], [False])

    # Règle 7 : relais.
    def test_relays_ignore_max_turns_and_resume_with_one_warning(self):
        for agent, name in (('codex', 'npx'), ('antigravity', 'agy')):
            with self.subTest(agent=agent):
                shutil.rmtree(self.record)
                self.record.mkdir()
                self.write_task(agent, extra='max_turns: 5\n')
                self.failing_first_verification()
                result = self.run_delegate()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.calls(name), 2)
                for n in (1, 2):
                    self.assertNotIn('--max-turns', self.args(n, name))
                    self.assertNotIn('--resume', self.args(n, name))
                self.assertEqual(self.status()['warnings'].count('max_turns ignoré pour ' + agent), 1)
                self.assertEqual([e['resumed'] for e in self.events('attempt_started')][-2:], [False, False])
                self.assertEqual([e['max_turns_reached'] for e in self.events('attempt_finished')][-2:], [False, False])

    def test_relay_without_max_turns_has_no_warning(self):
        self.write_task('codex')
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertFalse([w for w in self.status()['warnings'] if 'max_turns' in w])

    def test_claude_with_max_turns_has_no_warning(self):
        self.write_task(extra='max_turns: 5\n')
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertFalse([w for w in self.status()['warnings'] if 'max_turns' in w])

    # Règle 6 : compte rendu JSON.
    def test_every_executor_gets_the_report_section_after_the_task_body(self):
        for agent in ('claude-haiku', 'codex', 'antigravity'):
            with self.subTest(agent=agent):
                self.write_task(agent)
                self.assertEqual(self.run_delegate().returncode, 0)
                prompt = (self.root / '.workspace/run/delegate/demo/prompt.md').read_text()
                body, section = prompt.index('TASK BODY'), prompt.index('```json')
                self.assertLess(body, section)
                for key in ('files_changed', 'tests_run', 'exit_codes', 'notes'):
                    self.assertIn(key, prompt[section:])
                self.assertIn('prose', prompt[section:])

    def test_status_carries_the_report_found_in_the_answer(self):
        self.write_task()
        report = {'files_changed': ['tracked.txt'], 'tests_run': ['python3 -m unittest x'], 'exit_codes': [0], 'notes': 'ok'}
        answer = 'Terminé.\n```json\n%s\n```\n' % json.dumps(report)
        self.assertEqual(self.run_delegate([{'stdout': json.dumps({'result': answer})}]).returncode, 0)
        self.assertEqual(self.status()['report'], report)
        self.assertEqual(self.status()['report_format'], 'json')

    def test_status_reports_prose_without_a_report(self):
        for result in ('Terminé, tout va bien.', ''):
            with self.subTest(result=result):
                self.write_task()
                self.run_delegate([{'stdout': json.dumps({'result': result})}])
                self.assertIsNone(self.status()['report'])
                self.assertEqual(self.status()['report_format'], 'prose')

    def test_relay_answer_is_parsed_for_a_report(self):
        self.write_task('codex')
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertEqual((self.status()['report'], self.status()['report_format']), (None, 'prose'))


class ExtractReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.extract = staticmethod(load('delegate_run').extract_report)

    def block(self, **changes):
        report = {'files_changed': ['a.py'], 'tests_run': ['t'], 'exit_codes': [0], 'notes': 'n'}
        report.update(changes)
        return json.dumps(report)

    def test_returns_the_reduced_dict(self):
        text = 'avant ```json\n%s\n``` après' % json.dumps(
            {'files_changed': [], 'tests_run': [], 'exit_codes': [], 'notes': '', 'extra': 1}
        )
        self.assertEqual(self.extract(text), {'files_changed': [], 'tests_run': [], 'exit_codes': [], 'notes': ''})

    def test_last_valid_object_wins(self):
        text = self.block(notes='first') + '\ntexte\n' + self.block(notes='second')
        self.assertEqual(self.extract(text)['notes'], 'second')

    def test_later_invalid_object_does_not_hide_an_earlier_valid_one(self):
        text = self.block(notes='good') + '\n' + self.block(exit_codes=[True])
        self.assertEqual(self.extract(text)['notes'], 'good')

    def test_missing_key_or_wrong_types_are_ignored(self):
        report = json.loads(self.block())
        cases = [{k: v for k, v in report.items() if k != key} for key in report]
        cases += [
            dict(report, exit_codes=[True]),
            dict(report, exit_codes=[0.5]),
            dict(report, exit_codes='0'),
            dict(report, files_changed='a.py'),
            dict(report, files_changed=[1]),
            dict(report, tests_run=[None]),
            dict(report, tests_run='t'),
            dict(report, notes=None),
            dict(report, notes=['n']),
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertIsNone(self.extract(json.dumps(case)))

    def test_empty_text_and_non_report_json(self):
        for text in ('', 'pas de json', '{', '{"a": 1}', '[1, 2]', '{"unterminated": '):
            with self.subTest(text=text):
                self.assertIsNone(self.extract(text))

    def test_nested_report_inside_other_json_is_found(self):
        self.assertEqual(self.extract('{"wrapper": %s}' % self.block())['notes'], 'n')

    def test_only_the_last_64_kib_are_searched(self):
        early = self.block(notes='early')
        self.assertEqual(self.extract(early + 'x' * 70000), None)
        self.assertEqual(self.extract('x' * 70000 + early)['notes'], 'early')
        self.assertEqual(self.extract(early + 'x' * 60000)['notes'], 'early')

    def test_deep_nesting_and_binary_noise_do_not_crash(self):
        self.assertIsNone(self.extract('{"a":' * 5000))
        self.assertIsNone(self.extract('{' * 60000))
        self.assertEqual(self.extract('é' * 40000 + self.block())['notes'], 'n')


if __name__ == '__main__':
    unittest.main()
