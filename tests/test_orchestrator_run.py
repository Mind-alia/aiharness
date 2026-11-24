"""Tests de l'orchestrateur programme (fiche T14, fiche 2) : plan, reprise après kill -9, échec, verrou, plan terminé."""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'orchestrator'))

import plan as planning  # noqa: E402
import steps  # noqa: E402

RUN = ROOT / 'tools' / 'orchestrator' / 'run.py'
TIMEOUT = 90

FAKE_CIRCUIT = r'''
import datetime, json, os, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'lib'))
import consumption
import events
import secrets

FAKE = ROOT / '.workspace' / 'fake'


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def calls():
    path = FAKE / 'calls.log'
    return path.read_text().splitlines() if path.exists() else []


def log(line):
    FAKE.mkdir(parents=True, exist_ok=True)
    with open(str(FAKE / 'calls.log'), 'a') as stream:
        stream.write(line + '\n')


def control():
    path = FAKE / 'control.json'
    return json.loads(path.read_text()) if path.exists() else {}


def git(*args, cwd=ROOT):
    subprocess.run(['git'] + list(args), cwd=str(cwd), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def fiche_id(fiche):
    for line in Path(fiche).read_text().splitlines():
        if line.startswith('id:'):
            return line.split(':', 1)[1].strip()


def state_file(tid):
    return ROOT / '.workspace' / 'orchestration' / 'tasks' / tid / 'state.json'


def read_state(tid):
    return json.loads(state_file(tid).read_text()) if state_file(tid).exists() else None


def write_state(tid, state):
    state_file(tid).parent.mkdir(parents=True, exist_ok=True)
    state_file(tid).write_text(json.dumps(state))


def prepare(args):
    tid, retry = fiche_id(args[0]), '--retry' in args
    log('prepare %s%s' % (tid, ' retry' if retry else ''))
    old = read_state(tid)
    if control().get('prepare_fail') or (old and old['status'] != 'abandoned') or (retry and not old):
        return 3
    branch = 'workspace-task/' + tid
    if retry:
        git('branch', '-m', branch, 'archive/' + branch + '-tentative1')
    git('branch', branch, 'HEAD')
    git('worktree', 'add', '.workspace/worktrees/' + tid, branch)
    base = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(ROOT), stdout=subprocess.PIPE).stdout.decode().strip()
    write_state(tid, {'status': 'prepared', 'created_at': stamp(), 'branch': branch, 'base_commit': base})
    events.record(ROOT, tid, 'prepared', {'branch': branch})
    return 0


def delegate(args):
    tid = fiche_id(args[0])
    count = len([line for line in calls() if line == 'delegate ' + tid]) + 1
    log('delegate ' + tid)
    context = os.environ.get('WORKSPACE_STAGE_CONTEXT')
    seen = {'override': os.environ.get('WORKSPACE_AGENT_OVERRIDE'), 'context': Path(context).read_text() if context else None}
    seen['start'] = time.time()
    settings = control()
    sleeps = settings.get('sleeps', [])
    outcomes = settings.get('outcomes', {}).get(tid, [])
    outcome = outcomes[count - 1] if count <= len(outcomes) else 'done'
    status_file = ROOT / '.workspace' / 'run' / 'delegate' / tid / 'status.json'
    status_file.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    status = {'id': tid, 'agent': 'bric-fake', 'outcome': 'running', 'started_at': started}
    status_file.write_text(json.dumps(status))
    time.sleep(sleeps[count - 1] if count <= len(sleeps) else 0)
    worktree = ROOT / '.workspace' / 'worktrees' / tid
    # Comme delegate.py : une consigne qui porte un secret est refusée avant tout appel (T100).
    if seen['context'] and secrets.find_secrets(seen['context'], secrets.known_values(ROOT)):
        outcome = 'secret_in_prompt'
    if outcome == 'done':
        (worktree / 'src').mkdir(exist_ok=True)
        (worktree / 'src' / (tid + '.txt')).write_text('livré\n')
        status['files_changed'] = ['src/' + tid + '.txt']
        # Fichier Python de la ronde `count` (format et lint, T99), s'il est demandé.
        python = settings.get('python', {}).get(tid, [])
        if count <= len(python):
            (worktree / 'src' / (tid + '.py')).write_text(python[count - 1])
            status['files_changed'].append('src/' + tid + '.py')
        git('add', '-A', cwd=worktree)
        git('commit', '-q', '--allow-empty', '-m', 'feat: ' + tid, cwd=worktree)
        # Comme delegate.py, diff.txt ne montre que ce que cette délégation a changé.
        diff = 'diff --git a/src/{0}.txt b/src/{0}.txt\nnew file mode 100644\n--- /dev/null\n+++ b/src/{0}.txt\n+livré (ronde {1})\n'
        (status_file.parent / 'diff.txt').write_text(diff.format(tid, count))
    if outcome == 'quota':
        events.record(ROOT, tid, 'quota', {'provider': 'claude'})
        outcome = 'failed'
    if outcome == 'waiting_answer':
        questions = ROOT / '.workspace' / 'orchestration' / 'tasks' / tid / 'context' / 'questions'
        questions.mkdir(parents=True, exist_ok=True)
        (questions / '001.md').write_text('Quelle base de données faut-il garder ?\n')
        status['waiting_questions'] = ['001']
    # Comme delegate_run.py : chaque tentative entre au journal de consommation avec le prix du fournisseur (T102).
    reply = {'usage': {'input_tokens': 100, 'cache_read_input_tokens': 200, 'cache_creation_input_tokens': 300, 'output_tokens': 50}}
    reply['total_cost_usd'] = 0.125 * count
    (FAKE / ('executor-%s-%d.json' % (tid, count))).write_text(json.dumps(reply))
    consumption.record_claude(ROOT, 'execution', 'sonnet', tid, reply)
    seen['end'] = time.time()
    with open(str(FAKE / ('env-' + tid + '.jsonl')), 'a') as stream:
        stream.write(json.dumps(seen) + '\n')
    status_file.write_text(json.dumps(dict(status, outcome=outcome)))
    return 0 if outcome == 'done' else (75 if outcome == 'waiting_answer' else 1)


def merge(args):
    tid = args[0]
    log('merge ' + tid)
    time.sleep(control().get('merge_sleep', 0))
    if control().get('merge_fail'):
        return 1
    git('merge', '--no-ff', '-q', '-m', 'merge: task ' + tid, 'workspace-task/' + tid)
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(ROOT), stdout=subprocess.PIPE).stdout.decode().strip()
    events.record(ROOT, tid, 'merged', {'commit': head})
    git('worktree', 'remove', '--force', '.workspace/worktrees/' + tid)
    git('branch', '-q', '-D', 'workspace-task/' + tid)
    write_state(tid, dict(read_state(tid), status='merged'))
    return 0


def close(args):
    tid = args[0]
    log('close ' + tid)
    git('worktree', 'remove', '--force', '.workspace/worktrees/' + tid)
    write_state(tid, dict(read_state(tid), status='abandoned'))
    events.record(ROOT, tid, 'closed', {'outcome': 'abandoned'})
    return 0


if __name__ == '__main__':
    command, rest = sys.argv[1], sys.argv[2:]
    sys.exit({'prepare': prepare, 'delegate': delegate, 'merge': merge, 'close': close}[command](rest))
'''

