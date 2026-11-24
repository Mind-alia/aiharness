import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import consumption  # noqa: E402
import plan_check  # noqa: E402
import report_check  # noqa: E402
import task_cost  # noqa: E402

USAGE = dict(input_tokens=5, cache_read_input_tokens=7, cache_creation_input_tokens=11, output_tokens=13)


def completed(payload, code=0):
    return subprocess.CompletedProcess(['claude'], code, stdout=payload, stderr='')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def rows(self):
        path = self.root / '.workspace/metrics/consumption.jsonl'
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


class RecordClaudeTests(Base):
    def test_writes_counters_role_task_and_exit(self):
        output = dict(usage=USAGE, result='secret answer')
        self.assertTrue(consumption.record_claude(self.root, 'conseil', 'sonnet', 't1', output, duration=1.5, exit=0))
        row = self.rows()[0]
        self.assertEqual((row['role'], row['provider'], row['model'], row['task_id']), ('conseil', 'claude', 'sonnet', 't1'))
        self.assertEqual(row['tokens'], dict(input=5, cached_input=7, output=13, reasoning=None, cache_creation=11))
        self.assertIs(row['tokens_complete'], True)
        self.assertEqual((row['exit'], row['proxy']['duration_seconds']), (0, 1.5))

    def test_never_writes_response_content(self):
        consumption.record_claude(self.root, 'conseil', 'sonnet', 't1', dict(usage=USAGE, result='SECRET-ANSWER'))
        text = (self.root / '.workspace/metrics/consumption.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('SECRET-ANSWER', text)

    def test_model_usage_single_key_wins(self):
        consumption.record_claude(self.root, 'conseil', 'sonnet', 't1', dict(usage=USAGE, modelUsage={'claude-real-1': {}}))
        self.assertEqual(self.rows()[0]['model'], 'claude-real-1')

    def test_model_usage_several_keys_keeps_argument(self):
        consumption.record_claude(self.root, 'conseil', 'sonnet', 't1', dict(usage=USAGE, modelUsage={'a': {}, 'b': {}}))
        self.assertEqual(self.rows()[0]['model'], 'sonnet')

    def test_nonzero_exit_still_recorded(self):
        self.assertTrue(consumption.record_claude(self.root, 'conseil', 'sonnet', 't1', dict(usage=USAGE), exit=2))
        self.assertEqual(self.rows()[0]['exit'], 2)

    def test_missing_cache_creation_is_incomplete(self):
        usage = dict(USAGE)
        del usage['cache_creation_input_tokens']
        self.assertTrue(consumption.record_claude(self.root, 'conseil', 'sonnet', 't1', dict(usage=usage)))
        row = self.rows()[0]
        self.assertIsNone(row['tokens']['cache_creation'])
        self.assertIs(row['tokens_complete'], False)

    def test_invalid_counters_become_null(self):
        usage = dict(USAGE, input_tokens=-1, output_tokens='3', cache_read_input_tokens=True)
        consumption.record_claude(self.root, 'conseil', 'sonnet', 't1', dict(usage=usage))
        tokens = self.rows()[0]['tokens']
        self.assertEqual((tokens['input'], tokens['output'], tokens['cached_input']), (None, None, None))

    def test_non_dict_output_is_written_without_counters(self):
        self.assertTrue(consumption.record_claude(self.root, 'conseil', 'sonnet', 't1', ['x']))
        row = self.rows()[0]
        self.assertIs(row['tokens_complete'], False)
        self.assertIsNone(row['tokens']['input'])

    def test_unwritable_journal_returns_false(self):
        directory = self.root / '.workspace/metrics'
        directory.mkdir(parents=True)
        target = self.root / 'elsewhere.jsonl'
        target.write_text('')
        (directory / 'consumption.jsonl').symlink_to(target)
        self.assertFalse(consumption.record_claude(self.root, 'conseil', 'sonnet', 't1', dict(usage=USAGE)))
        self.assertEqual(target.read_text(), '')

    def test_invalid_role_returns_false(self):
        self.assertFalse(consumption.record_claude(self.root, 'inconnu', 'sonnet', 't1', dict(usage=USAGE)))


class PlanCheckAccountingTests(Base):
    def call(self, stdout, code=0, task_id='fiche-1'):
        with mock.patch.object(plan_check.subprocess, 'run', return_value=completed(stdout, code)):
            return plan_check._model_call(self.root, 'sonnet', 'prompt', task_id)

    def test_success_leaves_a_conseil_row(self):
        inner = json.dumps(dict(realisable=True))
        answer, reason = self.call(json.dumps(dict(result=inner, usage=USAGE)))
        self.assertEqual((answer, reason), (dict(realisable=True), None))
        row = self.rows()[0]
        self.assertEqual((row['role'], row['task_id'], row['exit']), ('conseil', 'fiche-1', 0))
        self.assertIsNotNone(row['proxy']['duration_seconds'])

    def test_failure_with_json_is_recorded_with_exit(self):
        answer, reason = self.call(json.dumps(dict(usage=USAGE)), code=1)
        self.assertIsNone(answer)
        self.assertEqual(self.rows()[0]['exit'], 1)

    def test_non_json_output_writes_nothing(self):
        answer, reason = self.call('not json')
        self.assertIsNone(answer)
        self.assertEqual(reason, 'réponse de claude illisible')
        self.assertEqual(self.rows(), [])

    def test_plan_uses_plan_task_id(self):
        self.assertEqual(plan_check.PLAN_TASK_ID, 'plan')
        (self.root / 'plan.md').write_text('# Plan\n', encoding='utf-8')
        inner = json.dumps(dict(realisable=True, contradictions=[]))
        with mock.patch.object(plan_check.subprocess, 'run', return_value=completed(json.dumps(dict(result=inner, usage=USAGE)))):
            result = plan_check.check_plan(self.root, self.root / 'plan.md')
        self.assertEqual(result['verdict'], 'bon')
        self.assertEqual(self.rows()[0]['task_id'], 'plan')

    def test_unwritable_journal_keeps_verdict(self):
        directory = self.root / '.workspace/metrics'
        directory.mkdir(parents=True)
        (directory / 'consumption.jsonl').symlink_to(self.root / 'elsewhere.jsonl')
        (self.root / 'plan.md').write_text('# Plan\n', encoding='utf-8')
        inner = json.dumps(dict(realisable=False, contradictions=['x']))
        with mock.patch.object(plan_check.subprocess, 'run', return_value=completed(json.dumps(dict(result=inner, usage=USAGE)))):
            result = plan_check.check_plan(self.root, self.root / 'plan.md')
        self.assertEqual(result['verdict'], 'à reprendre')


class ReportCheckAccountingTests(Base):
    def test_extract_records_relecture_row(self):
        lines = json.dumps(dict(citation='une affirmation'))
        payload = json.dumps(dict(result=lines, usage=USAGE))
        with mock.patch.object(report_check.subprocess, 'run', return_value=completed(payload)):
            claims, tokens = report_check.extract(self.root, 'consigne', 'r.md', 'texte', 5)
        self.assertEqual(len(claims), 1)
        row = self.rows()[0]
        self.assertEqual((row['role'], row['task_id'], row['model']), ('relecture', 'report-check-r', 'haiku'))

    def test_extract_records_before_failing(self):
        payload = json.dumps(dict(is_error=True, usage=USAGE))
        with mock.patch.object(report_check.subprocess, 'run', return_value=completed(payload, 1)):
            with self.assertRaises(report_check.Stop):
                report_check.extract(self.root, 'consigne', 'r.md', 'texte', 5)
        self.assertEqual(self.rows()[0]['exit'], 1)

    def test_extract_non_json_writes_nothing(self):
        with mock.patch.object(report_check.subprocess, 'run', return_value=completed('oops', 1)):
            with self.assertRaises(report_check.Stop):
                report_check.extract(self.root, 'consigne', 'r.md', 'texte', 5)
        self.assertEqual(self.rows(), [])

    def test_verify_relay_environment_carries_task_id(self):
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/codex.sh').write_text('')
        seen = {}

        def fake(*args, **kwargs):
            seen.update(kwargs['env'])
            return completed('')

        with mock.patch.object(report_check.subprocess, 'run', side_effect=fake):
            with mock.patch.object(report_check.delegate_run, 'read_relay_events', return_value=None):
                report_check.verify(self.root, 'codex', [str(self.root)], [dict(id='C01', citation='x')], 'regles', 'report-check-r')
        self.assertEqual((seen['WORKSPACE_ROLE'], seen['WORKSPACE_TASK_ID']), ('relecture', 'report-check-r'))


class RoleLinesTests(Base):
    def journal(self, *rows):
        directory = self.root / '.workspace/metrics'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'consumption.jsonl').write_text('\n'.join(json.dumps(row) for row in rows) + '\nnot json\n', encoding='utf-8')

    @staticmethod
    def row(provider, role, task_id='t1', **tokens):
        return dict(task_id=task_id, provider=provider, role=role, tokens=tokens)

    def test_invalid_id(self):
        with self.assertRaises(ValueError):
            task_cost.role_lines(self.root, 'Bad_Id')

    def test_no_calls(self):
        self.assertEqual(task_cost.role_lines(self.root, 't1'), ['rôles : aucun appel enregistré'])

    def test_lines_and_total(self):
        self.journal(
            self.row('claude', 'execution', input=1, cached_input=2, cache_creation=3, output=4),
            self.row('claude', 'execution', input=10, cached_input=20, cache_creation=30, output=40),
            self.row('codex', 'relecture', input=100, cached_input=60, output=5),
            self.row('claude', 'execution', task_id='other', input=999, cached_input=1, output=1),
        )
        self.assertEqual(
            task_cost.role_lines(self.root, 't1'),
            [
                'rôle execution claude : hors_cache=11, cache_lu=22, cache_cree=33, sortie=44, appels=2',
                'rôle relecture codex : hors_cache=40, cache_lu=60, cache_cree=-, sortie=5, appels=1',
                'total : hors_cache=51, cache_lu=82, cache_cree=33, sortie=49',
            ],
        )

    def test_codex_without_cached_input_is_unknown(self):
        self.journal(self.row('codex', 'relecture', input=100, output=5))
        self.assertEqual(
            task_cost.role_lines(self.root, 't1'),
            [
                'rôle relecture codex : hors_cache=?, cache_lu=?, cache_cree=-, sortie=5, appels=1',
                'total : hors_cache=?, cache_lu=?, cache_cree=0, sortie=5',
            ],
        )

    def test_one_missing_value_makes_sum_unknown(self):
        self.journal(
            self.row('claude', 'conseil', input=1, cached_input=2, cache_creation=3, output=4),
            self.row('claude', 'conseil', input=1, cached_input=2, output=4),
        )
        lines = task_cost.role_lines(self.root, 't1')
        self.assertIn('cache_cree=?', lines[0])
        self.assertIn('hors_cache=2', lines[0])

    def test_symlinked_journal_reads_nothing(self):
        directory = self.root / '.workspace/metrics'
        directory.mkdir(parents=True)
        target = self.root / 'real.jsonl'
        target.write_text(json.dumps(self.row('claude', 'conseil', input=1)) + '\n')
        (directory / 'consumption.jsonl').symlink_to(target)
        self.assertEqual(task_cost.role_lines(self.root, 't1'), ['rôles : aucun appel enregistré'])


