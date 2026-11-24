import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / 'template/scripts/lib'


class ConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(LIB / 'consumption.py'), str(self.root), *args], capture_output=True, text=True)

    def rows(self):
        return [json.loads(s) for s in (self.root / '.workspace/metrics/consumption.jsonl').read_text().splitlines()]

    def test_normalized_total_respects_provider_zero_and_unknown_values(self):
        cases = [
            ('claude', dict(input=12, cached_input=7, cache_creation=5, output=3), 27),
            ('codex', dict(input=12, cached_input=7, cache_creation=0, output=3, reasoning=2), 15),
            ('claude', dict(input=0, cached_input=0, cache_creation=0, output=0), 0),
            ('claude', dict(input=12, cached_input=7, output=3), None),
            ('codex', dict(input=12, output=None), None),
            ('codex', dict(total=True), None),
            ('codex', dict(input=12, output=4, total=None), None),
            ('codex', dict(input=-1, output=3), None),
            ('codex', dict(input_tokens=12, output_tokens=3), 15),
            ('claude', dict(input_tokens=12, cache_read_input_tokens=7, cache_creation_input_tokens=5, output_tokens=3), 27),
        ]
        code = (
            "import json,sys; sys.path.insert(0, sys.argv[1]); import consumption; "
            "print(json.dumps([consumption.token_total(t,p) for p,t in json.load(sys.stdin)]))"
        )
        result = subprocess.run(
            [sys.executable, '-c', code, str(LIB)], input=json.dumps([(p, t) for p, t, _ in cases]), capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [want for _, _, want in cases])

    def test_claude_cache_creation_survives_record_and_rejects_invalid_counts(self):
        result = self.cli(
            'record',
            '--role',
            'execution',
            '--provider',
            'claude',
            '--input',
            '12',
            '--cached-input',
            '7',
            '--cache-creation',
            '5',
            '--output',
            '3',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rows()[0]['tokens'], {'input': 12, 'cached_input': 7, 'cache_creation': 5, 'output': 3, 'reasoning': None})
        invalid = self.cli('record', '--role', 'execution', '--provider', 'claude', '--cache-creation', '-1')
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(len(self.rows()), 1)

    def test_validation_and_permissions(self):
        self.assertEqual(self.cli('record', '--role', 'execution', '--provider', 'codex').returncode, 0)
        row = self.rows()[0]
        self.assertEqual(set(row), {'at', 'role', 'provider', 'model', 'task_id', 'attempt', 'tokens', 'proxy', 'quota', 'exit'})
        self.assertTrue(all(v is None for v in row['tokens'].values()))
        path = self.root / '.workspace/metrics/consumption.jsonl'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        for args in [('--role', 'bad'), ('--provider', 'bad'), ('--input', '-1'), ('--duration', 'nan')]:
            self.assertNotEqual(self.cli('record', '--role', 'execution', '--provider', 'codex', *args).returncode, 0)
        self.assertEqual(len(self.rows()), 1)

    def test_concurrent_append_and_lock(self):
        code = "import sys; sys.path.insert(0, sys.argv[1]); import consumption; [consumption.record(sys.argv[2], role='execution', provider='codex') for _ in range(40)]"
        processes = [subprocess.Popen([sys.executable, '-c', code, str(LIB), str(self.root)]) for _ in range(2)]
        self.assertEqual([p.wait() for p in processes], [0, 0])
        self.assertEqual(len(self.rows()), 80)
        import fcntl

        with (self.root / '.workspace/metrics/consumption.jsonl').open() as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            p = subprocess.Popen(
                [sys.executable, str(LIB / 'consumption.py'), str(self.root), 'record', '--role', 'conseil', '--provider', 'ollama']
            )
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    p.wait(timeout=0.2)
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
            self.assertEqual(p.wait(timeout=5), 0)

    def test_codex_usage_limit_and_masking(self):
        source = self.root / 'events'
        source.write_text(
            '\n'.join(
                json.dumps(e)
                for e in [
                    {'type': 'turn.completed', 'usage': {'input_tokens': 12, 'cached_input_tokens': 3, 'output_tokens': 4}},
                    {'type': 'turn.completed', 'usage': {'input_tokens': 2, 'reasoning_output_tokens': 5}},
                    {'type': 'item.completed', 'text': 'PRIVATE PROMPT'},
                ]
            )
        )
        self.assertEqual(self.cli('from-codex-events', str(source), '--role', 'execution').returncode, 0)
        self.assertEqual(self.rows()[0]['tokens'], dict(input=14, cached_input=3, output=4, reasoning=5, total=None))
        self.assertEqual(self.rows()[0]['proxy']['turns'], 2)
        for kind in ('error', 'turn.failed'):
            source.write_text(json.dumps({'type': kind, 'error': {'message': 'Usage limit token=sk-secret123 ' + 'x' * 200}}))
            self.assertEqual(self.cli('from-codex-events', str(source), '--role', 'relecture').returncode, 0)
            row = self.rows()[-1]
            self.assertTrue(row['quota']['limit_reached'])
            self.assertLessEqual(len(row['quota']['detail']), 120)
            self.assertNotIn('sk-secret123', json.dumps(row))
        self.assertNotIn('PRIVATE PROMPT', json.dumps(self.rows()))

    def test_codex_partial_turns_preserve_observations_without_complete_total(self):
        measured = {'type': 'turn.completed', 'usage': {'input_tokens': 12, 'cached_input_tokens': 3, 'output_tokens': 4}}
        cases = [
            ([measured, {'type': 'turn.started'}], 12, 4, False),
            ([measured, {'type': 'turn.failed', 'usage': {'input_tokens': 2, 'output_tokens': 1}}], 14, 5, False),
            ([measured, {'type': 'error', 'message': 'connection lost'}], 12, 4, False),
            ([measured, []], 12, 4, False),
            ([measured, {'type': 'turn.started'}, {'type': 'turn.failed'}], 12, 4, False),
            ([measured, {'type': 'turn.completed'}], 12, 4, False),
            ([measured, {'type': 'turn.completed', 'usage': {'input_tokens': 2}}], 14, 4, False),
            ([measured, {'type': 'turn.completed', 'usage': {'input_tokens': True, 'output_tokens': -1}}], 12, 4, False),
            ([measured, 'invalid JSON'], 12, 4, False),
            ([{'type': 'turn.started'}, measured, measured], 24, 8, True),
            ([{'type': 'turn.completed', 'usage': {'input_tokens': 0, 'output_tokens': 0}}], 0, 0, True),
            ([{'type': 'turn.completed', 'usage': {'output_tokens': 0}}], None, 0, False),
            ([{'type': 'turn.completed', 'usage': {'input_tokens': 1.5, 'output_tokens': '3'}}], None, None, False),
            ([], None, None, False),
        ]
        source = self.root / 'events'
        for events, inp, out, complete in cases:
            with self.subTest(events=events):
                source.write_text('\n'.join(e if isinstance(e, str) else json.dumps(e) for e in events))
                result = self.cli('from-codex-events', str(source), '--role', 'execution')
                self.assertEqual(result.returncode, 0, result.stderr)
                row = self.rows()[-1]
                self.assertEqual((row['tokens']['input'], row['tokens']['output']), (inp, out))
                self.assertEqual(row.get('tokens_complete'), complete)
                if not complete:
                    self.assertIn('total', row['tokens'])
                    self.assertIsNone(row['tokens']['total'])

    def test_codex_nonzero_exit_qualifies_otherwise_complete_stream(self):
        source = self.root / 'events'
        source.write_text(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 12, 'output_tokens': 4}}))
        result = self.cli('from-codex-events', str(source), '--role', 'execution', '--exit', '137')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rows()[0]['tokens']['input'], 12)
        self.assertFalse(self.rows()[0].get('tokens_complete', True))
        self.assertIsNone(self.rows()[0]['tokens']['total'])

    def test_symlinks_refused(self):
        (self.root / '.workspace').mkdir()
        (self.root / '.workspace/metrics').symlink_to(self.root, target_is_directory=True)
        self.assertNotEqual(self.cli('record', '--role', 'execution', '--provider', 'codex').returncode, 0)

    def test_antigravity_proxy_and_limit_without_response_content(self):
        source = self.root / 'events'
        source.write_text(json.dumps({'turns': 3, 'error': {'message': 'quota exceeded'}, 'response': 'PRIVATE response'}))
        self.assertEqual(self.cli('from-antigravity-events', str(source), '--role', 'execution').returncode, 0)
        row = self.rows()[0]
        self.assertEqual(row['proxy']['turns'], 3)
        self.assertTrue(row['quota']['limit_reached'])
        self.assertTrue(all(v is None for v in row['tokens'].values()))
        self.assertNotIn('PRIVATE', json.dumps(row))

    def test_antigravity_success_response_does_not_signal_quota(self):
        source = self.root / 'events'
        source.write_text(json.dumps({'status': 'SUCCESS', 'turns': 1, 'response': 'Le quota est configurable'}))
        result = self.cli('from-antigravity-events', str(source), '--role', 'execution')
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self.rows()[0]
        self.assertEqual(row['quota'], {'limit_reached': False, 'detail': None})
        self.assertNotIn('configurable', json.dumps(row))

    def test_api_rejects_content_and_invalid_values(self):
        code = """import sys
sys.path.insert(0, sys.argv[1])
import consumption
for fields in ({'role': 'bad'}, {'provider': 'bad'}, {'prompt': 'private'},
               {'input': True}, {'quota_detail': 4}, {'limit_reached': 1}, {'tokens_complete': 1}):
    values = dict(role='execution', provider='codex')
    values.update(fields)
    try:
        consumption.record(sys.argv[2], **values)
    except ValueError:
        continue
    raise AssertionError(fields)
"""
        self.assertEqual(subprocess.run([sys.executable, '-c', code, str(LIB), str(self.root)]).returncode, 0)

    def test_relays_roles_quota_root_and_nonblocking_failure(self):
        import shutil

        scripts = self.root / 'scripts'
        shutil.copytree(LIB, scripts / 'lib', ignore=shutil.ignore_patterns('__pycache__'))
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        agent = self.root / 'agent'
        agent.mkdir()
        prompt = self.root / 'prompt.md'
        prompt.write_text('PRIVATE PROMPT')
        answer = self.root / 'answer.md'
        fake = """import json, os, pathlib, sys
if '--version' in sys.argv:
    sys.exit(0)
if os.environ.get('FAIL_CALL'):
    print(json.dumps({'type': 'error', 'message': 'rate limit sk-testsecret123'}))
    sys.exit(17)
if '-o' in sys.argv:
    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text('PRIVATE ANSWER')
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 7}}))
else:
    print(json.dumps({'status': 'SUCCESS', 'response': 'PRIVATE ANSWER', 'turns': 2}))
"""
        for executable in ('npx', 'agy'):
            path = bin_dir / executable
            path.write_text('#!' + sys.executable + '\n' + fake)
            path.chmod(0o755)
        env = dict(
            os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'], AGY_BIN=str(bin_dir / 'agy'), WORKSPACE_AGENT_ROOT=str(agent)
        )
        for key in ('WORKSPACE_ROLE', 'WORKSPACE_TASK_ID', 'WORKSPACE_ATTEMPT'):
            env.pop(key, None)
        for relay, provider in (('codex', 'codex'), ('gemini', 'antigravity')):
            shutil.copy2(LIB.parent / (relay + '.sh'), scripts)
            command = ['bash', str(scripts / (relay + '.sh')), 'review', str(prompt), str(answer)]
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            row = self.rows()[-1]
            self.assertEqual(row['role'], 'relecture')
            self.assertEqual(row['provider'], provider)
            self.assertEqual(row['exit'], 0)
            self.assertFalse((agent / '.workspace/metrics/consumption.jsonl').exists())
            result = subprocess.run(command, env=dict(env, FAIL_CALL='1'), capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            row = self.rows()[-1]
            self.assertEqual(row['exit'], 17)
            self.assertTrue(row['quota']['limit_reached'])
            self.assertNotIn('sk-testsecret123', json.dumps(row))
            log = self.root / '.workspace/metrics/consumption.jsonl'
            log.unlink()
            log.mkdir()
            for failed, expected in ((False, 0), (True, 2)):
                broken_env = dict(env, FAIL_CALL='1') if failed else env
                result = subprocess.run(command, env=broken_env, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertIn('Consumption', result.stderr)
            log.rmdir()
        self.assertNotIn('PRIVATE', json.dumps(row))
