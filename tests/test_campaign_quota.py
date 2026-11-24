import json
import os
import shlex
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/campaign'))
import quota_check
from quota_check import check, providers

PROMPT = 'Réponds seulement OK.'


class QuotaCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()  # Jamais les vrais dossiers : HOME et WSC_HOME sont factices.
        self.root = self.base / 'project'
        (self.root / 'scripts').mkdir(parents=True)
        self.home = self.base / 'home'
        self.env = dict(PATH=os.environ.get('PATH', ''), HOME=str(self.home))
        self.calls = self.base / 'calls'

    def config(self, *arms, **options):
        return dict(arms=list(arms), **options)

    def relay(self, name, body):
        """Relais factice : `$2` est la consigne, `$3` la réponse ; `calls` garde une trace de chaque appel."""
        (self.root / 'scripts' / (name + '.sh')).write_text(body)

    def codex(self, body):
        self.relay('codex', 'echo "codex $1" >> %s\n%s' % (shlex.quote(str(self.calls)), body))

    def statusline(self, folder, *records, name='claude-statusline.jsonl'):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(''.join(json.dumps(record) + '\n' for record in records))

    def stamp(self, delta=timedelta(0)):
        return (datetime.now(UTC) + delta).isoformat()

    def full(self, timestamp=None, resets=None, key='five_hour', used=100):
        window = dict(used_percentage=used, resets_at=resets if resets is not None else self.stamp(timedelta(hours=1)))
        return {'timestamp': timestamp or self.stamp(), key: window}

    def journal(self, *events):
        path = self.root / '.workspace/metrics/consumption.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(event if isinstance(event, str) else json.dumps(event) + '\n' for event in events))

    def limit(self, provider='claude', at=None):
        return dict(provider=provider, quota=dict(limit_reached=True), at=at or self.stamp(timedelta(hours=-1)))

    def test_providers_are_listed_in_fixed_order_without_duplicates(self):
        self.assertEqual(providers(self.config('systeme-antigravity', 'codex-seul', 'opus-seul')), ['claude', 'codex', 'antigravity'])
        self.assertEqual(providers(self.config('opus-seul', 'opus-seul')), ['claude'])

    def test_single_agent_arms_use_only_their_provider(self):
        self.assertEqual(providers(self.config('codex-seul')), ['codex'])
        self.assertEqual(providers(self.config('opus-seul')), ['claude'])

    def test_system_arm_adds_planner_and_default_codex_breaker(self):
        self.assertEqual(providers(self.config('systeme-codex')), ['claude', 'codex'])
        self.assertEqual(providers(self.config('systeme-antigravity')), ['claude', 'codex', 'antigravity'])

    def test_system_arm_with_claude_breaker_needs_no_codex(self):
        self.assertEqual(providers(self.config('systeme-claude-sonnet', breaker_agent='claude-haiku')), ['claude'])

    def test_breaker_object_is_per_arm(self):
        config = self.config(
            'systeme-claude-haiku',
            'systeme-antigravity',
            breaker_agent={'systeme-claude-haiku': 'antigravity', 'systeme-antigravity': 'claude-opus'},
        )
        self.assertEqual(providers(config), ['claude', 'antigravity'])

    def test_codex_relay_with_quota_text_is_dry_even_with_exit_2(self):
        self.codex('echo "usage limit reached" >&2\nexit 2\n')
        self.assertEqual(check(self.root, self.config('codex-seul'), self.env), [('codex', 'quota signalé par le relais')])

    def test_missing_relay_is_dry(self):
        self.assertEqual(check(self.root, self.config('codex-seul'), self.env), [('codex', 'script absent')])

    def test_relay_failure_without_quota_text_is_dry_with_its_code(self):
        self.codex('echo "boom"\nexit 2\n')
        self.assertEqual(check(self.root, self.config('codex-seul'), self.env), [('codex', 'contrôle en échec (code 2)')])

    def test_relay_timeout_is_dry(self):
        self.codex('exec sleep 5\n')
        with mock.patch.object(quota_check, 'PROBE_TIMEOUT', 1):
            self.assertEqual(check(self.root, self.config('codex-seul'), self.env), [('codex', 'délai dépassé (1 s)')])

    def test_empty_or_blank_answer_is_dry(self):
        for body in ('exit 0\n', 'printf "  \\n" > "$3"\n'):
            with self.subTest(body=body):
                self.codex(body)
                self.assertEqual(check(self.root, self.config('codex-seul'), self.env), [('codex', 'réponse vide')])

    def test_relay_gets_the_prompt_outside_the_project_in_orchestration_role(self):
        self.relay(
            'codex',
            'printf "%%s|%%s|%%s\\n" "$WORKSPACE_ROLE" "$(cat "$2")" "$PWD" >> %s\nprintf OK > "$3"\n' % shlex.quote(str(self.calls)),
        )
        self.assertEqual(check(self.root, self.config('codex-seul'), self.env), [])
        role, prompt, cwd = self.calls.read_text().strip().split('|')
        self.assertEqual((role, prompt), ('orchestration', PROMPT))
        self.assertEqual(Path(cwd).resolve(), self.root.resolve())
        self.assertNotIn(str(self.root.resolve()), str(self.calls))

    def test_each_relay_is_probed_once_per_check(self):
        self.relay('codex', 'echo "codex" >> %s\nprintf OK > "$3"\n' % shlex.quote(str(self.calls)))
        self.assertEqual(check(self.root, self.config('codex-seul', 'systeme-codex'), self.env), [])
        self.assertEqual(self.calls.read_text().splitlines(), ['codex'])

    def test_claude_is_never_called_and_absent_files_mean_nothing(self):
        self.relay('codex', 'echo "codex" >> %s\nexit 2\n' % shlex.quote(str(self.calls)))
        self.assertEqual(check(self.root, self.config('opus-seul'), self.env), [])
        self.assertFalse(self.calls.exists())

    def test_full_window_with_future_reset_is_dry(self):
        self.statusline(self.home / '.workspace-console', self.full())
        self.assertEqual([p for p, _ in check(self.root, self.config('opus-seul'), self.env)], ['claude'])

    def test_seven_day_window_is_dry_too(self):
        self.statusline(self.home / '.workspace-console', self.full(key='seven_day'))
        reasons = check(self.root, self.config('opus-seul'), self.env)
        self.assertEqual(len(reasons), 1)
        self.assertIn('seven_day', reasons[0][1])

    def test_window_already_reset_is_not_dry(self):
        self.statusline(self.home / '.workspace-console', self.full(resets=self.stamp(timedelta(minutes=-1))))
        self.assertEqual(check(self.root, self.config('opus-seul'), self.env), [])

    def test_full_window_without_reset_time_is_not_dry(self):
        record = dict(timestamp=self.stamp(), five_hour=dict(used_percentage=100))
        self.statusline(self.home / '.workspace-console', record)
        self.assertEqual(check(self.root, self.config('opus-seul'), self.env), [])

    def test_most_recent_valid_line_decides_across_both_files(self):
        folder = self.home / '.workspace-console'
        self.statusline(folder, self.full(timestamp=self.stamp(timedelta(hours=-2))), name='claude-statusline.jsonl.1')
        self.statusline(folder, dict(timestamp=self.stamp(), five_hour=dict(used_percentage=40)))
        self.assertEqual(check(self.root, self.config('opus-seul'), self.env), [])
        self.statusline(folder, self.full(timestamp=self.stamp(timedelta(minutes=1))), name='claude-statusline.jsonl.1')
        self.assertEqual(len(check(self.root, self.config('opus-seul'), self.env)), 1)

    def test_invalid_statusline_lines_are_ignored(self):
        folder = self.home / '.workspace-console'
        folder.mkdir(parents=True)
        without_timestamp = dict(self.full())
        del without_timestamp['timestamp']
        (folder / 'claude-statusline.jsonl').write_text(
            'pas du json\n[1, 2]\n'
            + json.dumps(without_timestamp)
            + '\n'
            + json.dumps(dict(timestamp=self.stamp(), five_hour=dict(used_percentage=10)))
            + '\n'
        )
        self.assertEqual(check(self.root, self.config('opus-seul'), self.env), [])

    def test_epoch_seconds_and_zulu_timestamps_are_read(self):
        now = datetime.now(UTC)
        resets = (now + timedelta(hours=1)).timestamp()
        record = dict(timestamp=now.strftime('%Y-%m-%dT%H:%M:%SZ'), five_hour=dict(used_percentage=100, resets_at=resets))
        self.statusline(self.home / '.workspace-console', record)
        self.assertEqual(len(check(self.root, self.config('opus-seul'), self.env)), 1)

    def test_wsc_home_overrides_home_and_empty_wsc_home_falls_back(self):
        console = self.base / 'console'
        self.statusline(console, self.full())
        self.assertEqual(len(check(self.root, self.config('opus-seul'), dict(self.env, WSC_HOME=str(console)))), 1)
        self.assertEqual(check(self.root, self.config('opus-seul'), dict(self.env, WSC_HOME=str(self.base / 'vide'))), [])
        self.statusline(self.home / '.workspace-console', self.full())
        self.assertEqual(len(check(self.root, self.config('opus-seul'), dict(self.env, WSC_HOME=''))), 1)

    def test_journal_limit_under_five_hours_is_dry(self):
        self.journal(self.limit(at=self.stamp(timedelta(hours=-1))))
        reasons = check(self.root, self.config('opus-seul'), self.env)
        self.assertEqual([p for p, _ in reasons], ['claude'])

    def test_journal_limit_older_than_five_hours_is_not_dry(self):
        self.journal(self.limit(at=self.stamp(timedelta(hours=-6))))
        self.assertEqual(check(self.root, self.config('opus-seul'), self.env), [])

    def test_journal_ignores_other_providers_unreadable_lines_and_false_flags(self):
        self.journal(
            'pas du json\n',
            self.limit(provider='codex'),
            dict(provider='claude', quota=dict(limit_reached=False), at=self.stamp()),
            dict(provider='claude', quota=dict(limit_reached=True)),
        )
        self.assertEqual(check(self.root, self.config('opus-seul'), self.env), [])

    def test_all_dry_providers_are_returned_in_fixed_order(self):
        self.codex('echo "quota atteint" >&2\nexit 2\n')
        self.relay('gemini', 'exit 3\n')
        self.statusline(self.home / '.workspace-console', self.full())
        config = self.config('systeme-antigravity', 'codex-seul')
        self.assertEqual([p for p, _ in check(self.root, config, self.env)], ['claude', 'codex', 'antigravity'])


if __name__ == '__main__':
    unittest.main()
