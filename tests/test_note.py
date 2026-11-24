"""Tests de template/scripts/note.sh : le vrai script, lancé dans un projet temporaire (aucun réseau)."""

import datetime
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / 'template/scripts'
ID_PATTERN = re.compile(r'^note-\d{8}T\d{6}Z-[0-9a-f]{6}$')
IGNORED = shutil.ignore_patterns('__tests__', '__pycache__')


class NoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        shutil.copytree(TEMPLATE, self.root / 'scripts', ignore=IGNORED)
        self.script = self.root / 'scripts/note.sh'
        self.path = self.root / '.workspace/events.jsonl'

    def run_note(self, *args):
        return subprocess.run(['sh', str(self.script), *args], capture_output=True, text=True, timeout=60)

    def start(self, *args):
        result = self.run_note('start', *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def rows(self):
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines()]

    def refused(self, code, *args):
        before = self.rows()
        result = self.run_note(*args)
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(len(result.stderr.strip().splitlines()), 1, result.stderr)
        self.assertEqual(self.rows(), before)
        return result.stderr

    def age(self, task_id, seconds):
        """Recule l'instant de départ de `seconds` pour mesurer une durée sans attendre."""
        rows = self.rows()
        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=seconds)
        for row in rows:
            if row['task_id'] == task_id:
                row['at'] = past.isoformat()
        self.path.write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in rows), encoding='utf-8')

    def test_start_prints_identifier_and_writes_started_event(self):
        task_id = self.start('Relecture du module', '--model', 'haiku', '--kind', 'check')
        self.assertRegex(task_id, ID_PATTERN)
        self.assertEqual(self.rows()[0]['task_id'], task_id)
        self.assertEqual(self.rows()[0]['event'], 'subagent_started')
        self.assertEqual(self.rows()[0]['detail'], {'label': 'Relecture du module', 'model': 'haiku', 'kind': 'check'})

    def test_start_defaults_model_to_null_and_kind_to_subagent(self):
        task_id = self.start('exploration')
        self.assertEqual(self.rows()[0]['detail'], {'label': 'exploration', 'model': None, 'kind': 'subagent'})
        self.assertEqual(self.rows()[0]['task_id'], task_id)

    def test_two_starts_give_two_identifiers(self):
        first = self.start('a')
        second = self.start('b')
        self.assertNotEqual(first, second)

    def test_label_keeps_accents_and_spaces(self):
        self.start('Vérification des accents, à la main')
        self.assertEqual(self.rows()[0]['detail']['label'], 'Vérification des accents, à la main')

    def test_end_writes_finished_event_with_copied_detail_and_duration(self):
        task_id = self.start('exploration', '--model', 'opus', '--kind', 'step')
        self.age(task_id, 125)
        result = self.run_note('end', task_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        finished = self.rows()[1]
        self.assertEqual(finished['event'], 'subagent_finished')
        self.assertEqual(finished['task_id'], task_id)
        self.assertEqual(finished['detail']['label'], 'exploration')
        self.assertEqual(finished['detail']['model'], 'opus')
        self.assertEqual(finished['detail']['kind'], 'step')
        self.assertEqual(finished['detail']['outcome'], 'done')
        self.assertIsInstance(finished['detail']['seconds'], int)
        self.assertGreaterEqual(finished['detail']['seconds'], 125)
        self.assertLess(finished['detail']['seconds'], 135)

    def test_end_takes_outcome(self):
        task_id = self.start('relecture')
        self.assertEqual(self.run_note('end', task_id, '--outcome', 'blocked').returncode, 0)
        self.assertEqual(self.rows()[1]['detail']['outcome'], 'blocked')

    def test_started_precedes_finished_in_journal(self):
        task_id = self.start('ordre')
        self.run_note('end', task_id)
        self.assertEqual([row['event'] for row in self.rows()], ['subagent_started', 'subagent_finished'])
        self.assertEqual({row['task_id'] for row in self.rows()}, {task_id})

    def test_end_unknown_identifier_is_refused(self):
        stderr = self.refused(1, 'end', 'note-20261019T013502Z-a1b2c3')
        self.assertIn('identifiant inconnu', stderr)

    def test_end_twice_is_refused(self):
        task_id = self.start('une fois')
        self.assertEqual(self.run_note('end', task_id).returncode, 0)
        stderr = self.refused(1, 'end', task_id)
        self.assertIn('déjà terminé', stderr)

    def test_unknown_or_missing_subcommand_is_refused(self):
        self.refused(64, 'stop', 'x')
        self.refused(64)

    def test_missing_argument_is_refused(self):
        self.refused(64, 'start')
        self.refused(64, 'end')

    def test_kind_outside_list_is_refused(self):
        self.refused(64, 'start', 'x', '--kind', 'robot')

    def test_empty_label_is_refused(self):
        self.refused(64, 'start', '')

    def test_label_length_limit(self):
        self.start('a' * 200)
        self.refused(64, 'start', 'a' * 201)

    def test_model_and_outcome_are_bounded(self):
        self.refused(64, 'start', 'x', '--model', '')
        self.refused(64, 'start', 'x', '--model', 'm' * 65)
        task_id = self.start('x', '--model', 'm' * 64)
        self.refused(64, 'end', task_id, '--outcome', '')
        self.refused(64, 'end', task_id, '--outcome', 'o' * 65)

    def test_control_characters_are_refused(self):
        self.refused(64, 'start', 'ligne\x01')
        self.refused(64, 'start', 'x', '--model', 'mod\x7fele')
        task_id = self.start('x')
        self.refused(64, 'end', task_id, '--outcome', 'fin\nprevue')
        self.assertEqual(len(self.rows()), 1)

    def merged(self, task_id, at='2026-10-19T08:00:00+00:00'):
        """Fusion verte fabriquée à la main, comme l'écrit l'orchestrateur."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(at=at, task_id=task_id, event='merged', detail=None)) + '\n')

    def test_defect_after_merge_writes_note_event_and_prints_identifier(self):
        self.merged('t06-quota')
        result = self.run_note('defect', 'quota recalculé à la baisse', '--task', 't06-quota')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 't06-quota\n')
        self.assertEqual(self.rows()[-1]['event'], 'note')
        self.assertEqual(self.rows()[-1]['task_id'], 't06-quota')
        self.assertEqual(self.rows()[-1]['detail'], {'kind': 'defect', 'label': 'quota recalculé à la baisse'})

    def test_two_defects_the_same_day_are_two_rows(self):
        self.merged('t06')
        self.assertEqual(self.run_note('defect', 'un', '--task', 't06').returncode, 0)
        self.assertEqual(self.run_note('defect', 'deux', '--task', 't06').returncode, 0)
        self.assertEqual([row['event'] for row in self.rows()], ['merged', 'note', 'note'])

    def test_defect_on_task_never_merged_is_refused(self):
        stderr = self.refused(1, 'defect', 'jamais', '--task', 'jamais')
        self.assertEqual(stderr.strip(), 'note: aucune fusion connue pour jamais')

    def test_defect_with_task_identifier_outside_pattern_is_refused(self):
        self.refused(64, 'defect', 'x', '--task', 'Bad_Id')
        self.refused(64, 'defect', 'x', '--task', '../t06')
        self.refused(64, 'defect', 'x', '--task', '')

    def test_defect_label_rules_are_those_of_start(self):
        self.merged('t06')
        self.refused(64, 'defect', '', '--task', 't06')
        self.refused(64, 'defect', 'a' * 201, '--task', 't06')
        self.refused(64, 'defect', 'ligne\x01', '--task', 't06')
        self.assertEqual(self.run_note('defect', 'a' * 200, '--task', 't06').returncode, 0)

    def test_defect_requires_task_option(self):
        self.refused(64, 'defect', 'sans tâche')

    def test_defect_is_counted_by_defects_module(self):
        self.merged('t06', at='2000-01-01T08:00:00+00:00')
        self.assertEqual(self.run_note('defect', 'signalé', '--task', 't06').returncode, 0)
        code = (
            'import sys; sys.path.insert(0, sys.argv[1]); import defects; from pathlib import Path; '
            'print(len(defects.from_events(Path(sys.argv[2]))))'
        )
        counted = subprocess.run(
            [sys.executable, '-I', '-c', code, str(self.root / 'scripts/lib'), str(self.root)], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(counted.returncode, 0, counted.stderr)
        self.assertEqual(counted.stdout, '1\n')

    def test_human_writes_intervention_and_prints_identifier(self):
        result = self.run_note('human', 'relecture du diff', '--task', 't06', '--minutes', '12')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 't06\n')
        self.assertEqual(self.rows()[-1]['event'], 'human_intervention')
        self.assertEqual(self.rows()[-1]['task_id'], 't06')
        self.assertEqual(self.rows()[-1]['detail'], {'label': 'relecture du diff', 'minutes': 12})

    def test_human_needs_no_merge_and_accepts_bounds(self):
        self.assertEqual(self.run_note('human', 'a', '--task', 't06', '--minutes', '1').returncode, 0)
        self.assertEqual(self.run_note('human', 'b' * 200, '--task', 't06', '--minutes', '1440').returncode, 0)
        self.assertEqual(len(self.rows()), 2)

    def test_human_minutes_must_be_decimal_integer_from_1_to_1440(self):
        for minutes in ('0', '1441', 'abc', '-5', '1.5', '', ' 5', '٥', '9' * 10000):
            with self.subTest(minutes=minutes):
                self.refused(64, 'human', 'x', '--task', 't06', '--minutes', minutes)
        self.refused(64, 'human', 'x', '--task', 't06')

    def test_human_label_and_task_rules(self):
        self.refused(64, 'human', '', '--task', 't06', '--minutes', '5')
        self.refused(64, 'human', 'a' * 201, '--task', 't06', '--minutes', '5')
        self.refused(64, 'human', 'ligne\x01', '--task', 't06', '--minutes', '5')
        self.refused(64, 'human', 'x', '--task', '../t1', '--minutes', '5')
        self.refused(64, 'human', 'x', '--minutes', '5')

    def test_report_task_counts_human_interventions_after_cost_line(self):
        report = [str(self.root / 'scripts/report.sh'), '--task', 't06']

        def run_report():
            result = subprocess.run(['sh', *report], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.splitlines()

        # Order: cost line, hidden tests line, reading line, human interventions line.
        self.assertEqual(run_report()[3], 'Interventions humaines : 0 intervention, 0 min')
        self.run_note('human', 'a', '--task', 't06', '--minutes', '10')
        lines = run_report()
        self.assertTrue(lines[0].startswith('coût t06 :'), lines[0])
        self.assertEqual(lines[1], 'tests cachés : non joués')
        self.assertTrue(lines[2].startswith("Lecture avant d'agir : "), lines[2])
        self.assertEqual(lines[3], 'Interventions humaines : 1 intervention, 10 min')
        self.run_note('human', 'b', '--task', 't06', '--minutes', '5')
        self.run_note('human', 'autre', '--task', 'autre', '--minutes', '99')
        self.assertEqual(run_report()[3], 'Interventions humaines : 2 interventions, 15 min')


if __name__ == '__main__':
    unittest.main()
