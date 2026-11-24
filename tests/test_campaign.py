import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/campaign/campaign.py'


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()  # /var est un lien vers /private/var sous macOS.
        self.project = self.base / 'project'
        self.project.mkdir()
        self.git('init', '-b', 'initial')
        self.git('config', 'user.email', 'test@example.org')
        self.git('config', 'user.name', 'Test')
        (self.project / '.gitignore').write_text('.workspace/\n')
        (self.project / 'src').mkdir()
        (self.project / 'src/code.txt').write_text('base')
        scripts = self.project / 'scripts'
        scripts.mkdir()
        (scripts / 'gate.sh').write_text('echo "FAIL statusline-binary"; exit 1\n')
        (scripts / 'prepare-task.sh').write_text('''mkdir -p tasks .workspace/metrics
printf '{"provider":"claude","role":"orchestration","tokens":{"input":7,"output":2}}\\n' >> .workspace/metrics/consumption.jsonl
for suffix in 1 2; do
id="$2-$suffix"
printf '%s\\n' "---" "id: $id" "agent: $4" "mode: write" "allowed: [src/**]" "attempts: 3" "---" > "tasks/$id.md"
echo "$id"
done
''')
        # Les bras s'exécutent dans leur worktree : marqueurs et appels vivent dans le projet principal ($MAIN).
        self.write_script(
            'task-workspace',
            '''echo "$*" >> "$MAIN/.workspace/calls"
if [ "$1" = prepare ]; then
id=$(sed -n 's/^id: //p' "$2")
mkdir -p "$WORKSPACE_WORK_DIR/$id" ".workspace/orchestration/tasks/$id"
printf '{"worktree": "%s"}' "$WORKSPACE_WORK_DIR/$id" > ".workspace/orchestration/tasks/$id/state.json"
elif [ "$1" = merge ]; then
printf delivered > src/code.txt
git add src/code.txt
git commit --allow-empty -qm delivered
fi
''',
        )
        self.write_script(
            'delegate',
            '''mkdir -p .workspace/metrics
echo "delegate $*" >> "$MAIN/.workspace/calls"
if [ -f "$MAIN/.workspace/quota" ]; then
printf '{"provider":"codex","role":"execution","quota":{"limit_reached":true}}\\n' >> .workspace/metrics/consumption.jsonl
exit 1
fi
printf '{"provider":"codex","role":"execution","tokens":{"input":11,"output":3}}\\n' >> .workspace/metrics/consumption.jsonl
echo '{"attempts":2,"outcome":"done"}'
''',
        )
        (scripts / 'breaker.sh').write_text('''mkdir -p ".workspace/breaker/$1"
printf '{"retained":[{"test":"bug"}]}' > ".workspace/breaker/$1/report.json"
''')
        # Relais de contrôle : répond OK, sauf si le marqueur codex-down / gemini-down existe.
        for relay in ('codex', 'gemini'):
            self.write_script(
                relay,
                '''echo "%s $*" >> "$MAIN/.workspace/relay-calls"
if [ -f "$MAIN/.workspace/%s-down" ]; then echo "usage limit reached" >&2; exit 2; fi
printf 'OK\\n' > "$3"
'''
                % (relay, relay),
            )
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        self.commit = self.git('rev-parse', 'HEAD')
        (self.base / 'request.md').write_text('Améliorer le code.')
        (self.base / 'hidden.py').write_text('secret evaluation')
        self.config = dict(
            project=str(self.project),
            start_commit=self.commit,
            seed=42,
            repetitions=2,
            arms=['codex-seul', 'systeme-claude-haiku'],
            tasks=[
                dict(
                    id='simple',
                    request='request.md',
                    evaluation_files=['hidden.py'],
                    evaluation_target='eval',
                    evaluation_command="test -f eval/hidden.py && echo '{\"passed\":3,\"total\":4}'",
                )
            ],
        )
        self.path = self.base / 'config.json'

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.project)] + list(args), text=True).strip()

    def write_script(self, name, body):
        """Script factice du projet ; $MAIN désigne le checkout principal, où vivent les marqueurs."""
        (self.project / 'scripts' / (name + '.sh')).write_text(
            'MAIN=%s\nmkdir -p "$MAIN/.workspace"\n%s' % (shlex.quote(str(self.project)), body)
        )

    def worktrees(self):
        listing = self.git('worktree', 'list', '--porcelain').splitlines()
        return [Path(line[len('worktree ') :]) for line in listing if line.startswith('worktree ')]

    def run_campaign(self, *options):
        self.path.write_text(json.dumps(self.config))
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.path), *options],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(
                os.environ,
                WORKSPACE_AGENT_ROOT='incorrect',
                WORKSPACE_WORK_DIR=str(self.base / 'work'),
                WSC_HOME=str(self.base / 'console'),
            ),
        )

    def relay_calls(self):
        path = self.project / '.workspace/relay-calls'
        return path.read_text().splitlines() if path.exists() else []

    def rows(self):
        return [json.loads(x) for x in (self.base / 'results.jsonl').read_text().splitlines()]

    def test_results_resume_and_main_branch_never_left(self):
        result = self.run_campaign()
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual(self.git('branch', '--show-current'), 'initial')
        self.assertEqual(self.git('rev-parse', 'initial'), self.commit)
        for k, row in enumerate(rows):
            self.assertEqual(row['branch'], 'campagne/simple/%s/%d-%d' % (row['arm'], row['repetition'], k))
            self.assertEqual(self.git('rev-parse', row['start_commit']), self.commit)
            self.assertNotEqual(self.git('rev-parse', row['branch']), self.commit)  # livraison commitée sur la branche du bras
        self.assertFalse((self.project / 'eval/hidden.py').exists())
        self.assertEqual(rows[0]['evaluation']['passed'], 3)
        self.assertTrue(rows[0]['gate']['statusline_binary_only'])
        system = next(r for r in rows if r['arm'].startswith('systeme'))
        self.assertEqual(system['breaker_retained'], 2)
        self.assertEqual(system['consumption']['claude']['orchestration']['input'], 7)
        calls = (self.project / '.workspace/calls').read_text()
        self.assertIn('--no-verify-again --no-integration', calls)
        self.assertEqual(self.run_campaign().returncode, 0)
        self.assertEqual(self.rows(), rows)
        self.assertTrue((self.base / 'results.md').exists())

    def test_completed_arms_remove_their_worktree_and_keep_the_branch(self):
        self.assertEqual(self.run_campaign().returncode, 0)
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(row['status'], 'completed')
            self.assertEqual(
                Path(row['arm_worktree']),
                self.project / '.workspace/campaign' / ('camp-simple-%s-%d-%d' % (row['arm'], row['repetition'], rows.index(row))),
            )
            self.assertIs(row['arm_worktree_kept'], False)
            self.assertFalse(Path(row['arm_worktree']).exists())
            self.assertTrue(self.git('rev-parse', '--verify', row['branch']))
        self.assertEqual(self.worktrees(), [self.project])
        self.assertEqual(self.git('stash', 'list'), '')

    def test_main_checkout_keeps_its_branch_and_head_at_every_step(self):
        steps = self.base / 'steps'
        trace = (
            'echo "$(basename "$0") $(git -C "$MAIN" branch --show-current) '
            '$(git -C "$MAIN" rev-parse HEAD) $(pwd -P)" >> %s\n' % shlex.quote(str(steps))
        )
        for name in ('prepare-task', 'task-workspace', 'delegate', 'breaker', 'gate'):
            script = self.project / 'scripts' / (name + '.sh')
            body = script.read_text()
            if not body.startswith('MAIN='):
                body = 'MAIN=%s\n' % shlex.quote(str(self.project)) + body
            script.write_text(body.replace('\n', '\n' + trace, 1))
        self.config['tasks'][0]['evaluation_command'] = (
            'echo "evaluation $(git -C %s branch --show-current) $(pwd -P)" >> %s; '
            'echo \'{"passed":1,"total":1}\'' % (shlex.quote(str(self.project)), shlex.quote(str(steps)))
        )
        self.git('add', '.')
        self.git('commit', '-qm', 'traced scripts')
        head = self.git('rev-parse', 'HEAD')
        self.config['start_commit'] = head
        self.assertEqual(self.run_campaign().returncode, 0)
        lines = [line.split() for line in steps.read_text().splitlines()]
        self.assertGreater(len(lines), 20)
        for line in lines:
            if line[0] == 'evaluation':
                branch, cwd = line[1], line[2]
            else:
                branch, cwd = line[1], line[3]
                self.assertEqual(line[2], head, line)
            self.assertEqual(branch, 'initial', line)
            self.assertTrue(cwd.startswith(str(self.project / '.workspace/campaign') + os.sep), line)
        self.assertEqual(
            {line[0] for line in lines}, {'prepare-task.sh', 'task-workspace.sh', 'delegate.sh', 'breaker.sh', 'gate.sh', 'evaluation'}
        )
        self.assertEqual(self.git('branch', '--show-current'), 'initial')
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_campaign_normalizes_totals_and_propagates_unknowns_per_role(self):
        self.config.update(arms=['codex-seul'], repetitions=1)
        events = [
            dict(provider='claude', role='orchestration', tokens=dict(input=12, cached_input=7, cache_creation=5, output=3)),
            dict(provider='claude', role='orchestration', tokens=dict(input=0, cached_input=0, cache_creation=0, output=0)),
            dict(provider='claude', role='execution', tokens=dict(input=12, cached_input=7, output=3)),
            dict(provider='claude', role='execution', tokens=dict(input=12, cached_input=7, cache_creation=5, output=3)),
            dict(provider='codex', role='execution', tokens=dict(input=12, cached_input=7, output=3, reasoning=2)),
            dict(provider='codex', role='relecture', tokens=dict(input=12, output=3, total=None), tokens_complete=False),
            dict(provider='codex', role='conseil', tokens=dict(input=0, output=0)),
            dict(provider='claude', role='recherche', tokens=dict(input=0, cached_input=0, cache_creation=0, output=0)),
        ]
        script = self.project / 'scripts/delegate.sh'
        script.write_text(
            "mkdir -p .workspace/metrics\ncat >> .workspace/metrics/consumption.jsonl <<'EVENTS'\n"
            + '\n'.join(json.dumps(e) for e in events)
            + "\nEVENTS\necho '{\"attempts\":1,\"outcome\":\"done\"}'\n"
        )
        self.git('add', '.')
        self.git('commit', '-qm', 'measured fake calls')
        self.config['start_commit'] = self.git('rev-parse', 'HEAD')
        result = self.run_campaign()
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self.rows()[0]
        self.assertEqual(row['consumption_events'], events)
        counts = row['consumption']
        known = counts['claude']['orchestration']
        self.assertEqual((known.get('cache_creation'), known.get('total_tokens'), known.get('unknown_token_calls')), (5, 27, 0))
        unknown = counts['claude']['execution']
        self.assertIsNone(unknown['cache_creation'])
        self.assertIsNone(unknown['total_tokens'])
        self.assertEqual(unknown['unknown_token_calls'], 1)
        self.assertEqual(counts['codex']['execution']['total_tokens'], 15)
        self.assertIsNone(counts['codex']['relecture']['total_tokens'])
        self.assertEqual(counts['codex']['relecture']['unknown_token_calls'], 1)
        self.assertEqual(counts['codex']['conseil']['total_tokens'], 0)
        self.assertEqual(counts['claude']['recherche']['total_tokens'], 0)
        markdown = (self.base / 'results.md').read_text()
        self.assertIn('claude/orchestration', markdown)
        self.assertIn('total=27', markdown)
        self.assertIn('total=inconnu', markdown)
        self.assertIn('inconnus=1', markdown)

    def test_seed_reproduces_order(self):
        self.assertEqual(self.run_campaign().returncode, 0)
        order = [(r['task'], r['arm'], r['repetition']) for r in self.rows()]
        (self.base / 'results.jsonl').unlink()
        # Les branches de bras ne sont jamais supprimées par la campagne : un nouveau départ repart de k = 0.
        for branch in self.git('branch', '--list', 'campagne/*', '--format=%(refname:short)').splitlines():
            self.git('branch', '-D', branch)
        self.assertEqual(self.run_campaign().returncode, 0)
        self.assertEqual(order, [(r['task'], r['arm'], r['repetition']) for r in self.rows()])

    def test_quota_stops_and_can_resume(self):
        (self.project / '.workspace').mkdir()
        (self.project / '.workspace/quota').touch()
        result = self.run_campaign()
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]['status'], 'quota')
        counts = self.rows()[0]['consumption']['codex']['execution']
        self.assertIsNone(counts['total_tokens'])
        self.assertEqual(counts['unknown_token_calls'], 1)
        self.assertEqual(self.rows()[0]['attempts'], 1)
        self.assertEqual(self.git('branch', '--show-current'), 'initial')
        stopped = self.rows()[0]
        self.assertIs(stopped['arm_worktree_kept'], True)
        self.assertTrue(Path(stopped['arm_worktree']).is_dir())
        (self.project / '.workspace/quota').unlink()
        self.assertEqual(self.run_campaign().returncode, 0)
        self.assertEqual(len(self.rows()), 5)
        self.assertEqual(self.rows()[-1]['status'], 'completed')
        # Reprise : nouveau k, donc nouvelle branche et nouveau worktree ; celui du quota reste.
        self.assertEqual(self.rows()[0], stopped)
        self.assertTrue(Path(stopped['arm_worktree']).is_dir())
        self.assertEqual(self.worktrees(), [self.project, Path(stopped['arm_worktree'])])
        self.assertEqual(len({r['branch'] for r in self.rows()}), 5)
        self.assertEqual(self.git('branch', '--show-current'), 'initial')
        for resumed in self.rows()[1:]:
            counts = resumed['consumption']['codex']['execution']
            self.assertIsNotNone(counts['total_tokens'])
            self.assertEqual(counts['unknown_token_calls'], 0)
            self.assertFalse(any(e.get('quota', {}).get('limit_reached') for e in resumed['consumption_events']))

    def test_evaluation_failure_cleans_tests_and_preserves_original(self):
        self.config['arms'] = ['opus-seul']
        self.config['repetitions'] = 1
        self.config['tasks'][0]['evaluation_command'] = 'echo changed > src/code.txt; exit 1'
        result = self.run_campaign()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('branch', '--show-current'), 'initial')
        self.assertEqual((self.project / 'src/code.txt').read_text(), 'base')
        self.assertFalse((self.project / 'eval').exists())
        row = self.rows()[0]
        self.assertEqual(row['evaluation']['exit'], 1)
        # Le bras est `completed` mais son worktree est sale : il reste, sans toucher au projet principal.
        self.assertEqual(row['status'], 'completed')
        self.assertIs(row['arm_worktree_kept'], True)
        self.assertEqual((Path(row['arm_worktree']) / 'src/code.txt').read_text(), 'changed\n')
        self.assertFalse((Path(row['arm_worktree']) / 'eval').exists())

    def test_quota_in_planner_counts_orchestration(self):
        self.config['arms'] = ['systeme-codex']
        script = self.project / 'scripts/prepare-task.sh'
        script.write_text(
            "mkdir -p .workspace/metrics\nprintf '%s\\n' "
            "'{\"provider\":\"claude\",\"role\":\"orchestration\","
            "\"quota\":{\"limit_reached\":true}}' "
            ">> .workspace/metrics/consumption.jsonl\nexit 3\n"
        )
        self.git('add', '.')
        self.git('commit', '-qm', 'planner quota')
        self.config['start_commit'] = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.run_campaign().returncode, 75)
        self.assertEqual(self.rows()[0]['status'], 'quota')
        self.assertIn('orchestration', self.rows()[0]['consumption']['claude'])
        self.assertEqual(self.git('branch', '--show-current'), 'initial')

    def test_unknown_evaluation_count_is_not_reported_as_one_test(self):
        self.config['arms'] = ['codex-seul']
        self.config['repetitions'] = 1
        self.config['tasks'][0]['evaluation_command'] = 'echo diagnostic; exit 1'
        self.assertEqual(self.run_campaign().returncode, 0)
        self.assertIsNone(self.rows()[0]['evaluation']['total'])
        self.assertEqual(self.rows()[0]['evaluation']['exit'], 1)

    def test_existing_evaluation_file_is_preserved(self):
        self.config['arms'] = ['codex-seul']
        self.config['repetitions'] = 1
        (self.project / 'eval').mkdir()
        (self.project / 'eval/hidden.py').write_text('original')
        self.git('add', '.')
        self.git('commit', '-qm', 'existing file')
        self.config['start_commit'] = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.run_campaign().returncode, 1)
        self.assertEqual((self.project / 'eval/hidden.py').read_text(), 'original')
        self.assertEqual(self.git('branch', '--show-current'), 'initial')
        self.assertEqual(self.rows()[0]['status'], 'failed')

    def test_quota_word_in_identifier_is_a_failure_not_a_limit(self):
        self.config.update(arms=['systeme-claude-haiku'], repetitions=2)
        self.write_script(
            'delegate',
            '''mkdir -p .workspace/metrics
echo "delegate $*" >> "$MAIN/.workspace/calls"
if [ -f "$MAIN/.workspace/fail-once" ]; then
rm "$MAIN/.workspace/fail-once"
echo '{"id":"camp-t1-quota-systeme-claude-sonnet-1-0","attempts":1,"outcome":"failed"}'
exit 1
fi
echo '{"attempts":1,"outcome":"done"}'
''',
        )
        (self.project / '.workspace').mkdir()
        (self.project / '.workspace/fail-once').touch()
        self.git('add', '.')
        self.git('commit', '-qm', 'failing delegate with quota identifier')
        self.config['start_commit'] = self.git('rev-parse', 'HEAD')
        result = self.run_campaign()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual([r['status'] for r in self.rows()], ['failed', 'completed'])
        self.assertIn('Délégation échouée', self.rows()[0]['error'])
        self.assertEqual(self.git('branch', '--show-current'), 'initial')

    def test_failed_single_agent_is_merged_without_verification(self):
        self.config['arms'] = ['codex-seul']
        self.config['repetitions'] = 1
        script = self.project / 'scripts/delegate.sh'
        script.write_text("echo '{\"attempts\":1,\"outcome\":\"verify_failed\"}'; exit 1\n")
        self.git('add', '.')
        self.git('commit', '-qm', 'failing solo')
        self.config['start_commit'] = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.run_campaign().returncode, 0)
        self.assertEqual(self.rows()[0]['attempts'], 1)
        self.assertEqual(self.rows()[0]['status'], 'completed')
        self.assertIn('--no-verify-again --no-integration', (self.project / '.workspace/calls').read_text())

    def fail_with_work(self):
        """Task clones outside the arm; the delegate leaves uncommitted work then fails; close refuses a dirty clone."""
        self.config.update(arms=['systeme-claude-haiku'], repetitions=2)
        self.write_script(
            'task-workspace',
            '''echo "$*" >> "$MAIN/.workspace/calls"
if [ "$1" = prepare ]; then
id=$(sed -n 's/^id: //p' "$2")
git worktree add -q "$WORKSPACE_WORK_DIR/$id" -b "workspace-task/$id"
mkdir -p ".workspace/orchestration/tasks/$id"
printf '{"worktree": "%s"}' "$WORKSPACE_WORK_DIR/$id" > ".workspace/orchestration/tasks/$id/state.json"
elif [ "$1" = close ]; then
case "$*" in *--force*) git worktree remove --force "$WORKSPACE_WORK_DIR/$2" ;; *) git worktree remove "$WORKSPACE_WORK_DIR/$2" ;; esac
elif [ "$1" = merge ]; then
git commit --allow-empty -qm delivered
fi
''',
        )
        self.write_script(
            'delegate',
            '''echo "delegate $*" >> "$MAIN/.workspace/calls"
id=$(basename "$1" .md)
if [ -f "$MAIN/.workspace/fail-once" ]; then
rm "$MAIN/.workspace/fail-once"
printf 'agent work' > "$WORKSPACE_WORK_DIR/$id/src/agent.txt"
echo '{"attempts":1,"outcome":"failed"}'
exit 1
fi
echo '{"attempts":1,"outcome":"done"}'
''',
        )
        (self.project / '.workspace').mkdir()
        (self.project / '.workspace/fail-once').touch()
        self.git('add', '.')
        self.git('commit', '-qm', 'failing delegate leaving work')
        self.config['start_commit'] = self.git('rev-parse', 'HEAD')

    def test_failed_arm_keeps_the_agent_work(self):
        self.fail_with_work()
        result = self.run_campaign()
        self.assertEqual(result.returncode, 1, result.stderr)
        rows = self.rows()
        self.assertEqual([r['status'] for r in rows], ['failed', 'completed'])
        closes = [line for line in (self.project / '.workspace/calls').read_text().splitlines() if line.startswith('close')]
        self.assertTrue(closes and all('--force' not in line for line in closes), closes)
        kept = rows[0]['failed_work']
        self.assertEqual(len(kept), 1)
        self.assertEqual(self.git('show', kept[0]['commit'] + ':src/agent.txt'), 'agent work')
        self.assertEqual(self.git('rev-parse', kept[0]['branch']), kept[0]['commit'])
        self.assertNotIn('cleanup_errors', rows[0])
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.git('branch', '--show-current'), 'initial')
        # Le bras en échec garde son worktree comme preuve ; le bras réussi retire le sien.
        self.assertIs(rows[0]['arm_worktree_kept'], True)
        self.assertEqual(self.git('-C', rows[0]['arm_worktree'], 'branch', '--show-current'), rows[0]['branch'])
        self.assertIs(rows[1]['arm_worktree_kept'], False)
        self.assertFalse(Path(rows[1]['arm_worktree']).exists())
        self.assertEqual(self.worktrees(), [self.project, Path(rows[0]['arm_worktree'])])
        for row in rows:
            self.assertTrue(self.git('rev-parse', '--verify', row['branch']))

    def test_discard_failed_restores_the_forced_close(self):
        self.fail_with_work()
        result = self.run_campaign('--discard-failed')
        self.assertEqual(result.returncode, 1, result.stderr)
        rows = self.rows()
        self.assertEqual(rows[0]['status'], 'failed')
        self.assertNotIn('failed_work', rows[0])
        self.assertIn('--abandoned --force', (self.project / '.workspace/calls').read_text())
        # --discard-failed retire aussi le worktree du bras ; sa branche reste.
        for row in rows:
            self.assertIs(row['arm_worktree_kept'], False)
            self.assertFalse(Path(row['arm_worktree']).exists())
            self.assertTrue(self.git('rev-parse', '--verify', row['branch']))
        self.assertEqual(self.worktrees(), [self.project])

    def test_evaluation_score_is_kept_when_it_fails_with_quota_word(self):
        self.config.update(arms=['codex-seul'], repetitions=2)
        self.config['tasks'][0]['evaluation_command'] = (
            'for i in 1 2 3 4 5 6 7 8 9 10 11; do echo "ok $i"; done; echo "not ok 12 quota projection"; exit 1'
        )
        result = self.run_campaign()
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = self.rows()
        self.assertEqual([r['status'] for r in rows], ['completed', 'completed'])
        self.assertEqual((rows[0]['evaluation']['exit'], rows[0]['evaluation']['passed'], rows[0]['evaluation']['total']), (1, 11, 12))
        self.assertNotIn('error', rows[0])
        self.assertEqual(self.git('branch', '--show-current'), 'initial')

    def test_gate_quota_word_is_not_a_limit(self):
        self.config.update(arms=['codex-seul'], repetitions=1)
        (self.project / 'scripts/gate.sh').write_text('echo "FAIL quota"; exit 1\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'gate quota word')
        self.config['start_commit'] = self.git('rev-parse', 'HEAD')
        result = self.run_campaign()
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self.rows()[0]
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['gate']['exit'], 1)
        self.assertEqual(row['evaluation']['passed'], 3)

    def test_agent_quota_text_still_stops_the_campaign(self):
        self.config.update(arms=['systeme-claude-haiku'], repetitions=1)
        (self.project / 'scripts/delegate.sh').write_text("echo 'usage limit reached'; exit 1\n")
        self.git('add', '.')
        self.git('commit', '-qm', 'delegate quota text')
        self.config['start_commit'] = self.git('rev-parse', 'HEAD')
        result = self.run_campaign()
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual([r['status'] for r in self.rows()], ['quota'])
        self.assertEqual(self.git('branch', '--show-current'), 'initial')

    def test_setup_runs_in_the_worktree_before_the_contracts(self):
        record = shlex.quote(str(self.project / '.workspace/setup'))
        self.config.update(arms=['codex-seul'], repetitions=1)
        self.config['setup'] = 'pwd -P > %s; ls -A tasks >> %s 2>/dev/null; mkdir -p .workspace && touch .workspace/deps' % (record, record)
        self.assertEqual(self.run_campaign().returncode, 0)
        row = self.rows()[0]
        self.assertEqual(row['status'], 'completed')
        recorded = (self.project / '.workspace/setup').read_text().splitlines()
        self.assertEqual(recorded, [row['arm_worktree']])  # dans le worktree, et aucun contrat encore
        self.assertEqual(row['commands'][0]['args'][:2], ['bash', '-c'])
        self.assertEqual(row['commands'][0]['exit'], 0)
        self.assertEqual(row['commands'][1]['args'][1:3], ['scripts/task-workspace.sh', 'prepare'])
        self.assertFalse((self.project / '.workspace/deps').exists())
        self.assertFalse((self.project / 'tasks').exists())

    def test_setup_accepts_an_argv_list(self):
        self.config.update(arms=['codex-seul'], repetitions=1)
        self.config['setup'] = ['sh', '-c', 'pwd -P > ' + shlex.quote(str(self.project / '.workspace/argv-setup'))]
        self.assertEqual(self.run_campaign().returncode, 0)
        row = self.rows()[0]
        self.assertEqual(row['commands'][0]['args'], self.config['setup'])
        self.assertEqual((self.project / '.workspace/argv-setup').read_text().strip(), row['arm_worktree'])

    def test_failed_setup_fails_the_arm_and_never_counts_as_quota(self):
        self.config.update(arms=['systeme-claude-haiku'], repetitions=1)
        limit = '{"provider":"codex","role":"execution","quota":{"limit_reached":true}}'
        self.config['setup'] = (
            "mkdir -p .workspace/metrics; echo '%s' >> .workspace/metrics/consumption.jsonl; echo 'usage limit reached'; exit 3" % limit
        )
        result = self.run_campaign()
        self.assertEqual(result.returncode, 1, result.stderr)
        row = self.rows()[0]
        self.assertEqual(row['status'], 'failed')
        self.assertIn('Commande échouée (3)', row['error'])
        self.assertEqual(len(row['commands']), 1)  # ni contrat ni délégation après l'échec
        self.assertFalse((self.project / '.workspace/calls').exists())
        self.assertIs(row['arm_worktree_kept'], True)
        self.assertTrue(Path(row['arm_worktree']).is_dir())
        self.assertEqual(self.git('branch', '--show-current'), 'initial')

    def test_invalid_setup_is_refused(self):
        self.config['setup'] = ['sh', 7]
        result = self.run_campaign()
        self.assertEqual(result.returncode, 1)
        self.assertIn('setup', result.stderr)
        self.assertFalse((self.base / 'results.jsonl').exists())

    def test_antigravity_system_arm_is_refused_before_any_run(self):
        self.config['arms'] = ['systeme-antigravity']
        result = self.run_campaign()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1, result.stderr)
        self.assertIn('campaign: ', result.stderr)
        self.assertIn('systeme-antigravity', result.stderr)
        self.assertIn('docs/ANTIGRAVITY-SANS-INTERACTION.md', result.stderr)
        self.assertFalse((self.base / 'results.jsonl').exists())

    def add_files_mode(self):
        (self.project / 'scripts/lib').mkdir()
        (self.project / 'scripts/lib/antigravity_files.py').write_text('OUTBOX = ".workspace/antigravity/out"\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'files mode')
        self.config['start_commit'] = self.git('rev-parse', 'HEAD')

    def test_antigravity_system_arm_is_accepted_when_the_project_has_the_files_mode(self):
        self.add_files_mode()
        self.config.update(arms=['systeme-antigravity'], repetitions=1)
        result = self.run_campaign()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('ANTIGRAVITY-SANS-INTERACTION', result.stderr)
        self.assertEqual([row['arm'] for row in self.rows()], ['systeme-antigravity'])

    def test_antigravity_breaker_stays_refused_with_the_files_mode(self):
        self.add_files_mode()
        self.config.update(arms=['systeme-claude-haiku'], breaker_agent='antigravity')
        result = self.run_campaign()
        self.assertEqual(result.returncode, 1)
        self.assertIn('docs/ANTIGRAVITY-SANS-INTERACTION.md', result.stderr)
        self.assertFalse((self.base / 'results.jsonl').exists())

    def breaker_commands(self, rows):
        return [c['args'] for r in rows for c in r['commands'] if c['args'][1:2] == ['scripts/breaker.sh']]

    def test_breaker_agent_defaults_to_codex_explicitly(self):
        self.config.update(arms=['codex-seul', 'systeme-claude-haiku'], repetitions=1)
        self.assertEqual(self.run_campaign().returncode, 0)
        rows = self.rows()
        self.assertEqual({r['arm']: r['breaker_agent'] for r in rows}, {'codex-seul': None, 'systeme-claude-haiku': 'codex'})
        commands = self.breaker_commands(rows)
        self.assertEqual(len(commands), 2)  # le faux prepare-task crée deux contrats par bras système
        for args in commands:
            self.assertEqual(args[3:], ['--promote', '--agent', 'codex'])

    def test_breaker_agent_string_applies_to_every_system_arm(self):
        self.config.update(arms=['codex-seul', 'opus-seul', 'systeme-claude-haiku', 'systeme-codex'], repetitions=1)
        self.config['breaker_agent'] = 'claude-sonnet'
        self.assertEqual(self.run_campaign().returncode, 0)
        rows = self.rows()
        self.assertEqual(
            {r['arm']: r['breaker_agent'] for r in rows},
            {'codex-seul': None, 'opus-seul': None, 'systeme-claude-haiku': 'claude-sonnet', 'systeme-codex': 'claude-sonnet'},
        )
        commands = self.breaker_commands(rows)
        self.assertEqual(len(commands), 4)  # deux contrats par bras système
        for args in commands:
            self.assertEqual(args[3:], ['--promote', '--agent', 'claude-sonnet'])

    def test_breaker_agent_object_is_per_arm_and_missing_arm_keeps_codex(self):
        self.config.update(arms=['codex-seul', 'systeme-claude-haiku', 'systeme-codex'], repetitions=1)
        self.config['breaker_agent'] = {'systeme-claude-haiku': 'claude-opus'}
        self.assertEqual(self.run_campaign().returncode, 0)
        rows = self.rows()
        self.assertEqual(
            {r['arm']: r['breaker_agent'] for r in rows},
            {'codex-seul': None, 'systeme-claude-haiku': 'claude-opus', 'systeme-codex': 'codex'},
        )
        for row in rows:
            used = [c['args'][-1] for c in row['commands'] if c['args'][1:2] == ['scripts/breaker.sh']]
            self.assertEqual(used, [row['breaker_agent']] * 2 if row['breaker_agent'] else [])

    def test_cascade_arm_delegates_with_the_cascade_and_sums_stage_attempts(self):
        self.write_script(
            'delegate',
            '''echo "delegate $*" >> "$MAIN/.workspace/calls"
id=$(basename "$3" .md)
mkdir -p ".workspace/run/delegate/$id"
printf '{"outcome":"done","stages":[{"attempts":2},{"attempts":3}]}' > ".workspace/run/delegate/$id/cascade.json"
echo '{"attempts":1,"outcome":"done"}'
''',
        )
        self.git('add', '.')
        self.git('commit', '-qm', 'cascade delegate')
        self.config.update(arms=['cascade'], repetitions=1, start_commit=self.git('rev-parse', 'HEAD'))
        self.config['breaker_agent'] = 'claude-sonnet'
        result = self.run_campaign()
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self.rows()[0]
        self.assertEqual((row['arm'], row['status'], row['breaker_agent']), ('cascade', 'completed', None))
        self.assertEqual(row['attempts'], 10)  # deux contrats, 2 + 3 tentatives chacun
        self.assertEqual(self.breaker_commands([row]), [])
        calls = (self.project / '.workspace/calls').read_text()
        self.assertIn('delegate --cascade haiku,sonnet,opus tasks/camp-simple-cascade-1-0-1.md', calls)
        prepare = next(c['args'] for c in row['commands'] if c['args'][1:2] == ['scripts/prepare-task.sh'])
        self.assertEqual(prepare[prepare.index('--executor') + 1], 'claude-haiku')
        self.assertNotIn('--no-verify-again', calls)

    def test_single_agent_arms_never_run_the_breaker(self):
        self.config.update(arms=['codex-seul', 'opus-seul'], repetitions=1)
        self.config['breaker_agent'] = 'claude-haiku'
        self.assertEqual(self.run_campaign().returncode, 0)
        rows = self.rows()
        self.assertEqual([r['breaker_agent'] for r in rows], [None, None])
        self.assertEqual(self.breaker_commands(rows), [])

    def test_invalid_breaker_agent_is_refused_before_any_run(self):
        self.config['arms'] = ['codex-seul', 'systeme-claude-haiku']
        doc = 'docs/ANTIGRAVITY-SANS-INTERACTION.md'
        cases = [
            ('antigravity', 'antigravity', doc),
            ({'systeme-claude-haiku': 'antigravity'}, 'antigravity', doc),
            ({'systeme-codex': 'codex'}, 'bras inconnu', 'systeme-codex'),
            ({'systeme-claude-haiku': 'gpt'}, 'invalide', 'gpt'),
            ('gpt', 'invalide', 'gpt'),
            (7, 'doit être', 'breaker_agent'),
            (['codex'], 'doit être', 'breaker_agent'),
            ({'systeme-claude-haiku': 3}, 'invalide', '3'),
            (None, 'doit être', 'breaker_agent'),
        ]
        for value, message, detail in cases:
            with self.subTest(value=value):
                self.config['breaker_agent'] = value
                result = self.run_campaign()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(len(result.stderr.strip().splitlines()), 1, result.stderr)
                self.assertIn('campaign: breaker_agent', result.stderr)
                self.assertIn(message, result.stderr)
                self.assertIn(detail, result.stderr)
                self.assertFalse((self.base / 'results.jsonl').exists())

    def dry_codex(self):
        (self.project / '.workspace').mkdir(exist_ok=True)
        (self.project / '.workspace/codex-down').touch()

    def test_dry_provider_refuses_before_any_arm_starts(self):
        self.config['arms'] = ['codex-seul']
        self.dry_codex()
        result = self.run_campaign()
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1, result.stderr)
        self.assertIn('campaign: codex à sec : ', result.stderr)
        self.assertIn('aucun bras lancé', result.stderr)
        self.assertIn('--ignore-quota', result.stderr)
        self.assertFalse((self.base / 'results.jsonl').exists())
        self.assertEqual(self.worktrees(), [self.project])
        self.assertEqual(self.git('branch', '--list', 'campagne/*'), '')
        self.assertFalse((self.project / '.workspace/calls').exists())

    def test_ignore_quota_starts_without_calling_the_relay(self):
        self.dry_codex()
        result = self.run_campaign('--ignore-quota')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.relay_calls(), [])
        self.assertEqual(len(self.rows()), 4)

    def test_available_relay_is_probed_once_then_the_campaign_runs(self):
        result = self.run_campaign()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.relay_calls()), 1)
        self.assertTrue(self.relay_calls()[0].startswith('codex review '))
        self.assertEqual(len(self.rows()), 4)

    def test_every_dry_provider_is_listed_in_fixed_order(self):
        self.dry_codex()
        statusline = self.base / 'console/claude-statusline.jsonl'
        statusline.parent.mkdir()
        now = datetime.now(UTC)
        statusline.write_text(
            json.dumps(
                dict(timestamp=now.isoformat(), five_hour=dict(used_percentage=100, resets_at=(now + timedelta(hours=1)).isoformat()))
            )
            + '\n'
        )
        result = self.run_campaign()
        self.assertEqual(result.returncode, 75, result.stderr)
        lines = result.stderr.strip().splitlines()
        self.assertEqual([line.split(' ')[1] for line in lines], ['claude', 'codex'], result.stderr)
        self.assertEqual(len(self.relay_calls()), 1)  # Codex est sondé même après Claude à sec.
        self.assertFalse((self.base / 'results.jsonl').exists())

    def test_dry_provider_is_checked_again_on_resume(self):
        self.assertEqual(self.run_campaign().returncode, 0)
        self.assertEqual(len(self.rows()), 4)
        self.dry_codex()
        result = self.run_campaign()
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(len(self.rows()), 4)
