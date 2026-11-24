import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/campaign/resume_study.py'

FICHE = '---\nid: demo\nagent: codex\nmode: write\nallowed: [a.txt]\nattempts: 3\n---\nid: ne pas toucher\nattempts: 9\nCORPS\n'

# Fake circuit scripts: log every call with the study switches, write the status of the delegation.
TASK_WORKSPACE = '''echo "task-workspace $*" >> "$LOG"
if [ "$1" = prepare ] && [ -n "$FAIL_PREPARE" ] && [ "$2" = ".workspace/tasks/$FAIL_PREPARE.md" ]; then echo broken; exit 1; fi
'''
DELEGATE = '''id=$(basename "$1" .md)
echo "delegate $* FORCE=${WORKSPACE_STUDY_FORCE_FAIL-unset} RESUME=${WORKSPACE_DELEGATE_RESUME-unset} ROOT=${WORKSPACE_AGENT_ROOT-unset} PWD=$(pwd)" >> "$LOG"
mkdir -p ".workspace/run/delegate/$id"
if [ "${WORKSPACE_DELEGATE_RESUME-}" = 0 ]; then read=0; second=40.5; else read=1000; second=20; fi
printf '{"outcome":"done","attempts":2,"metrics":{"duration_seconds":100.25,"attempt_durations_seconds":[50,%s],"tokens":{"input":10,"cached_input":%s,"cache_creation":5,"output":7}}}' "$second" "$read" > ".workspace/run/delegate/$id/status.json"
'''


class ResumeStudyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.project, self.source, self.out = base / 'project', base / 'kit', base / 'out'
        self.log = base / 'calls.log'
        (self.project / 'scripts').mkdir(parents=True)
        (self.project / '.gitignore').write_text('.workspace/\n')
        (self.project / 'scripts/task-workspace.sh').write_text(TASK_WORKSPACE)
        (self.project / 'scripts/delegate.sh').write_text(DELEGATE)
        self.git('init', '-q', '-b', 'main')
        self.git('add', '.')
        self.git('-c', 'user.name=T', '-c', 'user.email=t@example.invalid', 'commit', '-qm', 'base')
        for name, sha in (('t1', 'aaa111'), ('t2', 'bbb222')):
            self.add_source(name, sha)

    def git(self, *args):
        subprocess.run(['git', '-C', str(self.project), *args], check=True, capture_output=True)

    def add_source(self, name, sha):
        (self.source / '.workspace/tasks').mkdir(parents=True, exist_ok=True)
        (self.source / '.workspace/tasks' / (name + '.md')).write_text(FICHE.replace('demo', name))
        (self.source / '.workspace/acceptance' / name / 'tests').mkdir(parents=True)
        (self.source / '.workspace/acceptance' / name / 'tests/test_hidden.py').write_text('hidden ' + name)
        state = self.source / '.workspace/orchestration/tasks' / name
        state.mkdir(parents=True)
        (state / 'state.json').write_text(json.dumps({'base_commit': sha}))

    def run_study(self, tasks='t1,t2', extra=(), **env):
        environment = dict(os.environ, LOG=str(self.log), WORKSPACE_AGENT_ROOT='/should/be/removed', **env)
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                '--project',
                str(self.project),
                '--source',
                str(self.source),
                '--tasks',
                tasks,
                '--out',
                str(self.out),
                *extra,
            ],
            env=environment,
            text=True,
            capture_output=True,
        )

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def rows(self):
        return [json.loads(line) for line in (self.out / 'results.jsonl').read_text().splitlines()]

    def test_each_task_runs_once_per_mode_with_the_right_switches(self):
        result = self.run_study()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 12)
        self.assertEqual(calls[0], 'task-workspace prepare .workspace/tasks/t1-etude-reprise.md --base aaa111')
        self.assertTrue(calls[1].startswith('delegate .workspace/tasks/t1-etude-reprise.md FORCE=1 RESUME=unset ROOT=unset PWD='))
        self.assertTrue(calls[1].endswith(str(self.project)))
        self.assertEqual(calls[2], 'task-workspace close t1-etude-reprise --abandoned --force')
        self.assertEqual(calls[3], 'task-workspace prepare .workspace/tasks/t1-etude-sans-reprise.md --base aaa111')
        self.assertIn('FORCE=1 RESUME=0 ROOT=unset', calls[4])
        self.assertEqual(calls[6], 'task-workspace prepare .workspace/tasks/t2-etude-reprise.md --base bbb222')
        self.assertEqual(
            [r['study_id'] for r in self.rows()], ['t1-etude-reprise', 't1-etude-sans-reprise', 't2-etude-reprise', 't2-etude-sans-reprise']
        )
        self.assertFalse(any('merge' in call for call in calls))

    def test_study_fiche_rewrites_only_the_header_and_copies_the_hidden_tests(self):
        self.assertEqual(self.run_study('t1', ('--agent', 'claude-sonnet')).returncode, 0)
        fiche = (self.project / '.workspace/tasks/t1-etude-sans-reprise.md').read_text()
        self.assertEqual(
            fiche,
            FICHE.replace('demo', 't1-etude-sans-reprise')
            .replace('agent: codex', 'agent: claude-sonnet')
            .replace('attempts: 3', 'attempts: 2'),
        )
        self.assertIn('id: ne pas toucher\nattempts: 9\nCORPS', fiche)
        copy = self.project / '.workspace/acceptance/t1-etude-reprise/tests/test_hidden.py'
        self.assertEqual(copy.read_text(), 'hidden t1')

    def test_results_and_table_carry_durations_tokens_and_totals_per_mode(self):
        self.assertEqual(self.run_study('t1').returncode, 0)
        first, second = self.rows()
        self.assertEqual(
            first,
            {
                'task': 't1',
                'mode': 'reprise',
                'study_id': 't1-etude-reprise',
                'outcome': 'done',
                'attempts': 2,
                'duration_seconds': 100.25,
                'second_attempt_seconds': 20,
                'tokens': {'input': 10, 'cache_read': 1000, 'cache_write': 5, 'output': 7},
            },
        )
        self.assertEqual(second['second_attempt_seconds'], 40.5)
        self.assertEqual(second['tokens']['cache_read'], 0)
        lines = (self.out / 'resultats.md').read_text().splitlines()
        self.assertEqual(
            lines[0], '| Tâche | Mode | Issue | Tentatives | Durée (s) | 2e tentative (s) | Cache lu | Cache écrit | Entrée | Sortie |'
        )
        self.assertTrue(lines[2].startswith('| t1 | reprise | done | 2 | 100.2 | 20 | 1000 | 5 | 10 | 7 |'))
        self.assertTrue(lines[-2].startswith('| Total reprise |'))
        self.assertIn('| 100.2 | 20 | 1000 | 5 | 10 | 7 |', lines[-2])
        self.assertTrue(lines[-1].startswith('| Total sans-reprise |'))
        self.assertIn('| 100.2 | 40.5 | 0 | 5 | 10 | 7 |', lines[-1])

    def test_rerun_after_a_finished_pass_launches_nothing(self):
        self.assertEqual(self.run_study('t1').returncode, 0)
        before = self.calls()
        self.assertEqual(self.run_study('t1').returncode, 0)
        self.assertEqual(self.calls(), before)
        self.assertEqual(len(self.rows()), 2)

    def test_a_failed_prepare_is_an_error_row_without_delegation_and_is_retried(self):
        self.assertEqual(self.run_study('t1', FAIL_PREPARE='t1-etude-reprise').returncode, 0)
        first = self.rows()[0]
        self.assertIn('prepare en échec', first['error'])
        self.assertIsNone(first['outcome'])
        self.assertFalse(any(call.startswith('delegate') and 'etude-reprise.md' in call for call in self.calls()))
        self.assertEqual(self.run_study('t1').returncode, 0)
        rows = self.rows()
        self.assertEqual([('error' in r) for r in rows], [True, False, False])
        self.assertEqual(rows[1]['mode'], 'sans-reprise')
        self.assertEqual(rows[2]['mode'], 'reprise')

    def test_project_stays_clean(self):
        self.assertEqual(self.run_study().returncode, 0)
        status = subprocess.run(['git', '-C', str(self.project), 'status', '--porcelain'], text=True, capture_output=True)
        self.assertEqual(status.stdout, '')

    def assert_refused(self, result):
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.out.exists())
        self.assertFalse((self.project / '.workspace').exists())

    def test_dirty_project_is_refused_before_anything_runs(self):
        (self.project / 'stray.txt').write_text('x')
        self.assert_refused(self.run_study())

    def test_unknown_task_is_refused_before_anything_runs(self):
        self.assert_refused(self.run_study('t1,nope'))

    def test_task_without_hidden_tests_or_base_commit_is_refused(self):
        (self.source / '.workspace/orchestration/tasks/t2/state.json').write_text('{}')
        self.assert_refused(self.run_study())
        (self.source / '.workspace/orchestration/tasks/t2/state.json').write_text(json.dumps({'base_commit': 'bbb222'}))
        shutil.rmtree(self.source / '.workspace/acceptance/t2')
        self.assert_refused(self.run_study())

    def test_invalid_usage_is_refused(self):
        for tasks in ('', 't1,,t2', 't1,t1', '../t1'):
            with self.subTest(tasks):
                self.assert_refused(self.run_study(tasks))
        result = subprocess.run([sys.executable, str(SCRIPT), '--project', str(self.project)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 64)


if __name__ == '__main__':
    unittest.main()
