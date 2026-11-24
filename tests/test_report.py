import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReportTests(unittest.TestCase):
    def test_provider_totals_and_incomplete_claude_are_not_understated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / 'template/scripts', root / 'scripts')
            cases = [
                ('complete', 'claude-haiku', {'input': 12, 'cached_input': 7, 'cache_creation': 5, 'output': 3}, None),
                ('partial', 'claude-haiku', {'input': 12, 'cached_input': 7, 'output': 3}, None),
                ('zero', 'claude-sonnet', {'input': 0, 'cached_input': 0, 'cache_creation': 0, 'output': 3}, None),
                ('codex', 'codex', {'input': 12, 'cached_input': 7, 'output': 3}, None),
                ('lost-attempt', 'claude-opus', {'input': 12, 'cached_input': 7, 'cache_creation': 5, 'output': 3}, False),
            ]
            for name, agent, tokens, complete in cases:
                directory = root / '.workspace/run/delegate' / name
                directory.mkdir(parents=True)
                metrics = dict(duration_seconds=1, tokens=tokens)
                if complete is not None:
                    metrics['tokens_complete'] = complete
                (directory / 'status.json').write_text(
                    json.dumps(
                        dict(
                            id=name,
                            agent=agent,
                            isolation='none',
                            outcome='done',
                            attempts=1,
                            started_at='2026-10-17T12:00:00Z',
                            metrics=metrics,
                        )
                    )
                )
            output = subprocess.check_output(['bash', str(root / 'scripts/report.sh'), '--markdown'], text=True)
            self.assertIn('| complete | claude-haiku | none | done | null | 1 | 1.000 | 27 |', output)
            self.assertIn('| partial | claude-haiku | none | done | null | 1 | 1.000 | null |', output)
            self.assertIn('| zero | claude-sonnet | none | done | null | 1 | 1.000 | 3 |', output)
            self.assertIn('| codex | codex | none | done | null | 1 | 1.000 | 15 |', output)
            self.assertIn('| lost-attempt | claude-opus | none | done | null | 1 | 1.000 | null |', output)
            self.assertIn('| claude-haiku | 2 | 2 | 2.000 | null |', output)

    def test_totals_and_since(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / 'template/scripts', root / 'scripts')
            for name, agent, attempts, duration, tokens, date, outcome in [
                ('a', 'codex', 1, 2, 10, '2026-10-01', 'done'),
                ('b', 'codex', 2, 4, 20, '2026-10-02', 'done'),
                ('c', 'antigravity', 1, 3, None, '2026-10-03', 'failed'),
            ]:
                directory = root / '.workspace/run/delegate' / name
                directory.mkdir(parents=True)
                (directory / 'status.json').write_text(
                    json.dumps(
                        dict(
                            id=name,
                            agent=agent,
                            attempts=attempts,
                            isolation='none',
                            outcome=outcome,
                            started_at=date + 'T12:00:00+00:00',
                            metrics=dict(duration_seconds=duration, tokens={'total': tokens} if tokens is not None else None),
                        )
                    )
                )

            task_c_reg = root / '.workspace/orchestration/tasks/c'
            task_c_reg.mkdir(parents=True)
            (task_c_reg / 'state.json').write_text(json.dumps({'task_id': 'c', 'status': 'merged'}))

            def report(*args):
                return subprocess.check_output(['bash', str(root / 'scripts/report.sh'), *args], text=True)

            output = report('--markdown')
            self.assertIn('| codex | 2 | 3 | 6.000 | 30 |', output)
            self.assertIn('33.33% (1/3)', output)
            self.assertIn('| a | codex | none | done | null | 1 | 2.000 | 10 |', output)
            self.assertIn('| c | antigravity | none | failed | merged | 1 | 3.000 | null |', output)
            self.assertIn('Tâches fusionnées : 1/3', output)

            filtered = report('--since', '2026-10-02', '--markdown')
            self.assertNotIn('| a |', filtered)
            self.assertIn('0.00% (0/2)', filtered)
            self.assertIn('| b | codex | none | done | null | 2 | 4.000 | 20 |', filtered)
            self.assertIn('| codex | 1 | 2 | 4.000 | 20 |', filtered)
            self.assertIn('Tâches fusionnées : 1/2', filtered)

    def test_defensive_registry_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / 'template/scripts', root / 'scripts')
            cases = [
                'missing-dir',
                'missing-state',
                'invalid-json',
                'symlink-dir',
                'symlink-file',
            ]
            for name in cases:
                directory = root / '.workspace/run/delegate' / name
                directory.mkdir(parents=True)
                (directory / 'status.json').write_text(
                    json.dumps(
                        dict(
                            id=name,
                            agent='codex',
                            attempts=1,
                            isolation='none',
                            outcome='done',
                            started_at='2026-10-01T12:00:00+00:00',
                            metrics=dict(duration_seconds=1.0),
                        )
                    )
                )

            tasks_dir = root / '.workspace/orchestration/tasks'
            # 1. missing-dir: directory does not exist

            # 2. missing-state: directory exists, state.json absent
            (tasks_dir / 'missing-state').mkdir(parents=True)

            # 3. invalid-json: state.json is invalid JSON
            bad_json_dir = tasks_dir / 'invalid-json'
            bad_json_dir.mkdir(parents=True)
            (bad_json_dir / 'state.json').write_text('{invalid-json')

            # 4. symlink-dir: task directory is a symlink
            real_dir = root / 'other_dir'
            real_dir.mkdir(parents=True)
            (real_dir / 'state.json').write_text(json.dumps({'status': 'merged'}))
            tasks_dir.mkdir(parents=True, exist_ok=True)
            (tasks_dir / 'symlink-dir').symlink_to(real_dir)

            # 5. symlink-file: state.json is a symlink
            symlink_file_dir = tasks_dir / 'symlink-file'
            symlink_file_dir.mkdir(parents=True)
            real_file = root / 'outside_state.json'
            real_file.write_text(json.dumps({'status': 'merged'}))
            (symlink_file_dir / 'state.json').symlink_to(real_file)

            def report(*args):
                return subprocess.check_output(['bash', str(root / 'scripts/report.sh'), *args], text=True)

            output = report('--markdown')
            for name in cases:
                self.assertIn(f'| {name} | codex | none | done | null | 1 | 1.000 | null |', output)
            self.assertIn('Tâches fusionnées : 0/5', output)

    def test_events_listing_since_and_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / 'template/scripts', root / 'scripts')
            (root / '.workspace').mkdir()
            rows = [
                ('2026-10-01T10:00:00+00:00', 'a', 'prepared', {'branch': 'workspace-task/a', 'base_commit': 'abc'}),
                ('2026-10-02T10:00:00+00:00', 'b', 'delegate_started', {'agent': 'codex', 'isolation': 'exchange'}),
                ('2026-10-03T10:00:00+00:00', 'a', 'closed', None),
            ]
            (root / '.workspace/events.jsonl').write_text(
                '{broken\n'
                + ''.join(json.dumps(dict(at=at, task_id=task, event=event, detail=detail)) + '\n' for at, task, event, detail in rows),
                encoding='utf-8',
            )

            def report(*args):
                return subprocess.check_output(['bash', str(root / 'scripts/report.sh'), '--events', *args], text=True)

            self.assertEqual(
                report().splitlines(),
                [
                    '2026-10-01T10:00:00+00:00  a  prepared  {"base_commit":"abc","branch":"workspace-task/a"}',
                    '2026-10-02T10:00:00+00:00  b  delegate_started  {"agent":"codex","isolation":"exchange"}',
                    '2026-10-03T10:00:00+00:00  a  closed  -',
                ],
            )
            self.assertEqual(len(report('--task', 'a').splitlines()), 2)
            self.assertEqual(len(report('--since', '2026-10-02').splitlines()), 2)
            self.assertEqual(report('--since', '2099-01-01'), '')
            table = report('--markdown', '--task', 'b').splitlines()
            self.assertEqual(table[0], '| Date | Tâche | Événement | Détail |')
            self.assertEqual(table[2], '| 2026-10-02T10:00:00+00:00 | b | delegate_started | {"agent":"codex","isolation":"exchange"} |')
            self.assertEqual(report('--task', 'none'), '')

    def test_defects_weekly_table_and_last_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / 'template/scripts', root / 'scripts')
            (root / '.workspace').mkdir()
            rows = [
                ('2026-10-19T08:00:00+00:00', 'a', 'merged', None),
                ('2026-10-19T09:00:00+00:00', 'a', 'note', {'kind': 'defect', 'label': 'quota'}),
                ('2026-10-12T09:00:00+00:00', 'a', 'note', {'kind': 'defect', 'label': 'ancien'}),
            ]
            (root / '.workspace/events.jsonl').write_text(
                ''.join(json.dumps(dict(at=at, task_id=task, event=event, detail=detail)) + '\n' for at, task, event, detail in rows),
                encoding='utf-8',
            )
            (root / 'docs').mkdir()
            (root / 'docs/LOG.md').write_text('## [2026-10-19] feat | quota\n- [défaut après fusion] quota\n', encoding='utf-8')

            def report(*args):
                return subprocess.check_output(['bash', str(root / 'scripts/report.sh'), '--defauts', *args], text=True)

            output = report('--markdown')
            # The 2026-10-12 note precedes the merge of task a: not counted, so no W42 row.
            self.assertEqual(
                output.splitlines()[:3], ['| Semaine | Journal | LOG | Total |', '| --- | --- | --- | --- |', '| 2026-W43 | 1 | 1 | 2 |']
            )
            self.assertRegex(output.splitlines()[-1], r'^Défauts trouvés après fusion cette semaine : \d+$')
            filtered = report('--since', '2026-10-19', '--markdown').splitlines()
            self.assertEqual(filtered[2], '| 2026-W43 | 1 | 1 | 2 |')
            self.assertEqual(len(filtered), 4)
            none = report('--since', '2026-10-20', '--markdown').splitlines()
            self.assertEqual(none[:2], ['| Semaine | Journal | LOG | Total |', '| --- | --- | --- | --- |'])
            self.assertEqual(len(none), 3)

    def test_defects_empty_without_journal_or_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / 'template/scripts', root / 'scripts')
            output = subprocess.check_output(['bash', str(root / 'scripts/report.sh'), '--defauts', '--markdown'], text=True)
            self.assertEqual(
                output.splitlines(),
                ['| Semaine | Journal | LOG | Total |', '| --- | --- | --- | --- |', 'Défauts trouvés après fusion cette semaine : 0'],
            )
