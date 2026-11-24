import datetime
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which('bash')
CWD = os.path.realpath(os.getcwd())
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import orchestrator  # noqa: E402
import sandbox  # noqa: E402


def run(root, env, *args):
    return subprocess.run(
        [BASH, str(root / 'scripts/orchestrator.sh')] + list(args),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
    )


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'project'
        (self.root / 'scripts/lib').mkdir(parents=True)
        shutil.copytree(
            ROOT / 'template/scripts/lib', self.root / 'scripts/lib', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__')
        )
        shutil.copy(ROOT / 'template/scripts/orchestrator.sh', self.root / 'scripts/orchestrator.sh')
        self.bin = Path(tmp.name) / 'bin'
        self.bin.mkdir()
        self.fake_docker('printf "workspace-sandbox-a\\nworkspace-sandbox-b\\n"')
        self.env = self.make_env(self.bin)
        self.lock = self.root / '.workspace/orchestrator.json'

    def make_env(self, *path_dirs):
        env = dict(os.environ)
        for name in ('CLAUDE_CODE_SESSION_ID', 'CLAUDE_SESSION_ID'):
            env.pop(name, None)
        env['PATH'] = os.pathsep.join(str(item) for item in path_dirs)
        return env

    def fake_docker(self, body):
        script = self.bin / 'docker'
        script.write_text('#!/bin/sh\n' + body + '\n')
        script.chmod(0o755)
        # python3 and dirname are what orchestrator.sh needs; ps is deliberately left out unless faked.
        for name in ('python3', 'dirname'):
            target = self.bin / name
            if not target.exists():
                target.symlink_to(shutil.which(name))

    def fake_ps(self, listing):
        # PATH holds only the fake tools: no `cat`, so the fake prints the file with shell builtins.
        script = self.bin / 'ps'
        script.write_text('#!/bin/sh\nwhile IFS= read -r line; do echo "$line"; done < "%s"\n' % listing)
        script.chmod(0o755)

    def no_docker_env(self):
        empty = self.bin.parent / 'nodocker'
        empty.mkdir(exist_ok=True)
        for name in ('python3', 'dirname'):
            if not (empty / name).exists():
                (empty / name).symlink_to(shutil.which(name))
        return self.make_env(empty), empty

    def events(self):
        path = self.root / '.workspace/events.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def status_lines(self, env=None):
        done = run(self.root, env or self.env, 'status')
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.splitlines()

    def test_claim_status_refusal_force_release_cycle(self):
        done = run(self.root, self.env, 'claim', '--session', 'nuit', '--model', 'opus', '--pid', str(os.getpid()))
        self.assertEqual(done.returncode, 0, done.stderr)
        data = json.loads(self.lock.read_text(encoding='utf-8'))
        self.assertEqual(sorted(data), ['claude_session_id', 'cwd', 'launched_by', 'model', 'pid', 'repos', 'session', 'started_at'])
        self.assertIsNone(data['launched_by'])
        self.assertEqual((data['session'], data['model'], data['pid']), ('nuit', 'opus', os.getpid()))
        self.assertEqual(data['repos'], [str(self.root)])
        self.assertRegex(data['started_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')

        first = self.status_lines()[0]
        self.assertEqual(first, 'verrou : nuit (opus) depuis %s, pid %d (vivant)' % (data['started_at'], os.getpid()))

        before = self.lock.read_text(encoding='utf-8')
        refused = run(self.root, self.env, 'claim', '--session', 'jour')
        self.assertEqual(refused.returncode, 3)
        self.assertIn('nuit', refused.stderr)
        self.assertIn('--force', refused.stderr)
        self.assertEqual(self.lock.read_text(encoding='utf-8'), before)
        self.assertEqual(len(self.events()), 1)

        forced = run(self.root, self.env, 'claim', '--force', '--session', 'jour', '--repos', 'a, b,,c')
        self.assertEqual(forced.returncode, 0, forced.stderr)
        data = json.loads(self.lock.read_text(encoding='utf-8'))
        self.assertEqual((data['session'], data['model'], data['repos']), ('jour', 'inconnu', ['a', 'b', 'c']))

        released = run(self.root, self.env, 'release')
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.status_lines()[0], 'verrou : aucun')

        self.assertEqual(
            [(item['task_id'], item['event'], item['detail']) for item in self.events()],
            [
                (
                    'orchestrator',
                    'orchestrator_claimed',
                    {'session': 'nuit', 'model': 'opus', 'pid': os.getpid(), 'launched_by': None, 'cwd': CWD, 'claude_session_id': None},
                ),
                (
                    'orchestrator',
                    'orchestrator_claimed',
                    {'session': 'jour', 'model': 'inconnu', 'pid': data['pid'], 'launched_by': None, 'cwd': CWD, 'claude_session_id': None},
                ),
                ('orchestrator', 'orchestrator_released', {'session': 'jour'}),
            ],
        )

    def test_claim_defaults_and_file_mode(self):
        done = run(self.root, self.env, 'claim')
        self.assertEqual(done.returncode, 0, done.stderr)
        data = json.loads(self.lock.read_text(encoding='utf-8'))
        self.assertEqual(data['pid'], os.getpid())
        self.assertTrue(data['session'].endswith('-%d' % os.getpid()), data['session'])
        self.assertEqual(data['model'], 'inconnu')
        self.assertEqual(stat.S_IMODE(self.lock.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.lock.parent.stat().st_mode), 0o700)
        self.assertEqual([path.name for path in self.lock.parent.iterdir() if path.name.endswith('.tmp')], [])

    def test_claim_by_records_the_launcher_in_lock_journal_and_status(self):
        done = run(self.root, self.env, 'claim', '--session', 'nuit', '--model', 'opus', '--by', 'owner', '--pid', str(os.getpid()))
        self.assertEqual(done.returncode, 0, done.stderr)
        data = json.loads(self.lock.read_text(encoding='utf-8'))
        self.assertEqual(data['launched_by'], 'owner')
        self.assertEqual((data['session'], data['model'], data['pid']), ('nuit', 'opus', os.getpid()))
        self.assertEqual(
            self.events()[0]['detail'],
            {'session': 'nuit', 'model': 'opus', 'pid': os.getpid(), 'launched_by': 'owner', 'cwd': CWD, 'claude_session_id': None},
        )
        self.assertEqual(
            self.status_lines()[0], 'verrou : nuit (opus) depuis %s, pid %d (vivant), lancé par owner' % (data['started_at'], os.getpid())
        )

    def test_claim_without_by_records_a_null_launcher(self):
        done = run(self.root, self.env, 'claim', '--session', 's', '--pid', str(os.getpid()))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsNone(json.loads(self.lock.read_text(encoding='utf-8'))['launched_by'])
        self.assertIsNone(self.events()[0]['detail']['launched_by'])
        self.assertNotIn('lancé par', self.status_lines()[0])

    def test_claim_refuses_an_invalid_launcher_without_writing_the_lock(self):
        for value in ('', 'a\nb', 'tab\there', 'cloche\x07', 'x' * 65):
            done = run(self.root, self.env, 'claim', '--by', value, '--pid', str(os.getpid()))
            self.assertEqual(done.returncode, 64, repr(value))
            self.assertIn('--by', done.stderr)
            self.assertFalse(self.lock.exists(), repr(value))
        self.assertEqual(self.events(), [])
        done = run(self.root, self.env, 'claim', '--session', 'nuit', '--pid', str(os.getpid()))
        self.assertEqual(done.returncode, 0, done.stderr)
        before = self.lock.read_text(encoding='utf-8')
        refused = run(self.root, self.env, 'claim', '--force', '--by', '', '--pid', str(os.getpid()))
        self.assertEqual(refused.returncode, 64)
        self.assertEqual(self.lock.read_text(encoding='utf-8'), before)
        accepted = run(self.root, self.env, 'claim', '--force', '--by', 'x' * 64, '--pid', str(os.getpid()))
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(self.lock.read_text(encoding='utf-8'))['launched_by'], 'x' * 64)

    def test_release_without_lock_is_silent_for_the_journal(self):
        done = run(self.root, self.env, 'release')
        self.assertEqual(done.returncode, 0)
        self.assertIn('aucun verrou', done.stdout)
        self.assertEqual(self.events(), [])

    def test_symlinks_are_refused(self):
        outside = self.root.parent / 'outside.json'
        outside.write_text('{}')
        (self.root / '.workspace').mkdir()
        self.lock.symlink_to(outside)
        for command in (['release'], ['claim', '--force']):
            done = run(self.root, self.env, *command)
            self.assertNotEqual(done.returncode, 0, command)
            self.assertIn('symbolique', done.stderr)
        self.assertEqual(outside.read_text(), '{}')
        self.lock.unlink()
        elsewhere = self.root.parent / 'elsewhere'
        elsewhere.mkdir()
        (self.root / '.workspace').rmdir()
        (self.root / '.workspace').symlink_to(elsewhere)
        self.assertNotEqual(run(self.root, self.env, 'claim').returncode, 0)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_status_reports_dead_pid_and_unreadable_lock(self):
        dead = subprocess.Popen([sys.executable, '-c', 'pass'])
        dead.wait()
        run(self.root, self.env, 'claim', '--session', 's', '--pid', str(dead.pid))
        self.assertTrue(self.status_lines()[0].endswith('(terminé)'))
        self.lock.write_text('pas du json')
        self.assertEqual(self.status_lines()[0], 'verrou : illisible')

    def test_status_shows_launcher_only_for_a_non_empty_string(self):
        dead = subprocess.Popen([sys.executable, '-c', 'pass'])
        dead.wait()
        run(self.root, self.env, 'claim', '--session', 's', '--by', 'Fable', '--pid', str(dead.pid))
        self.assertTrue(self.status_lines()[0].endswith('(terminé), lancé par Fable'), self.status_lines()[0])
        legacy = {'session': 'ancien', 'model': 'opus', 'started_at': '2026-10-01T00:00:00Z', 'pid': dead.pid, 'repos': []}
        for extra in ({}, {'launched_by': None}, {'launched_by': ''}, {'launched_by': 3}):
            self.lock.write_text(json.dumps(dict(legacy, **extra)))
            self.assertEqual(
                self.status_lines()[0], 'verrou : ancien (opus) depuis 2026-10-01T00:00:00Z, pid %d (terminé)' % dead.pid, extra
            )

    def test_usage_names_the_launcher_option(self):
        done = run(self.root, self.env)
        self.assertEqual(done.returncode, 64)
        self.assertIn('[--by NOM]', done.stderr)

    def test_status_lists_active_tasks_and_tolerates_unreadable_state(self):
        tasks = self.root / '.workspace/orchestration/tasks'
        for name, content in [
            ('a-run', '{"status": "running"}'),
            ('b-done', '{"status": "merged"}'),
            ('c-conflict', '{"status": "merge_conflict"}'),
            ('d-bad', '{oups'),
            ('e-prep', '{"status": "prepared"}'),
            ('f-fail', '{"status": "integration_failed"}'),
            ('g-list', '[1]'),
        ]:
            (tasks / name).mkdir(parents=True)
            (tasks / name / 'state.json').write_text(content)
        lines = self.status_lines()
        self.assertEqual(
            [line for line in lines if line.startswith('tâche')],
            [
                'tâche active : a-run (running)',
                'tâche active : c-conflict (merge_conflict)',
                'tâche illisible : d-bad',
                'tâche active : e-prep (prepared)',
                'tâche active : f-fail (integration_failed)',
                'tâche illisible : g-list',
            ],
        )
        self.assertNotIn('tâches actives : aucune', lines)

    def test_status_without_tasks(self):
        lines = self.status_lines()
        self.assertIn('tâches actives : aucune', lines)
        self.assertIn('verrou : aucun', lines)

    def test_status_lists_containers_from_docker(self):
        lines = self.status_lines()
        self.assertIn('conteneur : workspace-sandbox-a', lines)
        self.assertIn('conteneur : workspace-sandbox-b', lines)
        self.fake_docker('exit 0')
        self.assertIn('conteneurs : aucun', self.status_lines())

    def test_status_without_docker_or_with_failing_docker(self):
        env, _ = self.no_docker_env()
        lines = self.status_lines(env)
        self.assertIn('conteneurs : docker indisponible', lines)
        self.fake_docker('echo boom >&2; exit 1')
        self.assertIn('conteneurs : docker indisponible', self.status_lines())

    def test_status_filters_processes_from_ps(self):
        listing = self.bin.parent / 'ps.txt'
        long_command = 'claude -p ' + 'x' * 400
        listing.write_text(
            '\n'.join(
                [
                    '  101 /usr/bin/python3 scripts/lib/campaign.py --go',
                    '  102 vim notes.md',
                    '  103 codex exec "tâche"',
                    '  %d codex mon parent' % os.getpid(),
                    '  104 ' + long_command,
                    '  105 node claude -p "x"',
                ]
            )
            + '\n'
        )
        self.fake_ps(listing)
        processes = [line for line in self.status_lines() if line.startswith('processus')]
        self.assertEqual(
            processes[:2], ['processus : 101 /usr/bin/python3 scripts/lib/campaign.py --go', 'processus : 103 codex exec "tâche"']
        )
        self.assertEqual(processes[2], 'processus : 104 ' + long_command[:160])
        self.assertEqual(processes[3], 'processus : 105 node claude -p "x"')
        self.assertEqual(len(processes), 4)

    def test_status_without_matching_process(self):
        listing = self.bin.parent / 'ps.txt'
        listing.write_text('  7 vim notes.md\n')
        self.fake_ps(listing)
        self.assertIn('processus : aucun', self.status_lines())

    def test_status_ignores_chatgpt_codex_services(self):
        listing = self.bin.parent / 'ps.txt'
        listing.write_text(
            '\n'.join(
                [
                    '  201 /Applications/ChatGPT.app/Contents/MacOS/ChatGPT',
                    '  202 Codex (Service)',
                    '  203 /Applications/ChatGPT.app/Contents/Resources/codex app-server',
                    '  204 /Applications/ChatGPT.app/Contents/Resources/codex --flag exec-server',
                    '  205 Codex Computer Use',
                    '  206 vim codex-notes.md',
                    '  207 /opt/homebrew/bin/codex exec "tâche"',
                    '  208 node /usr/lib/node_modules/@openai/codex@0.162.1 exec "x"',
                ]
            )
            + '\n'
        )
        self.fake_ps(listing)
        processes = [line for line in self.status_lines() if line.startswith('processus')]
        self.assertEqual(
            processes,
            [
                'processus : 207 /opt/homebrew/bin/codex exec "tâche"',
                'processus : 208 node /usr/lib/node_modules/@openai/codex@0.162.1 exec "x"',
            ],
        )

    def test_claim_without_pid_records_the_session_process_behind_shells(self):
        # Python appelant -> bash -c -> bash orchestrator.sh (exec python3) : le pid de l'appelant est retenu.
        env = self.make_env(self.bin, *os.environ.get('PATH', '').split(os.pathsep))
        done = subprocess.run(
            [BASH, '-c', 'bash "$0" claim; true', str(self.root / 'scripts/orchestrator.sh')],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(self.lock.read_text(encoding='utf-8'))['pid'], os.getpid())

    def test_status_lists_tasks_of_declared_repos_and_campaign_arms(self):
        other = self.root.parent / 'other'
        self.write_task(self.root / '.workspace/orchestration/tasks/z-own', 'running')
        self.write_task(self.root / '.workspace/campaign/u1/.workspace/orchestration/tasks/c-run', 'running')
        self.write_task(other / '.workspace/orchestration/tasks/x-run', 'running')
        self.write_task(other / '.workspace/campaign/u2/.workspace/orchestration/tasks/y-conf', 'merge_conflict')
        self.write_task(other / '.workspace/orchestration/tasks/w-merged', 'merged')
        (other / '.workspace/orchestration/tasks/v-link').mkdir(parents=True)
        outside = self.root.parent / 'outside-state.json'
        outside.write_text('{"status": "running"}')
        (other / '.workspace/orchestration/tasks/v-link/state.json').symlink_to(outside)
        missing = self.root.parent / 'absent'
        done = run(self.root, self.env, 'claim', '--session', 's', '--pid', str(os.getpid()), '--repos', '%s,%s' % (other, missing))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            [line for line in self.status_lines() if line.startswith('tâche')],
            [
                'tâche active : z-own (running)',
                'tâche active : c-run (running)',
                'tâche illisible : v-link [%s]' % other,
                'tâche active : x-run (running) [%s]' % other,
                'tâche active : y-conf (merge_conflict) [%s]' % other,
            ],
        )

    def test_status_without_lock_lists_only_the_current_repo(self):
        other = self.root.parent / 'other'
        self.write_task(other / '.workspace/orchestration/tasks/x-run', 'running')
        self.assertIn('tâches actives : aucune', self.status_lines())

    def write_task(self, directory, status):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'state.json').write_text(json.dumps({'status': status}))

    @unittest.skipUnless(os.path.isdir('/proc/self'), 'pas de /proc')
    def test_status_reads_proc_when_ps_is_absent(self):
        env, _ = self.no_docker_env()
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', 'campaign.py'])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        lines = self.status_lines(env)
        self.assertTrue(any(line.startswith('processus : %d ' % child.pid) and 'campaign.py' in line for line in lines), lines)

    def test_status_flags_an_orphan_lock_and_release_clears_it(self):
        dead = subprocess.Popen([sys.executable, '-c', 'pass'])
        dead.wait()
        run(self.root, self.env, 'claim', '--session', 's', '--pid', str(dead.pid))
        self.assertEqual(
            self.status_lines()[1], 'verrou orphelin : pid %d mort ; libérable par bash scripts/orchestrator.sh release' % dead.pid
        )
        self.assertEqual(run(self.root, self.env, 'release').returncode, 0)
        self.assertEqual(self.status_lines()[0], 'verrou : aucun')

    def test_no_orphan_line_for_a_live_pid_an_absent_or_an_unreadable_lock(self):
        run(self.root, self.env, 'claim', '--session', 's', '--pid', str(os.getpid()))
        self.assertFalse([line for line in self.status_lines() if 'orphelin' in line])
        self.lock.write_text('pas du json')
        self.assertFalse([line for line in self.status_lines() if 'orphelin' in line])
        self.lock.unlink()
        self.assertFalse([line for line in self.status_lines() if 'orphelin' in line])

    def test_delegation_alive_matches_only_the_task_file_of_a_delegate(self):
        listed = [(1, 'python3 delegate.py /r/.workspace/tasks/t-a.md')]
        self.assertTrue(orchestrator.delegation_alive('t-a', listed))
        self.assertTrue(orchestrator.delegation_alive('t-a', [(3, 'python3 delegate.py /r/.workspace/tasks/t-a/task.md')]))
        self.assertFalse(orchestrator.delegation_alive('t-b', listed))
        self.assertFalse(orchestrator.delegation_alive('t-a', [(2, 'vim /r/.workspace/tasks/t-a.md')]))
        self.assertFalse(orchestrator.delegation_alive('t-a', [(4, 'python3 delegate.py /r/.workspace/tasks/t-ab.md')]))
        self.assertFalse(orchestrator.delegation_alive('t-a', []))

    def test_delegation_alive_is_true_when_the_process_list_is_unreadable(self):
        with mock.patch.object(orchestrator, '_list_processes', side_effect=OSError('ps absent')):
            self.assertTrue(orchestrator.delegation_alive('t-a'))

    def write_delegation(self, task_id, outcome='running', text=None):
        folder = self.root / '.workspace/run/delegate' / task_id
        folder.mkdir(parents=True, exist_ok=True)
        content = text or json.dumps(
            {'id': task_id, 'agent': 'factice', 'outcome': outcome, 'attempts': 2, 'started_at': '2026-10-19T08:00:00Z'}
        )
        (folder / 'status.json').write_text(content, encoding='utf-8')
        return folder / 'status.json'

    def repair_with(self, listed, path_dir):
        with mock.patch.object(orchestrator, '_list_processes', return_value=listed), mock.patch.dict(os.environ, {'PATH': str(path_dir)}):
            return orchestrator.repair(self.root)

    def test_repair_interrupts_a_dead_delegation_once(self):
        status_file = self.write_delegation('t-mort')
        state_file = self.root / '.workspace/orchestration/tasks/t-mort/state.json'
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps({'status': 'running', 'phase': 'attempt 2'}), encoding='utf-8')
        log = self.bin.parent / 'docker.log'
        self.fake_docker('echo "$@" >> "%s"' % log)
        name = sandbox.get_container_name(self.root, 't-mort')

        first = self.repair_with([], self.bin)
        after_first = self.events()
        second = self.repair_with([], self.bin)

        self.assertEqual(first, ['réparé : délégation t-mort interrompue (processus mort), conteneur %s supprimé' % name])
        self.assertEqual(second, ['réparation : rien à faire'])
        data = json.loads(status_file.read_text(encoding='utf-8'))
        self.assertEqual(
            (data['outcome'], data['interrupted_by'], data['attempts'], data['agent']), ('interrupted', 'repair', 2, 'factice')
        )
        self.assertRegex(data['ended_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(stat.S_IMODE(status_file.stat().st_mode), 0o600)
        self.assertEqual(self.events(), after_first)
        self.assertEqual(
            [(item['task_id'], item['event'], item['detail']) for item in after_first if item['event'] != 'state'],
            [('t-mort', 'outcome', {'outcome': 'interrupted', 'attempts': 2, 'by': 'repair'})],
        )
        states = [item['detail'] for item in after_first if item['event'] == 'state']
        self.assertEqual([(detail['reason'], detail['state']['phase']) for detail in states], [('phase', 'failed')])
        self.assertEqual(json.loads(state_file.read_text(encoding='utf-8'))['phase'], 'failed')
        self.assertEqual(log.read_text(encoding='utf-8').splitlines(), ['rm -f %s' % name])

    def test_repair_leaves_a_live_delegation_alone(self):
        status_file = self.write_delegation('t-vivant')
        before = status_file.read_text(encoding='utf-8')
        listed = [(4242, 'python3 delegate.py %s/.workspace/tasks/t-vivant.md' % self.root)]
        self.assertEqual(self.repair_with(listed, self.bin), ['réparation : rien à faire'])
        self.assertEqual(status_file.read_text(encoding='utf-8'), before)
        self.assertEqual(self.events(), [])

    def test_repair_ignores_finished_unreadable_and_non_object_statuses(self):
        paths = [
            self.write_delegation('t-fini', outcome='done'),
            self.write_delegation('t-casse', text='{oups'),
            self.write_delegation('t-liste', text='[1, 2]'),
        ]
        before = [path.read_text(encoding='utf-8') for path in paths]
        self.assertEqual(self.repair_with([], self.bin), ['réparation : rien à faire'])
        self.assertEqual([path.read_text(encoding='utf-8') for path in paths], before)
        self.assertEqual(self.events(), [])

    def test_repair_says_docker_is_unavailable_when_docker_is_absent(self):
        status_file = self.write_delegation('t-sans-docker')
        empty = self.bin.parent / 'vide'
        empty.mkdir()
        self.assertEqual(
            self.repair_with([], empty), ['réparé : délégation t-sans-docker interrompue (processus mort), docker indisponible']
        )
        self.assertEqual(json.loads(status_file.read_text(encoding='utf-8'))['outcome'], 'interrupted')

    def test_repair_refuses_a_symlinked_status_and_repairs_the_others(self):
        outside = self.root.parent / 'dehors.json'
        outside.write_text(json.dumps({'outcome': 'running'}), encoding='utf-8')
        folder = self.root / '.workspace/run/delegate/t-lien'
        folder.mkdir(parents=True)
        (folder / 'status.json').symlink_to(outside)
        self.write_delegation('t-ok')
        lines = self.repair_with([], self.bin)
        self.assertEqual(lines[0], 'réparation refusée : t-lien (lien)')
        self.assertTrue(lines[1].startswith('réparé : délégation t-ok interrompue'), lines)
        self.assertEqual(json.loads(outside.read_text(encoding='utf-8')), {'outcome': 'running'})

    def test_status_repair_prints_the_status_then_the_repair_lines(self):
        self.write_delegation('t-cli')
        listing = self.bin.parent / 'ps.txt'
        listing.write_text('  7 vim notes.md\n')
        self.fake_ps(listing)
        name = sandbox.get_container_name(self.root, 't-cli')
        done = run(self.root, self.env, 'status', '--repair')
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[0], 'verrou : aucun')
        self.assertEqual(lines[-1], 'réparé : délégation t-cli interrompue (processus mort), conteneur %s supprimé' % name)

    def test_status_accepts_only_the_repair_option(self):
        done = run(self.root, self.env, 'status', '--force')
        self.assertEqual(done.returncode, 64)
        self.assertIn('status [--repair]', done.stderr)
        self.assertFalse(self.lock.exists())

    def test_usage(self):
        for args in ([], ['inconnue'], ['status', 'extra']):
            done = run(self.root, self.env, *args)
            self.assertEqual(done.returncode, 64, args)
            self.assertIn('usage', done.stderr)
        self.assertFalse(self.lock.exists())

    def write_lock(self, pid, **extra):
        data = {'session': 's', 'model': 'opus', 'started_at': '2026-10-19T08:00:00Z', 'pid': pid, 'repos': []}
        data.update(extra)
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        self.lock.write_text(json.dumps(data), encoding='utf-8')

    def beat_at(self, minutes):
        stamp = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes)
        return stamp.strftime('%Y-%m-%dT%H:%M:%SZ')

    def dead_pid(self):
        dead = subprocess.Popen([sys.executable, '-c', 'pass'])
        dead.wait()
        return dead.pid

    def test_recent_beat_keeps_a_dead_pid_alive(self):
        pid = self.dead_pid()
        self.write_lock(pid, beat_at=self.beat_at(3))
        lines = self.status_lines()
        self.assertEqual(lines[0], 'verrou : s (opus) depuis 2026-10-19T08:00:00Z, pid %d (vivant), battement il y a 3 min' % pid)
        self.assertFalse([line for line in lines if 'orphelin' in line])

    def test_old_beat_with_dead_pid_is_an_orphan(self):
        pid = self.dead_pid()
        self.write_lock(pid, beat_at=self.beat_at(25))
        lines = self.status_lines()
        self.assertEqual(lines[0], 'verrou : s (opus) depuis 2026-10-19T08:00:00Z, pid %d (terminé)' % pid)
        self.assertEqual(lines[1], 'verrou orphelin : pid %d mort ; libérable par bash scripts/orchestrator.sh release' % pid)

    def test_missing_malformed_or_future_beat_does_not_keep_a_dead_pid_alive(self):
        pid = self.dead_pid()
        for extra in (
            {},
            {'beat_at': None},
            {'beat_at': 3},
            {'beat_at': '2026-10-19 08:00:00'},
            {'beat_at': '2026-13-45T99:00:00Z'},
            {'beat_at': self.beat_at(-10)},
        ):
            self.write_lock(pid, **extra)
            lines = self.status_lines()
            self.assertTrue(lines[0].endswith('(terminé)'), (extra, lines[0]))
            self.assertEqual(lines[1].split(' :')[0], 'verrou orphelin', (extra, lines[1]))

    def test_beat_rewrites_only_the_beat_key(self):
        self.write_lock(os.getpid(), beat_at='2000-01-01T00:00:00Z', launched_by='owner')
        before = json.loads(self.lock.read_text(encoding='utf-8'))
        done = run(self.root, self.env, 'beat')
        self.assertEqual(done.returncode, 0, done.stderr)
        after = json.loads(self.lock.read_text(encoding='utf-8'))
        self.assertRegex(after['beat_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertNotEqual(after['beat_at'], '2000-01-01T00:00:00Z')
        self.assertEqual(done.stdout.strip(), 'battement : %s' % after['beat_at'])
        self.assertEqual(after, dict(before, beat_at=after['beat_at']))
        self.assertEqual(stat.S_IMODE(self.lock.stat().st_mode), 0o600)
        self.assertEqual(self.events(), [])

    def test_beat_without_readable_lock_fails_and_writes_nothing(self):
        done = run(self.root, self.env, 'beat')
        self.assertEqual(done.returncode, 1)
        self.assertIn('verrou absent', done.stderr)
        self.assertFalse(self.lock.exists())
        self.lock.parent.mkdir(parents=True)
        self.lock.write_text('pas du json', encoding='utf-8')
        done = run(self.root, self.env, 'beat')
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.lock.read_text(encoding='utf-8'), 'pas du json')

    def test_beat_refuses_arguments(self):
        self.write_lock(os.getpid())
        before = self.lock.read_text(encoding='utf-8')
        done = run(self.root, self.env, 'beat', 'trop')
        self.assertEqual(done.returncode, 64)
        self.assertIn('orchestrator.sh beat', done.stderr)
        self.assertEqual(self.lock.read_text(encoding='utf-8'), before)

    def test_status_repair_releases_an_orphan_lock_once(self):
        self.write_lock(self.dead_pid(), beat_at=self.beat_at(25))
        done = run(self.root, self.env, 'status', '--repair')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('verrou orphelin libéré : s', done.stdout.splitlines())
        self.assertFalse(self.lock.exists())
        self.assertEqual(
            [(item['task_id'], item['event'], item['detail']) for item in self.events()],
            [('orchestrator', 'orchestrator_released', {'session': 's', 'by': 'repair'})],
        )
        again = run(self.root, self.env, 'status', '--repair')
        self.assertEqual(again.stdout.splitlines()[0], 'verrou : aucun')
        self.assertNotIn('libéré', again.stdout)
        self.assertEqual(len(self.events()), 1)

    def test_status_repair_keeps_a_live_lock(self):
        for extra, pid in (({}, os.getpid()), ({'beat_at': self.beat_at(3)}, self.dead_pid())):
            self.write_lock(pid, **extra)
            before = self.lock.read_text(encoding='utf-8')
            done = run(self.root, self.env, 'status', '--repair')
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertNotIn('libéré', done.stdout)
            self.assertEqual(self.lock.read_text(encoding='utf-8'), before)
        self.assertEqual(self.events(), [])

    def test_status_repair_keeps_an_unreadable_lock(self):
        self.lock.parent.mkdir(parents=True)
        self.lock.write_text('pas du json', encoding='utf-8')
        done = run(self.root, self.env, 'status', '--repair')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('libéré', done.stdout)
        self.assertEqual(self.lock.read_text(encoding='utf-8'), 'pas du json')


class SessionPidTests(unittest.TestCase):
    def setUp(self):
        self.table = {}
        patcher = mock.patch.object(orchestrator, '_process_info', side_effect=lambda pid: self.table.get(pid))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_skips_shells_and_keeps_the_first_other_process(self):
        self.table = {10: (11, 'bash'), 11: (12, 'zsh'), 12: (13, 'python3'), 13: (1, 'launchd')}
        self.assertEqual(orchestrator._session_pid(10), 12)

    def test_a_shell_whose_parent_is_init_is_kept(self):
        self.table = {10: (1, 'bash')}
        self.assertEqual(orchestrator._session_pid(10), 10)

    def test_init_is_never_recorded_in_place_of_the_last_other_process(self):
        self.table = {10: (1, 'sh'), 1: (0, 'launchd')}
        self.assertNotEqual(orchestrator._session_pid(10), 1)

    def test_stops_after_sixteen_levels_of_shells(self):
        self.table = {pid: (pid + 1, 'bash') for pid in range(10, 27)}
        self.table[27] = (1, 'python3')
        self.assertEqual(orchestrator._session_pid(10), 26)

    def test_unreadable_process_keeps_the_current_pid(self):
        self.assertEqual(orchestrator._session_pid(10), 10)
        self.table = {10: (11, 'bash')}
        self.assertEqual(orchestrator._session_pid(10), 11)

    def test_command_name_drops_path_and_login_dash(self):
        self.assertEqual(orchestrator._command_name('-zsh'), 'zsh')
        self.assertEqual(orchestrator._command_name('/bin/bash'), 'bash')
        self.assertEqual(orchestrator._command_name(' python3 '), 'python3')


class ProcessInfoTests(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir('/proc/self'), 'pas de /proc')
    def test_process_info_reads_proc_without_ps(self):
        with mock.patch.object(orchestrator.shutil, 'which', return_value=None):
            info = orchestrator._process_info(os.getpid())
        self.assertEqual(info[0], os.getppid())
        self.assertTrue(info[1])


if __name__ == '__main__':
    unittest.main()