WRAPPERS = {
    'task-workspace.sh': 'exec python3 "$d/fake_circuit.py" "$1" "${@:2}"',
    'delegate.sh': 'exec python3 "$d/fake_circuit.py" delegate "$@"',
    'report-check.sh': 'echo "report-check $1" >> "$d/../.workspace/fake/calls.log"\necho "<!-- relu -->" >> "$1"',
}


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
prompt = sys.stdin.read()
model = sys.argv[sys.argv.index('--model') + 1]
# Le dossier du faux circuit ne dépend pas du dossier de travail : la relecture tourne dans le clone (T101).
fake = Path(os.environ['FAKE_DIR'])
fake.mkdir(parents=True, exist_ok=True)
(fake / ('cwd-%s.txt' % model)).write_text(os.getcwd())
with open(str(fake / 'calls.log'), 'a') as stream:
    stream.write('claude %s\n' % model)
(fake / ('prompt-%s.txt' % model)).write_text(prompt)
(fake / ('args-%s.txt' % model)).write_text('\n'.join(sys.argv[1:]))
control = json.loads((fake / 'control.json').read_text()) if (fake / 'control.json').exists() else {}
answers = control.get('claude', {}).get(model, [])
count = len([line for line in (fake / 'calls.log').read_text().splitlines() if line == 'claude ' + model])
(fake / ('prompt-%s-%d.txt' % (model, count))).write_text(prompt)
default = 'VERDICT: bon' if model == 'opus' else 'Cause probable : test rouge.'
result = answers[count - 1] if count <= len(answers) else default
usage = {'input_tokens': 10, 'cache_read_input_tokens': 20, 'cache_creation_input_tokens': 30, 'output_tokens': 5}
cost = {'opus': 0.5, 'sonnet': 0.0625}.get(model, 0.01)
if result == 'error_max_turns':
    print(json.dumps({'type': 'result', 'subtype': 'error_max_turns', 'is_error': True, 'usage': usage, 'total_cost_usd': cost}))
    sys.exit(1)
