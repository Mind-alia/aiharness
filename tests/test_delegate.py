import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel, sous-processus ou Docker simulé ; suite complète seulement')

try:
    from tests.test_procs import gone
except ImportError:
    from test_procs import gone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import workdir  # noqa: E402


class DelegateTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.workspace/tmp-tests').mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.workspace/tmp-tests')
        self.addCleanup(self.temp.cleanup)
        # Les clones de tâche vont hors du dépôt, jamais dans le vrai ~/.workspace-ia.
        self.work_dir = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.work_dir.cleanup)
        patcher = mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': os.path.realpath(self.work_dir.name)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = (Path(self.temp.name) / 'project with spaces').resolve()
        (self.root / 'scripts/lib').mkdir(parents=True, exist_ok=True)
        for script_file in (ROOT / 'template/scripts').glob('*.sh'):
            dest = self.root / 'scripts' / script_file.name
            shutil.copy2(script_file, dest)
            dest.chmod(0o755)

        for lib_file in (ROOT / 'template/scripts/lib').glob('*.py'):
            dest = self.root / 'scripts/lib' / lib_file.name
            shutil.copy2(lib_file, dest)

        # Création du faux docker hors du dépôt
        self.docker_bin_dir = self.root.parent / (self.root.name + '-docker-bin')
        self.docker_bin_dir.mkdir(parents=True, exist_ok=True)
        self.docker_log_file = self.root.parent / (self.root.name + '-docker_calls.json')
        self.setup_fake_docker()

        (self.root / 'docs').mkdir()
        (self.root / 'bin').mkdir()
        shutil.copy2(ROOT / 'template/.gitignore', self.root / '.gitignore')
        (self.root / 'tracked.txt').write_text('before\n')
        self.git('init', '-q')
        self.git('add', 'tracked.txt')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
        self.env = dict(
            os.environ,
            PATH=str(self.docker_bin_dir) + os.pathsep + str(self.root / 'bin') + os.pathsep + os.environ['PATH'],
            DELEGATE_RETRY_DELAY='0',
            AGY_BIN=str(self.root / 'bin/agy'),
            FAKE_DOCKER_LOG=str(self.docker_log_file),
        )
        # Fake CLIs exercise the real codex/gemini relay interfaces without models.
        fake = '''#!/usr/bin/env python3
import json, os, pathlib, sys
p=pathlib.Path('.')
if '-C' in sys.argv:
    os.chdir(sys.argv[sys.argv.index('-C')+1])
    p=pathlib.Path('.')
name=pathlib.Path(sys.argv[0]).name
if '--version' in sys.argv: sys.exit(0)
c=p/'count'; n=int(c.read_text())+1 if c.exists() else 1; c.write_text(str(n))
(p/'cwd.txt').write_text(str(p.resolve()))
(p/'args.json').write_text(json.dumps(sys.argv[1:]))
(p/'stdin.txt').write_text(sys.stdin.read())
behavior=os.environ.get('BEHAVIOR','ok')
if behavior in ('change', 'partial'):
    (p/'tracked.txt').write_text('after\\n'); (p/'new.txt').write_text('new\\n')
if behavior=='retry' and n==1 or behavior=='fail':
    print('503 UNAVAILABLE network', file=sys.stderr if name=='claude' else sys.stdout); sys.exit(1)
prose=os.environ.get('AGENT_PROSE', 'answer')
if name=='npx':
    pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text(prose)
    print('503 UNAVAILABLE' if behavior=='partial' else prose)
elif name=='claude' and behavior!='plain':
    print(json.dumps({'result':prose, 'usage':{'input_tokens':12, 'output_tokens':3, 'cache_creation_input_tokens':5, 'cache_read_input_tokens':7}, 'total_cost_usd':0.01, 'num_turns':2, 'duration_ms':1250, 'is_error':behavior=='auth'})); sys.exit(1 if behavior=='auth' else 0)
elif name=='agy': print(json.dumps({'status':'SUCCESS', 'response':prose}))
else: print(prose)
sys.exit(1 if behavior=='partial' else 0)
'''
        for name in ['npx', 'agy', 'claude']:
            self.executable('bin/' + name, fake)
        self.task = self.root / 'task.md'
        self.write_task()

    def test_unanswered_question_suspends_before_verify_retry(self):
        self.write_task('claude-haiku')
        self.task.write_text(self.task.read_text().replace('verify: echo targeted', 'verify: false'))
        self.executable(
            'bin/claude',
            "#!/usr/bin/env python3\nfrom pathlib import Path\nimport json\n"
            "q = Path('.workspace/orchestration/tasks/demo/context/questions')\n"
            "q.mkdir(parents=True, exist_ok=True)\n"
            "(q / '001.md').write_text('Need an answer')\n"
            "print(json.dumps({'result': 'answer'}))\n",
        )
        result = self.run_delegate()
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(self.status()['outcome'], 'waiting_answer')
        self.assertEqual(self.status()['attempts'], 1)
        self.assertEqual(self.status()['waiting_questions'], ['001'])

    def test_question_does_not_override_success_or_block_answered_or_old_retry(self):
        self.write_task('claude-haiku')
        self.executable(
            'bin/claude',
            """#!/usr/bin/env python3
from pathlib import Path
import json, os
ctx = Path('.workspace/orchestration/tasks/demo/context')
(ctx / 'questions').mkdir(parents=True, exist_ok=True)
question = ctx / 'questions/001.md'
question.write_text('question')
if os.environ['QUESTION_CASE'] == 'old':
    os.utime(question, (1, 1))
if os.environ['QUESTION_CASE'] == 'answered':
    (ctx / 'answers').mkdir(exist_ok=True)
    (ctx / 'answers/001.md').write_text('answer')
print(json.dumps({'result': 'answer'}))
""",
        )
        result = self.run_delegate(env={'QUESTION_CASE': 'open'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['outcome'], 'done')
        self.task.write_text(self.task.read_text().replace('verify: echo targeted', 'verify: false'))
        for case in ('old', 'answered'):
            with self.subTest(case=case):
                result = self.run_delegate(env={'QUESTION_CASE': case})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(self.status()['outcome'], 'verify_failed')
                self.assertEqual(self.status()['attempts'], 2)
                self.assertNotIn('waiting_questions', self.status())

    def test_unanswered_question_suspends_transient_retry(self):
        self.write_task('claude-haiku')
        self.executable(
            'bin/claude',
            "#!/usr/bin/env python3\nfrom pathlib import Path\nimport sys\n"
            "q = Path('.workspace/orchestration/tasks/demo/context/questions')\n"
            "q.mkdir(parents=True, exist_ok=True)\n(q / '001.md').write_text('question')\n"
            "print('503 UNAVAILABLE network', file=sys.stderr)\nsys.exit(1)\n",
        )
        result = self.run_delegate()
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(self.status()['attempts'], 1)
        self.assertEqual(self.status()['outcome'], 'waiting_answer')
        rows = [json.loads(line) for line in (self.root / '.workspace/events.jsonl').read_text().splitlines()]
        outcomes = [row for row in rows if row.get('event') == 'outcome']
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]['detail']['outcome'], 'waiting_answer')

    def test_exchange_question_suspends_before_verification_retry(self):
        self.executable(
            'bin/npx',
            """#!/usr/bin/env python3
from pathlib import Path
import sys
if '--version' in sys.argv:
    sys.exit(0)
q = Path('.workspace/exchange/questions')
q.mkdir(parents=True, exist_ok=True)
(q / '001.md').write_text('question')
Path(sys.argv[sys.argv.index('-o') + 1]).write_text('answer')
""",
        )
        task = self.prepare_exchange_task(verify='false')
        result = self.run_delegate(task_rel=str(task), env={'SIMULATE_EXEC_CODE': '1'})
        self.assertEqual(result.returncode, 75, result.stderr + str(self.status()))
        self.assertEqual(self.status()['outcome'], 'waiting_answer')
        self.assertEqual(self.status()['attempts'], 1)
        self.assertEqual(self.status()['waiting_questions'], ['001'])

    def test_unanswered_question_suspends_acceptance_retry(self):
        self.write_task('claude-haiku')
        self.acceptance_setup('python3 hidden.py')
        (self.root / '.workspace/acceptance/demo/hidden.py').write_text('raise SystemExit(1)\n')
        self.executable(
            'bin/claude',
            "#!/usr/bin/env python3\nfrom pathlib import Path\nimport json\n"
            "q = Path('.workspace/orchestration/tasks/demo/context/questions')\n"
            "q.mkdir(parents=True, exist_ok=True)\n(q / '001.md').write_text('question')\n"
            "print(json.dumps({'result': 'answer'}))\n",
        )
        result = self.run_delegate()
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(self.status()['outcome'], 'waiting_answer')
        self.assertEqual(self.status()['attempts'], 1)
        self.assertEqual(self.status()['acceptance_attempts'], 1)

    def acceptance_setup(self, command):
        hidden = self.root / '.workspace/acceptance/demo'
        hidden.mkdir(parents=True)
        (hidden / 'hidden.py').write_text('SECRET_TEST_CODE = 123\n')
        self.task.write_text(
            self.task.read_text().replace('attempts: 2', 'acceptance: [hidden.py]\nacceptance_verify: ' + command + '\nattempts: 2')
        )

    def test_claude_journal_preserves_cache_and_report_matches_status(self):
        self.write_task('claude-haiku')
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.consumption_rows()[0]['tokens'].get('cache_creation'), 5)
        output = subprocess.check_output(['bash', str(self.root / 'scripts/report.sh'), '--markdown'], text=True)
        self.assertIn('| demo | claude-haiku | none | done | null | 1 |', output)
        self.assertIn('| 27 |', output)

    def test_claude_retry_without_usage_makes_task_total_incomplete(self):
        self.write_task('claude-haiku')
        self.assertEqual(self.run_delegate('retry').returncode, 0)
        self.assertFalse(self.status()['metrics'].get('tokens_complete', True))
        rows = self.consumption_rows()
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]['tokens'].get('cache_creation'))
        self.assertEqual(rows[1]['tokens'].get('cache_creation'), 5)

    def test_acceptance_correction_preserves_per_attempt_usage_and_cleans_fixtures(self):
        self.write_task('claude-haiku')
        self.acceptance_setup('python3 hidden.py')
        hidden = self.root / '.workspace/acceptance/demo/hidden.py'
        hidden.write_text(
            "from pathlib import Path\n# PRIVATE_ACCEPTANCE_SOURCE\n"
            "if Path('tracked.txt').read_text() != 'corrected':\n"
            "    print('FAIL: test_corrected_value')\n"
            "    print('AssertionError: expected corrected value')\n"
            "    raise SystemExit(1)\n"
        )
        fake = """#!/usr/bin/env python3
import json
from pathlib import Path
import sys
prompt = sys.stdin.read()
Path('stdin.txt').write_text(prompt)
if 'test_corrected_value' in prompt:
    Path('tracked.txt').write_text('corrected')
print(json.dumps({'result': 'done', 'usage': {
    'input_tokens': 12, 'cache_read_input_tokens': 7,
    'cache_creation_input_tokens': 5, 'output_tokens': 3}}))
"""
        self.executable('bin/claude', fake)
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['acceptance_attempts'], 2)
        self.assertEqual((self.root / 'tracked.txt').read_text(), 'corrected')
        self.assertFalse((self.root / 'hidden.py').exists())
        self.assertEqual([row['attempt'] for row in self.consumption_rows()], [1, 2])
        self.assertEqual([row['tokens'].get('cache_creation') for row in self.consumption_rows()], [5, 5])
        self.assertNotIn('PRIVATE_ACCEPTANCE_SOURCE', (self.root / 'stdin.txt').read_text())
        output = subprocess.check_output(['bash', str(self.root / 'scripts/report.sh'), '--markdown'], text=True)
        self.assertIn('| 54 |', output)

    def test_promoted_breaker_fixtures_are_sealed(self):
        self.write_task('claude-haiku')
        hidden = self.root / '.workspace/acceptance/demo'
        fixture = hidden / 'breaker/__breaker__/test_hidden.py'
        fixture.parent.mkdir(parents=True)
        fixture.write_text('import unittest\nclass Bug(unittest.TestCase):\n def test_bug(self): self.assertEqual(1, 2)\n')
        (hidden / 'extra.txt').write_text('__breaker__/test_hidden.py\n')
        self.executable(
            'bin/claude',
            '#!/usr/bin/env python3\nfrom pathlib import Path\n'
            'Path(' + repr(str(fixture)) + ').write_text("# replaced")\nprint("answer")\n',
        )
        result = self.run_delegate()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status()['outcome'], 'control_tampered')

    def test_acceptance_green_cleanup(self):
        self.acceptance_setup('test -f hidden.py')
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['outcome'], 'done')
        self.assertEqual(self.status()['acceptance_attempts'], 1)
        self.assertFalse((self.root / 'hidden.py').exists())
        self.assertNotIn('hidden.py', self.status()['files_changed'])

    def test_acceptance_retry_feedback(self):
        self.acceptance_setup("echo 'FAIL: test_expected'; echo 'AssertionError: expected corrected'; test $(cat count) -eq 2")
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['acceptance_attempts'], 2)
        prompt = (self.root / '.workspace/run/delegate/demo/prompt.md').read_text()
        self.assertIn('test_expected', prompt)
        self.assertNotIn('SECRET_TEST_CODE', prompt)
        self.assertFalse((self.root / 'hidden.py').exists())

    def test_acceptance_exhausted(self):
        self.acceptance_setup("echo 'FAIL: test_expected'; echo 'AssertionError: incorrect'; exit 1")
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertEqual(self.status()['outcome'], 'acceptance_failed')
        self.assertEqual(self.status()['acceptance_attempts'], 2)
        self.assertFalse((self.root / 'hidden.py').exists())

    def test_acceptance_symlink_and_collision_rejected(self):
        self.acceptance_setup('true')
        hidden = self.root / '.workspace/acceptance/demo/hidden.py'
        hidden.unlink()
        hidden.symlink_to(self.root / 'tracked.txt')
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertFalse((self.root / 'hidden.py').exists())
        hidden.unlink()
        hidden.write_text('hidden code')
        (self.root / 'hidden.py').write_text('original')
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertEqual((self.root / 'hidden.py').read_text(), 'original')

    def test_acceptance_masks_secrets_and_omits_source(self):
        (self.root / '.env').write_text('DB=privatecredential\n')
        self.acceptance_setup("echo 'FAIL: test_hidden'; echo 'AssertionError: privatecredential'; echo 'SECRET_TEST_CODE'; exit 1")
        result = self.run_delegate()
        self.assertNotEqual(result.returncode, 0)
        run = self.root / '.workspace/run/delegate/demo'
        for path in run.iterdir():
            if path.is_file():
                self.assertNotIn('privatecredential', path.read_text(errors='replace'))
        feedback = (run / 'acceptance.txt').read_text()
        self.assertNotIn('SECRET_TEST_CODE', feedback)
        self.assertIn('***', feedback)

    def test_acceptance_agent_corrects_code(self):
        self.write_task('claude-haiku')
        self.acceptance_setup('python3 hidden.py')
        (self.root / '.workspace/acceptance/demo/hidden.py').write_text(
            "from pathlib import Path\n# HIDDEN_SOURCE_MARKER\n"
            "if Path('tracked.txt').read_text() != 'corrected':\n"
            "    print('FAIL: test_corrected_value')\n"
            "    print('AssertionError: expected corrected value')\n"
            "    raise SystemExit(1)\n"
        )
        self.executable(
            'bin/claude',
            '#!/bin/sh\nprompt=$(cat)\n'
            'case "$prompt" in *test_corrected_value*) echo "$prompt" > stdin.txt; printf corrected > tracked.txt;; esac\n'
            'echo answer\n',
        )
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['acceptance_attempts'], 2)
        self.assertEqual((self.root / 'tracked.txt').read_text(), 'corrected')
        self.assertNotIn('HIDDEN_SOURCE_MARKER', (self.root / 'stdin.txt').read_text())

    def test_acceptance_exchange_uses_container(self):
        task = self.prepare_exchange_task('hidden-exchange', profile='python', verify='true')
        hidden = self.root / '.workspace/acceptance/hidden-exchange'
        hidden.mkdir(parents=True)
        (hidden / 'hidden.py').write_text('# hidden')
        task.write_text(
            task.read_text().replace('agent: codex', 'agent: codex\nacceptance: [hidden.py]\nacceptance_verify: python3 hidden.py')
        )
        result = self.run_delegate(task_rel=str(task), env={'WORKSPACE_NO_SELFCHECK': '1'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any('python3 hidden.py' in ' '.join(c) for c in self.get_docker_calls() if c[0] == 'exec'))
        self.assertFalse((self.clone('hidden-exchange') / 'hidden.py').exists())

    def prepare_hidden_task(self):
        task = self.prepare_exchange_task('hidden-check', profile='python', verify='true')
        hidden = self.root / '.workspace/acceptance/hidden-check'
        hidden.mkdir(parents=True)
        (hidden / 'hidden.py').write_text('# hidden')
        task.write_text(
            task.read_text().replace('agent: codex', 'agent: codex\nacceptance: [hidden.py]\nacceptance_verify: python3 hidden.py')
        )
        return task

    def selfcheck_codes(self, task_id):
        return [
            e['detail']['code']
            for e in (json.loads(line) for line in (self.root / '.workspace/events.jsonl').read_text().splitlines())
            if e['task_id'] == task_id and e['event'] == 'selfcheck'
        ]

    def test_selfcheck_vacuous_hidden_test_stops_before_any_attempt(self):
        task = self.prepare_hidden_task()
        result = self.run_delegate(task_rel=str(task))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('acceptance_vacuous', result.stderr)
        status = self.status('hidden-check')
        self.assertEqual(status['outcome'], 'acceptance_vacuous')
        self.assertEqual(status['attempts'], 0)
        self.assertEqual(self.selfcheck_codes('hidden-check'), [0])
        self.assertFalse((self.clone('hidden-check') / 'hidden.py').exists())
        self.assertTrue(any(c[0] == 'rm' for c in self.get_docker_calls()))

    def test_selfcheck_failing_hidden_test_lets_delegation_run(self):
        task = self.prepare_hidden_task()
        result = self.run_delegate(task_rel=str(task), env={'SIMULATE_EXEC_CODE': '1'})
        self.assertEqual(self.selfcheck_codes('hidden-check'), [1])
        self.assertGreaterEqual(self.status('hidden-check')['attempts'], 1)
        self.assertNotIn('acceptance_vacuous', result.stderr)

    def test_selfcheck_skipped_when_worktree_is_not_at_base(self):
        task = self.prepare_hidden_task()
        (self.clone('hidden-check') / 'tracked.txt').write_text('worked on')
        self.run_delegate(task_rel=str(task))
        self.assertEqual(self.selfcheck_codes('hidden-check'), [])
        self.assertNotEqual(self.status('hidden-check')['outcome'], 'acceptance_vacuous')

    def consumption_rows(self):
        return [json.loads(line) for line in (self.root / '.workspace/metrics/consumption.jsonl').read_text().splitlines()]

    def test_consumption_retry_and_write_failure(self):
        self.assertEqual(self.run_delegate('retry').returncode, 0)
        rows = self.consumption_rows()
        self.assertEqual([r['attempt'] for r in rows], [1, 2])
        self.assertTrue(all(r['task_id'] == 'demo' and r['role'] == 'execution' for r in rows))
        self.assertEqual([r['exit'] for r in rows], [1, 0])
        (self.root / '.workspace/metrics/consumption.jsonl').unlink()
        (self.root / '.workspace/metrics/consumption.jsonl').mkdir()
        self.env['BEHAVIOR'] = 'ok'
        self.assertEqual(self.run_delegate().returncode, 0)

    def test_consumption_antigravity_and_root(self):
        self.write_task('antigravity', mode='review')
        agent_root = self.root.parent / 'agent'
        agent_root.mkdir()
        result = self.run_delegate(env={'WORKSPACE_AGENT_ROOT': str(agent_root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self.consumption_rows()[0]
        self.assertEqual(row['provider'], 'antigravity')
        self.assertEqual(row['role'], 'execution')
        self.assertTrue(all(value is None for value in row['tokens'].values()))
        self.assertGreaterEqual(row['proxy']['duration_seconds'], 0)
        self.assertFalse((agent_root / '.workspace/metrics/consumption.jsonl').exists())

    def test_metrics_tokens_and_attempts(self):
        events = [
            {'type': 'thread.started', 'thread_id': 'abc'},
            {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 8}},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'answer'}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 40, 'cached_input_tokens': 10, 'output_tokens': 2}},
        ]
        self.env['AGENT_PROSE'] = '\n'.join(json.dumps(e) for e in events)
        self.assertEqual(self.run_delegate().returncode, 0)
        metrics = self.status()['metrics']
        self.assertEqual(metrics['tokens'], {'input': 140, 'cached_input': 30, 'output': 10})
        self.assertEqual(metrics['attempts'], 1)
        self.assertEqual(len(metrics['attempt_durations_seconds']), 1)
        self.assertGreaterEqual(metrics['duration_seconds'], metrics['attempt_durations_seconds'][0])
        self.assertEqual(metrics['execution_requests'], 0)
        self.assertEqual(metrics['questions'], 0)

    def test_codex_partial_retry_and_second_delegation_keep_call_identity(self):
        self.executable(
            'bin/npx',
            '''#!/usr/bin/env python3
import json, pathlib, sys
if '--version' in sys.argv: sys.exit(0)
p = pathlib.Path('count')
n = int(p.read_text()) + 1 if p.exists() else 1
p.write_text(str(n))
pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text('answer' if n > 1 else '')
print(json.dumps({'type': 'turn.started'}))
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 12, 'cached_input_tokens': 3, 'output_tokens': 4}}))
if n == 1:
    print(json.dumps({'type': 'turn.started'}))
    print(json.dumps({'type': 'turn.failed', 'error': {'message': 'network stream interrupted'}}))
    sys.exit(1)
''',
        )
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        metrics = self.status()['metrics']
        self.assertEqual(metrics['tokens'], dict(input=24, cached_input=6, output=8))
        self.assertFalse(metrics.get('tokens_complete', True))
        report = subprocess.check_output(['bash', str(self.root / 'scripts/report.sh'), '--markdown'], text=True)
        task_row = next(line for line in report.splitlines() if line.startswith('| demo |'))
        self.assertTrue(task_row.endswith('| null |'), task_row)
        # A second delegation resumes the same task: attempts restart locally,
        # while every call and its observed usage remains in the append-only log.
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = self.consumption_rows()
        self.assertEqual([(r['task_id'], r['attempt']) for r in rows], [('demo', 1), ('demo', 2), ('demo', 1)])
        self.assertEqual([r['exit'] for r in rows], [1, 0, 0])
        self.assertEqual([r.get('tokens_complete') for r in rows], [False, True, True])
        self.assertEqual(sum(r['tokens']['input'] for r in rows), 36)
        self.assertTrue(self.status()['metrics']['tokens_complete'])
        dashboard = subprocess.check_output(
            [
                'python3',
                str(self.root / 'scripts/lib/metrics.py'),
                str(self.root),
                '--json',
                '--claude-projects-dir',
                str(self.root / 'absent'),
            ],
            text=True,
        )
        counts = json.loads(dashboard)['consumption']['by_provider']['codex']
        self.assertIsNone(counts['total_tokens'])
        self.assertEqual(counts['unknown_token_calls'], 1)

    def test_codex_success_with_partial_second_turn_is_incomplete_in_reports(self):
        events = [
            {'type': 'turn.completed', 'usage': {'input_tokens': 12, 'output_tokens': 4}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 2}},
        ]
        self.env['AGENT_PROSE'] = '\n'.join(json.dumps(e) for e in events)
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        metrics = self.status()['metrics']
        self.assertEqual(metrics['tokens'], dict(input=14, output=4))
        self.assertFalse(metrics.get('tokens_complete', True))
        row = self.consumption_rows()[0]
        self.assertEqual(row['tokens']['input'], 14)
        self.assertEqual(row['tokens']['output'], 4)
        self.assertFalse(row.get('tokens_complete', True))
        report = subprocess.check_output(['bash', str(self.root / 'scripts/report.sh'), '--markdown'], text=True)
        self.assertTrue(next(line for line in report.splitlines() if line.startswith('| demo |')).endswith('| null |'))

    def test_metrics_antigravity_and_missing_usage(self):
        self.write_task('antigravity')
        self.executable("bin/agy", "#!/bin/sh\necho '{\"status\":\"SUCCESS\",\"response\":\"answer\",\"usage\":{\"total_tokens\":123}}'\n")
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertEqual(self.status()['metrics']['tokens'], {'total': 123})
        self.write_task('claude-haiku')
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertEqual(self.status()['metrics']['tokens'], {'input': 12, 'cached_input': 7, 'output': 3, 'cache_creation': 5})

    def test_metrics_retry_and_claude_usage(self):
        self.assertEqual(self.run_delegate('retry').returncode, 0)
        metrics = self.status()['metrics']
        self.assertEqual(metrics['attempts'], 2)
        self.assertEqual(len(metrics['attempt_durations_seconds']), 2)
        self.assertIsNone(metrics['tokens'])
        self.write_task('claude-haiku')
        self.env['AGENT_PROSE'] = 'answer'
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertEqual(self.status()['metrics']['tokens'], {'input': 12, 'cached_input': 7, 'output': 3, 'cache_creation': 5})

    def test_claude_consumption_models_and_answer(self):
        for model in ('haiku', 'sonnet', 'opus'):
            self.write_task('claude-' + model)
            result = self.run_delegate()
            self.assertEqual(result.returncode, 0, result.stderr)
            row = self.consumption_rows()[-1]
            self.assertEqual((row['provider'], row['model'], row['role']), ('claude', model, 'execution'))
            self.assertEqual((row['task_id'], row['attempt'], row['exit']), ('demo', 1, 0))
            self.assertEqual(row['tokens']['input'], 12)
            self.assertEqual(row['tokens']['cached_input'], 7)
            self.assertEqual(row['tokens']['output'], 3)
            self.assertEqual(row['proxy'], {'turns': 2, 'duration_seconds': 1.25})
            self.assertEqual((self.root / '.workspace/run/delegate/demo/answer.md').read_text(), 'answer')
            args = json.loads((self.root / 'args.json').read_text())
            self.assertEqual(args[args.index('--output-format') + 1], 'json')

    def test_claude_plain_fallback_and_record_failure(self):
        self.write_task('claude-haiku')
        self.assertEqual(self.run_delegate('plain').returncode, 0)
        self.assertIsNone(self.status()['metrics']['tokens'])
        self.assertTrue(all(v is None for v in self.consumption_rows()[0]['tokens'].values()))
        self.assertEqual((self.root / '.workspace/run/delegate/demo/answer.md').read_text(), 'answer\n')
        path = self.root / '.workspace/metrics/consumption.jsonl'
        path.unlink()
        path.mkdir()
        self.assertEqual(self.run_delegate().returncode, 0)

    def test_claude_review_role_and_override(self):
        self.write_task('claude-sonnet', mode='review')
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertEqual(self.consumption_rows()[-1]['role'], 'relecture')
        self.assertEqual(self.run_delegate(env={'WORKSPACE_ROLE': 'conseil'}).returncode, 0)
        self.assertEqual(self.consumption_rows()[-1]['role'], 'conseil')

    def test_claude_expired_auth_and_limits(self):
        self.write_task('claude-opus')
        for text in ('OAuth session expired', 'Usage limit reached', 'Rate limit exceeded', 'Quota exhausted'):
            self.env['AGENT_PROSE'] = text
            self.assertNotEqual(self.run_delegate('auth').returncode, 0)
            row = self.consumption_rows()[-1]
            self.assertTrue(row['quota']['limit_reached'])
            if text.startswith('OAuth'):
                self.assertEqual(row['quota']['detail'], 'authentification expirée')
            self.assertEqual(row['exit'], 1)

    def test_claude_pretty_json_and_missing_usage(self):
        self.write_task('claude-haiku')
        for payload in ({'result': 'answer', 'usage': {'input_tokens': 0, 'output_tokens': 0}}, {'result': 'answer'}):
            script = '#!/usr/bin/env python3\nprint(' + repr(json.dumps(payload, indent=2)) + ')\n'
            self.executable('bin/claude', script)
            self.assertEqual(self.run_delegate().returncode, 0)
            tokens = self.status()['metrics']['tokens']
            if 'usage' in payload:
                self.assertEqual(tokens, {'input': 0, 'output': 0, 'cached_input': None, 'cache_creation': None})
            else:
                self.assertIsNone(tokens)
            self.assertEqual((self.root / '.workspace/run/delegate/demo/answer.md').read_text(), 'answer')

    def test_claude_stderr_expired_auth(self):
        self.write_task('claude-opus')
        self.executable('bin/claude', '#!/bin/sh\necho "OAuth session expired" >&2\nexit 1\n')
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertEqual(self.consumption_rows()[0]['quota'], {'limit_reached': True, 'detail': 'authentification expirée'})

    def test_claude_success_prose_is_not_a_limit(self):
        self.write_task('claude-haiku')
        self.env['AGENT_PROSE'] = 'Explain usage limit and quota'
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertFalse(self.consumption_rows()[0]['quota']['limit_reached'])

    def test_claude_retry_records_each_attempt(self):
        self.write_task('claude-haiku')
        self.assertEqual(self.run_delegate('retry').returncode, 0)
        self.assertEqual([r['attempt'] for r in self.consumption_rows()], [1, 2])
        self.assertEqual([r['exit'] for r in self.consumption_rows()], [1, 0])

    def test_metrics_counts_broker_requests_and_questions(self):
        self.executable('scripts/check-scope.sh', '#!/bin/sh\nexit 0\n')
        self.write_task('claude-haiku')
        self.executable(
            'bin/claude',
            '#!/bin/sh\nmkdir -p .workspace/orchestration/tasks/demo/exchange .workspace/orchestration/tasks/demo/context/questions\necho {} > .workspace/orchestration/tasks/demo/exchange/request-001.json\necho question > .workspace/orchestration/tasks/demo/context/questions/001.md\necho answer\n',
        )
        self.assertEqual(self.run_delegate().returncode, 0)
        metrics = self.status()['metrics']
        self.assertEqual(metrics['execution_requests'], 1)
        self.assertEqual(metrics['questions'], 1)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True)

    def executable(self, name, content):
        path = self.root / name
        path.write_text(content)
        path.chmod(0o755)

    def write_task(self, agent='codex', mode='write', attempts=2):
        self.task.write_text(
            '---\nid: demo\nagent: %s\nmode: %s\nallowed: [tracked.txt, new.txt, count, cwd.txt, args.json, stdin.txt, order, retried, checked, baseline-marker]\nverify: echo targeted\nattempts: %s\n---\nTASK BODY\n'
            % (agent, mode, attempts)
        )

    def setup_fake_docker(self):
        fake_script = '''#!/usr/bin/env python3
import json, os, pathlib, sys

log_file = pathlib.Path(os.environ.get('FAKE_DOCKER_LOG', '__DOCKER_LOG__'))
calls = []
if log_file.exists():
    try:
        calls = json.loads(log_file.read_text())
    except Exception:
        calls = []
calls.append(sys.argv[1:])
log_file.write_text(json.dumps(calls))

if os.environ.get('SIMULATE_DOCKER_STOPPED') == '1':
    print("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?", file=sys.stderr)
    sys.exit(1)

sub = sys.argv[1] if len(sys.argv) > 1 else ''
if sub == 'info':
    print("Docker info OK")
    sys.exit(0)
elif sub == 'run':
    print("fake-container-id-12345")
    sys.exit(0)
elif sub == 'inspect':
    fmt = ''
    if '--format' in sys.argv:
        fmt = sys.argv[sys.argv.index('--format') + 1]
    started = False
    for c in calls[:-1]:
        if c and c[0] == 'run':
            started = True
        elif c and c[0] == 'rm':
            started = False
    if not started and not os.environ.get('SIMULATE_CONTAINER_STOPPED') and os.environ.get('FAKE_ASSUME_RUNNING') != '1':
        print("Error: No such object", file=sys.stderr)
        sys.exit(1)
    if fmt == '{{.State.Running}}':
        if os.environ.get('SIMULATE_CONTAINER_STOPPED') == '1':
            print("false")
        elif os.environ.get('SIMULATE_CONTAINER_ABSENT') == '1':
            print("Error: No such object", file=sys.stderr)
            sys.exit(1)
        else:
            print("true")
        sys.exit(0)
    elif fmt == '{{.State.Status}}':
        if os.environ.get('SIMULATE_CONTAINER_ABSENT') == '1':
            print("Error: No such object", file=sys.stderr)
            sys.exit(1)
        print("running")
        sys.exit(0)
    data = [{"State": {"Running": True, "Status": "running"}}]
    print(json.dumps(data))
    sys.exit(0)
elif sub == 'exec':
    code = int(os.environ.get('SIMULATE_EXEC_CODE', '0'))
    output = os.environ.get('SIMULATE_EXEC_OUTPUT', 'fake exec output')
    if output:
        print(output)
    sys.exit(code)
elif sub == 'rm':
    sys.exit(0)
else:
    sys.exit(0)
'''.replace('__DOCKER_LOG__', str(self.docker_log_file))
        docker_path = self.docker_bin_dir / 'docker'
        docker_path.write_text(fake_script, encoding='utf-8')
        docker_path.chmod(0o755)

    def get_docker_calls(self):
        if not self.docker_log_file.exists():
            return []
        try:
            return json.loads(self.docker_log_file.read_text(encoding='utf-8'))
        except Exception:
            return []

    def clone(self, task_id='demo'):
        """Dossier de travail (clone sans historique, hors du projet) d'une tâche préparée."""
        return workdir.task_worktree(self.root, task_id)

    def prepare_exchange_task(  # noqa: PLR0913
        self, task_id='demo', profile='python', isolation='exchange', verify='echo targeted', agent='codex', allowed=None
    ):
        (self.root / '.gitignore').write_text('.workspace/\ntasks/\n__pycache__/\nbin/\n')
        if profile:
            (self.root / 'project.profile').write_text(profile + '\n')
            self.git('add', 'project.profile')
        self.git('add', '-A')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'clean before prepare')

        (self.root / 'tasks').mkdir(parents=True, exist_ok=True)
        task_file = self.root / f'tasks/{task_id}.md'
        iso_field = f"isolation: {isolation}\n" if isolation else ""
        allowed = allowed or [
            'tracked.txt',
            'new.txt',
            'count',
            'cwd.txt',
            'args.json',
            'stdin.txt',
            'order',
            'retried',
            'checked',
            'baseline-marker',
        ]
        task_file.write_text(
            f"---\n"
            f"id: {task_id}\n"
            f"agent: {agent}\n"
            f"mode: write\n"
            f"{iso_field}"
            f"allowed:\n" + ''.join(f"  - {name}\n" for name in allowed) + f"verify: {verify}\n"
            f"attempts: 2\n"
            f"---\n"
            f"TASK BODY\n",
            encoding='utf-8',
        )
        # These tests exercise delegation, not the early refusal of an incomplete node image (tests/test_full_image.py).
        res = subprocess.run(
            ['bash', str(self.root / 'scripts/task-workspace.sh'), 'prepare', str(task_file), '--allow-slim-image'],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(res.returncode, 0, f"task-workspace.sh prepare failed: {res.stderr}")
        return task_file

    def run_delegate(self, behavior='ok', task_rel='../task.md', env=None):
        run_env = dict(self.env, BEHAVIOR=behavior)
        if env:
            run_env.update(env)
        return subprocess.run(
            ['bash', str(self.root / 'scripts/delegate.sh'), task_rel], cwd=self.root / 'docs', env=run_env, text=True, capture_output=True
        )

    def status(self, task_id='demo'):
        return json.loads((self.root / f'.workspace/run/delegate/{task_id}/status.json').read_text())

    def test_agent_ask_timeout_default_and_override(self):
        self.executable('bin/claude', "#!/usr/bin/env python3\nimport os\nprint('timeout=' + os.environ.get('ASK_TIMEOUT', 'missing'))\n")
        self.write_task(agent='claude-sonnet')
        self.env.pop('ASK_TIMEOUT', None)
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        answer = self.root / '.workspace/run/delegate/demo/answer.md'
        self.assertIn('timeout=1800', answer.read_text())
        result = self.run_delegate(env={'ASK_TIMEOUT': '42'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('timeout=42', answer.read_text())

    def test_fix5_diff_without_final_newline(self):
        cases = [
            (None, 'xyz'),
            (None, 'xyz\n'),
            ('abc', None),
            ('abc\n', None),
            ('abc', 'xyz'),
            ('abc\n', 'xyz'),
            ('abc', 'xyz\n'),
            ('abc\n', 'xyz\n'),
            ('abc\ncontext', 'xyz\ncontext'),
        ]
        for before, after in cases:
            with self.subTest(before=before, after=after):
                path = self.root / 'tracked.txt'
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(before)
                self.write_task('claude-haiku')
                action = 'p.unlink()' if after is None else 'p.write_text(%r)' % after
                self.executable(
                    'bin/claude',
                    '#!/usr/bin/env python3\nfrom pathlib import Path\np = Path("tracked.txt")\n' + action + '\nprint("answer")\n',
                )
                result = self.run_delegate()
                self.assertEqual(result.returncode, 0, result.stderr)
                patch = self.root / '.workspace/run/delegate/demo/diff.txt'
                diff = patch.read_text()
                check = subprocess.run(['git', 'apply', '--check', '--reverse', str(patch)], cwd=self.root, text=True, capture_output=True)
                self.assertEqual(check.returncode, 0, check.stderr + diff)
                expected = sum(text is not None and not text.endswith('\n') for text in (before, after))
                if before and after and before.splitlines()[-1] == after.splitlines()[-1]:
                    expected = 1  # An unchanged final context line has one marker.
                self.assertEqual(diff.count('\\ No newline at end of file\n'), expected)

    def test_fix5_diff_without_final_newline_is_masked(self):
        (self.root / '.env').write_text('PASSWORD=fakecredential42\n')
        (self.root / 'tracked.txt').write_text('abc fakecredential42')
        self.write_task('claude-haiku')
        self.executable('bin/claude', '#!/bin/sh\nprintf "xyz fakecredential42" > tracked.txt\necho answer\n')
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        diff = (self.root / '.workspace/run/delegate/demo/diff.txt').read_text()
        self.assertNotIn('fakecredential42', diff)
        self.assertIn('-abc ***\n\\ No newline at end of file\n', diff)
        self.assertIn('+xyz ***\n\\ No newline at end of file\n', diff)

    def test_fix4_baseline_replacement_is_rejected(self):
        self.write_task('claude-haiku')
        self.executable(
            'bin/claude',
            '#!/bin/sh\necho forbidden > outside.txt\nbash scripts/scope-baseline.sh .workspace/run/delegate/demo/task.md\necho answer\n',
        )
        result = self.run_delegate()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status()['outcome'], 'control_tampered')

    def test_fix4_indirect_controls_are_sealed_even_in_scope(self):
        for name in ('gate.sh', 'docs-check.sh', 'check-secret-tracing.sh'):
            with self.subTest(control=name):
                self.write_task('claude-haiku')
                self.task.write_text(self.task.read_text().replace('tracked.txt,', 'scripts/**, tracked.txt,'))
                self.executable('scripts/' + name, '#!/bin/sh\nexit 7\n')
                self.executable('bin/claude', '#!/bin/sh\necho replaced > scripts/' + name + '\necho answer\n')
                self.assertNotEqual(self.run_delegate().returncode, 0)
                self.assertEqual(self.status()['outcome'], 'control_tampered')

    def test_fix4_empty_unindexed_deletion_is_in_diff(self):
        shutil.rmtree(self.root / '.git')
        self.git('init', '-q')
        (self.root / 'tracked.txt').write_text('')
        self.write_task('claude-haiku')
        self.executable('bin/claude', '#!/bin/sh\nrm tracked.txt\necho answer\n')
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        diff = (self.root / '.workspace/run/delegate/demo/diff.txt').read_text()
        self.assertIn('tracked.txt', diff)
        self.assertIn('deleted file mode', diff)

    def test_fix4_unborn_unindexed_initial_content_and_privacy(self):
        shutil.rmtree(self.root / '.git')
        self.git('init', '-q')
        (self.root / '.env').write_text('PASSWORD=fakecredential42\n')
        (self.root / 'tracked.txt').write_text('dirty initial fakecredential42\n')
        (self.root / 'new.txt').write_text('initial new\n')
        self.write_task('claude-haiku')
        self.executable('bin/claude', '#!/bin/sh\nrm tracked.txt\necho modified > new.txt\necho answer\n')
        self.assertEqual(self.run_delegate().returncode, 0)
        run = self.root / '.workspace/run/delegate/demo'
        diff = (run / 'diff.txt').read_text()
        self.assertIn('-dirty initial ***', diff)
        self.assertIn('-initial new', diff)
        self.assertIn('+modified', diff)
        self.assertIn('tracked.txt', self.status()['files_changed'])
        self.assertEqual(self.git('ls-files'), '')
        for path in run.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'fakecredential42', path.read_bytes(), str(path))

    def test_root_prompt_artifacts_and_changes(self):
        (self.root / 'docs/BRIEF-AGENTS.md').write_text('COMMON BRIEF')
        (self.root / 'docs/PIEGES.md').write_text('KNOWN PITFALLS')
        result = self.run_delegate('change')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'cwd.txt').read_text(), str(self.root))
        run = self.root / '.workspace/run/delegate/demo'
        prompt = (run / 'prompt.md').read_text()
        positions = [prompt.index(x) for x in ['COMMON BRIEF', 'KNOWN PITFALLS', 'tracked.txt', 'echo targeted', 'TASK BODY']]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(str(self.root), prompt)
        self.assertEqual((run / 'answer.md').read_text(), 'answer')
        self.assertIn('+after', (run / 'diff.txt').read_text())
        self.assertIn('+new', (run / 'diff.txt').read_text())
        self.assertIn('event', (run / 'events').read_text())
        status = self.status()
        self.assertEqual(status['outcome'], 'done')
        self.assertIn('tracked.txt', status['files_changed'])
        self.assertIn('new.txt', status['files_changed'])
        self.assertEqual(status['attempts'], 1)
        self.assertEqual(status['verify']['exit'], 0)
        self.assertTrue(status['started_at'] <= status['ended_at'])

    def test_unborn_repository_diff_and_status(self):
        shutil.rmtree(self.root / '.git')
        self.git('init', '-q')
        self.git('add', 'tracked.txt')
        self.write_task('claude-haiku')
        self.executable('bin/claude', '#!/bin/sh\nset -e\ngit rm -q -f tracked.txt\necho new > new.txt\ngit add new.txt\necho answer\n')
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['outcome'], 'done')
        self.assertEqual(self.status()['files_changed'], ['new.txt', 'tracked.txt'])
        diff = (self.root / '.workspace/run/delegate/demo/diff.txt').read_text()
        self.assertIn('-before', diff)
        self.assertIn('+new', diff)
        self.assertNotEqual(subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=self.root, capture_output=True).returncode, 0)

    def test_unborn_repository_includes_modified_and_untracked_files(self):
        shutil.rmtree(self.root / '.git')
        self.git('init', '-q')
        self.git('add', 'tracked.txt')
        result = self.run_delegate('change')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['outcome'], 'done')
        self.assertIn('tracked.txt', self.status()['files_changed'])
        self.assertIn('new.txt', self.status()['files_changed'])
        diff = (self.root / '.workspace/run/delegate/demo/diff.txt').read_text()
        self.assertIn('-before', diff)
        self.assertIn('+after', diff)
        self.assertIn('+new', diff)

    def test_auth_prose_does_not_block_delegation(self):
        self.task.write_text(self.task.read_text() + 'un en-tête « Bearer » suivi d un mot; Bearer suivi')
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['outcome'], 'done')

    def test_successful_agent_prose_is_not_a_transient_error(self):
        for agent in ('codex', 'antigravity', 'claude-haiku'):
            for word in ('network', '503', 'interrupted', 'stream was interrupted'):
                with self.subTest(agent=agent, word=word):
                    self.write_task(agent)
                    result = self.run_delegate('change', env={'AGENT_PROSE': word})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.status()['outcome'], 'done')
                    self.assertEqual(self.status()['attempts'], 1)

    def test_missing_optional_files_are_reported(self):
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertIn('docs/BRIEF-AGENTS.md', self.status()['warnings'][0])
        self.assertIn('docs/PIEGES.md', ' '.join(self.status()['warnings']))

    def test_final_error_envelope_is_transient_even_with_zero_exit(self):
        self.write_task('antigravity')
        self.executable(
            'scripts/gemini.sh',
            """#!/bin/sh
printf 'after\\n' > tracked.txt
printf 'partial answer' > "$3"
printf '%s\\n' '{"status":"ERROR","error":"503 UNAVAILABLE"}' > relay-events
printf 'events in %s/relay-events\\n' "$PWD"
""",
        )
        self.task.write_text(self.task.read_text().replace('allowed: [', 'allowed: [relay-events, '))
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['outcome'], 'done_with_warning')
        self.assertEqual(self.status()['attempts'], 1)

    def test_transient_partial_work_is_warning(self):
        self.assertEqual(self.run_delegate('partial').returncode, 0)
        self.assertEqual(self.status()['outcome'], 'done_with_warning')
        self.assertEqual(self.status()['attempts'], 1)

    def test_transient_retry_and_limit(self):
        self.assertEqual(self.run_delegate('retry').returncode, 0)
        self.assertEqual(self.status()['attempts'], 2)
        result = self.run_delegate('fail')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status()['outcome'], 'failed')
        self.assertEqual(self.status()['attempts'], 2)
        self.assertEqual((self.root / '.workspace/run/delegate/demo/answer.md').read_text(), '')

    def test_claude_stdin_model_and_permissions(self):
        for agent, mode, permission in [
            ('claude-haiku', 'write', 'acceptEdits'),
            ('claude-sonnet', 'review', 'plan'),
            ('claude-opus', 'write', 'acceptEdits'),
        ]:
            self.write_task(agent, mode)
            self.assertEqual(self.run_delegate().returncode, 0)
            args = json.loads((self.root / 'args.json').read_text())
            self.assertEqual(
                args,
                ['-p', '--output-format', 'json', '--model', agent.split('-')[1], '--permission-mode', permission, '--max-turns', '40'],
            )
            self.assertIn('TASK BODY', (self.root / 'stdin.txt').read_text())

    def test_scope_then_verify_and_outcomes(self):
        self.executable('scripts/check-scope.sh', '#!/bin/sh\necho scope >> order\necho scope-result\nexit "${SCOPE_EXIT:-0}"\n')
        self.executable('scripts/verify-task.sh', '#!/bin/sh\necho verify >> order\necho verify-result\nexit "${VERIFY_EXIT:-0}"\n')
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertEqual((self.root / 'order').read_text(), 'scope\nverify\nscope\n')
        self.assertEqual(self.status()['verify'], {'command': 'echo targeted', 'exit': 0})
        self.env['VERIFY_EXIT'] = '7'
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertEqual(self.status()['outcome'], 'verify_failed')
        self.assertEqual(self.status()['verify']['exit'], 7)
        self.env['SCOPE_EXIT'] = '1'
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertEqual(self.status()['outcome'], 'out_of_scope')

    def test_antigravity_retries_with_verification_output(self):
        self.write_task('antigravity')
        self.executable('scripts/verify-task.sh', '#!/bin/sh\nif [ ! -f retried ]; then touch retried; echo FIX THIS FAILURE; exit 1; fi\n')
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['attempts'], 2)
        args = json.loads((self.root / 'args.json').read_text())
        self.assertIn('FIX THIS FAILURE', args[args.index('-p') + 1])

    def test_any_agent_retries_with_verification_output(self):
        self.write_task('claude-haiku')
        self.executable('scripts/verify-task.sh', '#!/bin/sh\nif [ ! -f retried ]; then touch retried; echo FIX THIS LINT; exit 1; fi\n')
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['attempts'], 2)
        self.assertEqual(self.status()['outcome'], 'done')
        self.assertIn('FIX THIS LINT', (self.root / '.workspace/run/delegate/demo/prompt.md').read_text())

    def test_invalid_task_does_not_call_relay(self):
        self.task.write_text('---\nagent: codex\nallowed: []\n---\nbody')
        result = self.run_delegate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('id', result.stderr)
        self.assertFalse((self.root / 'count').exists())

    def test_preexisting_dirty_file_is_not_claimed(self):
        (self.root / 'tracked.txt').write_text('preexisting\n')
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertNotIn('tracked.txt', self.status()['files_changed'])

    def test_staged_deletion_is_saved_in_diff(self):
        self.executable('bin/claude', '#!/bin/sh\ngit rm -q tracked.txt\necho answer\n')
        self.write_task('claude-haiku')
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertIn('tracked.txt', self.status()['files_changed'])
        self.assertIn('-before', (self.root / '.workspace/run/delegate/demo/diff.txt').read_text())

    def test_antigravity_verification_budget_is_respected(self):
        self.write_task('antigravity', attempts=1)
        self.executable('scripts/verify-task.sh', '#!/bin/sh\necho still-broken\nexit 4\n')
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertEqual(self.status()['attempts'], 1)
        self.assertEqual(self.status()['outcome'], 'verify_failed')

    def test_antigravity_permission_refusal_keeps_its_cause(self):
        self.write_task('antigravity', attempts=2)
        self.executable(
            'scripts/gemini.sh', "#!/bin/sh\necho called >> calls\nprintf 'DENIED: read_file\\nDENIED: ViewFile\\n' >&2\nexit 2\n"
        )
        self.executable('scripts/verify-task.sh', '#!/bin/sh\ntouch verified\nexit 1\n')
        self.assertNotEqual(self.run_delegate().returncode, 0)
        status = self.status()
        self.assertEqual(status['outcome'], 'permission_denied')
        self.assertEqual(status['denied_actions'], ['read_file', 'ViewFile'])
        self.assertEqual(status['attempts'], 1)
        self.assertEqual((self.root / 'calls').read_text().count('called'), 1)
        self.assertFalse((self.root / 'verified').exists())

    def test_antigravity_relay_error_without_denial_keeps_failed_outcome(self):
        self.write_task('antigravity', attempts=2)
        self.executable('scripts/gemini.sh', "#!/bin/sh\necho 'boom' >&2\nexit 2\n")
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertEqual(self.status()['outcome'], 'failed')
        self.assertNotIn('denied_actions', self.status())
        self.assertEqual(self.status()['attempts'], 1)

    def test_denied_lines_only_count_for_antigravity(self):
        self.write_task('claude-haiku', attempts=2)
        self.executable('bin/claude', '#!/bin/sh\necho "DENIED: ViewFile" >&2\nexit 2\n')
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertEqual(self.status()['outcome'], 'failed')
        self.assertNotIn('denied_actions', self.status())

    def test_nontransient_error_is_not_retried(self):
        self.write_task('claude-haiku')
        self.executable('bin/claude', '#!/bin/sh\necho fatal >&2\nexit 2\n')
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertEqual(self.status()['attempts'], 1)
        self.assertEqual(self.status()['outcome'], 'failed')

    def test_scope_baseline_is_captured_before_relay(self):
        self.executable(
            'scripts/scope-baseline.sh',
            '#!/bin/sh\ntest ! -f count || exit 5\necho baseline > baseline-marker\nmkdir -p .workspace/run/delegate/demo\necho null > .workspace/run/delegate/demo/baseline.json\n',
        )
        self.executable('scripts/check-scope.sh', '#!/bin/sh\ntest -f baseline-marker\n')
        self.assertEqual(self.run_delegate().returncode, 0)
        self.assertEqual((self.root / 'baseline-marker').read_text(), 'baseline\n')

    def test_checks_run_even_after_relay_failure(self):
        self.executable('scripts/check-scope.sh', '#!/bin/sh\necho scope > checked\n')
        self.assertNotEqual(self.run_delegate('fail').returncode, 0)
        self.assertEqual((self.root / 'checked').read_text(), 'scope\n')

    def test_secret_prompt_refused_with_origin(self):
        secret = 'sk-testcredential123'
        (self.root / 'docs/PIEGES.md').write_text('safe\n' + secret + '\n')
        result = self.run_delegate()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status()['outcome'], 'secret_in_prompt')
        self.assertIn('docs/PIEGES.md:2', result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertFalse((self.root / 'count').exists())
        self.assertFalse((self.root / '.workspace/run/delegate/demo/prompt.md').exists())

    def test_known_value_masked_in_artifacts(self):
        (self.root / '.env').write_text('DB=privatecredential\n')
        self.write_task('claude-haiku')
        self.executable(
            'bin/claude', '#!/bin/sh\necho privatecredential\necho "Bearer token abc123def" >&2\necho privatecredential > tracked.txt\n'
        )
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.root / '.workspace/run/delegate/demo'
        for name in ('answer.md', 'events', 'diff.txt'):
            text = (run / name).read_text()
            self.assertNotIn('privatecredential', text)
            self.assertNotIn('abc123def', text)
            self.assertIn('***', text)

    def test_env_example_not_used_for_prompt_filter(self):
        (self.root / '.env.example').write_text('DB=examplecredential\n')
        self.task.write_text(self.task.read_text() + 'examplecredential\n')
        self.assertEqual(self.run_delegate().returncode, 0)

    def test_retry_prompt_is_filtered(self):
        self.write_task('antigravity')
        self.executable('scripts/verify-task.sh', '#!/bin/sh\necho "Bearer token abc123def"\nexit 1\n')
        result = self.run_delegate()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status()['outcome'], 'secret_in_prompt')
        self.assertEqual((self.root / 'count').read_text(), '1')

    def test_security_missing_controls(self):
        (self.root / 'scripts/check-scope.sh').unlink(missing_ok=True)
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertFalse((self.root / 'count').exists())

    def test_security_verify_output(self):
        (self.root / '.env').write_text('DB=privatecredential\n')
        self.executable('scripts/verify-task.sh', '#!/bin/sh\necho privatecredential\n')
        self.run_delegate()
        for path in (self.root / '.workspace/run').rglob('*'):
            if path.is_file():
                self.assertNotIn('privatecredential', path.read_text(errors='replace'), str(path))

    def test_security_verify_scope(self):
        self.executable('scripts/check-scope.sh', '#!/bin/sh\ntest ! -f outside.txt\n')
        self.executable('scripts/verify-task.sh', '#!/bin/sh\ntouch outside.txt\n')
        self.assertNotEqual(self.run_delegate().returncode, 0)

    def test_security_tampered_contract_and_controls(self):
        for target in ('task.md', 'scripts/check-scope.sh', 'scripts/verify-task.sh', 'scripts/scope-baseline.sh'):
            with self.subTest(target=target):
                for name in ('check-scope.sh', 'verify-task.sh', 'scope-baseline.sh'):
                    shutil.copy2(ROOT / 'template/scripts' / name, self.root / 'scripts' / name)
                self.write_task('claude-haiku')
                self.executable('bin/claude', '#!/bin/sh\necho changed >> ' + target + '\necho answer\n')
                self.assertNotEqual(self.run_delegate().returncode, 0)
                self.assertEqual(self.status()['outcome'], 'control_tampered')

    def test_security_artifact_symlink(self):
        outside = self.root / 'outside'
        outside.mkdir()
        run = self.root / '.workspace/run/delegate'
        run.mkdir(parents=True)
        (run / 'demo').symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])

    def test_security_ignored_sensitive_and_chmod(self):
        for filename, command in [('.env', 'echo changed > .env'), ('outside.sh', 'chmod 755 outside.sh')]:
            with self.subTest(filename=filename):
                (self.root / filename).write_text('original\n')
                (self.root / filename).chmod(0o600)
                (self.root / '.gitignore').write_text('.workspace/\n.env\n')
                self.git('add', 'outside.sh') if filename == 'outside.sh' else None
                self.write_task('claude-haiku')
                self.executable('bin/claude', '#!/bin/sh\n' + command + '\necho answer\n')
                self.assertNotEqual(self.run_delegate().returncode, 0)
                self.assertEqual(self.status()['outcome'], 'out_of_scope')

    def test_security_external_link(self):
        outside = self.root.parent / 'external'
        outside.write_text('untouched')
        (self.root / 'config').symlink_to(outside)
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertFalse((self.root / 'count').exists())

    def test_security_recursive_short_and_escaped_secrets(self):
        directory = self.root / 'apps/api'
        directory.mkdir(parents=True)
        (directory / '.env').write_text('PASSWORD=abc12\nOTHER="abc\\"def"\n')
        for value in ('abc12', 'abc"def'):
            self.write_task()
            self.task.write_text(self.task.read_text() + value)
            result = self.run_delegate()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.status()['outcome'], 'secret_in_prompt')
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_security_modes_and_relay_events(self):
        (self.root / '.env').write_text('DB=privatecredential\n')
        self.executable(
            'bin/npx',
            '#!/bin/sh\ncase " $* " in *" --version "*) exit 0;; esac\nwhile [ "${1:-}" != -o ]; do [ "$#" -gt 0 ] || exit 2; shift; done\necho answer > "$2"\necho privatecredential\n',
        )
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (self.root / '.workspace/run').rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600, str(path))
            if path.is_file():
                self.assertNotIn('privatecredential', path.read_text(errors='replace'))

    def test_security_interruption(self):
        import signal
        import time

        self.write_task('claude-haiku')
        self.executable('bin/claude', '#!/bin/sh\necho privatecredential\nsleep 30\n')
        (self.root / '.env').write_text('DB=privatecredential\n')
        proc = subprocess.Popen(
            ['bash', str(self.root / 'scripts/delegate.sh'), str(self.task)], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        status = self.root / '.workspace/run/delegate/demo/status.json'
        deadline = time.monotonic() + 5
        while not status.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(json.loads(status.read_text())['outcome'], 'running')
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=5)
        self.assertEqual(self.status()['outcome'], 'interrupted')
        self.assertNotIn(b'privatecredential', out + err)

    def spawn_agent(self, script, attempts=1, **env):
        """Start delegate.sh with a fake claude; return (process, main pid, background pid) once both exist."""
        import time

        pids = self.root.parent / 'agent-pids'
        shutil.rmtree(pids, ignore_errors=True)
        pids.mkdir()
        self.write_task('claude-haiku', attempts=attempts)
        self.executable('bin/claude', script.replace('PIDS', str(pids)))
        proc = subprocess.Popen(
            ['bash', str(self.root / 'scripts/delegate.sh'), str(self.task)],
            env=dict(self.env, **env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        found = []
        for name in ('main', 'bg'):
            deadline = time.monotonic() + 10
            while not (pids / name).is_file() or not (pids / name).read_text().strip():
                self.assertLess(time.monotonic(), deadline, name + ' pid file missing')
                time.sleep(0.02)
            found.append(int((pids / name).read_text()))
            self.addCleanup(lambda pid=found[-1]: gone(pid, 0) or os.kill(pid, 9))
        return proc, *found

    def test_agent_left_in_background_does_not_block_delegation(self):
        proc, main, background = self.spawn_agent(
            '#!/bin/sh\necho $$ > PIDS/main\nsleep 300 &\necho $! > PIDS/bg\necho \'{"result":"answer"}\'\n'
        )
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(gone(background, 5))

    def test_agent_processes_do_not_survive_delegate_signals(self):
        import signal

        script = '#!/bin/sh\necho $$ > PIDS/main\nsleep 300 &\necho $! > PIDS/bg\nwait\n'
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGKILL):
            with self.subTest(signal=sig.name):
                proc, main, background = self.spawn_agent(script)
                proc.send_signal(sig)
                proc.communicate(timeout=10)
                if sig != signal.SIGKILL:
                    self.assertEqual((proc.returncode, self.status()['outcome']), (130, 'interrupted'))
                self.assertTrue(gone(main, 10), 'agent still running')
                self.assertTrue(gone(background, 10), 'agent child still running')

    def test_agent_timeout_ends_the_attempt_with_code_124(self):
        import time

        started = time.monotonic()
        proc, main, background = self.spawn_agent(
            '#!/bin/sh\necho $$ > PIDS/main\nsleep 300 &\necho $! > PIDS/bg\nwait\n', DELEGATE_AGENT_TIMEOUT='2'
        )
        proc.communicate(timeout=30)
        self.assertLess(time.monotonic() - started, 30)
        self.assertNotEqual(proc.returncode, 0)
        events = [json.loads(line) for line in (self.root / '.workspace/events.jsonl').read_text().splitlines()]
        finished = [e['detail'] for e in events if e['event'] == 'attempt_finished']
        self.assertEqual(finished, [{'attempt': 1, 'code': 124, 'max_turns_reached': False}])
        self.assertIn("délai de l'agent dépassé (2 s)", (self.root / '.workspace/run/delegate/demo/events').read_text())
        self.assertTrue(gone(main, 5) and gone(background, 5))

    def test_agent_timeout_variable_is_validated(self):
        for value in ('', '0'):
            with self.subTest(value=value):
                self.assertEqual(self.run_delegate(env={'DELEGATE_AGENT_TIMEOUT': value}).returncode, 0)
        for value in ('-1', 'abc', 'nan'):
            with self.subTest(value=value):
                result = self.run_delegate(env={'DELEGATE_AGENT_TIMEOUT': value})
                self.assertEqual(result.returncode, 64)

    def test_security_large_output(self):
        self.write_task('claude-haiku')
        self.executable('bin/claude', '#!/usr/bin/env python3\nprint("a" * (3 * 1024 * 1024))\n')
        self.assertEqual(self.run_delegate().returncode, 0)
        answer = self.root / '.workspace/run/delegate/demo/answer.md'
        self.assertLess(answer.stat().st_size, 2 * 1024 * 1024 + 100)
        self.assertIn('[TRUNCATED]', answer.read_text())

    def test_security_ignored_external_directory_link(self):
        external = self.root.parent / 'external-dir'
        external.mkdir()
        (self.root / '.gitignore').write_text('config\n.workspace/\n')
        (self.root / 'config').symlink_to(external, target_is_directory=True)
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertFalse((self.root / 'count').exists())

    def test_security_all_control_outputs_masked(self):
        (self.root / '.env').write_text('DB=privatecredential\n')
        for name in ('scope-baseline.sh', 'check-scope.sh', 'verify-task.sh'):
            self.executable(
                'scripts/' + name,
                '#!/bin/sh\necho privatecredential\necho privatecredential >&2\n'
                + (
                    "mkdir -p .workspace/run/delegate/demo\necho null > .workspace/run/delegate/demo/baseline.json\n"
                    if name == 'scope-baseline.sh'
                    else ''
                ),
            )
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('privatecredential', result.stdout + result.stderr)
        for path in (self.root / '.workspace/run').rglob('*'):
            if path.is_file():
                self.assertNotIn('privatecredential', path.read_text(errors='replace'))

    def test_security_each_missing_or_deleted_control(self):
        for name in ('check-scope.sh', 'scope-baseline.sh', 'verify-task.sh'):
            with self.subTest(name=name):
                for n in ('check-scope.sh', 'scope-baseline.sh', 'verify-task.sh'):
                    shutil.copy2(ROOT / 'template/scripts' / n, self.root / 'scripts' / n)
                self.write_task('claude-haiku')
                self.executable('bin/claude', '#!/bin/sh\nrm scripts/' + name + '\necho answer\n')
                self.assertNotEqual(self.run_delegate().returncode, 0)
                self.assertEqual(self.status()['outcome'], 'control_tampered')
                self.assertNotEqual(self.run_delegate().returncode, 0)

    def test_security_answer_symlink_rejected(self):
        self.write_task('claude-haiku')
        (self.root / 'outside').write_text('untouched')
        self.executable(
            'bin/claude',
            '#!/bin/sh\nrm .workspace/run/delegate/demo/answer.md\nln -s "$PWD/outside" .workspace/run/delegate/demo/answer.md\necho overwritten\n',
        )
        self.assertNotEqual(self.run_delegate().returncode, 0)
        self.assertEqual((self.root / 'outside').read_text(), 'untouched')

    def test_security_gate_output_via_delegate(self):
        self.task.write_text(self.task.read_text().replace('verify: echo targeted\n', ''))
        (self.root / '.env').write_text('DB=privatecredential\n')
        self.executable('scripts/gate.sh', '#!/bin/sh\necho privatecredential\necho privatecredential >&2\n')
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('privatecredential', result.stdout + result.stderr)
        self.assertNotIn('privatecredential', (self.root / '.workspace/run/delegate/demo/verify.txt').read_text())

    def test_security_baseline_error_diagnostics(self):
        (self.root / '.env').write_text('DB=privatecredential\n')
        self.executable('scripts/scope-baseline.sh', '#!/bin/sh\necho privatecredential >&2\nexit 1\n')
        result = self.run_delegate()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('privatecredential', result.stdout + result.stderr)
        self.assertEqual(self.status()['outcome'], 'failed')
        self.assertNotIn('privatecredential', (self.root / '.workspace/run/delegate/demo/baseline.txt').read_text())

    def test_security_large_tracked_file_and_diff(self):
        with (self.root / 'tracked.txt').open('wb') as stream:
            stream.write(b'x' * (5 * 1024 * 1024))
        self.git('add', 'tracked.txt')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'large')
        self.write_task('claude-haiku')
        self.executable(
            'bin/claude',
            '#!/usr/bin/env python3\nfrom pathlib import Path\nPath("tracked.txt").write_bytes(b"y" * (5 * 1024 * 1024))\nprint("answer")\n',
        )
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        diff = self.root / '.workspace/run/delegate/demo/diff.txt'
        self.assertLess(diff.stat().st_size, 2 * 1024 * 1024 + 100)
        self.assertIn('[TRUNCATED]', diff.read_text())

    def test_isolation_none_nominal_and_status(self):
        result = self.run_delegate()
        self.assertEqual(result.returncode, 0, result.stderr)
        st = self.status('demo')
        self.assertEqual(st['outcome'], 'done')
        self.assertEqual(st['isolation'], 'none')
        self.assertTrue(bool(st.get('isolation_reason')))
        self.assertEqual(self.get_docker_calls(), [])

    def test_isolation_full_refused_with_outcome_isolation_unavailable(self):
        self.task.write_text(self.task.read_text().replace('attempts: 2\n', 'attempts: 2\nisolation: full\n'))
        result = self.run_delegate()
        self.assertNotEqual(result.returncode, 0)
        st = self.status('demo')
        self.assertEqual(st['outcome'], 'isolation_unavailable')
        self.assertEqual(st['isolation'], 'full')
        self.assertTrue(bool(st.get('isolation_reason')))
        self.assertEqual(self.get_docker_calls(), [])

    def test_isolation_exchange_unprepared_task_rejected(self):
        self.task.write_text(self.task.read_text().replace('attempts: 2\n', 'attempts: 2\nisolation: exchange\n'))
        result = self.run_delegate()
        self.assertNotEqual(result.returncode, 0)
        st = self.status('demo')
        self.assertEqual(st['outcome'], 'task_not_prepared')
        self.assertEqual(st['isolation'], 'exchange')
        self.assertTrue(bool(st.get('isolation_reason')))
        self.assertIn('task-workspace.sh', result.stderr)
        self.assertEqual(self.get_docker_calls(), [])

    def test_exchange_claude_gets_the_executor_hook_and_the_workshop_prompt(self):
        task_file = self.prepare_exchange_task('guarded', agent='claude-haiku')
        result = self.run_delegate(task_rel=str(task_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        worktree = self.clone('guarded')
        args = json.loads((worktree / 'args.json').read_text())
        self.assertEqual(args[args.index('--disallowedTools') + 1], 'WebFetch,WebSearch,Agent')
        self.assertEqual(args[args.index('--allowedTools') + 1], 'Bash(bash scripts/run-in-sandbox.sh:*),Bash(bash scripts/ask.sh:*)')
        command = json.loads(args[args.index('--settings') + 1])['hooks']['PreToolUse'][0]['hooks'][0]['command']
        parts = shlex.split(command)
        # delegate.py is started through python.sh (python3.14 first): same interpreter, possibly another name (python3 in the image).
        self.assertEqual(os.path.realpath(parts[0]), os.path.realpath(sys.executable))
        self.assertEqual(parts[1:], [str(self.root / 'scripts/lib/hooks_guard.py'), 'executor', str(self.root), str(worktree), 'guarded'])
        prompt = (self.root / '.workspace/run/delegate/guarded/prompt.md').read_text()
        self.assertTrue(prompt.startswith("Atelier de l'exécutant"), prompt[:80])
        self.assertIn('Ton atelier est ce worktree et son conteneur, rien d\'autre.', prompt)
        self.assertIn('Racine absolue du worktree : ' + str(worktree), prompt)
        self.assertNotIn('Racine absolue du projet', prompt)

    def test_exchange_codex_keeps_no_hook_but_gets_the_workshop_prompt(self):
        task_file = self.prepare_exchange_task('plain-codex')
        self.assertEqual(self.run_delegate(task_rel=str(task_file)).returncode, 0)
        worktree = self.clone('plain-codex')
        self.assertNotIn('--settings', json.loads((worktree / 'args.json').read_text()))
        prompt = (self.root / '.workspace/run/delegate/plain-codex/prompt.md').read_text()
        self.assertTrue(prompt.startswith("Atelier de l'exécutant"), prompt[:80])
        self.assertNotIn('Racine absolue du projet', prompt)

    def test_isolation_exchange_nominal_start_before_agent_exec_verify_and_stop(self):
        task_file = self.prepare_exchange_task('demo-exch', profile='python', isolation='exchange', verify='pytest')
        result = self.run_delegate(task_rel=str(task_file))
        self.assertEqual(result.returncode, 0, result.stderr)

        st = self.status('demo-exch')
        self.assertEqual(st['outcome'], 'done')
        self.assertEqual(st['isolation'], 'exchange')
        self.assertTrue(bool(st.get('isolation_reason')))

        prompt_text = (self.root / '.workspace/run/delegate/demo-exch/prompt.md').read_text()
        self.assertIn('bash scripts/run-in-sandbox.sh -- <commande>', prompt_text)
        self.assertIn('bash scripts/ask.sh "<question>"', prompt_text)

        calls = self.get_docker_calls()
        run_indices = [i for i, c in enumerate(calls) if c and c[0] == 'run']
        self.assertTrue(run_indices)
        run_idx = run_indices[0]
        run_call = calls[run_idx]
        self.assertIn('-e', run_call)
        e_idx = run_call.index('-e')
        self.assertEqual(run_call[e_idx + 1], 'HOME=/tmp')

        exec_indices = [i for i, c in enumerate(calls) if c and c[0] == 'exec']
        self.assertTrue(exec_indices)
        exec_idx = exec_indices[0]
        self.assertGreater(exec_idx, run_idx)
        exec_call = calls[exec_idx]
        self.assertIn('pytest', ' '.join(exec_call))

        rm_indices = [i for i, c in enumerate(calls) if c and c[0] == 'rm']
        self.assertTrue(rm_indices)
        rm_idx = rm_indices[-1]
        self.assertGreater(rm_idx, exec_idx)

    def antigravity_files_task(self, task_id, outside):
        """Exchange task for Antigravity whose fake relay answers with files only, as the real one does."""
        self.executable(
            'scripts/gemini.sh',
            "#!/bin/sh\n"
            "out=.workspace/antigravity/out\n"
            "mkdir -p \"$out/src\"\n"
            "printf 'print(1)\\n' > \"$out/src/app.py\"\n"
            + ("printf 'x' > \"$out/outside.txt\"\n" if outside else "")
            + "echo called >> .workspace/antigravity/calls\n"
            "printf 'done' > \"$3\"\n",
        )
        return self.prepare_exchange_task(task_id, agent='antigravity', allowed=['src/app.py'])

    def test_antigravity_files_mode_applies_scope_files_and_refuses_the_rest(self):
        task_file = self.antigravity_files_task('agy-out', outside=True)
        result = self.run_delegate(task_rel=str(task_file))
        self.assertEqual(result.returncode, 1, result.stderr)
        st = self.status('agy-out')
        worktree = self.clone('agy-out')
        self.assertEqual(st['outcome'], 'out_of_scope')
        self.assertEqual(st['applied_files'], ['src/app.py'])
        self.assertEqual(st['refused_files'], [{'path': 'outside.txt', 'reason': 'out_of_scope'}])
        self.assertEqual((worktree / 'src/app.py').read_text(), 'print(1)\n')
        self.assertFalse((worktree / 'outside.txt').exists())
        self.assertEqual(st['attempts'], 1)
        self.assertEqual((worktree / '.workspace/antigravity/calls').read_text().count('called'), 1)
        self.assertEqual(st['verify'], {'command': 'echo targeted', 'exit': 0})
        self.assertIn('echo targeted', ' '.join(' '.join(c) for c in self.get_docker_calls() if c and c[0] == 'exec'))

    def test_antigravity_files_mode_done_and_prompt_has_no_command(self):
        task_file = self.antigravity_files_task('agy-done', outside=False)
        result = self.run_delegate(task_rel=str(task_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        st = self.status('agy-done')
        self.assertEqual(st['outcome'], 'done')
        self.assertEqual(st['applied_files'], ['src/app.py'])
        self.assertEqual(st['refused_files'], [])
        self.assertIn('src/app.py', st['files_changed'])
        prompt_text = (self.root / '.workspace/run/delegate/agy-done/prompt.md').read_text()
        self.assertIn('.workspace/antigravity/out', prompt_text)
        self.assertIn('- src/app.py', prompt_text)
        for forbidden in ('run-in-sandbox', 'ask.sh', 'scripts/'):
            self.assertNotIn(forbidden, prompt_text)

    def test_other_agents_keep_the_exchange_prompt_and_status_fields(self):
        task_file = self.prepare_exchange_task('codex-plain')
        self.assertEqual(self.run_delegate(task_rel=str(task_file)).returncode, 0)
        st = self.status('codex-plain')
        self.assertNotIn('applied_files', st)
        self.assertNotIn('refused_files', st)

    def test_exchange_verify_then_types_and_lint_use_container(self):
        (self.root / 'tsconfig.json').write_text('{}')
        (self.root / 'package.json').write_text('{"scripts":{"lint":"fake"}}')
        task_file = self.prepare_exchange_task('demo-types', profile='node')
        result = self.run_delegate(task_rel=str(task_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = [c for c in self.get_docker_calls() if c and c[0] == 'exec']
        self.assertEqual(len(commands), 3, commands)
        self.assertIn('echo targeted', ' '.join(commands[0]))
        self.assertIn('npx tsc -b', ' '.join(commands[1]))
        self.assertIn('npm run lint', ' '.join(commands[2]))
        self.assertIn('package.json', ' '.join(commands[2]))

    def test_isolation_exchange_stops_container_when_agent_fails(self):
        task_file = self.prepare_exchange_task('demo-fail', profile='python', isolation='exchange')
        result = self.run_delegate(behavior='fail', task_rel=str(task_file))
        self.assertNotEqual(result.returncode, 0)

        st = self.status('demo-fail')
        self.assertEqual(st['outcome'], 'failed')

        calls = self.get_docker_calls()
        run_idx = next((i for i, c in enumerate(calls) if c and c[0] == 'run'), None)
        rm_idx = next((i for i, c in enumerate(calls) if c and c[0] == 'rm'), None)
        self.assertIsNotNone(run_idx, "docker run aurait dû être appelé")
        self.assertIsNotNone(rm_idx, "docker rm aurait dû être appelé pour arrêter le conteneur")
        self.assertGreater(rm_idx, run_idx)

    def test_isolation_exchange_stops_container_on_interruption(self):
        import signal
        import time

        task_file = self.prepare_exchange_task('demo-intr', profile='python', isolation='exchange')
        self.write_task('claude-haiku')
        self.executable('bin/claude', '#!/bin/sh\nsleep 30\n')
        proc = subprocess.Popen(
            ['bash', str(self.root / 'scripts/delegate.sh'), str(task_file)], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        status_file = self.root / '.workspace/run/delegate/demo-intr/status.json'
        # Interrompre seulement une fois le conteneur réellement démarré (sinon il n'y a rien à arrêter).
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if status_file.exists() and any(c and c[0] == 'run' for c in self.get_docker_calls()):
                break
            time.sleep(0.02)
        self.assertTrue(any(c and c[0] == 'run' for c in self.get_docker_calls()), 'le conteneur aurait dû démarrer')
        time.sleep(0.2)
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=5)
        st = self.status('demo-intr')
        self.assertEqual(st['outcome'], 'interrupted')
        calls = self.get_docker_calls()
        rm_call = next((c for c in calls if c and c[0] == 'rm'), None)
        self.assertIsNotNone(rm_call, "docker rm aurait dû être appelé lors de l'interruption")

    def test_sandbox_start_includes_home_tmp_flag(self):
        self.prepare_exchange_task('demo-tmp', profile='node', isolation='exchange')
        res = subprocess.run(
            ['bash', str(self.root / 'scripts/sandbox.sh'), 'start', 'demo-tmp'],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        calls = self.get_docker_calls()
        run_call = next(c for c in calls if c and c[0] == 'run')
        self.assertIn('-e', run_call)
        e_idx = run_call.index('-e')
        self.assertEqual(run_call[e_idx + 1], 'HOME=/tmp')
