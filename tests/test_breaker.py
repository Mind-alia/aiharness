import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'template/scripts/lib'))
import breaker
import test_delegate as fixtures
import workdir

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel, sous-processus ou Docker simulé ; suite complète seulement')


PASSING = 'import unittest\nclass Ok(unittest.TestCase):\n def test_ok(self): self.assertEqual(1, 1)\n'
FAILING = 'import unittest\nclass Bug(unittest.TestCase):\n def test_bug(self): self.assertEqual(1, 2)\n'
READS_IMPLEMENTATION = (
    'import unittest\nfrom pathlib import Path\nclass Impl(unittest.TestCase):\n'
    ' def test_impl(self): self.assertEqual(Path("tracked.txt").read_text(), "implemented\\n")\n'
)
CORRUPTS_PRODUCTION = (
    'import unittest\nfrom pathlib import Path\nclass T(unittest.TestCase):\n'
    ' def test_t(self):\n  p=Path("tracked.txt"); ok=p.read_text()=="implemented\\n"\n'
    '  p.write_text("corrupted\\n"); self.assertTrue(ok)\n'
)
HIDDEN_READS_IMPLEMENTATION = 'from pathlib import Path\nraise SystemExit(Path("tracked.txt").read_text() != "implemented\\n")\n'
HIDDEN_READS_DELIVERABLE = 'from pathlib import Path\nraise SystemExit(Path("tests/data.txt").read_text() != "deliverable\\n")\n'


