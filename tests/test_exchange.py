from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel, sous-processus ou Docker simulé ; suite complète seulement')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
TEST_TMP = ROOT / '.workspace/tmp-tests'
TEST_TMP.mkdir(parents=True, exist_ok=True)

import workdir  # noqa: E402


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.temp.cleanup)
        self.root = (Path(self.temp.name) / 'project with spaces').resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.scripts_dir = self.root / 'scripts'
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.lib_dir = self.scripts_dir / 'lib'
        self.lib_dir.mkdir(parents=True, exist_ok=True)

        # Copie des scripts et bibliothèques depuis template
        lib_source = ROOT / 'template/scripts/lib'
        if lib_source.exists():
            for lib_file in lib_source.glob('*.py'):
                shutil.copy2(lib_file, self.lib_dir / lib_file.name)

        for script_file in (ROOT / 'template/scripts').glob('*.sh'):
            dest = self.scripts_dir / script_file.name
            shutil.copy2(script_file, dest)
            dest.chmod(0o755)

        # Copie de docs
        docs_source = ROOT / 'template/docs'
        if docs_source.exists():
            dest_docs = self.root / 'docs'
            dest_docs.mkdir(parents=True, exist_ok=True)
            for doc_file in docs_source.glob('*.md'):
                shutil.copy2(doc_file, dest_docs / doc_file.name)

        # Dépôt Git propre
        self.git('init', '-q')
        (self.root / '.gitignore').write_text('.workspace/\n.workspace/exchange/\ntasks/\nbin/\n__pycache__/\n*.log\n')
        (self.root / 'project.profile').write_text('python\n')
        (self.root / 'README.md').write_text('# Test project\n')
        (self.root / 'src').mkdir(parents=True, exist_ok=True)
        (self.root / 'src/app.py').write_text('print("initial")\n')
        (self.root / 'tracked.txt').write_text('initial tracked\n')
        (self.root / 'secrets').mkdir(parents=True, exist_ok=True)
        (self.root / 'secrets/key').write_text('super-secret-token-12345\n')

        self.git('add', '-A')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'initial commit')

        # Fake docker en dehors du dépôt
        self.bin_dir = self.root.parent / (self.root.name + '-bin')
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.docker_log_file = self.root.parent / (self.root.name + '-docker_calls.json')
        self.setup_fake_docker()

        # Fake agents CLI (npx / codex, agy / gemini, claude)
        self.setup_fake_agents()

        # Les clones de tâche vont hors du dépôt, jamais dans le vrai ~/.workspace-ia.
        self.work_dir = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.work_dir.cleanup)
        self.env = dict(
            os.environ,
            PATH=str(self.bin_dir) + os.pathsep + os.environ.get('PATH', ''),
            FAKE_DOCKER_LOG=str(self.docker_log_file),
            DELEGATE_RETRY_DELAY='0',
            WORKSPACE_WORK_DIR=os.path.realpath(self.work_dir.name),
        )
        # Le courtier tourne dans ce processus, pas dans celui du client.
        broker_env = mock.patch.dict(os.environ, self.env)
        broker_env.start()
        self.addCleanup(broker_env.stop)

    def clone(self, task_id):
        """Dossier de travail (clone sans historique, hors du projet) d'une tâche préparée."""
        return workdir.task_worktree(self.root, task_id)

    def git(self, *args, cwd=None):
        cmd = [
            'git',
            '-C',
            str(cwd or self.root),
            '-c',
            'user.name=Test',
            '-c',
            'user.email=test@example.invalid',
            '-c',
            'core.hooksPath=',
            *args,
        ]
        return subprocess.check_output(cmd, text=True)

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
    started = False
    for call in calls[:-1]:
        if call and call[0] == 'run':
            started = True
        elif call and call[0] == 'rm':
            started = False
    if not started:
        print('Error: No such object', file=sys.stderr)
        sys.exit(1)
    fmt = ''
    if '--format' in sys.argv:
        fmt = sys.argv[sys.argv.index('--format') + 1]
    if fmt == '{{.State.Running}}':
        print("true")
        sys.exit(0)
    elif fmt == '{{.State.Status}}':
        print("running")
        sys.exit(0)
    data = [{"State": {"Running": True, "Status": "running"}}]
    print(json.dumps(data))
    sys.exit(0)