class DollarTests(Base):
    """T102 : le prix donné par `claude -p` (`total_cost_usd`) entre au journal et dans `report.sh --task`."""

    def test_program_call_keeps_the_provider_price(self):
        consumption.record_claude(self.root, 'relecture', 'opus', 't1', dict(usage=USAGE, total_cost_usd=0.5625))
        consumption.record_claude(self.root, 'reprise', 'sonnet', 't1', dict(usage=USAGE, total_cost_usd='beaucoup'))
        rows = self.rows()
        self.assertEqual(rows[0]['cost_usd'], 0.5625)
        self.assertNotIn('cost_usd', rows[1])

    def test_invalid_cost_is_refused(self):
        for cost in (-1, float('nan'), '0.1', True):
            with self.assertRaises(ValueError):
                consumption.record(self.root, role='execution', provider='claude', cost_usd=cost)

    def test_executor_attempt_keeps_the_provider_price(self):
        import delegate_run

        task = {'id': 't1', 'agent': 'claude-sonnet', 'mode': 'write'}
        output = json.dumps(dict(result='fait', usage=USAGE, total_cost_usd=0.25, num_turns=3))
        delegate_run.claude_response(self.root, {'tokens': None}, task, 1, 0, output, '')
        row = self.rows()[0]
        self.assertEqual((row['role'], row['model'], row['cost_usd']), ('execution', 'sonnet', 0.25))

    @staticmethod
    def row(provider, role, **tokens):
        return dict(task_id='t1', provider=provider, role=role, tokens=tokens)

    def test_role_lines_add_every_priced_call(self):
        rows = [
            dict(self.row('claude', 'execution', input=1, cached_input=0, cache_creation=0, output=1), cost_usd=0.25),
            dict(self.row('claude', 'execution', input=1, cached_input=0, cache_creation=0, output=1), cost_usd=0.5),
            dict(self.row('claude', 'relecture', input=1, cached_input=0, cache_creation=0, output=1), cost_usd=0.125),
            self.row('codex', 'relecture', input=1, cached_input=0, output=1),
        ]
        directory = self.root / '.workspace/metrics'
        directory.mkdir(parents=True)
        (directory / 'consumption.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        lines = task_cost.role_lines(self.root, 't1')
        self.assertTrue(lines[0].endswith('appels=2, dollars=0.7500'), lines[0])
        self.assertTrue(lines[1].endswith('appels=1, dollars=0.1250'), lines[1])
        self.assertTrue(lines[2].endswith('appels=1'), lines[2])
        self.assertTrue(lines[3].endswith('dollars=0.8750+?'), lines[3])


if __name__ == '__main__':
    unittest.main()