class BreakerTests(unittest.TestCase):
    setUp = fixtures.DelegateTests.setUp
    git = fixtures.DelegateTests.git
    executable = fixtures.DelegateTests.executable
    write_task = fixtures.DelegateTests.write_task
    setup_fake_docker = fixtures.DelegateTests.setup_fake_docker
    prepare_exchange_task = fixtures.DelegateTests.prepare_exchange_task
    get_docker_calls = fixtures.DelegateTests.get_docker_calls
    consumption_rows = fixtures.DelegateTests.consumption_rows

    def prepare(self, tests, production=False, allowed=None):
        self.original = self.prepare_exchange_task(verify='true', allowed=allowed)
        self.original.write_text(self.original.read_text().replace('verify: true', 'verify: true\nverify_profile: off'))
        self.wt = workdir.task_worktree(self.root, 'demo')
        (self.wt / 'tracked.txt').write_text('implemented\n')
        # Execute container commands locally only in this fake Docker fixture.
        docker = self.docker_bin_dir / 'docker'
        text = docker.read_text().replace(
            "elif sub == 'exec':",
            """elif sub == 'exec':
    import subprocess
    args = sys.argv[2:]
    while args[0].startswith('-'):
        flag = args.pop(0)
        if flag in ('-w', '--workdir', '-e', '--env', '-u', '--user'): args.pop(0)
    args.pop(0)
    result = subprocess.run(args, cwd=os.environ['BREAKER_WT'])
    sys.exit(result.returncode)
elif sub == 'unused-exec':""",
        )
        docker.write_text(text)
        self.env['BREAKER_WT'] = str(self.wt)
        self.executable(
            'bin/claude',
            '#!/usr/bin/env python3\n'
            'import json\nfrom pathlib import Path\n'
            'tests = ' + repr(tests) + '\n'
            'for name, content in tests.items():\n'
            ' p=Path("__breaker__")/name; p.parent.mkdir(exist_ok=True); p.write_text(content)\n'
            + ('Path("tracked.txt").write_text("forbidden")\n' if production else '')
            + 'print(json.dumps({"result":"answer", "usage":{"input_tokens":1,"output_tokens":1}}))\n',
        )

    def run_breaker(self, *args):
        return subprocess.run(
            ['bash', str(self.root / 'scripts/breaker.sh'), 'demo', '--agent', 'claude-haiku', *args],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )

    def report(self):
        path = self.root / '.workspace/breaker/demo/report.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        return json.loads(path.read_text())

    def test_reproducible_pass_unstable_and_compile_error(self):
        self.prepare(
            {
                'test_bug.py': 'import unittest\nclass Bug(unittest.TestCase):\n def test_extreme(self): self.assertEqual(1, 2, "privatecredential")\n',
                'test_pass.py': 'import unittest\nclass Pass(unittest.TestCase):\n def test_ok(self): self.assertEqual(1, 1)\n',
                'test_flaky.py': 'import unittest\nfrom pathlib import Path\nclass Flaky(unittest.TestCase):\n def test_flaky(self):\n  p=Path('
                + repr(str(self.root.parent / 'flaky-marker'))
                + '); n=p.exists(); p.write_text("1"); self.assertTrue(n)\n',
                'test_syntax.py': 'invalid syntax !!!\n',
            }
        )
        (self.root / '.env').write_text('DB=privatecredential\n')
        result = self.run_breaker('--max-findings', '4')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report()
        self.assertEqual([r['test'] for r in report['retained']], ['__breaker__/test_bug.py'])
        self.assertEqual({r['reason'] for r in report['discarded']}, {'passe', 'instable', 'ne compile pas'})
        self.assertNotIn('privatecredential', json.dumps(report))
        self.assertFalse((self.wt / '__breaker__').exists())
        self.assertNotIn('__breaker__', subprocess.check_output(['git', '-C', str(self.wt), 'ls-files'], text=True))
        self.assertTrue(all(r['role'] == 'relecture' for r in self.consumption_rows()))
        contract = (self.root / '.workspace/breaker/demo/task.md').read_text()
        self.assertIn('isolation: exchange', contract)
        self.assertIn('attempts: 1', contract)

    def test_promote_and_following_delegation_executes_hidden_test(self):
        self.prepare({'test_bug.py': 'import unittest\nclass Bug(unittest.TestCase):\n def test_extreme(self): self.assertEqual(1, 2)\n'})
        result = self.run_breaker('--promote')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / '.workspace/acceptance/demo/breaker/__breaker__/test_bug.py').is_file())
        self.assertTrue((self.root / '.workspace/acceptance/demo/extra.txt').is_file())
        self.executable(
            'bin/npx',
            '#!/bin/sh\ncase " $* " in *" --version "*) exit 0;; esac\nwhile [ "$1" != -o ]; do shift; done\necho answer > "$2"\necho answer\n',
        )
        result = subprocess.run(
            ['bash', str(self.root / 'scripts/delegate.sh'), str(self.original)],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        status = json.loads((self.root / '.workspace/run/delegate/demo/status.json').read_text())
        self.assertEqual(status['outcome'], 'acceptance_failed')
        self.assertFalse((self.wt / '__breaker__').exists())

    def test_runtime_crash_is_a_reproducible_bug(self):
        self.prepare(
            {
                'test_crash.py': 'import unittest\nclass Bug(unittest.TestCase):\n def test_extreme(self): raise OverflowError("extreme production input")\n'
            }
        )
        result = self.run_breaker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.report()['retained']), 1)

    def test_failed_project_suite_discards_findings(self):
        self.prepare({'test_bug.py': 'import unittest\nclass Bug(unittest.TestCase):\n def test_extreme(self): self.assertEqual(1, 2)\n'})
        self.original.write_text(self.original.read_text().replace('verify: true', 'verify: false'))
        result = self.run_breaker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.report()['retained'], [])
        self.assertEqual(self.report()['discarded'][0]['reason'], 'ne compile pas')
        self.assertFalse((self.wt / '__breaker__').exists())

    def test_done_status_and_max_findings(self):
        self.prepare(
            {
                name: 'import unittest\nclass Bug(unittest.TestCase):\n def test_bug(self): self.assertEqual(1, 2)\n'
                for name in ('test_a.py', 'test_b.py')
            }
        )
        state_path = self.root / '.workspace/orchestration/tasks/demo/state.json'
        state = json.loads(state_path.read_text())
        state['status'] = 'done'
        state_path.write_text(json.dumps(state))
        result = self.run_breaker('--max-findings', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.report()['retained']), 1)
        self.assertEqual(self.report()['discarded'][0]['reason'], 'limite dépassée')

    def test_bytecode_written_by_breaker_is_never_a_candidate(self):
        self.prepare({'test_bug.py': FAILING, '__pycache__/x.pyc': 'compiled', 'y.pyo': 'compiled', 'y.pyc': 'compiled'})
        result = self.run_breaker('--max-findings', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report()
        self.assertEqual([r['test'] for r in report['retained']], ['__breaker__/test_bug.py'])
        self.assertEqual(report['discarded'], [])
        self.assertFalse((self.wt / '__breaker__').exists())

    def test_staged_breaker_files_are_removed_from_index(self):
        self.prepare({'test_bug.py': 'import unittest\nclass Bug(unittest.TestCase):\n def test_bug(self): self.assertEqual(1, 2)\n'})
        agent = self.root / 'bin/claude'
        agent.write_text(
            agent.read_text().replace(
                'print(json.dumps(', 'import subprocess\nsubprocess.check_call(["git", "add", "__breaker__"])\nprint(json.dumps('
            )
        )
        result = self.run_breaker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('__breaker__', subprocess.check_output(['git', '-C', str(self.wt), 'ls-files'], text=True))
        self.assertFalse((self.wt / '__breaker__').exists())

    def test_test_mutating_production_is_not_promoted(self):
        self.prepare(
            {
                'test_bug.py': 'import unittest\nfrom pathlib import Path\nclass Bug(unittest.TestCase):\n def test_bug(self):\n  Path("tracked.txt").write_text("corrupted")\n  self.assertEqual(1, 2)\n'
            }
        )
        result = self.run_breaker('--promote')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.report()['retained'], [])
        self.assertFalse((self.wt / '__breaker__').exists())
        self.assertFalse((self.root / '.workspace/acceptance/demo/extra.txt').exists())
        self.assertEqual((self.wt / 'tracked.txt').read_text(), 'implemented\n')

    def test_default_codex_consumption_is_review(self):
        self.prepare({})
        self.executable(
            'bin/npx',
            '#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n'
            'if "--version" in sys.argv: raise SystemExit(0)\n'
            'p=Path("__breaker__"); p.mkdir(); (p/"test_bug.py").write_text('
            + repr('import unittest\nclass Bug(unittest.TestCase):\n def test_bug(self): self.assertEqual(1, 2)\n')
            + ')\n'
            'Path(sys.argv[sys.argv.index("-o")+1]).write_text("answer")\nprint("answer")\n',
        )
        result = subprocess.run(
            ['bash', str(self.root / 'scripts/breaker.sh'), 'demo'], cwd=self.root, env=self.env, text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.report()['retained']), 1)
        self.assertTrue(all(r['role'] == 'relecture' for r in self.consumption_rows()))
        calls = self.get_docker_calls()
        confirmations = [c for c in calls if c[0] == 'exec' and 'python3' in c]
        self.assertEqual(len(confirmations), 2)
        self.assertEqual(calls[-1][0], 'rm')

    def test_interruption_stops_agent_before_cleanup(self):
        self.prepare({})
        marker = self.root.parent / 'agent-started'
        self.executable(
            'bin/claude',
            '#!/usr/bin/env python3\nimport time\nfrom pathlib import Path\n'
            'p=Path("__breaker__"); p.mkdir(); (p/"test_started.py").write_text("# started")\n'
            'Path(' + repr(str(marker)) + ').touch()\ntime.sleep(2)\n'
            'p.mkdir(exist_ok=True); (p/"test_late.py").write_text("# late write")\nprint("answer")\n',
        )
        process = subprocess.Popen(
            ['bash', str(self.root / 'scripts/breaker.sh'), 'demo', '--agent', 'claude-haiku'],
            cwd=self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists())
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=10)
            time.sleep(2.2)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse((self.wt / '__breaker__').exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_non_neighbor_breaker_is_refused_and_removed(self):
        self.prepare({'test_bug.py': 'assert False\n'})
        (self.wt / 'tracked.txt').write_text('before\n')
        (self.wt / 'src').mkdir()
        (self.wt / 'src/app.py').write_text('value = 1\n')
        subprocess.check_call(['git', '-C', str(self.wt), 'add', 'src/app.py'])
        result = self.run_breaker()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('out_of_scope', result.stderr)
        self.assertFalse((self.wt / '__breaker__').exists())
        self.assertFalse((self.wt / 'src/__breaker__').exists())

    def test_report_masks_json_escaped_secret(self):
        secret = 'private"credential'
        self.prepare(
            {'test_bug.py': 'import unittest\nclass Bug(unittest.TestCase):\n def test_bug(self): self.fail(' + repr(secret) + ')\n'}
        )
        (self.root / '.env').write_text("DB='" + secret + "'\n")
        result = self.run_breaker()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report()
        self.assertNotIn(secret, report['retained'][0]['assertion'])
        self.assertIn('***', report['retained'][0]['assertion'])

    def test_unknown_task_refused(self):
        result = self.run_breaker()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('tâche', result.stderr.lower())

    def test_production_change_refused_and_cleaned(self):
        self.prepare({'test_bug.py': 'assert False\n'}, production=True)
        result = self.run_breaker()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.wt / '__breaker__').exists())
        self.assertEqual(self.report()['retained'], [])
        self.assertIn('out_of_scope', result.stderr)
        self.assertEqual((self.wt / 'tracked.txt').read_text(), 'implemented\n')

    def test_vitest5_json_on_stdout_is_read_without_report_file(self):
        # Vitest 5 writes .vitest/json/output.json unless --outputFile.json=/dev/stdout is given.
        self.prepare({'test_vitest.test.ts': 'test("bug", () => { expect(1).toBe(2) })\n'})
        self.executable(
            'bin/npx',
            '\n'.join(
                [
                    '#!/usr/bin/env python3',
                    'import json, sys',
                    'from pathlib import Path',
                    "if '--outputFile.json=/dev/stdout' in sys.argv:",
                    "    print(json.dumps({'numRuntimeErrorTestSuites': 0, 'testResults': [{'assertionResults': [{'status': 'failed', 'failureMessages': ['expected 1 to be 2']}]}]}))",
                    '    raise SystemExit(1)',
                    "p = Path('.vitest/json'); p.mkdir(parents=True)",
                    "(p / 'output.json').write_text('{}')",
                    "print('JSON report written to .vitest/json/output.json')",
                ]
            )
            + '\n',
        )
        result = self.run_breaker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([r['test'] for r in self.report()['retained']], ['__breaker__/test_vitest.test.ts'])
        self.assertFalse((self.wt / '.vitest').exists())
        self.assertTrue(any('--outputFile.json=/dev/stdout' in c for c in self.get_docker_calls()))

    def test_result_data_ignores_vitest5_trailer_after_json(self):
        # Vitest 5 appends its trailer on the same line as the JSON, without a newline.
        report = {
            'numRuntimeErrorTestSuites': 0,
            'testResults': [
                {
                    'assertionResults': [
                        {'status': 'passed', 'failureMessages': []},
                        {'status': 'failed', 'failureMessages': ['expected 1 to be 2']},
                    ]
                }
            ],
        }
        data = breaker.result_data(json.dumps(report) + 'JSON report written to /dev/stdout\n')
        self.assertEqual(data['tests'], 2)
        self.assertEqual(data['failures'], 1)
        self.assertEqual(data['errors'], 0)
        self.assertEqual(data['skipped'], 0)
        self.assertEqual(data['message'], 'expected 1 to be 2')

    def test_result_data_without_json_keeps_fallback(self):
        data = breaker.result_data('no report here\n')
        self.assertEqual(data, dict(tests=0, failures=0, errors=1, skipped=0, message='Diagnostic de test absent.'))

    def snapshot(self):
        files = {}
        for path in sorted(self.wt.rglob('*')):
            relative = path.relative_to(self.wt)
            if relative.parts[0] == '.git' or path.is_dir():
                continue
            files[relative.as_posix()] = (path.read_bytes(), path.stat().st_mode)
        index = subprocess.check_output(['git', '-C', str(self.wt), 'rev-parse', '--git-path', 'index'], text=True).strip()
        index = Path(index) if os.path.isabs(index) else self.wt / index
        status = subprocess.check_output(['git', '-C', str(self.wt), 'status', '--porcelain', '-uall'], text=True)
        return files, index.read_bytes(), status

    def add_project_test(self, text):
        (self.wt / 'tests').mkdir(exist_ok=True)
        (self.wt / 'tests/test_project.py').write_text(text)

    def add_hidden_test(self, text):
        hidden = self.root / '.workspace/acceptance/demo'
        hidden.mkdir(parents=True)
        (hidden / 'hidden.py').write_text(text)
        self.original.write_text(
            self.original.read_text().replace('verify: true', 'verify: true\nacceptance: [hidden.py]\nacceptance_verify: python3 hidden.py')
        )

    def merge(self):
        identity = dict(
            GIT_AUTHOR_NAME='T', GIT_AUTHOR_EMAIL='t@example.invalid', GIT_COMMITTER_NAME='T', GIT_COMMITTER_EMAIL='t@example.invalid'
        )
        return subprocess.run(
            ['bash', str(self.root / 'scripts/task-workspace.sh'), 'merge', 'demo', '--no-integration'],
            cwd=self.root,
            env=dict(self.env, **identity),
            text=True,
            capture_output=True,
        )

    def task_state(self):
        return json.loads((self.root / '.workspace/orchestration/tasks/demo/state.json').read_text())

    def git_wt(self, *args):
        subprocess.check_call(
            ['git', '-C', str(self.wt), '-c', 'user.name=T', '-c', 'user.email=t@example.invalid', *args], stdout=subprocess.DEVNULL
        )

    def run_none(self):
        return subprocess.run(
            ['bash', str(self.root / 'scripts/breaker.sh'), 'demo', '--agent', 'none'],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )

    def test_is_test_path(self):
        for name in (
            'tests/a.txt',
            'a/test/b.py',
            'x/__tests__/y.js',
            'x/__breaker__/y',
            'test_a.py',
            'a/b_test.py',
            'a.test.ts',
            'a/b.spec.mjs',
            'c.test.cts',
            'd.spec.tsx',
        ):
            self.assertTrue(breaker.is_test_path(name), name)
        for name in (
            'src/app.py',
            'tests',
            'testing/a.py',
            'a.test.py',
            'a.spec.json',
            'contest.py',
            'src/test_a.txt',
            'README.md',
            'a_tests.py',
        ):
            self.assertFalse(breaker.is_test_path(name), name)

    def test_task_changes_lists_tracked_and_untracked_without_workspace_or_breaker(self):
        self.prepare({})
        base = self.task_state()['base_commit']
        (self.wt / 'new dir').mkdir()
        (self.wt / 'new dir/z.py').write_text('z = 1\n')
        (self.wt / 'a.py').write_text('a = 1\n')
        (self.wt / '__breaker__').mkdir()
        (self.wt / '__breaker__/test_x.py').write_text('x\n')
        (self.wt / 'bin').mkdir()
        (self.wt / 'bin/ignored.txt').write_text('ignored\n')
        self.assertEqual(breaker.task_changes(self.wt, base), ['a.py', 'new dir/z.py', 'tracked.txt'])

    def test_fingerprint_is_the_same_committed_or_not_and_changes_with_content(self):
        self.prepare({})
        base = self.task_state()['base_commit']
        (self.wt / 'tests').mkdir()
        (self.wt / 'tests/test_a.py').write_text(PASSING)
        before = breaker.change_fingerprint(self.wt, base)
        self.git_wt('add', '-A')
        self.git_wt('commit', '-qm', 'task')
        self.assertEqual(breaker.change_fingerprint(self.wt, base), before)
        (self.wt / 'tracked.txt').write_text('other\n')
        self.assertNotEqual(breaker.change_fingerprint(self.wt, base), before)
        self.assertRegex(before, r'^[0-9a-f]{64}$')

    def test_diff_without_implementation_is_reported_and_merge_refused(self):
        self.prepare({'test_pass.py': PASSING}, allowed=['tracked.txt', 'tests/test_project.py'])
        self.add_project_test(PASSING)
        before = self.snapshot()
        result = self.run_breaker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('no_implementation', result.stdout)
        report = self.report()
        self.assertEqual(report['verdict'], 'no_implementation')
        self.assertIsNone(report['discriminant'])
        self.assertEqual(report['retained'], [])
        self.assertEqual([d['reason'] for d in report['discarded']], ['passe'])
        self.assertEqual(report['base_commit'], self.task_state()['base_commit'])
        self.assertEqual(report['fingerprint'], breaker.change_fingerprint(self.wt, report['base_commit']))
        self.assertEqual(self.snapshot(), before)
        merge = self.merge()
        self.assertNotEqual(merge.returncode, 0)
        self.assertIn('no_implementation', merge.stderr)
        self.assertIn('"outcome": "no_implementation"', (self.root / '.workspace/events.jsonl').read_text())
        self.assertNotEqual(self.task_state()['status'], 'merged')

    def test_stale_unreadable_or_absent_report_blocks_nothing(self):
        self.prepare({})
        base = self.task_state()['base_commit']
        self.assertIsNone(breaker.merge_refusal(self.root, 'demo', self.wt, base))
        self.add_project_test(PASSING)
        self.assertEqual(self.run_breaker().returncode, 0)
        self.assertTrue(breaker.merge_refusal(self.root, 'demo', self.wt, base).startswith('no_implementation'))
        self.assertIsNone(breaker.merge_refusal(self.root, 'demo', self.wt, 'f' * 40))
        (self.wt / 'tracked.txt').write_text('fixed implementation\n')
        self.assertIsNone(breaker.merge_refusal(self.root, 'demo', self.wt, base))
        (self.wt / 'tracked.txt').write_text('implemented\n')
        self.assertTrue(breaker.merge_refusal(self.root, 'demo', self.wt, base).startswith('no_implementation'))
        for text in ('{not json', '[]', '{"verdict": "no_implementation", "base_commit": 3}'):
            (self.root / '.workspace/breaker/demo/report.json').write_text(text)
            self.assertIsNone(breaker.merge_refusal(self.root, 'demo', self.wt, base))

    def test_corrected_diff_after_no_implementation_report_is_merged(self):
        self.prepare({}, allowed=['tracked.txt', 'tests/test_project.py'])
        self.add_project_test(PASSING)
        self.assertEqual(self.run_breaker().returncode, 0)
        self.assertEqual(self.report()['verdict'], 'no_implementation')
        self.assertNotEqual(self.merge().returncode, 0)
        self.add_project_test(READS_IMPLEMENTATION)
        merge = self.merge()
        self.assertEqual(merge.returncode, 0, merge.stderr)
        self.assertEqual(self.task_state()['status'], 'merged')

    def test_hidden_test_is_the_discriminant(self):
        self.prepare({})
        self.add_hidden_test(HIDDEN_READS_IMPLEMENTATION)
        before = self.snapshot()
        result = self.run_breaker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('no_implementation', result.stdout)
        report = self.report()
        self.assertEqual((report['verdict'], report['discriminant']), ('sans_faille', dict(source='acceptance', test='hidden.py')))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.wt / 'hidden.py').exists())
        self.assertIsNone(breaker.merge_refusal(self.root, 'demo', self.wt, report['base_commit']))
        merge = self.merge()
        self.assertEqual(merge.returncode, 0, merge.stderr)

    def test_hidden_test_passing_on_base_is_not_a_discriminant(self):
        self.prepare({})
        self.add_hidden_test('raise SystemExit(0)\n')
        self.assertEqual(self.run_breaker().returncode, 0)
        self.assertEqual(self.report()['verdict'], 'no_implementation')

    def test_touched_project_test_is_the_discriminant(self):
        self.prepare({})
        self.add_project_test(READS_IMPLEMENTATION)
        before = self.snapshot()
        self.assertEqual(self.run_breaker().returncode, 0)
        report = self.report()
        self.assertEqual((report['verdict'], report['discriminant']), ('sans_faille', dict(source='projet', test='tests/test_project.py')))
        self.assertEqual(self.snapshot(), before)

    def test_breaker_candidate_is_the_discriminant_only_if_it_fails_on_base(self):
        self.prepare({'test_a_pass.py': PASSING, 'test_b_impl.py': READS_IMPLEMENTATION})
        before = self.snapshot()
        result = self.run_breaker()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report()
        self.assertEqual(report['retained'], [])
        self.assertEqual(report['verdict'], 'sans_faille')
        self.assertEqual(report['discriminant'], dict(source='casseur', test='__breaker__/test_b_impl.py'))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.wt / '__breaker__').exists())

    def test_candidate_passing_everywhere_is_not_a_discriminant(self):
        self.prepare({'test_pass.py': PASSING})
        self.assertEqual(self.run_breaker().returncode, 0)
        self.assertEqual((self.report()['verdict'], self.report()['discriminant']), ('no_implementation', None))

    def test_retained_flaw_is_reported_without_search(self):
        self.prepare({'test_bug.py': FAILING})
        self.add_project_test(READS_IMPLEMENTATION)
        result = self.run_breaker()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report()
        self.assertEqual((report['verdict'], report['discriminant']), ('failles', None))
        self.assertEqual(len(report['retained']), 1)
        self.assertNotIn('no_implementation', result.stdout)
        runs = [c for c in self.get_docker_calls() if c[0] == 'exec' and 'python3' in c]
        self.assertEqual(len(runs), 2)

    def test_agent_none_judges_hidden_and_project_tests_without_any_agent(self):
        self.prepare({'test_bug.py': FAILING})
        self.add_project_test(READS_IMPLEMENTATION)
        self.original.write_text(self.original.read_text().replace('verify: true', 'verify: false'))
        result = self.run_none()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report()
        self.assertEqual(report['retained'] + report['discarded'], [])
        self.assertEqual((report['verdict'], report['discriminant']['source']), ('sans_faille', 'projet'))
        self.assertFalse((self.root / '.workspace/metrics/consumption.jsonl').exists())
        self.assertFalse((self.root / '.workspace/run/delegate/demo').exists())
        self.assertFalse((self.root / '.workspace/breaker/demo/task.md').exists())

    def test_agent_none_without_discriminant_is_no_implementation(self):
        self.prepare({})
        result = self.run_none()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('no_implementation', result.stdout)
        self.assertEqual(self.report()['verdict'], 'no_implementation')

    def test_trial_on_base_restores_added_deleted_and_executable_files(self):
        self.prepare({})
        self.add_project_test(READS_IMPLEMENTATION)
        (self.wt / 'pkg').mkdir()
        (self.wt / 'pkg/tool.sh').write_text('#!/bin/sh\n')
        (self.wt / 'pkg/tool.sh').chmod(0o755)
        (self.wt / 'extra.txt').write_text('new\n')
        self.git_wt('add', 'extra.txt')
        (self.wt / 'README.md').write_text('doc\n')
        self.git_wt('commit', '-qm', 'more')
        (self.wt / 'extra.txt').unlink()
        (self.wt / 'tracked.txt').write_text('implemented\n')
        before = self.snapshot()
        self.assertEqual(self.run_none().returncode, 0)
        self.assertEqual(self.report()['verdict'], 'sans_faille')
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.wt / 'extra.txt').exists())
        self.assertTrue((self.wt / 'pkg/tool.sh').stat().st_mode & 0o100)

    def test_production_written_by_a_base_trial_is_refused_and_restored(self):
        self.prepare({})
        self.add_project_test(CORRUPTS_PRODUCTION)
        result = self.run_breaker()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.wt / 'tracked.txt').read_text(), 'implemented\n')
        self.assertIn('error', self.report())
        self.assertNotIn('verdict', self.report())

    def test_hidden_test_judges_a_deliverable_filed_under_tests(self):
        self.prepare({})
        (self.wt / 'tracked.txt').write_text('before\n')
        (self.wt / 'tests').mkdir(exist_ok=True)
        (self.wt / 'tests/data.txt').write_text('deliverable\n')
        self.git_wt('add', 'tests/data.txt')
        self.add_hidden_test(HIDDEN_READS_DELIVERABLE)
        before_files, _, before_status = self.snapshot()
        before_stage = subprocess.check_output(['git', '-C', str(self.wt), 'ls-files', '-s'], text=True)
        result = self.run_none()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report()
        self.assertEqual((report['verdict'], report['discriminant']), ('sans_faille', dict(source='acceptance', test='hidden.py')))
        # The index bytes can change through Git's stat refresh; compare its staged entries instead.
        after_files, _, after_status = self.snapshot()
        self.assertEqual((after_files, after_status), (before_files, before_status))
        self.assertEqual(subprocess.check_output(['git', '-C', str(self.wt), 'ls-files', '-s'], text=True), before_stage)
        self.assertEqual((self.wt / 'tests/data.txt').read_text(), 'deliverable\n')

    def test_wrong_deliverable_filed_under_tests_is_no_implementation(self):
        self.prepare({})
        (self.wt / 'tracked.txt').write_text('before\n')
        (self.wt / 'tests').mkdir(exist_ok=True)
        (self.wt / 'tests/data.txt').write_text('wrong\n')
        self.git_wt('add', 'tests/data.txt')
        self.add_hidden_test(HIDDEN_READS_DELIVERABLE)
        result = self.run_none()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.report()['verdict'], self.report()['discriminant']), ('no_implementation', None))
        self.assertEqual((self.wt / 'tests/data.txt').read_text(), 'wrong\n')
