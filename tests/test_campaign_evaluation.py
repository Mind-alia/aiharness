import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/campaign/evaluation.py'
sys.path.insert(0, str(SCRIPT.parent))
from evaluation import score

EVALUATION = '''import unittest
import calc


class Evaluation(unittest.TestCase):
    def test_add(self):
        self.assertEqual(calc.add(2, 3), 5)

    def test_sub(self):
        self.assertEqual(calc.sub(5, 3), 2)
'''


class EvaluateDiffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'project'
        self.config_dir = self.base / 'campaign'
        self.scratch = self.base / 'scratch'
        for directory in (self.project, self.config_dir / 'evaluation', self.scratch):
            directory.mkdir(parents=True)
        self.git('init', '-b', 'initial')
        self.git('config', 'user.email', 'test@example.org')
        self.git('config', 'user.name', 'Test')
        (self.project / 'calc.py').write_text('def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a + b\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        self.commit = self.git('rev-parse', 'HEAD')
        (self.config_dir / 'evaluation/test_eval.py').write_text(EVALUATION)
        self.task = dict(
            id='calc',
            request='demande.md',
            evaluation_files=['evaluation/test_eval.py'],
            evaluation_target='eval',
            evaluation_command=['python3', '-m', 'unittest', 'discover', '-s', 'eval'],
        )
        (self.config_dir / 'demande.md').write_text('Corriger calc.py.')
        self.config = dict(project='../project', start_commit=self.commit, arms=['opus-seul'], tasks=[self.task])
        self.write_config()

    def git(self, *args):
        done = subprocess.run(['git'] + list(args), cwd=str(self.project), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(done.returncode, 0, done.stdout)
        return done.stdout.strip()

    def write_config(self):
        (self.config_dir / 'config.json').write_text(json.dumps(self.config))

    def make_diff(self, add, sub):
        (self.project / 'calc.py').write_text('def add(a, b):\n    return a %s b\n\n\ndef sub(a, b):\n    return a %s b\n' % (add, sub))
        diff = self.git('diff') + '\n'
        self.git('checkout', '--', '.')
        return self.write_diff(diff)

    def write_diff(self, text):
        path = self.scratch / 'diff.txt'
        path.write_text(text)
        return path

    def snapshot(self):
        files = sorted(str(p.relative_to(self.project)) for p in self.project.rglob('*') if '.git' not in p.parts)
        return (
            self.git('rev-parse', 'HEAD'),
            self.git('branch', '--list', '--all'),
            self.git('worktree', 'list'),
            self.git('status', '--porcelain'),
            self.git('stash', 'list'),
            files,
            (self.project / 'calc.py').read_text(),
        )

    def evaluate(self, diff, task='calc', base=None, extra=None):
        before = self.snapshot()
        temp = self.base / 'temp'
        temp.mkdir(exist_ok=True)
        env = dict(os.environ, TMPDIR=str(temp))
        args = [
            sys.executable,
            str(SCRIPT),
            str(self.config_dir / 'config.json'),
            '--task',
            task,
            '--diff',
            str(diff),
            '--base',
            base or self.commit,
        ] + (extra or [])
        done = subprocess.run(args, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(self.snapshot(), before, 'le projet d’origine a changé')
        self.assertEqual(list(temp.iterdir()), [], 'le clone n’a pas été supprimé')
        return done

    def last_line(self, done):
        return done.stdout.splitlines()[-1]

    def test_correct_diff_scores_everything(self):
        done = self.evaluate(self.make_diff('+', '-'))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.last_line(done), '2/2')
        self.assertIn('Ran 2 tests', done.stderr)

    def test_partial_diff_scores_partially(self):
        done = self.evaluate(self.make_diff('+', '+'))
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.last_line(done), '1/2')

    def test_corrupt_diff_is_refused(self):
        diff = self.make_diff('+', '-')
        for text in (diff.read_text().replace('return a - b', 'return a * b', 1), diff.read_text()[:-40], '', '\n', 'rien à voir\n'):
            done = self.evaluate(self.write_diff(text))
            self.assertEqual(done.returncode, 2, text)
            self.assertEqual(self.last_line(done), '?/?')

    def test_masked_diff_is_refused(self):
        diff = self.make_diff('+', '-')
        done = self.evaluate(self.write_diff(diff.read_text().replace('+    return a + b', '+    return ***')))
        self.assertEqual(done.returncode, 2)

    def test_unknown_task_base_and_usage(self):
        diff = self.make_diff('+', '-')
        self.assertEqual(self.evaluate(diff, task='absente').returncode, 64)
        self.assertEqual(self.evaluate(diff, base='0' * 40).returncode, 64)
        self.assertEqual(self.evaluate(diff, base='absente').returncode, 64)
        self.assertEqual(self.evaluate(self.scratch / 'absent.txt').returncode, 64)
        self.assertEqual(subprocess.run([sys.executable, str(SCRIPT)], stderr=subprocess.PIPE, stdout=subprocess.PIPE).returncode, 64)
        self.assertEqual(self.evaluate(diff, extra=['--debug']).returncode, 64)
        (self.config_dir / 'config.json').write_text('{')
        self.assertEqual(self.evaluate(diff).returncode, 64)

    def test_base_other_than_head_and_branch_name(self):
        diff = self.make_diff('+', '-')
        (self.project / 'autre.txt').write_text('autre')
        self.git('add', '.')
        self.git('commit', '-qm', 'later')
        self.assertEqual(self.last_line(self.evaluate(diff, base=self.commit)), '2/2')
        self.assertEqual(self.last_line(self.evaluate(diff, base='HEAD~1')), '2/2')
        self.git('branch', 'depart', self.commit)
        self.assertEqual(self.last_line(self.evaluate(diff, base='depart')), '2/2')

    def test_dirty_project_is_ignored_and_untouched(self):
        diff = self.make_diff('+', '-')
        (self.project / 'calc.py').write_text('cassé\n')
        (self.project / 'neuf.txt').write_text('non suivi')
        done = self.evaluate(diff)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_unknown_score_and_failing_command(self):
        diff = self.make_diff('+', '-')
        self.task['evaluation_command'] = 'echo bonjour'
        self.write_config()
        done = self.evaluate(diff)
        self.assertEqual((done.returncode, self.last_line(done)), (1, '?/?'))
        self.assertIn('bonjour', done.stderr)

    def test_failing_exit_code_is_not_success(self):
        diff = self.make_diff('+', '-')
        self.task['evaluation_command'] = 'echo \'{"passed": 3, "total": 3}\'; exit 3'
        self.write_config()
        done = self.evaluate(diff)
        self.assertEqual((done.returncode, self.last_line(done)), (1, '3/3'))

    def test_zero_total_is_not_success(self):
        diff = self.make_diff('+', '-')
        self.task['evaluation_command'] = ['bash', '-c', 'echo \'{"passed": 0, "total": 0}\'']
        self.write_config()
        done = self.evaluate(diff)
        self.assertEqual((done.returncode, self.last_line(done)), (1, '0/0'))

    def test_stderr_is_merged_into_output(self):
        diff = self.make_diff('+', '-')
        self.task['evaluation_command'] = 'echo erreur >&2; echo \'{"passed": 1, "total": 1}\''
        self.write_config()
        done = self.evaluate(diff)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), '1/1')
        self.assertIn('erreur', done.stderr)

    def test_setup_runs_after_diff_in_the_clone(self):
        diff = self.make_diff('+', '-')
        self.config['setup'] = 'grep -q "a + b" calc.py && echo prêt > marker.txt'
        self.task['evaluation_command'] = 'test -f marker.txt && echo \'{"passed": 1, "total": 1}\''
        self.write_config()
        done = self.evaluate(diff)
        self.assertEqual((done.returncode, self.last_line(done)), (0, '1/1'), done.stderr)
        self.config['setup'] = ['python3', '-c', 'open("marker.txt", "w").write("x")']
        self.write_config()
        self.assertEqual(self.evaluate(diff).returncode, 0)

    def test_failing_setup(self):
        diff = self.make_diff('+', '-')
        self.config['setup'] = 'echo installation impossible; exit 7'
        self.write_config()
        done = self.evaluate(diff)
        self.assertEqual((done.returncode, self.last_line(done)), (1, '?/?'))
        self.assertIn('installation impossible', done.stderr)

    def test_evaluation_file_never_overwrites(self):
        (self.project / 'eval').mkdir()
        (self.project / 'eval/test_eval.py').write_text('# déjà là\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'eval')
        self.commit = self.git('rev-parse', 'HEAD')
        done = self.evaluate(self.make_diff('+', '-'))
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.last_line(done), '?/?')
        self.assertIn('écraser', done.stderr)

    def test_diff_creating_the_evaluation_file_is_refused(self):
        diff = (
            'diff --git a/eval/test_eval.py b/eval/test_eval.py\nnew file mode 100644\n--- /dev/null\n'
            '+++ b/eval/test_eval.py\n@@ -0,0 +1 @@\n+# triche\n'
        )
        done = self.evaluate(self.write_diff(diff))
        self.assertEqual(done.returncode, 1)
        self.assertIn('écraser', done.stderr)

    def test_reserved_target_is_a_configuration_error(self):
        self.task['evaluation_target'] = 'scripts'
        self.write_config()
        self.assertEqual(self.evaluate(self.make_diff('+', '-')).returncode, 64)

    def test_symlinked_target_cannot_escape_the_clone(self):
        outside = self.base / 'dehors'
        outside.mkdir()
        diff = (
            'diff --git a/eval b/eval\nnew file mode 120000\n--- /dev/null\n+++ b/eval\n@@ -0,0 +1 @@\n+%s\n'
            '\\ No newline at end of file\n' % outside
        )
        done = self.evaluate(self.write_diff(diff))
        self.assertEqual(done.returncode, 1)
        self.assertEqual(list(outside.iterdir()), [])


class ScoreTests(unittest.TestCase):
    def test_nested_vitest_tap_counts_only_leaves(self):
        leaves = ['    %s %d - test %d' % ('not ok' if n == 5 else 'ok', n, n) for n in range(1, 13)]
        leaves.insert(5, '      ---\n      error: attendu 2')
        output = '\n'.join(['TAP version 13', 'ok 1 - tests/calc.test.ts {', '  ok 1 - calc {'] + leaves + ['  }', '}', '1..1', ''])
        self.assertEqual(score(output, 1), (11, 12))

    def test_flat_tap_is_unchanged(self):
        self.assertEqual(score('ok 1 - a\nnot ok 2 - b\nok 3 - c # SKIP\n', 1), (2, 3))

    def test_group_lines_alone_are_unknown(self):
        self.assertEqual(score('ok 1 - fichier.test.ts {\n    ok 1 - groupe {\n    }\n}\n', 0), (None, None))


if __name__ == '__main__':
    unittest.main()