print(json.dumps({'result': result, 'usage': usage, 'total_cost_usd': cost}))
'''


def fiche_text(task_id):
    return f'---\nid: {task_id}\nagent: bric\n---\n# Fiche {task_id}\n'


class ProjectCase(unittest.TestCase):
    """Projet Git temporaire avec faux circuit et vrai `orchestrator.sh`."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(folder.cleanup)
        self.base = Path(os.path.realpath(folder.name))
        self.root = self.base / 'project'
        self.env = dict(
            os.environ,
            GIT_AUTHOR_NAME='T',
            GIT_AUTHOR_EMAIL='t@example.org',
            GIT_COMMITTER_NAME='T',
            GIT_COMMITTER_EMAIL='t@example.org',
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM='1',
            ORCHESTRATOR_BEAT_SECONDS='0.3',
            ORCHESTRATOR_POLL_SECONDS='0.2',
            ORCHESTRATOR_TMP=str(self.base / 'verify-tmp'),
            PYTHONDONTWRITEBYTECODE='1',
            # Aucune alerte vers le téléphone du propriétaire depuis les tests (alerts.py lit ce sujet ntfy).
            WSC_NTFY_TOPIC='',
        )
        for name in ('WORKSPACE_AGENT_ROOT', 'WORKSPACE_IMAGE_PYTHON'):
            self.env.pop(name, None)
        (self.base / 'verify-tmp').mkdir()
        fake_claude = self.base / 'fake-claude'
        fake_claude.write_text(FAKE_CLAUDE)
        fake_claude.chmod(0o755)
        self.env['ORCHESTRATOR_CLAUDE'] = str(fake_claude)
        self.env['FAKE_DIR'] = str(self.root / '.workspace' / 'fake')
        self.build_project()

    def git(self, *args):
        return (
            subprocess.run(
                ['git'] + list(args), cwd=str(self.root), env=self.env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            .stdout.decode()
            .strip()
        )

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def build_project(self):
        self.root.mkdir()
        self.git('init', '-q')
        self.git('symbolic-ref', 'HEAD', 'refs/heads/main')
        scripts = self.root / 'scripts'
        shutil.copytree(
            str(ROOT / 'template' / 'scripts' / 'lib'), str(scripts / 'lib'), ignore=shutil.ignore_patterns('__pycache__', '__tests__')
        )
        shutil.copy(str(ROOT / 'template' / 'scripts' / 'orchestrator.sh'), str(scripts / 'orchestrator.sh'))
        self.write('scripts/fake_circuit.py', FAKE_CIRCUIT)
        for name, body in WRAPPERS.items():
            self.write('scripts/' + name, '#!/bin/bash\nd=$(cd "$(dirname "$0")" && pwd)\n' + body + '\n')
        self.write('template/scripts/marker.txt', 'miroir\n')
        self.write('.gitignore', '.workspace/\n__pycache__/\n')
        for task_id in ('t1', 't2'):
            self.write(f'fiches/{task_id}.md', fiche_text(task_id))
        self.write(
            'docs/plan.md',
            '\n'.join(
                [
                    'session: S1',
                    'by: owner',
                    'verify: test -f src/t1.txt && test -d "$WORKSPACE_TEST_TMP"',
                    'report: docs/RAPPORT.md',
                    '- fiche: fiches/t1.md',
                    '- fusion validée: t1',
                    '',
                ]
            ),
        )
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'init')

    def control(self, **values):
        path = self.root / '.workspace' / 'fake' / 'control.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values))

    def run_cli(self, *args, plan='docs/plan.md'):
        return subprocess.run(
            [sys.executable, str(RUN), plan, '--root', str(self.root)] + list(args),
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=TIMEOUT,
        )

    def delegate_env(self, task_id):
        path = self.root / '.workspace' / 'fake' / ('env-' + task_id + '.jsonl')
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def calls(self):
        path = self.root / '.workspace' / 'fake' / 'calls.log'
        return path.read_text().splitlines() if path.exists() else []

    def journal(self):
        path = self.root / '.workspace' / 'orchestrator-run.json'
        return json.loads(path.read_text()) if path.exists() else None

    def events(self):
        path = self.root / '.workspace' / 'events.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def wait_for(self, check, what):
        deadline = time.time() + 30
        while time.time() < deadline:
            value = check()
            if value:
                return value
            time.sleep(0.05)
        self.fail('délai dépassé : ' + what)

    def lock(self):
        return self.root / '.workspace' / 'orchestrator.json'

    def detach(self, *args, plan='docs/plan.md'):
        done = self.run_cli('--detach', *args, plan=plan)
        self.assertEqual(done.returncode, 0, done.stderr)
        found = re.fullmatch(
            r'détaché : pid (\d+), journal \.workspace/orchestrator-run\.json, '
            r'sortie \.workspace/orchestrator-run/run\.log\n',
            done.stdout,
        )
        self.assertIsNotNone(found, done.stdout)
        return int(found.group(1))

    def assert_delivered(self, task_id='t1', merges=1):
        merged = [e for e in self.events() if e['event'] == 'merged' and e['task_id'] == task_id]
        self.assertEqual(len(merged), merges)
        self.assertEqual(self.journal()['tasks'][task_id]['merge_commit'], merged[-1]['detail']['commit'])
        subjects = self.git('log', '--merges', '--format=%s').splitlines()
        self.assertEqual(subjects.count('merge: task ' + task_id), merges)


class PlanTests(ProjectCase):
    def test_markdown_plan_with_defaults(self):
        self.write(
            'docs/p.md',
            '# Plan\n\nsession: S.2\nby: owner\nmodel: sonnet\nverify: `make test`\n'
            'report_check: non\n- fiche: `fiches/t1.md`\n* fiche: fiches/t2.md\ntexte libre\n',
        )
        plan = planning.load(self.root, 'docs/p.md', {'by': 'Bob', 'model': None})
        self.assertEqual([item['id'] for item in plan['tasks']], ['t1', 't2'])
        self.assertEqual(plan['tasks'][1]['fiche'], 'fiches/t2.md')
        self.assertEqual((plan['session'], plan['by'], plan['model']), ('S.2', 'Bob', 'sonnet'))
        self.assertEqual(plan['verify'], 'make test')
        self.assertFalse(plan['report_check'])
        self.assertTrue(plan['resync'])
        self.assertFalse(plan['commit_report'])
        self.assertEqual(plan['report'], '.workspace/orchestrator-run/RAPPORT-S.2.md')
        self.assertEqual(plan['image'], 'workspace-python-git:3.14')
        self.assertEqual(len(plan['sha256']), 64)
        self.assertEqual(plan['path'], 'docs/p.md')

    def test_json_plan(self):
        self.write(
            'docs/p.json',
            json.dumps({'session': 'S', 'by': 'A', 'tasks': ['fiches/t1.md'], 'verify': None, 'resync': False, 'report': 'docs/R.md'}),
        )
        plan = planning.load(self.root, 'docs/p.json')
        self.assertEqual((plan['model'], plan['verify'], plan['resync'], plan['commit_report']), ('opus', None, False, True))

    def test_invalid_plans_are_refused(self):
        (self.root / 'link').symlink_to(self.root / 'fiches')
        base = 'session: S\nby: A\n'
        cases = {
            'plan absent': (None, 'docs/absent.md'),
            'aucune fiche': (base, 'docs/p.md'),
            'by manquant': ('session: S\n- fiche: fiches/t1.md\n', 'docs/p.md'),
            'session invalide': ('session: a b\nby: A\n- fiche: fiches/t1.md\n', 'docs/p.md'),
            'fiche absente': (base + '- fiche: fiches/zz.md\n', 'docs/p.md'),
            'hors racine': (base + '- fiche: ../elsewhere.md\n', 'docs/p.md'),
            'lien symbolique': (base + '- fiche: link/t1.md\n', 'docs/p.md'),
            'doublon': (base + '- fiche: fiches/t1.md\n- fiche: fiches/t1.md\n', 'docs/p.md'),
            'clé inconnue': (json.dumps({'session': 'S', 'by': 'A', 'tasks': ['fiches/t1.md'], 'x': 1}), 'docs/p.json'),
        }
        self.write('fiches/nohead.md', '# sans en-tête\n')
        self.write('fiches/noid.md', '---\nagent: bric\n---\n')
        self.write('fiches/badid.md', '---\nid: Bad_Id\n---\n')
        for name in ('nohead', 'noid', 'badid'):
            cases['fiche ' + name] = (base + f'- fiche: fiches/{name}.md\n', 'docs/p.md')
        for label, (text, path) in cases.items():
            with self.subTest(label):
                if text is not None:
                    self.write(path, text)
                with self.assertRaises(planning.PlanError):
                    planning.load(self.root, path)
                done = self.run_cli(plan=path)
                self.assertEqual(done.returncode, 64, done.stderr)
                self.assertFalse((self.root / '.workspace' / 'orchestrator-run.json').exists())
                self.assertFalse(self.lock().exists())

    def test_usage_error_is_64(self):
        done = subprocess.run(
            [sys.executable, str(RUN), '--inconnu'],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=TIMEOUT,
        )
        self.assertEqual(done.returncode, 64)

    def test_process_probes(self):
        self.assertTrue(steps.alive(os.getpid()))
        self.assertFalse(steps.alive(0))
        self.assertFalse(steps.alive(None))
        self.assertIn('python', steps.command_of(os.getpid()).lower())
        self.assertFalse(steps.child_alive(2**22 + 12345))


class RunTests(ProjectCase):
    def delegation_child(self):
        # The prepare child comes first and may still be listed: wait for the delegation itself,
        # and for the fake delegate to have started (a kill before its first line would hide the call).
        child = (self.journal() or {}).get('child') or {}
        return child if child.get('step') == 'delegate' and 'delegate t1' in self.calls() else None

    def test_kill_during_delegation_waits_for_child_and_finishes(self):
        self.control(sleeps=[4])
        program = self.detach()
        child = self.wait_for(self.delegation_child, 'délégation lancée')
        self.assertEqual(child['step'], 'delegate')
        os.kill(program, signal.SIGKILL)
        self.wait_for(lambda: not steps.alive(program), 'programme tué')
        self.assertTrue(steps.child_alive(child['pid']))
        self.assertEqual(self.journal()['status'], 'running')
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn('étape delegate de t1 encore en cours (pid {})'.format(child['pid']), done.stdout)
        self.assertEqual([c for c in self.calls() if not c.startswith('report')], ['prepare t1', 'delegate t1', 'merge t1'])
        self.assertEqual(self.calls().count('report-check docs/RAPPORT.md'), 1)
        data = self.journal()
        self.assertEqual((data['status'], data['resumes'], data['done'], data['failed']), ('finished', 1, ['t1'], []))
        self.assertIsNone(data['child'])
        self.assertIsNone(data['current'])
        self.assertEqual(data['tasks']['t1']['agent'], 'bric-fake')
        self.assertEqual(
            {name: data['tasks']['t1']['steps'][name]['status'] for name in ('prepare', 'delegate', 'verify', 'merge')},
            {'prepare': 'done', 'delegate': 'done', 'verify': 'done', 'merge': 'done'},
        )
        self.assertTrue(any('reprise après interruption' in text for text in data['decisions']))
        self.assert_delivered()
        self.assertFalse(self.lock().exists())
        kinds = [e['event'] for e in self.events() if e['event'].startswith('orchestrator_')]
        self.assertEqual(kinds[-1], 'orchestrator_released')
        self.assertEqual(kinds.count('orchestrator_claimed'), 2)
        runs = [
            e['detail']['run']
            for e in self.events()
            if e['event'] == 'note' and e['task_id'] == 'orchestrator' and e['detail']['run'] != 'state'
        ]
        self.assertEqual(runs, ['started', 'resumed', 'finished'])
        self.assertTrue((self.root / 'docs' / 'RAPPORT.md').read_text().startswith('# Rapport'))
        self.assertEqual(self.git('log', '-1', '--format=%s'), 'docs: orchestrator report S1')
        self.assertIn('chore(circuit): resync scripts mirror after t1', self.git('log', '--format=%s'))
        self.assertTrue((self.root / 'scripts' / 'marker.txt').exists())
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(os.stat(str(self.root / '.workspace' / 'orchestrator-run.json')).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(str(self.root / '.workspace' / 'orchestrator-run')).st_mode & 0o777, 0o700)

    def test_kill_of_program_and_child_runs_delegation_alone(self):
        self.control(sleeps=[60])
        program = self.detach()
        child = self.wait_for(self.delegation_child, 'délégation lancée')
        os.kill(program, signal.SIGKILL)
        os.killpg(child['pid'], signal.SIGKILL)
        self.wait_for(lambda: not steps.alive(child['pid']) and not steps.alive(program), 'processus morts')
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual([c for c in self.calls() if not c.startswith('report')], ['prepare t1', 'delegate t1', 'delegate t1', 'merge t1'])
        self.assertEqual(self.journal()['resumes'], 1)
        self.assert_delivered()

    def test_kill_during_merge_does_not_merge_twice(self):
        self.control(merge_sleep=4)
        program = self.detach()
        self.wait_for(lambda: ((self.journal() or {}).get('child') or {}).get('step') == 'merge', 'fusion lancée')
        os.kill(program, signal.SIGKILL)
        self.wait_for(lambda: not steps.alive(program), 'programme tué')
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.calls().count('merge t1'), 1)
        self.assertEqual(self.calls().count('prepare t1'), 1)
        self.assert_delivered()

    def test_failure_then_prepare_retry(self):
        self.control(outcomes={'t1': ['failed', 'done']})
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(
            [c for c in self.calls() if not c.startswith('report')],
            ['prepare t1', 'delegate t1', 'claude sonnet', 'close t1', 'prepare t1 retry', 'delegate t1', 'merge t1'],
        )
        data = self.journal()
        self.assertEqual((data['tasks']['t1']['failures'], data['done'], data['failed']), (1, ['t1'], []))
        self.assertTrue(any('première erreur' in text for text in data['decisions']))
        # T14 B1 : la reprise reçoit le diagnostic de Sonnet, nourri des journaux de l'échec.
        seen = self.delegate_env('t1')
        self.assertIsNone(seen[0]['context'])
        self.assertIn('Cause probable : test rouge.', seen[1]['context'])
        self.assertIn('délégation : failed', (self.root / '.workspace/fake/prompt-sonnet.txt').read_text())
        args = (self.root / '.workspace/fake/args-sonnet.txt').read_text().splitlines()
        self.assertEqual(args[args.index('--max-turns') + 1], '3')
        self.assert_delivered()

    def test_diagnosis_blaming_the_circuit_stops_without_retry(self):
        # E15 (12/11) : le diagnostic accusait le circuit, la reprise a refait le même échec (environ 0,70 $ perdus).
        self.control(outcomes={'t1': ['failed', 'done']}, claude={'sonnet': ['Le clone ignore les exclusions.\nCAUSE: circuit']})
        done = self.run_cli()
        self.assertEqual(done.returncode, 4, done.stderr + done.stdout)
        self.assertEqual([c for c in self.calls() if not c.startswith('report')], ['prepare t1', 'delegate t1', 'claude sonnet'])
        self.assertIn('CAUSE: circuit', (self.root / '.workspace/fake/prompt-sonnet.txt').read_text())
        data = self.journal()
        entry = data['tasks']['t1']
        self.assertEqual((entry['failures'], entry['state'], data['failed']), (0, 'awaiting_user', []))
        self.assertEqual(entry['notice'], '.workspace/orchestrator-run/CIRCUIT-t1.md')
        notice = (self.root / entry['notice']).read_text()
        self.assertIn('`CAUSE: circuit`', notice)
        self.assertIn('.workspace/orchestrator-run/diagnostic-t1.md', notice)
        self.assertIn('relancer la même commande', notice)
        self.assertTrue(any("ne met pas l'exécutant en cause" in text for text in data['decisions']))
        self.assertFalse(any('première erreur' in text for text in data['decisions']))
        # La cause corrigée, la même commande rejoue la délégation sur le même clone : ni clôture ni `prepare --retry`.
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(
            [c for c in self.calls() if not c.startswith('report')],
            ['prepare t1', 'delegate t1', 'claude sonnet', 'delegate t1', 'merge t1'],
        )
        self.assertEqual(self.journal()['tasks']['t1']['failures'], 0)
        self.assert_delivered()

    def test_diagnosis_blaming_the_sheet_stops_with_a_diagnostic_notice(self):
        self.control(outcomes={'t1': ['failed', 'done']}, claude={'sonnet': ['CAUSE: circuit\nLa fiche se contredit.\n`CAUSE: fiche`']})
        done = self.run_cli()
        self.assertEqual(done.returncode, 4, done.stderr + done.stdout)
        self.assertEqual([c for c in self.calls() if not c.startswith('report')], ['prepare t1', 'delegate t1', 'claude sonnet'])
        entry = self.journal()['tasks']['t1']
        self.assertEqual((entry['failures'], entry['notice']), (0, '.workspace/orchestrator-run/DIAGNOSTIC-t1.md'))
        self.assertIn('`CAUSE: fiche`', (self.root / entry['notice']).read_text())

    def test_diagnosis_naming_the_executor_retries_as_before(self):
        self.control(outcomes={'t1': ['failed', 'done']}, claude={'sonnet': ['Test rouge.\nCAUSE: executant']})
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(
            [c for c in self.calls() if not c.startswith('report')],
            ['prepare t1', 'delegate t1', 'claude sonnet', 'close t1', 'prepare t1 retry', 'delegate t1', 'merge t1'],
        )
        self.assertEqual(self.journal()['tasks']['t1']['failures'], 1)
        self.assertFalse((self.root / '.workspace/orchestrator-run/CIRCUIT-t1.md').exists())
        self.assertIn('CAUSE: executant', self.delegate_env('t1')[1]['context'])

    def test_diagnostic_off_retries_without_a_model(self):
        self.write('docs/plan.md', (self.root / 'docs/plan.md').read_text() + 'diagnostic: non\n')
        self.control(outcomes={'t1': ['failed', 'done']})
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertNotIn('claude sonnet', self.calls())
        self.assertEqual([item['context'] for item in self.delegate_env('t1')], [None, None])

    def test_second_failure_exports_the_diff_closes_the_clone_and_moves_on(self):
        self.write(
            'docs/plan2.md',
            'session: S1\nby: owner\nverify: test -d "$WORKSPACE_TEST_TMP"\n'
            'report: docs/RAPPORT.md\n- fiche: fiches/t1.md\n- fiche: fiches/t2.md\n- fusion validée: t1\n- fusion validée: t2\n',
        )
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'plan 2')
        self.control(outcomes={'t1': ['failed', 'failed']})
        done = self.run_cli(plan='docs/plan2.md')
        self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
        data = self.journal()
        self.assertEqual((data['done'], data['failed'], data['status']), (['t2'], ['t1'], 'finished'))
        self.assertEqual(data['tasks']['t1']['failures'], 2)
        self.assertEqual(data['tasks']['t1']['reason'], 'délégation : failed')
        self.assertEqual(self.calls().count('delegate t1'), 2)
        # T120 (nuit du 3/11) : le clone d'une fiche en échec bloquait le périmètre des suivantes. Le travail est gardé
        # dans un diff complet, la branche et les preuves restent, puis le clone est fermé.
        self.assertEqual(self.calls().count('close t1'), 2)
        self.assertEqual(data['tasks']['t1']['patch'], '.workspace/orchestrator-run/t1-echec.patch')
        self.assertTrue((self.root / data['tasks']['t1']['patch']).is_file())
        self.assertFalse((self.root / '.workspace' / 'worktrees' / 't1').exists())
        self.assertTrue(self.git('branch', '--list', 'workspace-task/t1'))
        self.assertTrue((self.root / '.workspace' / 'run' / 'delegate' / 't1' / 'status.json').is_file())
        self.assertFalse(self.lock().exists())
        report = (self.root / 'docs' / 'RAPPORT.md').read_text()
        self.assertIn('échec', report)
        self.assertIn('diff complet `.workspace/orchestrator-run/t1-echec.patch`', report)

    def test_preparation_failure_is_final(self):
        self.control(prepare_fail=True)
        done = self.run_cli()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.journal()['tasks']['t1']['reason'], 'préparation : code 3')
        self.assertEqual(self.calls().count('prepare t1'), 1)

    def test_finished_plan_does_nothing(self):
        self.assertEqual(self.run_cli().returncode, 0)
        calls, events = self.calls(), self.events()
        before = self.journal()
        done = self.run_cli()
        self.assertEqual(done.returncode, 0)
        self.assertIn('plan déjà terminé ({}) : rien à faire'.format(before['finished_at']), done.stdout)
        self.assertEqual((self.calls(), self.events(), self.journal()), (calls, events, before))
        self.assertFalse(self.lock().exists())

    def test_finished_journal_of_other_plan_is_archived(self):
        self.assertEqual(self.run_cli().returncode, 0)
        self.write('docs/other.md', 'session: S1\nby: owner\nreport: docs/R2.md\n- fiche: fiches/t2.md\n- fusion validée: t2\n')
        done = self.run_cli(plan='docs/other.md')
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.journal()['plan'], 'docs/other.md')
        self.assertEqual(len(list((self.root / '.workspace' / 'orchestrator-run' / 'archive').glob('*.json'))), 1)

    def test_lock_held_by_other_session(self):
        claim = subprocess.run(
            ['bash', 'scripts/orchestrator.sh', 'claim', '--session', 'autre'],
            cwd=str(self.root),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(claim.returncode, 0)
        done = self.run_cli()
        self.assertEqual(done.returncode, 3)
        self.assertEqual(self.calls(), [])
        self.assertIsNone(self.journal())
        self.assertEqual(json.loads(self.lock().read_text())['session'], 'autre')

    def test_quota_stops_cleanly_then_resumes_and_guards_the_plan(self):
        self.control(outcomes={'t1': ['quota']})
        done = self.run_cli()
        self.assertEqual(done.returncode, 75, done.stderr + done.stdout)
        data = self.journal()
        self.assertEqual((data['status'], data['stop_reason'], data['done']), ('stopped', 'quota', []))
        self.assertFalse(self.lock().exists())
        self.assertTrue((self.root / 'docs' / 'RAPPORT.md').exists())
        self.write('docs/other.md', 'session: S1\nby: owner\n- fiche: fiches/t2.md\n- fusion validée: t2\n')
        self.assertEqual(self.run_cli(plan='docs/other.md').returncode, 3)
        (self.root / 'docs' / 'other.md').unlink()
        original = (self.root / 'docs' / 'plan.md').read_text()
        self.write('docs/plan.md', original + 'model: sonnet\n')
        self.assertEqual(self.run_cli().returncode, 64)
        self.write('docs/plan.md', original)
        self.control()
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        data = self.journal()
        self.assertEqual((data['status'], data['resumes'], data['done']), ('finished', 1, ['t1']))
        self.assertEqual(self.calls().count('delegate t1'), 2)
        self.assertEqual(self.calls().count('prepare t1'), 1)
        self.assertEqual(self.calls().count('report-check docs/RAPPORT.md'), 2)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_sigterm_stops_and_leaves_lock_and_child(self):
        self.control(sleeps=[3])
        proc = subprocess.Popen(
            [sys.executable, str(RUN), 'docs/plan.md', '--root', str(self.root)],
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(proc.wait)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.stderr.close)
        child = self.wait_for(self.delegation_child, 'délégation lancée')
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=30), 143)
        data = self.journal()
        self.assertEqual((data['status'], data['stop_reason']), ('stopped', 'signal'))
        self.assertTrue(self.lock().exists())
        self.assertTrue(steps.child_alive(child['pid']))
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.calls().count('delegate t1'), 1)
        self.assertEqual(self.journal()['resumes'], 1)
        self.assert_delivered()

    def test_task_already_merged_is_not_redone(self):
        self.write_merged_state()
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual([c for c in self.calls() if not c.startswith('report')], [])
        task = self.journal()['tasks']['t1']
        self.assertEqual(task['merge_commit'], 'abc123')
        self.assertEqual(
            {name: task['steps'][name]['status'] for name in ('prepare', 'delegate', 'verify', 'merge')},
            {'prepare': 'done', 'delegate': 'done', 'verify': 'done', 'merge': 'done'},
        )

    def write_merged_state(self):
        self.write('.workspace/orchestration/tasks/t1/state.json', json.dumps({'status': 'merged'}))
        self.write(
            '.workspace/events.jsonl',
            json.dumps({'at': '2026-01-01T00:00:00+00:00', 'task_id': 't1', 'event': 'merged', 'detail': {'commit': 'abc123'}}) + '\n',
        )


if __name__ == '__main__':
    unittest.main()
