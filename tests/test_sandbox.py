from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel, sous-processus ou Docker simulé ; suite complète seulement')

ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = ROOT / '.workspace/tmp-tests'
TEST_TMP.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import workdir  # noqa: E402


class SandboxTests(unittest.TestCase):
    def test_container_names_are_stable_and_project_specific(self):
        spec = importlib.util.spec_from_file_location('sandbox_names', ROOT / 'template/scripts/lib/sandbox.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        first = module.get_container_name(self.root, 'demo')
        self.assertEqual(first, module.get_container_name(self.root, 'demo'))
        self.assertEqual(first, module.get_container_name(self.root / '..' / self.root.name, 'demo'))
        self.assertNotEqual(first, module.get_container_name(self.root.parent / 'other', 'demo'))
        self.assertRegex(first, r'^workspace-sandbox-[0-9a-f]{10}-demo$')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.temp.cleanup)
        # Les clones de tâche vont hors du dépôt, jamais dans le vrai ~/.workspace-ia.
        self.work_dir = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(self.work_dir.cleanup)
        patcher = mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': os.path.realpath(self.work_dir.name)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = (Path(self.temp.name) / 'project with spaces').resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.scripts_dir = self.root / 'scripts'
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.lib_dir = self.scripts_dir / 'lib'
        self.lib_dir.mkdir(parents=True, exist_ok=True)

        # Copie des bibliothèques et scripts depuis template
        lib_source = ROOT / 'template/scripts/lib'
        if lib_source.exists():
            for lib_file in lib_source.glob('*.py'):
                shutil.copy2(lib_file, self.lib_dir / lib_file.name)

        for script_name in ('task-workspace.sh', 'sandbox.sh', 'ask.sh', 'answer.sh'):
            src = ROOT / 'template/scripts' / script_name
            if src.exists():
                dest = self.scripts_dir / script_name
                shutil.copy2(src, dest)
                dest.chmod(0o755)

        # Initialisation Git du projet de test
        self.git('init', '-q')
        (self.root / '.gitignore').write_text('.workspace/\ntasks/\n__pycache__/\n')
        (self.root / 'README.md').write_text('# Test project\n')
        (self.root / 'src').mkdir()
        (self.root / 'src/app.py').write_text('print("initial")\n')
        (self.root / 'secrets').mkdir()
        (self.root / 'secrets/key').write_text('super-secret-token-12345\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial commit')

        # Création du faux docker
        # Hors du dépôt : des fichiers non suivis rendraient l'arbre sale et prepare refuserait.
        self.bin_dir = self.root.parent / (self.root.name + '-bin')
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.docker_log_file = self.root.parent / (self.root.name + '-docker_calls.json')
        self.setup_fake_docker()

        self.env = dict(
            os.environ, PATH=str(self.bin_dir) + os.pathsep + os.environ.get('PATH', ''), FAKE_DOCKER_LOG=str(self.docker_log_file)
        )
        # The default images are asserted below: an image chosen by the caller's shell (or by the orchestration program,
        # which exports the plan's image to every circuit command) must not leak into these tests.
        for name in ('WORKSPACE_IMAGE_PYTHON', 'WORKSPACE_IMAGE_NODE', 'WORKSPACE_IMAGE_C'):
            self.env.pop(name, None)

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
elif sub == 'volume':
    action = sys.argv[2]
    name = sys.argv[-1]
    exists = False
    for c in calls[:-1]:
        if c[:2] == ['volume', 'create'] and c[-1] == name: exists = True
        if c[:2] == ['volume', 'rm'] and c[-1] == name: exists = False
    if action == 'inspect': sys.exit(0 if exists else 1)
    sys.exit(0)
elif sub == 'run':
    if 'workspace.role=deps' in sys.argv and os.environ.get('FAIL_DEPS') == '1':
        print('super-secret-token-12345', file=sys.stderr)
        sys.exit(9)
    print("fake-container-id-12345")
    sys.exit(0)
elif sub == 'inspect':
    fmt = ''
    if '--format' in sys.argv:
        fmt = sys.argv[sys.argv.index('--format') + 1]
    # Fidèle au vrai docker : aucun conteneur tant qu'aucun « run » n'a eu lieu (ou après « rm »).
    started = False
    for c in calls[:-1]:
        if c and c[0] == 'run' and 'workspace.role=sandbox' in c:
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
elif sub == 'image':
    # Sans FAKE_IMAGE_ID : image absente (sortie vide, code 0), comme un vrai « image inspect » sans image.
    if 'FAKE_IMAGE_ID' in os.environ:
        print(os.environ['FAKE_IMAGE_ID'])
    sys.exit(0)
