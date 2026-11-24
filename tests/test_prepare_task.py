import copy
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
from task import read_task


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class PrepareTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / 'template/scripts', self.root / 'scripts')
        (self.root / 'request.md').write_text('Créer une fonction de somme.')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('python3', 'bash'):
            (self.bin / name).symlink_to(shutil.which(name))
        self.env = dict(os.environ, PATH=str(self.bin))
        self.env.pop('WORKSPACE_AGENT_ROOT', None)
        twelve = 'class Cases:\n' + ''.join('    def test_case_%d(self):\n        pass\n' % i for i in range(12))
        self.plan = dict(
            tasks=[
                dict(suffix='', allowed=['src/sum.py'], **{'class': 'small'}, verify='python3 -m unittest', body='# Besoin\nSomme stricte.')
            ],
            acceptance=[dict(task_suffix='', path='tests/test_sum.py', content='assert True\n' + twelve)],
            acceptance_verify={'': 'python3 tests/test_sum.py'},
        )

    def fake(self, payload=None, envelope=True):
        payload = self.plan if payload is None else payload
        answer = json.dumps(payload) if not isinstance(payload, str) else payload
        output = (
            json.dumps(dict(result=answer, usage=dict(input_tokens=12, output_tokens=7, cache_read_input_tokens=3))) if envelope else answer
        )
        script = '#!' + sys.executable + '\nimport sys, pathlib\n'
        script += 'pathlib.Path("call.json").write_text(__import__("json").dumps(dict(args=sys.argv[1:], prompt=sys.stdin.read(), cwd=str(pathlib.Path.cwd()))))\n'
        script += 'print(' + repr(output) + ')\n'
        (self.bin / 'claude').write_text(script)
        (self.bin / 'claude').chmod(0o700)

    def run_prepare(self, *extra):
        return subprocess.run(
            ['bash', 'scripts/prepare-task.sh', 'request.md', 'sum', '--executor', 'claude-haiku', *extra],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def assert_rejected(self):
        result = self.run_prepare()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertFalse((self.root / 'tasks').exists())
        self.assertFalse((self.root / '.workspace/acceptance').exists())

    def test_nominal_and_consumption(self):
        self.fake()
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'sum\n')
        task = read_task(self.root / 'tasks/sum.md')
        self.assertEqual((task['agent'], task['mode'], task['attempts']), ('claude-haiku', 'write', 2))
        self.assertEqual(task['acceptance'], ['tests/test_sum.py'])
        hidden = self.root / '.workspace/acceptance/sum'
        self.assertEqual((hidden.stat().st_mode & 0o777), 0o700)
        self.assertEqual((hidden / 'tests/test_sum.py').stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root / 'tests/test_sum.py').exists())
        row = json.loads((self.root / '.workspace/metrics/consumption.jsonl').read_text())
        self.assertEqual((row['role'], row['task_id'], row['tokens']['input']), ('orchestration', 'sum', 12))
        call = json.loads((self.root / 'call.json').read_text())
        self.assertIn('--permission-mode', call['args'])
        self.assertIn('Read,Glob,Grep', call['args'])
        self.assertIn('Contrat strict', call['prompt'])
        self.assertIn('Créer une fonction', call['prompt'])
        self.assertEqual(Path(call['cwd']), self.root.resolve())

    def test_two_tasks_fenced(self):
        original = copy.deepcopy(self.plan)
        self.plan = dict(tasks=[], acceptance=[], acceptance_verify={})
        for suffix in ('-1', '-2'):
            self.plan['tasks'].append(dict(original['tasks'][0], suffix=suffix))
            self.plan['acceptance'].append(dict(original['acceptance'][0], task_suffix=suffix))
            self.plan['acceptance_verify'][suffix] = original['acceptance_verify']['']
        self.fake('```json\n' + json.dumps(self.plan) + '\n```')
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'sum-1\nsum-2\n')
        self.assertEqual(read_task(self.root / 'tasks/sum-2.md')['id'], 'sum-2')

    def test_invalid_json(self):
        self.fake('not json')
        self.assert_rejected()

    def test_parent_path(self):
        self.plan['acceptance'][0]['path'] = '../escape.py'
        self.fake()
        self.assert_rejected()

    def test_absent_planner(self):
        self.assertEqual(self.run_prepare().returncode, 3)

    def test_strict_validation(self):
        variants = []
        for field, value in [('allowed', []), ('allowed', ['../escape']), ('suffix', '-2'), ('body', 'x' * 70000)]:
            plan = copy.deepcopy(self.plan)
            plan['tasks'][0][field] = value
            variants.append(plan)
        plan = copy.deepcopy(self.plan)
        plan['unknown'] = True
        variants.append(plan)
        plan = copy.deepcopy(self.plan)
        plan['acceptance'] *= 4
        variants.append(plan)
        for plan in variants:
            with self.subTest(plan=str(plan)[:90]):
                self.fake(plan)
                self.assert_rejected()

    def test_full_gate_refused(self):
        for verify in ('bash scripts/gate.sh', 'scripts/gate.sh', 'gate.sh', 'python3 -m unittest && ./gate.sh'):
            with self.subTest(verify=verify):
                plan = copy.deepcopy(self.plan)
                plan['tasks'][0]['verify'] = verify
                self.fake(plan)
                self.assert_rejected()

    def test_symlink_destination(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'tasks').symlink_to(outside, target_is_directory=True)
        self.fake()
        self.assertEqual(self.run_prepare().returncode, 2)
        self.assertEqual(list(outside.iterdir()), [])

    def test_write_failure_rolls_back(self):
        self.plan['acceptance'].append(dict(task_suffix='', path='tests/test_sum.py/child', content='bad'))
        self.fake()
        self.assert_rejected()

    def test_existing_task_is_preserved(self):
        (self.root / 'tasks').mkdir()
        path = self.root / 'tasks/sum.md'
        path.write_text('original')
        self.fake()
        self.assertEqual(self.run_prepare().returncode, 2)
        self.assertEqual(path.read_text(), 'original')
        self.assertFalse((self.root / '.workspace/acceptance').exists())

    def test_codex_review(self):
        relay = self.root / 'scripts/codex.sh'
        relay.write_text('#!/usr/bin/env bash\nexec python3 scripts/fake_codex.py "$@"\n')
        fake = self.root / 'scripts/fake_codex.py'
        fake.write_text(
            'import os, sys, pathlib\n'
            'assert sys.argv[1] == "review"\n'
            'assert os.environ["WORKSPACE_ROLE"] == "orchestration"\n'
            'assert os.environ["WORKSPACE_TASK_ID"] == "sum"\n'
            'pathlib.Path(sys.argv[3]).write_text(' + repr(json.dumps(self.plan)) + ')\n'
        )
        result = self.run_prepare('--planner', 'codex')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'sum\n')

    def test_prompt_warns_against_full_gate(self):
        spec = importlib.util.spec_from_file_location('prepare_task_prompt', ROOT / 'template/scripts/lib/prepare_task.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        prompt = module.prompt_for(self.root, 'Créer une fonction de somme.')
        self.assertIn('scripts/gate.sh', prompt)
        self.assertIn('ciblée', prompt)
        self.assertIn('verify est une commande ciblée', prompt)

    def start_hung_planner(self):
        script = '#!' + sys.executable + '\nimport os, subprocess, sys, time\n'
        script += "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
        script += "open('pids.txt', 'w').write('%d %d' % (os.getpid(), child.pid))\n"
        script += 'time.sleep(600)\n'
        (self.bin / 'claude').write_text(script)
        (self.bin / 'claude').chmod(0o700)
        process = subprocess.Popen(
            ['bash', 'scripts/prepare-task.sh', 'request.md', 'sum', '--executor', 'claude-haiku'],
            cwd=self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()

    def wait_for_planner(self):
        path = self.root / 'pids.txt'
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            pids = path.read_text().split() if path.is_file() else []
            if len(pids) == 2:
                pids = [int(pid) for pid in pids]
                self.addCleanup(self.kill_quietly, pids)
                return pids
            time.sleep(0.05)
        self.fail('Le faux planificateur ne démarre pas')

    @staticmethod
    def kill_quietly(pids):
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def assert_gone(self, pids):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(alive(pid) for pid in pids):
            time.sleep(0.05)
        self.assertEqual([pid for pid in pids if alive(pid)], [])

    def test_sigterm_stops_planner_group(self):
        process = self.start_hung_planner()
        pids = self.wait_for_planner()
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 130, stderr)
        self.assertIn('Préparation interrompue.', stderr)
        self.assert_gone(pids)
        self.assertFalse((self.root / 'tasks').exists())

    def test_sigkill_guardian_stops_planner_group(self):
        process = self.start_hung_planner()
        pids = self.wait_for_planner()
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=10)
        self.assertEqual(process.returncode, -signal.SIGKILL)
        self.assert_gone(pids)

    def test_install_circuit(self):
        spec = importlib.util.spec_from_file_location('workspace_prepare_test', ROOT / 'scripts/workspace.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIn('prepare-task.sh', module.CIRCUIT_SCRIPTS)
