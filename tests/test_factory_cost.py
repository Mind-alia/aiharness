import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'template/scripts/lib'
sys.path.insert(0, str(LIB))
import factory_cost  # noqa: E402
import metrics_claude  # noqa: E402

UTC = datetime.UTC
NOW = datetime.datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)


class FactoryCostTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(os.path.realpath(tmp.name))
        self.root = base / 'project'
        self.root.mkdir()
        self.claude = base / 'claude'
        self.claude.mkdir()
        (self.root / '.workspace').mkdir()
        self.counter = 0

    def event(self, kind, at, session, cwd=None, model='opus'):
        detail = {'session': session, 'model': model}
        if cwd is not None:
            detail['cwd'] = str(cwd)
        line = {'at': at, 'task_id': 'orchestrator', 'event': kind, 'detail': detail}
        with (self.root / '.workspace/events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(line) + '\n')

    def message(self, at, cwd, model='m1', msg_id=None, usage=(1, 2, 3, 4), name='a.jsonl', kind='assistant', entrypoint='claude-desktop'):  # noqa: PLR0913
        self.counter += 1
        record = {
            'type': kind,
            'cwd': str(cwd) if cwd is not None else None,
            'timestamp': at,
            'entrypoint': entrypoint,
            'message': {
                'id': msg_id or 'id%d' % self.counter,
                'model': model,
                'usage': {
                    'input_tokens': usage[0],
                    'cache_read_input_tokens': usage[1],
                    'cache_creation_input_tokens': usage[2],
                    'output_tokens': usage[3],
                },
            },
        }
        with (self.claude / name).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record) + '\n')

    def collect(self, session='u'):
        return factory_cost.collect(self.root, session, claude_dir=self.claude, now=NOW)

    def test_windows_close_on_next_claim_or_release(self):
        self.event('orchestrator_claimed', '2026-10-01T10:00:00Z', 'u', cwd=self.root)
        self.event('orchestrator_released', '2026-10-01T11:00:00Z', 'u')
        self.event('orchestrator_claimed', '2026-10-01T12:00:00Z', 'u')
        self.event('orchestrator_claimed', '2026-10-01T13:00:00Z', 'autre')
        self.event('orchestrator_claimed', '2026-10-01T14:00:00Z', 'u', cwd='')
        found = factory_cost.windows(self.root, 'u')
        self.assertEqual(
            [(w['start'], w['end'], w['cwd']) for w in found],
            [
                ('2026-10-01T10:00:00Z', '2026-10-01T11:00:00Z', str(self.root)),
                ('2026-10-01T12:00:00Z', '2026-10-01T13:00:00Z', None),
                ('2026-10-01T14:00:00Z', None, None),
            ],
        )

    def test_no_window_gives_zero_totals(self):
        result = self.collect()
        self.assertEqual((result['windows'], result['by_model']), ([], {}))
        self.assertEqual(result['total']['messages'], 0)

    def test_folder_time_and_boundaries(self):
        self.event('orchestrator_claimed', '2026-10-01T10:00:00Z', 'u', cwd=self.root)
        self.event('orchestrator_released', '2026-10-01T11:00:00Z', 'u')
        self.message('2026-10-01T09:59:59Z', self.root)
        self.message('2026-10-01T10:00:00Z', self.root)
        self.message('2026-10-01T11:00:00Z', self.root)
        self.message('2026-10-01T11:00:01Z', self.root)
        self.message('2026-10-01T10:30:00Z', self.root / 'sous-dossier')
        self.message('2026-10-01T10:30:00Z', self.root / '.workspace/worktrees/x')
        self.message('2026-10-01T10:30:00Z', self.root.parent / 'etranger')
        self.message('2026-10-01T10:30:00Z', None)
        self.message('pas une date', self.root)
        self.message('2026-10-01T10:30:00Z', self.root, kind='user')
        result = self.collect()
        self.assertEqual(result['total'], {'messages': 3, 'input': 3, 'cache_read': 6, 'cache_creation': 9, 'output': 12, 'total': 30})
        self.assertEqual(list(result['by_model']), ['m1'])
        self.assertEqual((result['windows'][0]['cwd_source'], result['windows'][0]['open']), ('événement', False))

    def test_sibling_factories_count_only_their_messages(self):
        self.event('orchestrator_claimed', '2026-10-01T10:00:00Z', 'u1', cwd=self.root)
        self.event('orchestrator_released', '2026-10-01T11:00:00Z', 'u1')
        self.event('orchestrator_claimed', '2026-10-01T11:30:00Z', 'u2', cwd=self.root)
        self.message('2026-10-01T10:30:00Z', self.root, model='b')
        self.message('2026-10-01T11:15:00Z', self.root)
        self.message('2026-10-01T11:45:00Z', self.root, model='a')
        first, second = self.collect('u1'), self.collect('u2')
        self.assertEqual((first['total']['messages'], list(first['by_model'])), (1, ['b']))
        self.assertEqual((second['total']['messages'], list(second['by_model'])), (1, ['a']))
        self.assertTrue(second['windows'][0]['open'])

    def test_open_window_ends_at_now_and_old_event_uses_root(self):
        self.event('orchestrator_claimed', '2026-10-23T10:00:00Z', 'u')
        self.message('2026-10-23T12:00:00Z', self.root)
        self.message('2026-10-23T12:00:01Z', self.root)
        result = self.collect()
        self.assertEqual(result['total']['messages'], 1)
        self.assertEqual((result['windows'][0]['cwd_source'], result['windows'][0]['folder']), ('racine', str(self.root)))

    def test_overlapping_windows_and_duplicate_ids_count_once(self):
        self.event('orchestrator_claimed', '2026-10-01T10:00:00Z', 'u', cwd=self.root)
        self.event('orchestrator_released', '2026-10-01T11:00:00Z', 'u')
        self.event('orchestrator_claimed', '2026-10-01T11:00:00Z', 'u', cwd=self.root)
        self.message('2026-10-01T11:00:00Z', self.root, msg_id='same', usage=(1, 1, 1, 1))
        self.message('2026-10-01T11:00:00Z', self.root, msg_id='same', usage=(5, 0, 0, 2), name='b.jsonl')
        result = self.collect()
        self.assertEqual(result['total'], {'messages': 1, 'input': 5, 'cache_read': 1, 'cache_creation': 1, 'output': 2, 'total': 9})

    def test_symlinked_transcript_is_ignored(self):
        self.event('orchestrator_claimed', '2026-10-01T10:00:00Z', 'u', cwd=self.root)
        outside = self.claude.parent / 'outside.jsonl'
        outside.write_text(
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(self.root),
                    'timestamp': '2026-10-01T10:30:00Z',
                    'message': {'id': 'x', 'model': 'm', 'usage': {'output_tokens': 9}},
                }
            )
            + '\n'
        )
        (self.claude / 'link.jsonl').symlink_to(outside)
        self.assertEqual(self.collect()['total']['messages'], 0)
        self.assertEqual(metrics_claude.assistant_messages(self.claude), {})

    def test_assistant_messages_fields(self):
        self.message('2026-10-01T10:30:00Z', self.root, model='m2', usage=(1, 2, 3, 4))
        found = metrics_claude.assistant_messages(self.claude)
        self.assertEqual(
            list(found.values()),
            [
                {
                    'cwd': str(self.root),
                    'timestamp': '2026-10-01T10:30:00Z',
                    'model': 'm2',
                    'input': 1,
                    'cache_read': 2,
                    'cache_creation': 3,
                    'output': 4,
                    'entrypoint': 'claude-desktop',
                    'sidechain': False,
                    'session_id': None,
                }
            ],
        )

    def run_command(self, *args):
        return subprocess.run(
            [sys.executable, str(LIB / 'factory_cost.py'), str(self.root)] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
        )

    def test_command_output_and_errors(self):
        self.event('orchestrator_claimed', '2026-10-01T10:00:00Z', 'u', cwd=self.root)
        self.event('orchestrator_released', '2026-10-01T11:00:00Z', 'u')
        self.event('orchestrator_claimed', '2026-10-01T12:00:00Z', 'u')
        self.message('2026-10-01T10:30:00Z', self.root, model='b')
        self.message('2026-10-01T10:31:00Z', self.root, model='a')
        done = self.run_command('--usine', 'u', '--claude-dir', str(self.claude))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                'Usine u : orchestrateur',
                "Fenêtre 2026-10-01T10:00:00Z → 2026-10-01T11:00:00Z, dossier %s (cwd de l'événement)" % self.root,
                'Fenêtre 2026-10-01T12:00:00Z → en cours, dossier %s (racine du projet : événement sans cwd)' % self.root,
                '| Modèle | Messages | Entrée hors cache | Cache lu | Cache créé | Sortie |',
                '| --- | --- | --- | --- | --- | --- |',
                '| a | 1 | 1 | 2 | 3 | 4 |',
                '| b | 1 | 1 | 2 | 3 | 4 |',
                '| Total | 2 | 2 | 4 | 6 | 8 |',
                '| Sous-agents et appels directs | 0 | 0 | 0 | 0 | 0 |',
            ],
        )
        missing = self.run_command('--usine', 'zz', '--claude-dir', str(self.claude))
        self.assertEqual((missing.returncode, missing.stdout), (1, ''))
        self.assertIn("aucune fenêtre pour l'usine zz", missing.stderr)
        for bad in ('a b', '', 'x' * 65, 'é'):
            self.assertEqual(self.run_command('--usine', bad).returncode, 64, bad)


if __name__ == '__main__':
    unittest.main()
