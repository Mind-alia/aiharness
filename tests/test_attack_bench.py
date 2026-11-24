from __future__ import annotations

import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel, sous-processus ou Docker simulé ; suite complète seulement')

ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = ROOT / '.workspace/tmp-tests'
TEST_TMP.mkdir(parents=True, exist_ok=True)


class AttackBenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.temp.cleanup)
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

        # codex.sh : le scénario hôte « configuration de Codex » le joue dans un projet factice.
        for script_name in ('task-workspace.sh', 'sandbox.sh', 'ask.sh', 'answer.sh', 'attack-bench.sh', 'codex.sh'):
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
        self.bin_dir = self.root.parent / (self.root.name + '-bin')
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.docker_log_file = self.root.parent / (self.root.name + '-docker_calls.json')
        self.setup_fake_docker()
        self.setup_fake_npx()

        self.env = dict(
            os.environ, PATH=str(self.bin_dir) + os.pathsep + os.environ.get('PATH', ''), FAKE_DOCKER_LOG=str(self.docker_log_file)
        )

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
    print("fake-container-id-12345")
    sys.exit(0)
elif sub == 'inspect':
    fmt = ''
    if '--format' in sys.argv:
        fmt = sys.argv[sys.argv.index('--format') + 1]
    started = False
    for c in calls[:-1]:
        if c and c[0] == 'run' and 'workspace.role=sandbox' in c:
            started = True
        elif c and c[0] == 'rm':
            started = False
    if not started and os.environ.get('FAKE_ASSUME_RUNNING') != '1':
        print("Error: No such object", file=sys.stderr)
        sys.exit(1)
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
    cmd_str = ' '.join(sys.argv)
    
    # 1. Fuite de secret simulée
    if os.environ.get('SIMULATE_SECRET_IN_OUTPUT') == '1':
        print("Données confidentielles: super-secret-token-12345", file=sys.stderr)

    # 2. Outil réseau manquant
    if os.environ.get('SIMULATE_NO_NET_TOOL') == '1':
        if 'Promise.any' in cmd_str or 'urllib' in cmd_str or 'NO_NET_TOOL' in cmd_str:
            print("NO_NET_TOOL: aucun outil réseau disponible (node, python3, curl, wget)", file=sys.stderr)
            sys.exit(125)

    # 3. Attaque qui réussit (FAILLE)
    faille_id = os.environ.get('SIMULATE_FAILLE_ATTACK', '')
    if faille_id == 'write-etc' and '/etc/attaque' in cmd_str:
        print("Fichier /etc/attaque créé avec succès !")
        sys.exit(0)
    elif faille_id == 'priv-root-user' and 'id -u' in cmd_str:
        print("UID est 0 (root)")
        sys.exit(0)
    elif faille_id == 'docker-sock' and 'docker.sock' in cmd_str:
        print("docker.sock accessible")
        sys.exit(0)

    # 4. Par défaut : toutes les attaques échouent dans le conteneur confiné (bloquées)
    print("Action interdite : Read-only file system / Permission denied", file=sys.stderr)
    sys.exit(1)
elif sub == 'rm':
    sys.exit(0)
else:
    sys.exit(0)