else:
    sys.exit(0)
'''.replace('__DOCKER_LOG__', str(self.docker_log_file))
        docker_path = self.bin_dir / 'docker'
        docker_path.write_text(fake_script, encoding='utf-8')
        docker_path.chmod(0o755)

    def get_docker_calls(self):
        if not self.docker_log_file.exists():
            return []
        try:
            return json.loads(self.docker_log_file.read_text(encoding='utf-8'))
        except Exception:
            return []

    def prepare_task(self, task_id='task-1', profile=None, network='off'):
        task_file = self.root / f'tasks/{task_id}.md'
        task_file.parent.mkdir(parents=True, exist_ok=True)
        content = f"---\nid: {task_id}\nagent: codex\nallowed:\n  - src/**\nnetwork: {network}\n---\nConsigne de test\n"
        task_file.write_text(content, encoding='utf-8')

        if profile:
            # Le profil est un fichier suivi : il doit être validé pour que l'arbre reste propre.
            (self.root / 'project.profile').write_text(profile)
            self.git('add', 'project.profile')
            self.git('commit', '-qm', 'profile')

        # Préparation via task-workspace.sh ; ces tests visent le démarrage du conteneur, pas le refus précoce
        # d'une image node incomplète (tests/test_full_image.py) : la préparation l'autorise donc explicitement.
        res = subprocess.run(
            ['bash', str(self.scripts_dir / 'task-workspace.sh'), 'prepare', str(task_file), '--allow-slim-image'],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        return task_id

    def run_sandbox(self, *args, env=None):
        run_env = dict(self.env)
        if env:
            run_env.update(env)
        return subprocess.run(
            ['bash', str(self.scripts_dir / 'sandbox.sh'), *args], cwd=str(self.root), capture_output=True, text=True, env=run_env
        )

    def test_start_security_options_and_no_docker_sock_or_home(self):
        task_id = self.prepare_task('task-sec', profile='python', network='off')
        res = self.run_sandbox('start', task_id)
        self.assertEqual(res.returncode, 0, res.stderr)

        calls = self.get_docker_calls()
        run_call = next((c for c in calls if c and c[0] == 'run'), None)
        self.assertIsNotNone(run_call, "Aucun appel à 'docker run' trouvé")

        # 1. Utilisateur non-root
        self.assertIn('--user', run_call)
        user_idx = run_call.index('--user')
        self.assertEqual(run_call[user_idx + 1], '1000:1000')

        # 2. --cap-drop ALL
        self.assertIn('--cap-drop', run_call)
        cap_idx = run_call.index('--cap-drop')
        self.assertEqual(run_call[cap_idx + 1], 'ALL')

        # 3. --security-opt no-new-privileges
        self.assertIn('--security-opt', run_call)
        sec_idx = run_call.index('--security-opt')
        self.assertEqual(run_call[sec_idx + 1], 'no-new-privileges')

        # 4. Limites cpus et memory
        self.assertIn('--cpus', run_call)
        self.assertIn('--memory', run_call)

        # 5. Réseau coupé par défaut
        self.assertIn('--network', run_call)
        net_idx = run_call.index('--network')
        self.assertEqual(run_call[net_idx + 1], 'none')

        # 6. Read-only avec tmpfs
        self.assertIn('--read-only', run_call)
        self.assertIn('--tmpfs', run_call)
        tmpfs_idx = run_call.index('--tmpfs')
        self.assertEqual(run_call[tmpfs_idx + 1], '/tmp:rw,exec,size=1g,mode=1777')
        self.assertEqual(run_call[run_call.index('--pids-limit') + 1], '512')
        self.assertIn('NODE_OPTIONS=--max-old-space-size=3072', run_call)
        self.assertIn('XDG_CACHE_HOME=/tmp/.cache', run_call)
        self.assertIn('npm_config_cache=/tmp/.npm', run_call)

        # 7. Worktree monté sur /work
        worktree_path = workdir.task_worktree(self.root, task_id).resolve()
        # En lecture seule : l'hôte lit ce worktree, le conteneur n'y pose pas de lien.
        expected_work_mount = f"{worktree_path}:/work:ro"
        self.assertIn(expected_work_mount, run_call)
        self.assertFalse(any(arg.endswith(':/work:rw') for arg in run_call))

        # 8. Masquage du dossier .git du clone par un dossier vide en lecture seule
        self.assertTrue((worktree_path / '.git').is_dir())
        empty_git_dir = (self.root / f'.workspace/orchestration/tasks/{task_id}/sandbox/empty_git_dir').resolve()
        self.assertTrue(empty_git_dir.is_dir())
        self.assertEqual(list(empty_git_dir.iterdir()), [])
        expected_git_mount = f"{empty_git_dir}:/work/.git:ro"
        self.assertIn(expected_git_mount, run_call)

        # 9. Jamais /var/run/docker.sock ni le dossier personnel
        for arg in run_call:
            self.assertNotIn('/var/run/docker.sock', arg)
            home_dir = str(Path.home())
            # Si le test tourne dans un sous-dossier de home, s'assurer qu'aucun montage n'est juste home
            self.assertNotEqual(arg, f"{home_dir}:/work:rw")
            self.assertNotEqual(arg, f"-v {home_dir}:/work:rw")

        # 10. Nom et étiquettes uniques par tâche
        self.assertIn('--name', run_call)
        name_idx = run_call.index('--name')
        self.assertEqual(
            run_call[name_idx + 1], f'workspace-sandbox-{hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:10]}-{task_id}'
        )
        self.assertIn(f'workspace.task_id={task_id}', ' '.join(run_call))

        self.assertIn('workspace.project=' + hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:10], run_call)

        # 11. Image Python par défaut
        self.assertIn('python:3.12-slim-bookworm', run_call)

    def run_call_of(self, task_id):
        res = self.run_sandbox('start', task_id)
        self.assertEqual(res.returncode, 0, res.stderr)
        return next(c for c in self.get_docker_calls() if c and c[0] == 'run')

    def test_circuit_paths_of_the_worktree_are_mounted_read_only_over_the_work_mount(self):
        task_id = self.prepare_task('task-ro', profile='python')
        worktree = workdir.task_worktree(self.root, task_id).resolve()
        (worktree / '.claude').mkdir()
        run_call = self.run_call_of(task_id)
        work = run_call.index(f'{worktree}:/work:ro')
        for name in ('scripts', '.claude', 'project.profile'):
            with self.subTest(name=name):
                mount = f'{worktree / name}:/work/{name}:ro'
                self.assertIn(mount, run_call)
                index = run_call.index(mount)
                self.assertEqual(run_call[index - 1], '-v')
                self.assertGreater(index, work)
                self.assertLess(index, run_call.index('-w'))

    def sandbox_dir_of(self, task_id):
        return (self.root / f'.workspace/orchestration/tasks/{task_id}/sandbox').resolve()

    def test_scripts_and_claude_are_always_mounted_but_project_profile_only_when_present(self):
        task_id = self.prepare_task('task-ro-missing')
        worktree = workdir.task_worktree(self.root, task_id).resolve()
        self.assertFalse((worktree / '.claude').exists())
        self.assertFalse((worktree / 'project.profile').exists())
        run_call = self.run_call_of(task_id)
        mounts = [arg for arg in run_call if arg.endswith(':ro') and not arg.endswith(':/work/.git:ro') and not arg.endswith(':/work:ro')]
        empty_claude = self.sandbox_dir_of(task_id) / 'empty_claude'
        self.assertEqual(mounts, [f'{worktree / "scripts"}:/work/scripts:ro', f'{empty_claude}:/work/.claude:ro'])
        self.assertEqual(run_call[run_call.index(mounts[1]) - 1], '-v')
        self.assertGreater(run_call.index(mounts[1]), run_call.index(f'{worktree}:/work:ro'))
        self.assertTrue(empty_claude.is_dir())
        self.assertEqual(list(empty_claude.iterdir()), [])
        self.assertFalse(any(arg.endswith(':/work/project.profile:ro') for arg in run_call))

    def test_an_existing_claude_folder_is_mounted_from_the_worktree_not_from_the_placeholder(self):
        task_id = self.prepare_task('task-ro-claude', profile='python')
        worktree = workdir.task_worktree(self.root, task_id).resolve()
        (worktree / '.claude').mkdir()
        run_call = self.run_call_of(task_id)
        self.assertIn(f'{worktree / ".claude"}:/work/.claude:ro', run_call)
        self.assertEqual([arg for arg in run_call if arg.endswith(':/work/.claude:ro')], [f'{worktree / ".claude"}:/work/.claude:ro'])
        self.assertFalse((self.sandbox_dir_of(task_id) / 'empty_claude').exists())

    def test_a_missing_scripts_folder_is_replaced_by_an_empty_read_only_one(self):
        task_id = self.prepare_task('task-ro-noscripts', profile='python')
        worktree = workdir.task_worktree(self.root, task_id).resolve()
        shutil.rmtree(worktree / 'scripts')
        run_call = self.run_call_of(task_id)
        empty_scripts = self.sandbox_dir_of(task_id) / 'empty_scripts'
        self.assertIn(f'{empty_scripts}:/work/scripts:ro', run_call)
        self.assertEqual(list(empty_scripts.iterdir()), [])
        self.assertEqual(empty_scripts.stat().st_mode & 0o777, 0o500)

    def test_the_empty_claude_placeholder_is_read_only_and_survives_a_second_start(self):
        task_id = self.prepare_task('task-ro-twice', profile='python')
        self.run_call_of(task_id)
        empty_claude = self.sandbox_dir_of(task_id) / 'empty_claude'
        self.assertEqual(empty_claude.stat().st_mode & 0o777, 0o500)
        self.assertIn(f'{empty_claude}:/work/.claude:ro', self.run_call_of(task_id))

    def test_a_linked_empty_claude_placeholder_is_refused(self):
        task_id = self.prepare_task('task-ro-placeholder-link', profile='python')
        outside = Path(self.temp.name).resolve() / 'dehors'
        outside.mkdir()
        sandbox = self.sandbox_dir_of(task_id)
        sandbox.mkdir(parents=True, exist_ok=True)
        os.symlink(outside, sandbox / 'empty_claude')
        res = self.run_sandbox('start', task_id)
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse(any(c and c[0] == 'run' for c in self.get_docker_calls()))

    def test_integration_worktree_gets_the_same_read_only_mounts(self):
        task_id = self.prepare_task('task-ro-integ', profile='python')
        integration = self.root / '.workspace/integration' / task_id
        (integration / 'scripts').mkdir(parents=True)
        (integration / '.git').write_text('gitdir: /elsewhere\n')
        self.set_state_worktree(task_id, '.workspace/integration/' + task_id)
        run_call = self.run_call_of(task_id)
        self.assertIn(f'{integration.resolve() / "scripts"}:/work/scripts:ro', run_call)
        self.assertNotIn(f'{integration.resolve() / ".claude"}:/work/.claude:ro', run_call)

    def test_a_linked_circuit_path_in_the_worktree_is_refused(self):
        task_id = self.prepare_task('task-ro-link', profile='python')
        worktree = workdir.task_worktree(self.root, task_id).resolve()
        outside = Path(self.temp.name).resolve() / 'dehors'
        outside.mkdir()
        shutil.rmtree(worktree / 'scripts')
        os.symlink(outside, worktree / 'scripts')
        res = self.run_sandbox('start', task_id)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('scripts', res.stderr)
        self.assertFalse(any(c and c[0] == 'run' for c in self.get_docker_calls()))

    def test_start_runs_real_init_as_pid_one_before_image(self):
        task_id = self.prepare_task('task-init', profile='python', network='off')
        res = self.run_sandbox('start', task_id)
        self.assertEqual(res.returncode, 0, res.stderr)

        run_call = next((c for c in self.get_docker_calls() if c and c[0] == 'run'), None)
        self.assertIsNotNone(run_call, "Aucun appel à 'docker run' trouvé")
        self.assertIn('--init', run_call)
        self.assertLess(run_call.index('--init'), run_call.index('python:3.12-slim-bookworm'))

    def test_start_with_network_on_still_offline(self):
        task_id = self.prepare_task('task-net', profile='node', network='on')
        res = self.run_sandbox('start', task_id)
        self.assertEqual(res.returncode, 0, res.stderr)

        calls = self.get_docker_calls()
        run_call = next((c for c in calls if c and c[0] == 'run'), None)
        self.assertIsNotNone(run_call)
        net_idx = run_call.index('--network')
        self.assertEqual(run_call[net_idx + 1], 'none')
        self.assertIn('node:24-bookworm-slim', run_call)

    def test_image_override_via_environment_variables(self):
        task_id = self.prepare_task('task-img', profile='node')
        res = self.run_sandbox('start', task_id, env={'WORKSPACE_IMAGE_NODE': 'custom-node:latest'})
        self.assertEqual(res.returncode, 0, res.stderr)
        calls = self.get_docker_calls()
        run_call = next((c for c in calls if c and c[0] == 'run'), None)
        self.assertIn('custom-node:latest', run_call)

    def test_exec_returns_command_exit_code_and_masks_secrets(self):
        task_id = self.prepare_task('task-exec', profile='python')
        self.assertEqual(self.run_sandbox('start', task_id).returncode, 0)

        # Simulation d'un exec avec code 42 et secret dans la sortie
        simulated_output = "Mot de passe découvert: super-secret-token-12345 et clé sk-ant-api03-abcdef1234567890\n"
        res = self.run_sandbox('exec', task_id, '--', 'pytest', env={'SIMULATE_EXEC_CODE': '42', 'SIMULATE_EXEC_OUTPUT': simulated_output})
        # Vérification du code de retour transmis
        self.assertEqual(res.returncode, 42)

        # Vérification du masquage sur stdout
        self.assertNotIn('super-secret-token-12345', res.stdout)
        self.assertIn('***', res.stdout)

        # Vérification de l'enregistrement dans exec-001.log
        log_file = self.root / f'.workspace/orchestration/tasks/{task_id}/sandbox/exec-001.log'
        self.assertTrue(log_file.is_file())
        logged = log_file.read_text(encoding='utf-8')
        self.assertNotIn('super-secret-token-12345', logged)
        self.assertIn('***', logged)

        # Deuxième exécution : incrément de numérotation exec-002.log
        res2 = self.run_sandbox('exec', task_id, 'ls', env={'SIMULATE_EXEC_CODE': '0'})
        self.assertEqual(res2.returncode, 0)
        log_file2 = self.root / f'.workspace/orchestration/tasks/{task_id}/sandbox/exec-002.log'
        self.assertTrue(log_file2.is_file())

    def sandbox_run_call(self, res):
        self.assertEqual(res.returncode, 0, res.stderr)
        return next((c for c in self.get_docker_calls() if c and c[0] == 'run' and 'workspace.role=sandbox' in c), None)

    def image_inspect_calls(self):
        return [c for c in self.get_docker_calls() if c[:2] == ['image', 'inspect']]

    def test_node_profile_prefers_full_image_when_present(self):
        task_id = self.prepare_task('task-full', profile='node')
        res = self.run_sandbox('start', task_id, env={'FAKE_IMAGE_ID': 'sha256:0123456789ab'})
        run_call = self.sandbox_run_call(res)
        self.assertIn('workspace-node-git:24-bookworm', run_call)
        self.assertNotIn('node:24-bookworm-slim', run_call)
        self.assertNotIn('workspace-node-git', res.stderr)
        self.assertEqual(len(self.image_inspect_calls()), 1)

    def test_node_profile_falls_back_with_one_build_hint_when_full_image_absent(self):
        task_id = self.prepare_task('task-absent', profile='node')
        res = self.run_sandbox('start', task_id)
        run_call = self.sandbox_run_call(res)
        self.assertIn('node:24-bookworm-slim', run_call)
        self.assertNotIn('workspace-node-git:24-bookworm', run_call)
        lines = res.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn('docker build -t workspace-node-git:24-bookworm -f template/docker/node-git.Dockerfile template/docker', lines[0])

    def test_empty_image_inspect_output_counts_as_absent(self):
        task_id = self.prepare_task('task-empty-id', profile='node')
        res = self.run_sandbox('start', task_id, env={'FAKE_IMAGE_ID': ''})
        run_call = self.sandbox_run_call(res)
        self.assertIn('node:24-bookworm-slim', run_call)
        self.assertNotIn('workspace-node-git:24-bookworm', run_call)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1)

    def test_workspace_image_wins_without_probing_docker(self):
        task_id = self.prepare_task('task-override', profile='node')
        res = self.run_sandbox(
            'start', task_id, env={'WORKSPACE_IMAGE': 'custom-any:1', 'WORKSPACE_IMAGE_NODE': 'custom-node:1', 'FAKE_IMAGE_ID': 'sha256:x'}
        )
        run_call = self.sandbox_run_call(res)
        self.assertIn('custom-any:1', run_call)
        self.assertEqual(self.image_inspect_calls(), [])
        self.assertEqual(res.stderr, '')

    def test_workspace_image_node_skips_probe(self):
        task_id = self.prepare_task('task-node-override', profile='node')
        res = self.run_sandbox('start', task_id, env={'WORKSPACE_IMAGE_NODE': 'custom-node:1', 'FAKE_IMAGE_ID': 'sha256:x'})
        run_call = self.sandbox_run_call(res)
        self.assertIn('custom-node:1', run_call)
        self.assertEqual(self.image_inspect_calls(), [])

    def test_empty_workspace_image_node_is_treated_as_absent(self):
        task_id = self.prepare_task('task-node-empty', profile='node')
        res = self.run_sandbox('start', task_id, env={'WORKSPACE_IMAGE_NODE': '', 'FAKE_IMAGE_ID': 'sha256:x'})
        run_call = self.sandbox_run_call(res)
        self.assertIn('workspace-node-git:24-bookworm', run_call)
        self.assertEqual(len(self.image_inspect_calls()), 1)

    def test_python_profile_never_probes_full_image(self):
        task_id = self.prepare_task('task-py-noprobe', profile='python')
        res = self.run_sandbox('start', task_id, env={'FAKE_IMAGE_ID': 'sha256:x'})
        run_call = self.sandbox_run_call(res)
        self.assertIn('python:3.12-slim-bookworm', run_call)
        self.assertEqual(self.image_inspect_calls(), [])
        self.assertEqual(res.stderr, '')

    def test_node_full_dockerfile_starts_from_node_and_documents_build(self):
        dockerfile = ROOT / 'template/docker/node-git.Dockerfile'
        text = dockerfile.read_text(encoding='utf-8')
        first_instruction = next(line for line in text.splitlines() if line and not line.startswith('#'))
        self.assertEqual(first_instruction, 'FROM node:24-bookworm-slim')
        self.assertIn('docker build -t workspace-node-git:24-bookworm -f template/docker/node-git.Dockerfile template/docker', text)

    def test_stop_is_idempotent_and_preserves_worktree(self):
        task_id = self.prepare_task('task-stop', profile='python')
        self.assertEqual(self.run_sandbox('start', task_id).returncode, 0)

        worktree_dir = workdir.task_worktree(self.root, task_id)
        test_file = worktree_dir / 'src/worktree_artifact.txt'
        test_file.write_text('données importantes de l\'agent')

        # Premier stop
        res1 = self.run_sandbox('stop', task_id)
        self.assertEqual(res1.returncode, 0, res1.stderr)
        self.assertTrue(test_file.exists())
        self.assertEqual(test_file.read_text(), 'données importantes de l\'agent')

        # Deuxième stop (idempotent)
        res2 = self.run_sandbox('stop', task_id)
        self.assertEqual(res2.returncode, 0, res2.stderr)
        self.assertTrue(test_file.exists())

    def test_status_returns_json(self):
        task_id = self.prepare_task('task-stat', profile='python')
        self.assertEqual(self.run_sandbox('start', task_id).returncode, 0)

        res = self.run_sandbox('status', task_id)
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data.get('task_id'), task_id)
        self.assertEqual(data.get('status'), 'running')

    def test_unknown_task_fails_cleanly(self):
        res = self.run_sandbox('start', 'non-existent-task')
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['inconnue', 'introuvable', 'erreur']))

    def test_refuse_symlink_worktree(self):
        task_id = self.prepare_task('task-sym', profile='python')
        # On remplace le worktree par un lien symbolique
        wt = workdir.task_worktree(self.root, task_id)
        shutil.rmtree(wt)
        target = self.root / 'src'
        wt.symlink_to(target)

        res = self.run_sandbox('start', task_id)
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in (res.stderr + res.stdout).lower() for w in ['symbolique', 'symlink']))

    def set_state_worktree(self, task_id, value):
        state_file = self.root / f'.workspace/orchestration/tasks/{task_id}/state.json'
        state = json.loads(state_file.read_text(encoding='utf-8'))
        state['worktree'] = value
        state_file.write_text(json.dumps(state), encoding='utf-8')

    def test_refuse_worktree_other_than_clone_or_integration(self):
        task_id = self.prepare_task('task-other', profile='python')
        other_clone = workdir.task_worktree(self.root, 'task-someone-else')
        other_clone.mkdir(parents=True)
        for value in (
            '.workspace/worktrees/task-other',
            'src',
            str(self.root / 'src'),
            str(other_clone),
            '.workspace/integration/task-someone-else',
            str(Path(self.temp.name)),
        ):
            with self.subTest(value=value):
                self.set_state_worktree(task_id, value)
                res = self.run_sandbox('start', task_id)
                self.assertNotEqual(res.returncode, 0)
                self.assertFalse(any(c and c[0] == 'run' for c in self.get_docker_calls()))

    def test_accept_integration_worktree_and_mask_its_git_file(self):
        task_id = self.prepare_task('task-integ', profile='python')
        integration = self.root / '.workspace/integration' / task_id
        integration.mkdir(parents=True)
        (integration / '.git').write_text('gitdir: /elsewhere\n')
        self.set_state_worktree(task_id, '.workspace/integration/' + task_id)
        res = self.run_sandbox('start', task_id)
        self.assertEqual(res.returncode, 0, res.stderr)
        run_call = next(c for c in self.get_docker_calls() if c and c[0] == 'run')
        self.assertIn(f'{integration.resolve()}:/work:rw', run_call)
        empty_git_file = (self.root / f'.workspace/orchestration/tasks/{task_id}/sandbox/empty_git').resolve()
        self.assertTrue(empty_git_file.is_file())
        self.assertIn(f'{empty_git_file}:/work/.git:ro', run_call)

    def test_docker_missing_in_path_gives_clear_message(self):
        task_id = self.prepare_task('task-nodocker', profile='python')
        # python3 may live outside /usr/bin (python:3.12-slim: /usr/local/bin) and the launcher needs it, but its
        # directory may also hold a real docker (Homebrew): expose python3 alone through a wrapper, never a link.
        python_bin = Path(self.temp.name) / 'python-only-bin'
        python_bin.mkdir()
        (python_bin / 'python3').write_text('#!/bin/sh\nexec "%s" "$@"\n' % shutil.which('python3'))
        (python_bin / 'python3').chmod(0o755)
        limited_path = os.pathsep.join([str(python_bin), '/usr/bin', '/bin'])
        res = self.run_sandbox('start', task_id, env={'PATH': limited_path, 'FAKE_DOCKER_BIN': ''})
        # Si /usr/bin/docker n'existe pas ou si docker n'est pas trouvé
        if not shutil.which('docker', path=limited_path):
            self.assertNotEqual(res.returncode, 0)
            self.assertIn('docker', res.stderr.lower())

    def test_docker_daemon_stopped_gives_clear_message(self):
        task_id = self.prepare_task('task-daemon-stop', profile='python')
        res = self.run_sandbox('start', task_id, env={'SIMULATE_DOCKER_STOPPED': '1'})
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(any(w in res.stderr.lower() for w in ['arrêté', 'inaccessible', 'daemon', 'moteur']))

    def load_sandbox(self):
        spec = importlib.util.spec_from_file_location('sandbox_work_mount', ROOT / 'template/scripts/lib/sandbox.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def make_worktree(self, name, git_is_file=False):
        worktree = Path(self.temp.name).resolve() / name
        worktree.mkdir()
        if git_is_file:
            (worktree / '.git').write_text('gitdir: /elsewhere\n')
        else:
            (worktree / '.git').mkdir()
        return worktree

    def test_work_mount_is_read_only_for_a_task_clone_and_writable_for_the_integration_worktree(self):
        sandbox = self.load_sandbox()
        clone = self.make_worktree('clone')
        integration = self.make_worktree('integration', git_is_file=True)
        self.assertEqual(sandbox.work_mount(clone), ['-v', f'{clone}:/work:ro'])
        self.assertEqual(sandbox.work_mount(integration), ['-v', f'{integration}:/work:rw'])
        self.assertEqual(sandbox.work_mount(clone / '..' / 'clone'), ['-v', f'{clone}:/work:ro'])

    def test_writable_dirs_per_profile(self):
        sandbox = self.load_sandbox()
        for profile in ('python', 'docs', 'c'):
            self.assertEqual(sandbox.WRITABLE_DIRS[profile], ('.workspace',))
        self.assertEqual(sandbox.WRITABLE_DIRS['node'], ('.workspace', 'dist', 'coverage', 'test-results', 'playwright-report'))

    def test_writable_overlays_create_the_mount_points_and_mount_them_in_memory(self):
        sandbox = self.load_sandbox()
        clone = self.make_worktree('clone')
        self.assertEqual(sandbox.writable_overlays(clone, 'python'), ['--tmpfs', '/work/.workspace:rw,exec,size=256m,mode=1777'])
        self.assertTrue((clone / '.workspace').is_dir())
        self.assertEqual(sandbox.writable_overlays(clone, 'no-such-profile'), ['--tmpfs', '/work/.workspace:rw,exec,size=256m,mode=1777'])
        self.assertFalse((clone / 'dist').exists())
        overlays = sandbox.writable_overlays(clone, 'node')
        self.assertEqual(
            overlays[1::2],
            [
                f'/work/{name}:rw,exec,size=256m,mode=1777'
                for name in ('.workspace', 'dist', 'coverage', 'test-results', 'playwright-report')
            ],
        )
        self.assertEqual(overlays[0::2], ['--tmpfs'] * 5)
        for name in ('dist', 'coverage', 'test-results', 'playwright-report'):
            self.assertTrue((clone / name).is_dir(), name)

    def test_writable_overlays_keep_an_existing_folder_and_its_content(self):
        sandbox = self.load_sandbox()
        clone = self.make_worktree('clone')
        (clone / 'dist').mkdir()
        (clone / 'dist/a.js').write_text('x')
        sandbox.writable_overlays(clone, 'node')
        self.assertEqual((clone / 'dist/a.js').read_text(), 'x')

    def test_writable_overlays_are_empty_for_the_integration_worktree(self):
        sandbox = self.load_sandbox()
        integration = self.make_worktree('integration', git_is_file=True)
        self.assertEqual(sandbox.writable_overlays(integration, 'node'), [])
        self.assertFalse((integration / '.workspace').exists())

    def test_writable_overlays_refuse_a_link_or_a_file(self):
        sandbox = self.load_sandbox()
        outside = Path(self.temp.name).resolve() / 'dehors'
        outside.mkdir()
        linked = self.make_worktree('linked')
        os.symlink(outside, linked / '.workspace')
        with self.assertRaises(ValueError):
            sandbox.writable_overlays(linked, 'python')
        filed = self.make_worktree('filed')
        (filed / '.workspace').write_text('x')
        with self.assertRaises(ValueError):
            sandbox.writable_overlays(filed, 'python')
        linked_node = self.make_worktree('linked-node')
        os.symlink(outside, linked_node / 'dist')
        with self.assertRaises(ValueError):
            sandbox.writable_overlays(linked_node, 'node')
        self.assertEqual(list(outside.iterdir()), [])

    def test_start_mounts_the_task_clone_read_only_with_in_memory_work_folders(self):
        task_id = self.prepare_task('task-overlay', profile='python')
        worktree = workdir.task_worktree(self.root, task_id).resolve()
        run_call = self.run_call_of(task_id)
        self.assertIn(f'{worktree}:/work:ro', run_call)
        self.assertFalse(any(arg.endswith(':/work:rw') for arg in run_call))
        overlay = '/work/.workspace:rw,exec,size=256m,mode=1777'
        index = run_call.index(overlay)
        self.assertEqual(run_call[index - 1], '--tmpfs')
        self.assertGreater(index, run_call.index(f'{worktree / "scripts"}:/work/scripts:ro'))
        self.assertLess(index, run_call.index('-w'))
        self.assertGreater(run_call.index(f'{worktree}:/work:ro'), run_call.index('/tmp:rw,exec,size=1g,mode=1777'))

    def test_start_mounts_the_node_work_folders_in_memory(self):
        task_id = self.prepare_task('task-overlay-node', profile='node')
        worktree = workdir.task_worktree(self.root, task_id).resolve()
        self.assertFalse((worktree / 'dist').exists())
        run_call = self.run_call_of(task_id)
        self.assertTrue((worktree / 'dist').is_dir())
        for name in ('.workspace', 'dist', 'coverage', 'test-results', 'playwright-report'):
            with self.subTest(name=name):
                self.assertEqual(run_call.count(f'/work/{name}:rw,exec,size=256m,mode=1777'), 1)
                self.assertLess(run_call.index(f'/work/{name}:rw,exec,size=256m,mode=1777'), run_call.index('-w'))

    def test_start_refuses_a_linked_work_folder_and_runs_nothing(self):
        task_id = self.prepare_task('task-overlay-link', profile='python')
        worktree = workdir.task_worktree(self.root, task_id).resolve()
        outside = Path(self.temp.name).resolve() / 'dehors'
        outside.mkdir()
        shutil.rmtree(worktree / '.workspace', ignore_errors=True)
        if (worktree / '.workspace').exists():
            (worktree / '.workspace').unlink()
        os.symlink(outside, worktree / '.workspace')
        res = self.run_sandbox('start', task_id)
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse(any(c and c[0] == 'run' for c in self.get_docker_calls()))
        self.assertEqual(list(outside.iterdir()), [])

    def test_start_of_the_integration_worktree_has_no_in_memory_folder(self):
        task_id = self.prepare_task('task-overlay-integ', profile='python')
        integration = self.root / '.workspace/integration' / task_id
        integration.mkdir(parents=True)
        (integration / '.git').write_text('gitdir: /elsewhere\n')
        self.set_state_worktree(task_id, '.workspace/integration/' + task_id)
        run_call = self.run_call_of(task_id)
        self.assertFalse(any(arg.startswith('/work/.workspace:') for arg in run_call))
        self.assertNotIn(f'{integration.resolve()}:/work:ro', run_call)


if __name__ == '__main__':
    unittest.main()