elif sub == 'exec':
    code = int(os.environ.get('SIMULATE_EXEC_CODE', '0'))
    output_file = os.environ.get('SIMULATE_EXEC_OUTPUT_FILE')
    output = pathlib.Path(output_file).read_text() if output_file else os.environ.get('SIMULATE_EXEC_OUTPUT', '')
    if not output:
        # sys.argv is ['docker', 'exec', container_name, *cmd]
        cmd_args = sys.argv[3:]
        if cmd_args and cmd_args[0] == 'echo':
            output = ' '.join(cmd_args[1:])
        elif cmd_args:
            output = 'fake exec: ' + ' '.join(cmd_args)
    if output:
        print(output)
    sys.exit(code)
elif sub == 'rm':
    sys.exit(0)
else:
    sys.exit(0)
'''.replace('__DOCKER_LOG__', str(self.docker_log_file))
        docker_path = self.bin_dir / 'docker'
        docker_path.write_text(fake_script, encoding='utf-8')
        docker_path.chmod(0o755)

    def setup_fake_agents(self):
        fake = '''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
p = pathlib.Path('.')
if '-C' in sys.argv:
    os.chdir(sys.argv[sys.argv.index('-C')+1])
    p = pathlib.Path('.')
name = pathlib.Path(sys.argv[0]).name
if '--version' in sys.argv:
    sys.exit(0)

# Enregistrement du répertoire courant de l'agent
(p / 'cwd.txt').write_text(str(p.resolve()))
(p / 'args.json').write_text(json.dumps(sys.argv[1:]))

behavior = os.environ.get('BEHAVIOR', 'ok')
if behavior == 'modify':
    (p / 'src/app.py').write_text('modified by agent\\n')
elif behavior == 'run_sandbox_command':
    # Appel de run-in-sandbox.sh depuis le worktree
    cmd = ['bash', 'scripts/run-in-sandbox.sh', '--', 'echo', 'result from container']
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    (p / 'sandbox_cmd_out.txt').write_text(f"{res.returncode}:{res.stdout}")
elif behavior == 'ask_question':
    cmd = ['bash', 'scripts/ask.sh', 'Comment configurer X ?']
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    (p / 'ask_out.txt').write_text(f"{res.returncode}:{res.stdout}")

if name == 'npx':
    if '-o' in sys.argv:
        pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('answer from codex')
    print('Codex event')
elif name == 'agy':
    print(json.dumps({'status': 'SUCCESS', 'response': 'answer from agy'}))
else:
    print('answer from claude')