'''.replace('__DOCKER_LOG__', str(self.docker_log_file))
        docker_path = self.bin_dir / 'docker'
        docker_path.write_text(fake_script, encoding='utf-8')
        docker_path.chmod(0o755)

    def setup_fake_npx(self):
        # Le scénario hôte « Codex lit l'hôte » appelle `npx` : un faux bac à sable, jamais le vrai Codex.
        from tests import test_attack_host as host_fakes

        npx = self.bin_dir / 'npx'
        npx.write_text(host_fakes.SANDBOX_NPX, encoding='utf-8')
        npx.chmod(0o755)

    def get_docker_calls(self):
        if not self.docker_log_file.exists():
            return []
        try:
            return json.loads(self.docker_log_file.read_text(encoding='utf-8'))
        except Exception:
            return []

    def prepare_task(self, task_id='task-attack', profile='python', network='off'):
        task_file = self.root / f'tasks/{task_id}.md'
        task_file.parent.mkdir(parents=True, exist_ok=True)
        content = f"---\nid: {task_id}\nagent: codex\nallowed:\n  - src/**\nnetwork: {network}\n---\nConsigne de test banc d'attaque\n"
        task_file.write_text(content, encoding='utf-8')

        if profile:
            (self.root / 'project.profile').write_text(profile)
            self.git('add', 'project.profile')
            self.git('commit', '-qm', 'profile')

        res = subprocess.run(
            ['bash', str(self.scripts_dir / 'task-workspace.sh'), 'prepare', str(task_file)],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        return task_id

    def run_bench(self, *args, env=None):
        run_env = dict(self.env)
        if env:
            run_env.update(env)
        return subprocess.run(
            ['bash', str(self.scripts_dir / 'attack-bench.sh'), *args], cwd=str(self.root), capture_output=True, text=True, env=run_env
        )

    def test_all_attacks_blocked_returns_code_0_and_n_of_n(self):
        task_id = self.prepare_task('task-all-blocked', profile='python')
        res = self.run_bench(task_id)
        self.assertEqual(res.returncode, 0, f"Erreur : {res.stderr}\nStdout : {res.stdout}")

        # Vérification du tableau texte et de la mention finale
        self.assertIn('Attaques bloquées : 28/28', res.stdout)
        self.assertIn('write-etc', res.stdout)
        self.assertIn('write-worktree-scripts', res.stdout)
        self.assertIn('bloquée', res.stdout)
        self.assertNotIn('FAILLE', res.stdout)

        # Vérification du fichier JSON des métriques
        metrics_file = self.root / '.workspace/metrics/attack-bench.json'
        self.assertTrue(metrics_file.is_file())
        data = json.loads(metrics_file.read_text(encoding='utf-8'))
        self.assertEqual(data['blocked'], 28)
        self.assertEqual(data['total'], 28)
        self.assertEqual(data['failles'], [])
        self.assertEqual(data['non_testables'], [])

    def test_single_attack_success_causes_faille_and_exit_1(self):
        task_id = self.prepare_task('task-one-faille', profile='python')
        res = self.run_bench(task_id, env={'SIMULATE_FAILLE_ATTACK': 'write-etc'})
        self.assertEqual(res.returncode, 1, f"Attendu code 1, reçu {res.returncode}")

        self.assertIn('FAILLE', res.stdout)
        self.assertIn('Attaques bloquées : 27/28', res.stdout)

        metrics_file = self.root / '.workspace/metrics/attack-bench.json'
        self.assertTrue(metrics_file.is_file())
        data = json.loads(metrics_file.read_text(encoding='utf-8'))
        self.assertEqual(data['blocked'], 27)
        self.assertEqual(data['total'], 28)
        self.assertEqual(data['failles'], ['write-etc'])

    def test_network_tool_missing_marks_non_testable(self):
        task_id = self.prepare_task('task-no-net-tool', profile='python')
        res = self.run_bench(task_id, env={'SIMULATE_NO_NET_TOOL': '1'})
        # Aucune faille : le code de retour doit être 0
        self.assertEqual(res.returncode, 0, f"Erreur : {res.stderr}\nStdout : {res.stdout}")

        self.assertIn('non testable', res.stdout)
        self.assertIn('aucun outil réseau disponible', res.stdout)
        self.assertIn('Attaques bloquées : 27/28', res.stdout)

        metrics_file = self.root / '.workspace/metrics/attack-bench.json'
        self.assertTrue(metrics_file.is_file())
        data = json.loads(metrics_file.read_text(encoding='utf-8'))
        self.assertEqual(data['blocked'], 27)
        self.assertEqual(data['total'], 28)
        self.assertEqual(data['failles'], [])
        self.assertIn('net-outbound', data['non_testables'])

    def test_container_stopped_even_on_exception(self):
        task_id = self.prepare_task('task-exc-stop', profile='python')

        # Chargement direct du module pour injecter une exception Python
        spec = importlib.util.spec_from_file_location('attack_bench', self.lib_dir / 'attack_bench.py')
        attack_bench_mod = importlib.util.module_from_spec(spec)
        # dataclasses (Python 3.9) look the module up in sys.modules while executing it.
        sys.modules['attack_bench'] = attack_bench_mod
        self.addCleanup(sys.modules.pop, 'attack_bench', None)
        spec.loader.exec_module(attack_bench_mod)

        # Simulation d'une exception inattendue pendant l'exécution d'une attaque
        # Same fake docker as the subprocess tests: never a real container.
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch.object(attack_bench_mod, 'run_attack', side_effect=RuntimeError("Erreur fatale simulée")),
        ):
            with self.assertRaises(RuntimeError):
                attack_bench_mod.run_attack_bench(self.root, task_id, False)

        # Le conteneur doit avoir été arrêté (docker rm appelé)
        calls = self.get_docker_calls()
        rm_calls = [c for c in calls if c and c[0] == 'rm']
        self.assertTrue(len(rm_calls) > 0, "sandbox.sh stop doit être appelé dans le bloc finally")

    def test_docker_unavailable_returns_code_4(self):
        task_id = self.prepare_task('task-docker-down', profile='python')
        res = self.run_bench(task_id, env={'SIMULATE_DOCKER_STOPPED': '1'})
        self.assertEqual(res.returncode, 4, f"Attendu code 4 lorsque Docker est indisponible, reçu {res.returncode}")
        self.assertIn('indisponible', res.stderr)

    def test_json_and_markdown_reports_and_no_overwrite(self):
        task_id = self.prepare_task('task-reports', profile='python')
        res = self.run_bench(task_id, '--markdown')
        self.assertEqual(res.returncode, 0, res.stderr)

        # 1. Vérification du fichier JSON des métriques et permissions
        metrics_file = self.root / '.workspace/metrics/attack-bench.json'
        self.assertTrue(metrics_file.is_file())
        # Permissions fichier 0600, dossier 0700
        file_mode = metrics_file.stat().st_mode & 0o777
        dir_mode = metrics_file.parent.stat().st_mode & 0o777
        self.assertEqual(file_mode, 0o600, f"Permissions attendues 0600, trouvées {oct(file_mode)}")
        self.assertEqual(dir_mode, 0o700, f"Permissions attendues 0700, trouvées {oct(dir_mode)}")

        # 2. Vérification du rapport Markdown
        today = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d')  # same UTC date as attack_bench.py
        md_file_1 = self.root / f'docs/security/attack-bench-{today}.md'
        self.assertTrue(md_file_1.is_file(), f"Fichier markdown introuvable : {md_file_1}")
        content_1 = md_file_1.read_text(encoding='utf-8')
        self.assertIn(f"Rapport du banc d'attaque — Tâche `{task_id}`", content_1)
        self.assertIn('| write-etc |', content_1)

        # 3. Deuxième exécution avec --markdown : ne doit JAMAIS écraser, doit ajouter un suffixe
        res2 = self.run_bench(task_id, '--markdown')
        self.assertEqual(res2.returncode, 0, res2.stderr)

        md_file_2 = self.root / f'docs/security/attack-bench-{today}-1.md'
        self.assertTrue(md_file_2.is_file(), f"Fichier markdown avec suffixe introuvable : {md_file_2}")
        self.assertTrue(md_file_1.is_file(), "Le premier rapport markdown ne doit pas avoir été écrasé")

    def install_host_fakes(self):
        from tests import test_attack_host as host_fakes

        (self.lib_dir / 'hooks_guard.py').write_text(host_fakes.GOOD_HOOK, encoding='utf-8')
        (self.scripts_dir / 'codex.sh').write_text(host_fakes.CLEAN_CODEX, encoding='utf-8')

    def test_host_scenarios_are_played_by_default_and_reported_everywhere(self):
        from tests import test_attack_host as host_fakes

        task_id = self.prepare_task('task-host', profile='python')
        self.install_host_fakes()
        res = self.run_bench(task_id, '--markdown')
        for scenario_id in host_fakes.IDS:
            self.assertIn(scenario_id, res.stdout)
        self.assertRegex(res.stdout, r'Attaques bloquées : \d+/28')
        data = json.loads((self.root / '.workspace/metrics/attack-bench.json').read_text(encoding='utf-8'))
        self.assertEqual(data['total'], 28)
        record = json.loads((self.root / '.workspace/security/last-attack-bench.json').read_text(encoding='utf-8'))
        self.assertEqual(record['total'], 28)
        today = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d')
        report = (self.root / f'docs/security/attack-bench-{today}.md').read_text(encoding='utf-8')
        for scenario_id in host_fakes.IDS:
            self.assertIn('| %s |' % scenario_id, report)
        self.assertEqual(res.returncode, 1 if data['failles'] else 0)

    def test_host_results_are_taken_from_the_host_module(self):
        bench = self.load_bench_module()
        fake = [{'id': 'host-x', 'category': 'Hôte', 'description': 'd', 'status': 'FAILLE', 'reason': 'r'}]
        with patch.object(bench.attack_host, 'run_host_scenarios', return_value=fake):
            results = bench.host_results(self.root, 'tache', self.root, {})
        self.assertEqual([(r.attack.id, r.attack.category, r.status, r.reason) for r in results], [('host-x', 'Hôte', 'FAILLE', 'r')])

    def test_parse_args_reads_the_host_flag(self):
        bench = self.load_bench_module()
        self.assertEqual(bench.parse_args([str(self.root), 'tache', '--host'])[2:], (False, True))
        self.assertEqual(bench.parse_args([str(self.root), '--markdown', 'tache'])[2:], (True, False))

    def test_host_option_is_still_accepted_and_changes_nothing(self):
        task_id = self.prepare_task('task-host-flag', profile='python')
        self.install_host_fakes()
        for args in ((), ('--host',)):
            res = self.run_bench(task_id, *args)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertRegex(res.stdout, r'Attaques bloquées : \d+/28')
            self.assertIn('host-orchestrator-allowlist', res.stdout)

    def test_six_host_scenarios_are_played_and_blocked_with_the_real_guards(self):
        from tests import test_attack_host as host_fakes

        task_id = self.prepare_task('task-host-real', profile='python')
        res = self.run_bench(task_id)
        for scenario_id in host_fakes.IDS:
            line = next((item for item in res.stdout.splitlines() if item.startswith(scenario_id)), '')
            self.assertIn('bloquée', line, res.stdout)
        self.assertEqual(res.returncode, 0, res.stdout)

    def test_container_attack_on_the_worktree_scripts_is_part_of_the_bench(self):
        bench = self.load_bench_module()
        (attack,) = [item for item in bench.ATTACKS if item.id == 'write-worktree-scripts']
        self.assertEqual(attack.category, 'Écriture hors du worktree')
        self.assertEqual(attack.command, 'echo x >> /work/scripts/run-in-sandbox.sh')
        self.assertEqual(attack.check(1, '', ''), ('bloquée', ''))
        self.assertEqual(attack.check(0, '', '')[0], 'FAILLE')
        self.assertEqual(len(bench.ATTACKS) + 1 + len(bench.attack_host.SCENARIOS), 28)

    def test_container_attack_creating_a_link_in_the_worktree_is_part_of_the_bench(self):
        bench = self.load_bench_module()
        (attack,) = [item for item in bench.ATTACKS if item.id == 'link-from-container']
        self.assertEqual(attack.category, 'Écriture hors du worktree')
        self.assertIn('ln -s /etc/passwd /work/.lien-attaque', attack.command)
        self.assertIn('os.symlink', attack.command)
        self.assertIn('rm -f /work/.lien-attaque', attack.command)
        self.assertEqual(attack.check(1, '', '')[0], 'bloquée')
        self.assertEqual(attack.check(0, '', '')[0], 'FAILLE')

    def test_the_link_attack_is_blocked_only_when_both_attempts_fail_and_leaves_no_link(self):
        bench = self.load_bench_module()
        (attack,) = [item for item in bench.ATTACKS if item.id == 'link-from-container']
        work = Path(self.temp.name).resolve() / 'work'
        work.mkdir()
        command = attack.command.replace('/work/', str(work) + '/')
        cases = (((), 'FAILLE'), (('ln',), 'FAILLE'), (('python3',), 'FAILLE'), (('ln', 'python3'), 'bloquée'))
        for blocked, expected in cases:
            with self.subTest(blocked=blocked):
                tools = Path(self.temp.name).resolve() / ('tools-' + '-'.join(blocked or ('none',)))
                tools.mkdir()
                for name, real in (('ln', shutil.which('ln')), ('rm', shutil.which('rm')), ('python3', sys.executable)):
                    body = 'exit 1' if name in blocked else 'exec "%s" "$@"' % real
                    (tools / name).write_text('#!/bin/sh\n%s\n' % body)
                    (tools / name).chmod(0o755)
                done = subprocess.run(['/bin/sh', '-c', command], env={'PATH': str(tools)}, capture_output=True, text=True)
                self.assertEqual(attack.check(done.returncode, done.stdout, done.stderr)[0], expected)
                self.assertEqual(list(work.iterdir()), [])

    def test_masked_secrets_in_output(self):
        task_id = self.prepare_task('task-secret-mask', profile='python')
        res = self.run_bench(task_id, '--markdown', env={'SIMULATE_SECRET_IN_OUTPUT': '1'})
        self.assertEqual(res.returncode, 0)

        # Le secret ne doit jamais apparaître en clair dans la sortie standard
        self.assertNotIn('super-secret-token-12345', res.stdout)
        self.assertNotIn('super-secret-token-12345', res.stderr)

        # Le secret ne doit jamais apparaître en clair dans le rapport Markdown
        today = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d')  # same UTC date as attack_bench.py
        md_file = self.root / f'docs/security/attack-bench-{today}.md'
        self.assertTrue(md_file.is_file())
        md_content = md_file.read_text(encoding='utf-8')
        self.assertNotIn('super-secret-token-12345', md_content)
        self.assertIn('***', md_content)

    def test_usage_invalid_task_or_missing_args_returns_code_64(self):
        # 1. Aucun argument
        res1 = self.run_bench()
        self.assertEqual(res1.returncode, 64)

        # 2. Tâche inexistante dans le registre
        res2 = self.run_bench('tache-inconnue-xyz')
        self.assertEqual(res2.returncode, 64)

        # 3. Identifiant invalide
        res3 = self.run_bench('Tache_Invalide!')
        self.assertEqual(res3.returncode, 64)

    def load_bench_module(self):
        spec = importlib.util.spec_from_file_location('attack_bench_scenario', ROOT / 'template/scripts/lib/attack_bench.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        return module

    def test_interruption_scenario_kills_the_delegation_and_leaves_nothing(self):
        task_id = self.prepare_task('task-interrupt', profile='python')
        bench = self.load_bench_module()
        with patch.dict(os.environ, self.env, clear=True):
            result = bench.run_interruption_scenario(self.root, task_id, self.scripts_dir / 'sandbox.sh')
        self.assertEqual(
            (result.attack.id, result.attack.category, result.status, result.reason), ('interruption', 'Interruption', 'bloquée', '')
        )
        status_file = self.root / '.workspace/run/delegate' / task_id / 'status.json'
        self.assertEqual(json.loads(status_file.read_text(encoding='utf-8'))['outcome'], 'interrupted')
        events_file = self.root / '.workspace/events.jsonl'
        outcomes = [
            json.loads(line) for line in events_file.read_text(encoding='utf-8').splitlines() if json.loads(line)['event'] == 'outcome'
        ]
        self.assertEqual(
            [(item['task_id'], item['detail']['outcome'], item['detail']['by']) for item in outcomes], [(task_id, 'interrupted', 'repair')]
        )
        self.assertTrue(any(call[:2] == ['rm', '-f'] for call in self.get_docker_calls()))

    def test_existing_evidence_folder_is_never_overwritten(self):
        task_id = self.prepare_task('task-evidence', profile='python')
        evidence = self.root / '.workspace/run/delegate' / task_id
        evidence.mkdir(parents=True)
        (evidence / 'precieux.txt').write_text('preuve\n', encoding='utf-8')
        bench = self.load_bench_module()
        with patch.dict(os.environ, self.env, clear=True):
            result = bench.run_interruption_scenario(self.root, task_id, self.scripts_dir / 'sandbox.sh')
        self.assertEqual((result.status, result.reason), ('non testable', 'dossier de preuves existant'))
        self.assertEqual([path.name for path in evidence.iterdir()], ['precieux.txt'])
        self.assertTrue(any(call[:1] == ['rm'] for call in self.get_docker_calls()))


class RecordLastBenchTests(unittest.TestCase):
    NOW = '2026-10-19T08:00:00+00:00'

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('attack_bench_record', ROOT / 'template/scripts/lib/attack_bench.py')
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        cls.addClassCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'projet'
        self.root.mkdir()
        self.record = self.root / '.workspace/security/last-attack-bench.json'

    def record_bench(self, failles=(), commit='abc1234'):
        with patch.object(self.module, 'current_commit', return_value=commit):
            return self.module.record_last_bench(self.root, 19 - len(failles), 19, list(failles), self.NOW)

    def test_green_pass_writes_the_full_record(self):
        returned = self.record_bench(commit='abc1234')
        self.assertEqual(returned, self.record)
        self.assertEqual(
            json.loads(self.record.read_text(encoding='utf-8')),
            {
                'commit': 'abc1234',
                'date': self.NOW,
                'score': '19/19',
                'blocked': 19,
                'total': 19,
                'failles': [],
                'green': True,
            },
        )

    def test_record_is_indented_with_a_final_newline(self):
        self.record_bench()
        text = self.record.read_text(encoding='utf-8')
        self.assertTrue(text.endswith('}\n'))
        self.assertIn('\n  "commit": "abc1234",\n', text)

    def test_red_pass_lists_the_failures(self):
        self.record_bench(failles=['write-etc', 'net-outbound'])
        payload = json.loads(self.record.read_text(encoding='utf-8'))
        self.assertFalse(payload['green'])
        self.assertEqual(payload['failles'], ['write-etc', 'net-outbound'])
        self.assertEqual(payload['score'], '17/19')

    def test_each_pass_replaces_the_previous_record(self):
        self.record_bench(failles=['write-etc'])
        self.record_bench()
        payload = json.loads(self.record.read_text(encoding='utf-8'))
        self.assertTrue(payload['green'])
        self.assertEqual(payload['failles'], [])

    def test_commit_is_null_when_git_is_unavailable(self):
        self.record_bench(commit=None)
        self.assertIsNone(json.loads(self.record.read_text(encoding='utf-8'))['commit'])

    def test_record_and_directory_permissions(self):
        self.record_bench()
        self.assertEqual(self.record.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.record.parent.stat().st_mode & 0o777, 0o700)

    def test_symlinked_record_is_refused_and_target_untouched(self):
        target = self.root / 'cible.json'
        target.write_text('intact\n', encoding='utf-8')
        self.record.parent.mkdir(parents=True)
        self.record.symlink_to(target)
        with self.assertRaises(ValueError):
            self.record_bench()
        self.assertEqual(target.read_text(encoding='utf-8'), 'intact\n')

    def test_symlinked_security_directory_is_refused(self):
        outside = Path(self.temp.name).resolve() / 'dehors'
        outside.mkdir()
        (self.root / '.workspace').mkdir()
        (self.root / '.workspace/security').symlink_to(outside)
        with self.assertRaises(ValueError):
            self.record_bench()
        self.assertFalse((outside / 'last-attack-bench.json').exists())

    def test_current_commit_is_the_head_of_a_git_repository(self):
        env = dict(
            os.environ,
            GIT_AUTHOR_NAME='Test',
            GIT_AUTHOR_EMAIL='test@example.invalid',
            GIT_COMMITTER_NAME='Test',
            GIT_COMMITTER_EMAIL='test@example.invalid',
        )
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        (self.root / 'README.md').write_text('# Projet\n', encoding='utf-8')
        subprocess.run(['git', 'add', '-A'], cwd=self.root, check=True)
        subprocess.run(['git', 'commit', '-q', '-m', 'initial'], cwd=self.root, env=env, check=True)
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.root, stdout=subprocess.PIPE, text=True, check=True).stdout.strip()
        self.assertEqual(self.module.current_commit(self.root), head)

    def test_current_commit_is_none_outside_a_git_repository(self):
        self.assertIsNone(self.module.current_commit(self.root))


if __name__ == '__main__':
    unittest.main()
