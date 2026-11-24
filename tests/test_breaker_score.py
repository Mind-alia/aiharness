import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CAMPAIGN = Path(__file__).resolve().parents[1] / 'tools/campaign'
sys.path.insert(0, str(CAMPAIGN))
import breaker_score

IDENTITY = dict(
    GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.org', GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.org'
)
FICHE = '# Fiche\nid: t1\nbody: id: not me\n'
DIFF = 'diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-x = 1\n+x = %s\n'
BROKEN_DIFF = DIFF.replace('-x = 1', '-x = 99') % 3
MANIFEST_ETALONS = [
    ('bon-a', 'accept', 'bon'),
    ('mauvais-b', 'reject', 'correctif partiel'),
    ('mauvais-c', 'reject', 'test qui triche'),
    ('mauvais-hors', 'reject', 'hors périmètre'),
]
# Faux task-workspace.sh : journal des appels, état et worktree Git comme le vrai circuit.
FAKE_TASK_WORKSPACE = '''import json, os, subprocess, sys
from pathlib import Path
root = Path.cwd()
config = json.loads((root / '.workspace/fake.json').read_text())
args = sys.argv[1:]
def log(*parts):
    with open(root / '.workspace/calls.log', 'a') as handle:
        handle.write(json.dumps([*parts, 'WORKSPACE_AGENT_ROOT' in os.environ]) + '\\n')
if args[0] == 'prepare':
    for line in (root / args[1]).read_text().splitlines():
        if line.startswith('id:'):
            ident = line.split(': ')[1]
            break
    log(*args)
    state = root / '.workspace/orchestration/tasks' / ident / 'state.json'
    if ident in config.get('prepare_fail', []) or (state.exists() and '--retry' not in args):
        sys.exit(1)
    work = root / '.workspace/worktrees' / ident
    subprocess.run(['git', 'worktree', 'remove', '--force', str(work)], cwd=root, capture_output=True)
    subprocess.run(['git', 'worktree', 'add', '-B', 'fake/' + ident, str(work), args[args.index('--base') + 1]],
                   cwd=root, check=True, capture_output=True)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({'worktree': '.workspace/worktrees/' + ident, 'status': 'ready'}))
else:
    log(*args)
    if args[0] == 'close':
        closes = [json.loads(line) for line in (root / '.workspace/calls.log').read_text().splitlines()]
        count = sum(1 for call in closes if call[:2] == ['close', args[1]])
        sys.exit(1 if count <= config.get('close_fail', {}).get(args[1], 0) else 0)
'''
FAKE_SANDBOX = '''import json, sys
from pathlib import Path
with open(Path.cwd() / '.workspace/calls.log', 'a') as handle:
    handle.write(json.dumps(['sandbox', *sys.argv[1:]]) + '\\n')
'''
FAKE_BREAKER = '''import json, sys
from pathlib import Path
root = Path.cwd()
config = json.loads((root / '.workspace/fake.json').read_text())
ident = sys.argv[1]
with open(root / '.workspace/calls.log', 'a') as handle:
    handle.write(json.dumps(['breaker', *sys.argv[1:]]) + '\\n')
entry = config.get('breaker', {}).get(ident, {})
if entry.get('exit'):
    sys.exit(entry['exit'])
target = root / '.workspace/breaker' / ident
target.mkdir(parents=True)
if entry.get('raw') is not None:
    (target / 'report.json').write_text(entry['raw'])
else:
    report = {'retained': [{'test': 't%d' % n} for n in range(entry.get('retained', 0))],
              'discarded': [{'test': 'd', 'reason': 'x'}] * entry.get('discarded', 1)}
    (target / 'report.json').write_text(json.dumps(report))
'''


def git(cwd, *args):
    subprocess.run(['git', *args], cwd=str(cwd), check=True, capture_output=True, env=dict(os.environ, **IDENTITY))


class BreakerScoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.origin, self.clone, self.out = base / 'origin', base / 'clone', base / 'out'
        (self.origin / '.workspace/orchestration/tasks/t1').mkdir(parents=True)
        (self.origin / '.workspace/tasks').mkdir()
        (self.origin / '.workspace/tasks/t1.md').write_text(FICHE)
        (self.origin / '.workspace/orchestration/tasks/t1/state.json').write_text(json.dumps({'source_task': '.workspace/tasks/t1.md'}))
        (self.origin / 'tests').mkdir()
        (base / 'ev.py').write_text('pass\n')
        (base / 'config.json').write_text(
            json.dumps(
                {
                    'project': 'origin',
                    'tasks': [{'id': 't1', 'evaluation_files': ['ev.py'], 'evaluation_target': 'tests', 'evaluation_command': 'true'}],
                }
            )
        )
        for name, text in (('good', DIFF % 2), ('bad', DIFF % 3), ('broken', BROKEN_DIFF)):
            (base / (name + '.diff')).write_text(text)
        (self.clone / 'scripts').mkdir(parents=True)
        (self.clone / 'calc.py').write_text('x = 1\n')
        (self.clone / '.gitignore').write_text('.workspace/\n')
        for name, code in (('task-workspace', FAKE_TASK_WORKSPACE), ('breaker', FAKE_BREAKER), ('sandbox', FAKE_SANDBOX)):
            (self.clone / ('scripts/fake_%s.py' % name)).write_text(code)
            (self.clone / ('scripts/%s.sh' % name)).write_text('exec python3 "$(dirname "$0")/fake_%s.py" "$@"\n' % name)
        git(self.clone, 'init', '-q')
        git(self.clone, 'add', '.')
        git(self.clone, 'commit', '-q', '-m', 'init')
        self.set_fake({})
        env = mock.patch.dict(os.environ, dict(IDENTITY, WORKSPACE_AGENT_ROOT='/somewhere'))
        env.start()
        self.addCleanup(env.stop)
        self.etalons = [
            dict(id=i, diff='bad.diff' if e == 'reject' else 'good.diff', base='HEAD', expected=e, kind=k, source='s')
            for i, e, k in MANIFEST_ETALONS
        ]
        self.write_manifest()

    def write_manifest(self):
        path = Path(self.temp.name) / 'manifest.json'
        path.write_text(json.dumps({'config': 'config.json', 'task': 't1', 'etalons': self.etalons}))
        self.manifest = str(path)

    def set_fake(self, config):
        (self.clone / '.workspace').mkdir(exist_ok=True)
        (self.clone / '.workspace/fake.json').write_text(json.dumps(config))

    def breaker(self, **entries):
        self.set_fake(dict(prepare_fail=entries.pop('prepare_fail', []), close_fail=entries.pop('close_fail', {}), breaker=entries))

    def run_score(self, *extra, agents=('claude-haiku', 'claude-sonnet')):
        args = [self.manifest, '--project', str(self.clone), '--out', str(self.out)]
        for agent in agents:
            args += ['--agent', agent]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            return breaker_score.main(args + list(extra)), stderr.getvalue()

    def calls(self):
        path = self.clone / '.workspace/calls.log'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def rows(self):
        return [json.loads(line) for line in (self.out / 'results.jsonl').read_text().splitlines()]

    def kinds(self, name):
        return [call for call in self.calls() if call[0] == name]

    def standard(self):
        self.breaker(
            **{
                'mauvais-b-cas-haiku': dict(retained=2),
                'mauvais-c-cas-haiku': dict(retained=1),
                'mauvais-b-cas-sonnet': dict(retained=1),
                'mauvais-c-cas-sonnet': dict(retained=3),
                'bon-a-cas-sonnet': dict(retained=1),
            }
        )

    def test_exact_and_lying_breaker_are_scored(self):
        self.standard()
        code, _ = self.run_score()
        self.assertEqual(code, 0)
        report = (self.out / 'casseur.md').read_text()
        self.assertTrue(report.startswith('## Casseur\n\n- Date : '))
        self.assertIn('| Exécutant du casseur | Mauvais attrapés | Bons sans faux test | Erreurs |', report)
        self.assertIn('| claude-haiku | 2/2 | 1/1 | 0 |', report)
        self.assertIn('| claude-sonnet | 2/2 | 0/1 | 0 |', report)
        self.assertIn('| Étalon | Nature | Attendu | claude-haiku | claude-sonnet |', report)
        self.assertIn('| bon-a | bon | accept | propre (0) | faux test (1) |', report)
        self.assertIn('| mauvais-c | test qui triche | reject | attrapé (1) | attrapé (3) |', report)
        self.assertNotIn('mauvais-hors', report)

    def test_verdict_of_covers_every_case(self):
        none = {'retained': [], 'discarded': [], 'verdict': 'no_implementation'}
        clean = {'retained': [], 'discarded': [], 'verdict': 'sans_faille'}
        found = {'retained': [{'test': 't'}], 'discarded': [], 'verdict': 'failles'}
        legacy = {'retained': [], 'discarded': []}
        self.assertEqual(
            [breaker_score.verdict_of('reject', r) for r in (found, none, clean, legacy)], ['attrapé', 'attrapé', 'manqué', 'manqué']
        )
        self.assertEqual(
            [breaker_score.verdict_of('accept', r) for r in (found, none, clean, legacy)], ['faux test', 'faux rejet', 'propre', 'propre']
        )
        self.assertEqual(breaker_score.verdict_of('accept', dict(none, retained=[{'test': 't'}])), 'faux test')

    def test_no_implementation_report_is_caught_for_bad_and_false_rejection_for_good(self):
        raw = json.dumps({'retained': [], 'discarded': [], 'verdict': 'no_implementation', 'discriminant': None})
        self.breaker(**{'bon-a-cas-haiku': dict(raw=raw), 'mauvais-b-cas-haiku': dict(raw=raw), 'mauvais-c-cas-haiku': dict(raw=raw)})
        code, _ = self.run_score(agents=('claude-haiku',))
        self.assertEqual(code, 0)
        self.assertEqual([r['verdict'] for r in self.rows()], ['faux rejet', 'attrapé', 'attrapé'])
        report = (self.out / 'casseur.md').read_text()
        self.assertIn('| claude-haiku | 2/2 | 0/1 | 0 |', report)
        self.assertIn('| bon-a | bon | accept | faux rejet (0) |', report)

    def test_rows_and_out_of_scope_etalon_ignored(self):
        self.standard()
        self.run_score()
        rows = self.rows()
        self.assertEqual(
            [(r['etalon'], r['agent']) for r in rows],
            [(e, a) for e in ('bon-a', 'mauvais-b', 'mauvais-c') for a in ('claude-haiku', 'claude-sonnet')],
        )
        self.assertEqual(
            rows[1],
            dict(
                etalon='bon-a',
                task='t1',
                expected='accept',
                kind='bon',
                agent='claude-sonnet',
                retained=1,
                discarded=1,
                error='',
                verdict='faux test',
            ),
        )
        self.assertEqual(rows[2]['verdict'], 'attrapé')
        self.assertFalse(any('mauvais-hors' in json.dumps(call) for call in self.calls()))

    def test_original_task_copied_with_new_id_and_commands(self):
        self.standard()
        self.run_score(agents=('claude-haiku',))
        written = (self.clone / '.workspace/tasks/bon-a-cas-haiku.md').read_text()
        self.assertEqual(written, FICHE.replace('id: t1', 'id: bon-a-cas-haiku'))
        self.assertEqual(self.kinds('prepare')[0], ['prepare', '.workspace/tasks/bon-a-cas-haiku.md', '--base', 'HEAD', False])
        self.assertEqual(self.kinds('breaker')[0], ['breaker', 'bon-a-cas-haiku', '--agent', 'claude-haiku'])
        self.assertEqual(self.kinds('close')[0], ['close', 'bon-a-cas-haiku', '--abandoned', '--force', False])
        self.assertEqual((len(self.kinds('prepare')), len(self.kinds('close'))), (3, 3))

    def test_clone_stays_clean_and_short_names(self):
        self.standard()
        self.run_score('--only', 'bon-a', agents=('claude-opus', 'codex', 'claude-opus'))
        status = subprocess.run(['git', 'status', '--porcelain'], cwd=str(self.clone), capture_output=True, text=True)
        self.assertEqual(status.stdout, '')
        self.assertEqual([c[1] for c in self.kinds('breaker')], ['bon-a-cas-opus', 'bon-a-cas-codex'])

    def test_only_filters_in_manifest_order(self):
        self.standard()
        self.run_score('--only', 'mauvais-c,bon-a')
        self.assertEqual({r['etalon'] for r in self.rows()}, {'bon-a', 'mauvais-c'})
        self.assertNotIn('mauvais-b', (self.out / 'casseur.md').read_text())

    def test_broken_breaker_is_error_and_counts_in_denominators(self):
        self.standard()
        self.breaker(**{'mauvais-b-cas-haiku': dict(exit=3), 'mauvais-c-cas-haiku': dict(retained=1)})
        code, _ = self.run_score()
        self.assertEqual(code, 0)
        failed = [r for r in self.rows() if r['verdict'] == 'erreur']
        self.assertEqual(len(failed), 1)
        self.assertIsNone(failed[0]['retained'])
        self.assertIsNone(failed[0]['discarded'])
        self.assertIn('casseur', failed[0]['error'])
        self.assertIn('| claude-haiku | 1/2 | 1/1 | 1 |', (self.out / 'casseur.md').read_text())
        self.assertIn('| mauvais-b | correctif partiel | reject | erreur |', (self.out / 'casseur.md').read_text())
        self.assertEqual(len(self.kinds('close')), 6)

    def test_rerun_after_complete_pass_launches_nothing(self):
        self.standard()
        self.run_score()
        before = self.calls()
        first = (self.out / 'results.jsonl').read_text()
        self.assertEqual(self.run_score()[0], 0)
        self.assertEqual(self.calls(), before)
        self.assertEqual((self.out / 'results.jsonl').read_text(), first)

    def test_rerun_redoes_only_error_rows(self):
        self.breaker(**{'mauvais-b-cas-sonnet': dict(exit=1)})
        self.run_score()
        self.assertEqual(len(self.kinds('prepare')), 6)
        self.standard()
        self.run_score()
        prepares = self.kinds('prepare')
        self.assertEqual([c[1] for c in prepares[6:]], ['.workspace/tasks/mauvais-b-cas-sonnet.md'])
        self.assertIn('--retry', prepares[6])
        self.assertEqual(sum(r['verdict'] == 'erreur' for r in self.rows()), 0)
        self.assertEqual(len(self.rows()), 6)

    def test_prepare_failure_is_error_without_close(self):
        self.breaker(prepare_fail=['bon-a-cas-haiku'])
        self.run_score(agents=('claude-haiku',))
        first = self.rows()[0]
        self.assertEqual((first['verdict'], first['retained']), ('erreur', None))
        self.assertIn('prepare', first['error'])
        self.assertNotIn('bon-a-cas-haiku', [c[1] for c in self.kinds('close')])
        self.assertEqual(len(self.kinds('close')), 2)

    def test_close_failing_once_is_retried_after_sandbox_stop(self):
        self.breaker(close_fail={'bon-a-cas-haiku': 1})
        self.assertEqual(self.run_score(agents=('claude-haiku',))[0], 0)
        first = self.rows()[0]
        self.assertEqual((first['verdict'], first['error'], first['retained']), ('propre', '', 0))
        closes = [c[1] for c in self.kinds('close')]
        self.assertEqual(closes.count('bon-a-cas-haiku'), 2)
        self.assertEqual(self.kinds('sandbox'), [['sandbox', 'stop', 'bon-a-cas-haiku']])
        self.assertEqual(len(self.kinds('close')), 4)

    def test_close_failing_twice_is_error_and_run_goes_on(self):
        self.breaker(
            close_fail={'bon-a-cas-haiku': 5}, **{'mauvais-b-cas-haiku': dict(retained=2), 'mauvais-c-cas-haiku': dict(retained=1)}
        )
        self.assertEqual(self.run_score(agents=('claude-haiku',))[0], 0)
        rows = self.rows()
        self.assertEqual([r['etalon'] for r in rows], ['bon-a', 'mauvais-b', 'mauvais-c'])
        self.assertEqual((rows[0]['verdict'], rows[0]['retained'], rows[0]['discarded']), ('erreur', None, None))
        self.assertIn('close', rows[0]['error'])
        self.assertEqual([r['verdict'] for r in rows[1:]], ['attrapé', 'attrapé'])
        self.assertEqual([c[1] for c in self.kinds('close')].count('bon-a-cas-haiku'), 2)
        self.assertEqual(self.kinds('sandbox'), [['sandbox', 'stop', 'bon-a-cas-haiku']])

    def test_close_failure_is_appended_to_existing_error(self):
        self.breaker(close_fail={'bon-a-cas-haiku': 5}, **{'bon-a-cas-haiku': dict(exit=3)})
        self.run_score(agents=('claude-haiku',))
        first = self.rows()[0]
        self.assertEqual((first['verdict'], first['retained']), ('erreur', None))
        self.assertEqual(first['error'], 'casseur (code 3) ; close (code 1)')

    def test_successful_close_launches_no_sandbox_stop(self):
        self.standard()
        self.run_score()
        self.assertEqual(self.kinds('sandbox'), [])
        self.assertEqual(len(self.kinds('close')), 6)

    def test_diff_failure_is_error_and_task_still_closed(self):
        (Path(self.temp.name) / 'bad.diff').write_text(BROKEN_DIFF)
        self.run_score(agents=('claude-haiku',))
        row = [r for r in self.rows() if r['etalon'] == 'mauvais-b'][0]
        self.assertEqual(row['verdict'], 'erreur')
        self.assertIn('diff', row['error'])
        self.assertEqual(len(self.kinds('breaker')), 1)
        self.assertEqual(len(self.kinds('close')), 3)

    def test_unreadable_report_is_error(self):
        self.breaker(**{'bon-a-cas-haiku': dict(raw='pas du json'), 'mauvais-b-cas-haiku': dict(raw='{"retained": []}')})
        self.run_score(agents=('claude-haiku',))
        rows = self.rows()
        self.assertEqual([r['verdict'] for r in rows[:2]], ['erreur', 'erreur'])
        self.assertIn('rapport', rows[0]['error'])
        self.assertEqual(len(self.kinds('close')), 3)

    def test_missing_original_task_is_error_row(self):
        (self.origin / '.workspace/orchestration/tasks/t1/state.json').unlink()
        self.assertEqual(self.run_score(agents=('claude-haiku',))[0], 0)
        self.assertEqual({r['verdict'] for r in self.rows()}, {'erreur'})
        self.assertIn('fiche', self.rows()[0]['error'])
        self.assertEqual(self.calls(), [])

    def test_usage_errors_run_nothing(self):
        (self.clone / 'dirty.txt').write_text('x')
        self.assertEqual(self.run_score()[0], 64)
        (self.clone / 'dirty.txt').unlink()
        self.assertEqual(self.run_score('--bogus')[0], 64)
        self.assertEqual(self.run_score(agents=())[0], 64)
        self.assertEqual(self.run_score('--only', ',')[0], 64)
        self.assertEqual(self.run_score(agents=('../evil',))[0], 64)
        Path(self.manifest).write_text('{"etalons": []}')
        self.assertEqual(self.run_score()[0], 64)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(breaker_score.main([]), 64)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.calls(), [])

    def test_non_git_clone_is_refused(self):
        plain = Path(self.temp.name) / 'plain'
        plain.mkdir()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = breaker_score.main([self.manifest, '--project', str(plain), '--agent', 'codex', '--out', str(self.out)])
        self.assertEqual(code, 64)

    def test_write_appends_section_when_absent(self):
        self.standard()
        target = Path(self.temp.name) / 'rapport.md'
        target.write_text('# Rapport\n\ntexte')
        self.assertEqual(self.run_score('--write', str(target))[0], 0)
        text = target.read_text()
        self.assertTrue(text.startswith('# Rapport\n\ntexte\n\n## Casseur\n'))
        self.assertEqual(text[len('# Rapport\n\ntexte\n\n') :], (self.out / 'casseur.md').read_text())

    def test_write_replaces_existing_section_only(self):
        self.standard()
        target = Path(self.temp.name) / 'rapport.md'
        target.write_text('# Rapport\n\n## Avant\n\na\n\n## Casseur\n\nvieux\n\n## Après\n\nb\n')
        self.run_score('--write', str(target))
        text = target.read_text()
        self.assertTrue(text.startswith('# Rapport\n\n## Avant\n\na\n\n## Casseur\n\n- Date : '))
        self.assertTrue(text.endswith('\n\n## Après\n\nb\n'))
        self.assertNotIn('vieux', text)
        self.assertEqual(text.count('## Casseur'), 1)
        self.run_score('--write', str(target))
        self.assertEqual(target.read_text(), text)

    def test_merge_section_edge_cases(self):
        new = '## Casseur\n\nnouveau\n'
        self.assertEqual(breaker_score.merge_section('', new), new)
        self.assertEqual(breaker_score.merge_section('a\n', new), 'a\n\n' + new)
        self.assertEqual(breaker_score.merge_section('a\n\n', new), 'a\n\n' + new)
        self.assertEqual(breaker_score.merge_section('## Casseur\nvieux\n', new), new)
        self.assertEqual(breaker_score.merge_section('x\n## Casseur\nv\n## Suite\ny\n', new), 'x\n' + new + '\n## Suite\ny\n')


if __name__ == '__main__':
    unittest.main()