sys.exit(0)
'''
        for name in ('npx', 'agy', 'claude'):
            agent_file = self.bin_dir / name
            agent_file.write_text(fake, encoding='utf-8')
            agent_file.chmod(0o755)

    def get_docker_calls(self):
        if not self.docker_log_file.exists():
            return []
        try:
            return json.loads(self.docker_log_file.read_text(encoding='utf-8'))
        except Exception:
            return []

    def prepare_exchange_task(self, task_id='demo-exch', verify='echo ok'):
        task_file = self.root / f'tasks/{task_id}.md'
        task_file.parent.mkdir(parents=True, exist_ok=True)
        content = (
            f"---\n"
            f"id: {task_id}\n"
            f"agent: codex\n"
            f"mode: write\n"
            f"isolation: exchange\n"
            f"allowed:\n"
            f"  - src/**\n"
            f"  - tracked.txt\n"
            f"  - cwd.txt\n"
            f"  - args.json\n"
            f"  - sandbox_cmd_out.txt\n"
            f"  - ask_out.txt\n"
            f"verify: {verify}\n"
            f"attempts: 1\n"
            f"---\n"
            f"Consigne de test exchange\n"
        )
        task_file.write_text(content, encoding='utf-8')

        self.assertEqual(self.git('status', '--porcelain'), '', 'prepare exige un dépôt propre')
        res = subprocess.run(
            ['bash', str(self.scripts_dir / 'task-workspace.sh'), 'prepare', str(task_file)],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(res.returncode, 0, f"task-workspace.sh prepare a échoué: {res.stderr}")
        return task_file

    def run_delegate(self, task_file, behavior='ok', env=None):
        run_env = dict(self.env, BEHAVIOR=behavior)
        if env:
            run_env.update(env)
        return subprocess.run(
            ['bash', str(self.scripts_dir / 'delegate.sh'), str(task_file)], cwd=str(self.root), capture_output=True, text=True, env=run_env
        )

    def stop_broker(self, stop_event, broker_thread):
        stop_event.set()
        broker_thread.join(timeout=2.0)
        self.assertFalse(broker_thread.is_alive(), 'le courtier doit être arrêté avant le nettoyage')

    def test_request_executed_in_container_and_result_returned(self):
        task_id = 'test-req-exec'
        self.prepare_exchange_task(task_id)
        worktree = self.clone(task_id)
        exchange_dir = worktree / '.workspace/exchange'

        # Démarrage sandbox
        res_start = subprocess.run(
            ['bash', str(self.scripts_dir / 'sandbox.sh'), 'start', task_id],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(res_start.returncode, 0)
        self.addCleanup(lambda: subprocess.run(['bash', str(self.scripts_dir / 'sandbox.sh'), 'stop', task_id], env=self.env))

        # Démarrage courtier en thread
        from exchange import run_broker

        stop_event = threading.Event()
        broker_thread = threading.Thread(target=run_broker, args=(self.root, task_id, stop_event), daemon=True)
        broker_thread.start()
        self.addCleanup(self.stop_broker, stop_event, broker_thread)

        # Lancement de run-in-sandbox.sh depuis le worktree
        res = subprocess.run(
            ['bash', str(worktree / 'scripts/run-in-sandbox.sh'), '--', 'echo', 'hello', 'world'],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('hello world', res.stdout)

        self.assertIn(
            [
                'exec',
                'workspace-sandbox-' + hashlib.sha256(str(self.root.resolve()).encode('utf-8')).hexdigest()[:10] + '-' + task_id,
                'echo',
                'hello',
                'world',
            ],
            self.get_docker_calls(),
        )

        # Vérification des fichiers de résultat et de journalisation
        res_file = exchange_dir / 'results/001.json'
        self.assertTrue(res_file.is_file())
        res_data = json.loads(res_file.read_text())
        self.assertEqual(res_data['exit_code'], 0)
        self.assertIn('hello world', res_data['output'])

        log_dir = self.root / f'.workspace/orchestration/tasks/{task_id}/exchange'
        self.assertTrue((log_dir / 'request-001.json').is_file())
        self.assertTrue((log_dir / 'result-001.json').is_file())

    def test_exit_code_forwarded(self):
        task_id = 'test-exit-code'
        self.prepare_exchange_task(task_id)
        worktree = self.clone(task_id)

        res_start = subprocess.run(
            ['bash', str(self.scripts_dir / 'sandbox.sh'), 'start', task_id],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(res_start.returncode, 0)
        self.addCleanup(lambda: subprocess.run(['bash', str(self.scripts_dir / 'sandbox.sh'), 'stop', task_id], env=self.env))

        from exchange import run_broker

        stop_event = threading.Event()
        broker_thread = threading.Thread(target=run_broker, args=(self.root, task_id, stop_event), daemon=True)
        broker_thread.start()
        self.addCleanup(self.stop_broker, stop_event, broker_thread)

        # Simulation d'un code de sortie 42
        os.environ['SIMULATE_EXEC_CODE'] = '42'
        test_env = dict(self.env)
        res = subprocess.run(
            ['bash', str(worktree / 'scripts/run-in-sandbox.sh'), '--', 'pytest'],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            env=test_env,
        )
        self.assertEqual(res.returncode, 42)

    def run_broker_until(self, task_id, done):
        import exchange

        stop_event = threading.Event()
        broker_thread = threading.Thread(target=exchange.run_broker, args=(self.root, task_id, stop_event), daemon=True)
        broker_thread.start()
        deadline = time.monotonic() + 10
        while not done() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.stop_broker(stop_event, broker_thread)

    def test_broker_does_not_replay_answered_requests(self):
        task_id = 'test-no-replay'
        self.prepare_exchange_task(task_id)
        worktree = self.clone(task_id)
        requests_dir = worktree / '.workspace/exchange/requests'
        results_dir = worktree / '.workspace/exchange/results'
        log_dir = self.root / f'.workspace/orchestration/tasks/{task_id}/exchange'
        requests_dir.mkdir(parents=True, exist_ok=True)
        results_dir.mkdir(parents=True, exist_ok=True)

        for number in (1, 2, 3):
            (requests_dir / f'{number:03d}.json').write_text(json.dumps({'command': ['echo', str(number)]}))
        previous = {}
        for number in (1, 2):
            previous[number] = json.dumps({'exit_code': 0, 'code': 0, 'output': f'old {number}\n'})
            (results_dir / f'{number:03d}.json').write_text(previous[number])

        executed = []

        def fake_execute(root, tid, req_file, req_num, log):
            executed.append(req_num)
            return 0, f'new {req_num}\n'

        with mock.patch('exchange.execute_request', side_effect=fake_execute):
            self.run_broker_until(task_id, lambda: (results_dir / '003.json').is_file())
        self.assertEqual(executed, [3])
        for number in (1, 2):
            self.assertEqual((results_dir / f'{number:03d}.json').read_text(), previous[number])
            self.assertFalse((log_dir / f'result-{number:03d}.json').exists())
        self.assertEqual(json.loads((results_dir / '003.json').read_text())['output'], 'new 3\n')
        self.assertTrue((log_dir / 'result-003.json').is_file())

        # Second lancement : toutes les requêtes ont un résultat, rien n'est rejoué.
        executed.clear()
        with mock.patch('exchange.execute_request', side_effect=fake_execute):
            self.run_broker_until(task_id, lambda: True)
        self.assertEqual(executed, [])
        self.assertEqual((results_dir / '001.json').read_text(), previous[1])
        self.assertEqual(json.loads((results_dir / '003.json').read_text())['output'], 'new 3\n')

    def test_output_secrets_masked(self):
        task_id = 'test-mask-secrets'
        self.prepare_exchange_task(task_id)
        worktree = self.clone(task_id)

        res_start = subprocess.run(
            ['bash', str(self.scripts_dir / 'sandbox.sh'), 'start', task_id],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(res_start.returncode, 0)
        self.addCleanup(lambda: subprocess.run(['bash', str(self.scripts_dir / 'sandbox.sh'), 'stop', task_id], env=self.env))

        from exchange import run_broker

        stop_event = threading.Event()
        broker_thread = threading.Thread(target=run_broker, args=(self.root, task_id, stop_event), daemon=True)
        broker_thread.start()
        self.addCleanup(self.stop_broker, stop_event, broker_thread)

        # La sortie contient le secret connu du dépôt
        os.environ['SIMULATE_EXEC_OUTPUT'] = 'Le secret est super-secret-token-12345 termine.'
        test_env = dict(self.env)
        res = subprocess.run(
            ['bash', str(worktree / 'scripts/run-in-sandbox.sh'), '--', 'ls'],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            env=test_env,
        )
        self.assertEqual(res.returncode, 0)
        self.assertNotIn('super-secret-token-12345', res.stdout)
        self.assertIn('***', res.stdout)

    def test_output_truncated_at_200k(self):
        task_id = 'test-trunc-output'
        self.prepare_exchange_task(task_id)
        worktree = self.clone(task_id)

        res_start = subprocess.run(
            ['bash', str(self.scripts_dir / 'sandbox.sh'), 'start', task_id],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(res_start.returncode, 0)
        self.addCleanup(lambda: subprocess.run(['bash', str(self.scripts_dir / 'sandbox.sh'), 'stop', task_id], env=self.env))

        from exchange import run_broker

        stop_event = threading.Event()
        broker_thread = threading.Thread(target=run_broker, args=(self.root, task_id, stop_event), daemon=True)
        broker_thread.start()
        self.addCleanup(self.stop_broker, stop_event, broker_thread)

        # Sortie volumineuse > 250 Ko
        big_output = 'x' * (250 * 1024)
        output_file = self.root.parent / 'large-output.txt'
        output_file.write_text(big_output)
        os.environ['SIMULATE_EXEC_OUTPUT_FILE'] = str(output_file)
        test_env = dict(self.env)
        res = subprocess.run(
            ['bash', str(worktree / 'scripts/run-in-sandbox.sh'), '--', 'cat', 'bigfile'],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            env=test_env,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn('[TRUNCATED]', res.stdout)
        self.assertLessEqual(len(res.stdout), 200 * 1024 + 100)

    def test_malformed_request_rejected(self):
        task_id = 'test-malformed-req'
        self.prepare_exchange_task(task_id)
        worktree = self.clone(task_id)
        requests_dir = worktree / '.workspace/exchange/requests'
        results_dir = worktree / '.workspace/exchange/results'
        requests_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        results_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        res_start = subprocess.run(
            ['bash', str(self.scripts_dir / 'sandbox.sh'), 'start', task_id],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(res_start.returncode, 0)
        self.addCleanup(lambda: subprocess.run(['bash', str(self.scripts_dir / 'sandbox.sh'), 'stop', task_id], env=self.env))

        from exchange import run_broker

        stop_event = threading.Event()
        broker_thread = threading.Thread(target=run_broker, args=(self.root, task_id, stop_event), daemon=True)
        broker_thread.start()
        self.addCleanup(self.stop_broker, stop_event, broker_thread)

        # 1. Requête malformée (JSON invalide)
        (requests_dir / '001.json').write_text('{broken json', encoding='utf-8')
        deadline = time.monotonic() + 3.0
        while not (results_dir / '001.json').exists() and time.monotonic() < deadline:
            time.sleep(0.02)

        self.assertTrue((results_dir / '001.json').is_file())
        res1 = json.loads((results_dir / '001.json').read_text())
        self.assertEqual(res1['exit_code'], 64)
        self.assertIn('malformée', res1['output'])
        self.assertFalse(any(c[0] == 'exec' for c in self.get_docker_calls()))

        # 2. Requête suivante valide : le courtier continue de fonctionner
        (requests_dir / '002.json').write_text(json.dumps({'command': ['echo', 'suite_valide']}), encoding='utf-8')
        deadline = time.monotonic() + 3.0
        while not (results_dir / '002.json').exists() and time.monotonic() < deadline:
            time.sleep(0.02)

        self.assertTrue((results_dir / '002.json').is_file())
        res2 = json.loads((results_dir / '002.json').read_text())
        self.assertEqual(res2['exit_code'], 0)
        self.assertIn('suite_valide', res2['output'])

    def test_timeout_exceeded(self):
        task_id = 'test-timeout-exec'
        self.prepare_exchange_task(task_id)
        worktree = self.clone(task_id)

        # Le dossier est normalement créé au démarrage du courtier.
        (worktree / '.workspace/exchange').mkdir(parents=True)

        # Lancement sans courtier avec délai très court
        start = time.monotonic()
        res = subprocess.run(
            ['bash', str(worktree / 'scripts/run-in-sandbox.sh'), '--', 'echo', 'timeout'],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            env=dict(self.env, RUN_TIMEOUT='0.2'),
        )
        duration = time.monotonic() - start
        self.assertLess(duration, 2.0)
        self.assertEqual(res.returncode, 124, res.stderr)
        self.assertTrue(any(w in res.stderr.lower() for w in ['délai', 'dépassé', 'courtier']))

    def test_broker_stopped_cleanly_at_end_and_on_interruption(self):
        task_id = 'test-stop-broker'
        self.prepare_exchange_task(task_id)

        from exchange import run_broker

        stop_event = threading.Event()
        broker_thread = threading.Thread(target=run_broker, args=(self.root, task_id, stop_event), daemon=True)
        broker_thread.start()

        self.assertTrue(broker_thread.is_alive())
        stop_event.set()
        broker_thread.join(timeout=1.0)
        self.assertFalse(broker_thread.is_alive())

    def test_agent_launched_in_worktree(self):
        task_id = 'test-agent-wt'
        task_file = self.prepare_exchange_task(task_id)
        worktree = self.clone(task_id)

        result = self.run_delegate(task_file, behavior='ok')
        self.assertEqual(result.returncode, 0, result.stderr)

        cwd_file = worktree / 'cwd.txt'
        self.assertTrue(cwd_file.is_file(), "L'agent aurait dû écrire cwd.txt dans le worktree")
        self.assertEqual(cwd_file.read_text().strip(), str(worktree.resolve()))

    def test_agent_modification_present_on_task_branch(self):
        task_id = 'test-agent-mod'
        task_file = self.prepare_exchange_task(task_id)
        worktree = self.clone(task_id)

        # L'agent modifie src/app.py dans son worktree
        result = self.run_delegate(task_file, behavior='modify')
        self.assertEqual(result.returncode, 0, result.stderr)

        # La racine principale n'a PAS été modifiée
        self.assertEqual((self.root / 'src/app.py').read_text().strip(), 'print("initial")')

        # Le worktree de tâche a bien été modifié
        self.assertEqual((worktree / 'src/app.py').read_text().strip(), 'modified by agent')

        # Le statut rapporte la modification
        status_file = self.root / f'.workspace/run/delegate/{task_id}/status.json'
        status = json.loads(status_file.read_text())
        self.assertEqual(status['outcome'], 'done')
        self.assertIn('src/app.py', status['files_changed'])

        # Le diff contient la modification
        diff_file = self.root / f'.workspace/run/delegate/{task_id}/diff.txt'
        self.assertIn('+modified by agent', diff_file.read_text())

    def test_ask_and_answer_in_worktree(self):
        task_id = 'test-ask-wt'
        self.prepare_exchange_task(task_id)
        worktree = self.clone(task_id)
        exchange_dir = worktree / '.workspace/exchange'
        (exchange_dir / 'questions').mkdir(parents=True, exist_ok=True, mode=0o700)
        (exchange_dir / 'answers').mkdir(parents=True, exist_ok=True, mode=0o700)

        # Thread de réponse orchestrateur
        def orchestrator_answer():
            deadline = time.monotonic() + 5.0
            q_file = exchange_dir / 'questions/001.md'
            while not q_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            # Répond via answer.sh
            res = subprocess.run(
                ['bash', str(self.scripts_dir / 'answer.sh'), task_id, '001', 'Configuration valide reçue.'],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                env=self.env,
            )
            self.assertEqual(res.returncode, 0, res.stderr)

        t = threading.Thread(target=orchestrator_answer)
        t.start()

        # Agent pose une question depuis son worktree
        res_ask = subprocess.run(
            ['bash', str(worktree / 'scripts/ask.sh'), 'Où trouver le token ?'],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            env=dict(self.env, ASK_TIMEOUT='10'),
        )
        t.join()

        self.assertEqual(res_ask.returncode, 0, res_ask.stderr)
        self.assertIn('Configuration valide reçue.', res_ask.stdout)

        # La question et la réponse sont présentes dans exchange
        self.assertTrue((exchange_dir / 'questions/001.md').is_file())
        self.assertTrue((exchange_dir / 'answers/001.md').is_file())

        # Et synchronisées dans context de l'orchestration
        ctx_q = self.root / f'.workspace/orchestration/tasks/{task_id}/context/questions/001.md'
        ctx_a = self.root / f'.workspace/orchestration/tasks/{task_id}/context/answers/001.md'
        self.assertTrue(ctx_q.is_file())
        self.assertTrue(ctx_a.is_file())
        self.assertEqual(ctx_a.read_text().strip(), 'Configuration valide reçue.')

    def test_agent_executes_run_in_sandbox_end_to_end(self):
        task_id = 'test-e2e-run'
        task_file = self.prepare_exchange_task(task_id)
        worktree = self.clone(task_id)

        result = self.run_delegate(task_file, behavior='run_sandbox_command')
        self.assertEqual(result.returncode, 0, result.stderr)

        status_file = self.root / f'.workspace/run/delegate/{task_id}/status.json'
        status = json.loads(status_file.read_text())
        self.assertEqual(status['outcome'], 'done')

        out_file = worktree / 'sandbox_cmd_out.txt'
        self.assertTrue(out_file.is_file())
        self.assertIn('0:result from container', out_file.read_text())


if __name__ == '__main__':
    unittest.main()
