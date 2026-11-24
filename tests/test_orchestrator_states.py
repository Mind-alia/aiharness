"""États des fiches du programme d'orchestration (GT2) : `journal.transition`, passages par le faux circuit, `--status`."""

import copy
import hashlib
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'orchestrator'))

import journal  # noqa: E402

WAITING = 4
USAGE = 64
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')


def fresh(*ids):
    return {'tasks': {task_id: {'steps': {}} for task_id in ids}, 'order': list(ids)}


class TransitionTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(journal.STATES, ('queued', 'in_progress', 'blocked', 'awaiting_user', 'done', 'failed'))
        self.assertEqual(set(journal.TRANSITIONS), set(journal.STATES))
        self.assertEqual(journal.TRANSITIONS['queued'], {'in_progress'})
        self.assertEqual(journal.TRANSITIONS['in_progress'], {'awaiting_user', 'blocked', 'done', 'failed'})
        self.assertEqual((journal.TRANSITIONS['done'], journal.TRANSITIONS['failed']), (set(), set()))

    def test_legacy_entry_is_queued_and_accepts_a_transition(self):
        data = fresh('a')
        journal.transition(data, 'a', 'in_progress', 'prepare')
        entry = data['tasks']['a']
        self.assertEqual(entry['state'], 'in_progress')
        self.assertEqual(len(entry['transitions']), 1)
        record = entry['transitions'][0]
        self.assertEqual(sorted(record), ['at', 'from', 'reason', 'to'])
        self.assertEqual((record['from'], record['to'], record['reason']), ('queued', 'in_progress', 'prepare'))
        self.assertRegex(record['at'], STAMP)

    def test_same_state_adds_nothing(self):
        data = fresh('a')
        journal.transition(data, 'a', 'in_progress', 'prepare')
        before = copy.deepcopy(data)
        journal.transition(data, 'a', 'in_progress', 'delegate')
        self.assertEqual(data, before)

    def test_refused_transitions_leave_data_intact(self):
        data = fresh('a')
        journal.transition(data, 'a', 'in_progress', 'x')
        journal.transition(data, 'a', 'done', 'fusionnée')
        before = copy.deepcopy(data)
        with self.assertRaisesRegex(ValueError, 'done.*in_progress'):
            journal.transition(data, 'a', 'in_progress', 'x')
        for to, reason in (('nowhere', 'x'), ('done', ''), ('done', None), ('done', '  ')):
            with self.assertRaises(ValueError):
                journal.transition(data, 'a', to, reason)
        self.assertEqual(data, before)
        other = fresh('b')
        with self.assertRaisesRegex(ValueError, 'queued.*done'):
            journal.transition(other, 'b', 'done', 'x')
        self.assertEqual(other, fresh('b'))

    def test_new_journal_gives_each_task_its_own_state(self):
        plan = {
            'path': 'p.md',
            'sha256': 'x',
            'session': 'S',
            'by': 'A',
            'model': 'opus',
            'report_check': True,
            'report': 'r.md',
            'tasks': [{'id': 'a', 'fiche': 'a.md'}, {'id': 'b', 'fiche': 'b.md'}],
        }
        data = journal.new(plan)
        self.assertEqual(data['schema_version'], 1)
        journal.transition(data, 'a', 'in_progress', 'prepare')
        self.assertEqual((data['tasks']['a']['state'], data['tasks']['b']['state']), ('in_progress', 'queued'))
        self.assertEqual(data['tasks']['b']['transitions'], [])

    def test_status_lines(self):
        data = fresh('a', 'b')
        data.update(plan='p.md', status='stopped', stop_reason=None, resumes=2)
        data['tasks']['a']['steps'] = {'prepare': {}, 'delegate': {}}
        journal.transition(data, 'a', 'in_progress', 'prepare')
        lines = journal.status_lines(data, './p.md', 'p.md')
        self.assertEqual(lines[0], 'plan : ./p.md ; passage : stopped (en cours) ; reprises : 2')
        first = lines[1].split('\t')
        self.assertEqual((first[0], first[1], first[2], first[4]), ('a', 'in_progress', 'delegate', 'prepare'))
        self.assertEqual(lines[2], 'b\tqueued\t-\t-\t-')
        self.assertEqual(journal.status_lines(None, 'p.md', 'p.md'), ['aucun passage pour p.md'])
        self.assertEqual(journal.status_lines(data, 'q.md', 'q.md'), ["journal d'un autre plan : p.md"])


try:
    from tests.test_orchestrator_phase_b import PhaseBCase
except ImportError:
    from test_orchestrator_phase_b import PhaseBCase


def moves(data, task_id):
    return [(item['from'], item['to'], item['reason']) for item in data['tasks'][task_id]['transitions']]


@unittest.skipIf(os.environ.get('WORKSPACE_TESTS') == 'fast', 'module lent : Git réel et sous-processus')
class PassTests(PhaseBCase):
    def state_events(self):
        return [e['detail'] for e in self.events() if e['event'] == 'note' and e['detail'].get('run') == 'state']

    def test_full_pass_goes_through_validation(self):
        name = self.plan('- fiche: fiches/t1.md')
        self.assertEqual(self.run_cli(plan=name).returncode, WAITING)
        data = self.journal()
        self.assertEqual(data['tasks']['t1']['state'], 'awaiting_user')
        self.assertEqual(moves(data, 't1'), [('queued', 'in_progress', 'prepare'), ('in_progress', 'awaiting_user', 'validation')])
        # Rien n'a changé : la fiche garde sa transition d'origine.
        self.assertEqual(self.run_cli(plan=name).returncode, WAITING)
        self.assertEqual(self.journal()['tasks']['t1']['transitions'], data['tasks']['t1']['transitions'])
        self.add(name, '- fusion validée: t1')
        self.assertEqual(self.run_cli(plan=name).returncode, 0)
        data = self.journal()
        self.assertEqual(data['tasks']['t1']['state'], 'done')
        self.assertEqual(
            moves(data, 't1')[2:], [('awaiting_user', 'in_progress', 'validation reçue'), ('in_progress', 'done', 'fusionnée')]
        )
        events = self.state_events()
        self.assertEqual([(e['task'], e['from'], e['to'], e['reason']) for e in events], [('t1',) + m for m in moves(data, 't1')])

    def test_straight_pass_and_status(self):
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr)
        data = self.journal()
        self.assertEqual(moves(data, 't1'), [('queued', 'in_progress', 'prepare'), ('in_progress', 'done', 'fusionnée')])
        path = self.root / '.workspace' / 'orchestrator-run.json'
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        shown = self.run_cli('--status')
        self.assertEqual(shown.returncode, 0, shown.stderr)
        lines = shown.stdout.splitlines()
        self.assertEqual(lines[0], 'plan : docs/plan.md ; passage : finished (en cours) ; reprises : 0')
        fields = lines[1].split('\t')
        self.assertEqual(
            (fields[0], fields[1], fields[3], fields[4]), ('t1', 'done', data['tasks']['t1']['transitions'][-1]['at'], 'fusionnée')
        )
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertTrue(shown.stdout.endswith('\n'))

    def tree(self):
        return sorted(str(path) for path in (self.root / '.workspace').rglob('*'))

    def test_status_writes_nothing_without_journal(self):
        before = self.tree()
        shown = self.run_cli('--status')
        self.assertEqual((shown.returncode, shown.stdout), (0, 'aucun passage pour docs/plan.md\n'))
        self.assertEqual(self.tree(), before)

    def test_status_with_another_plan_journal(self):
        self.assertEqual(self.run_cli().returncode, 0)
        other = self.plan('- fiche: fiches/t2.md', name='docs/other.md')
        shown = self.run_cli('--status', plan=other)
        self.assertEqual((shown.returncode, shown.stdout), (0, "journal d'un autre plan : docs/plan.md\n"))

    def test_status_usage_errors(self):
        before = self.tree()
        both = self.run_cli('--status', '--detach')
        self.assertEqual((both.returncode, both.stdout), (USAGE, ''))
        missing = self.run_cli('--status', plan='docs/absent.md')
        self.assertEqual((missing.returncode, missing.stdout), (USAGE, ''))
        self.assertEqual(self.tree(), before)

    def test_failures_end_in_failed_and_the_next_task_goes_on(self):
        self.control(outcomes={'t1': ['failed', 'failed']})
        name = self.plan('- fiche: fiches/t1.md', '- fiche: fiches/t2.md', '- fusion validée: t1', '- fusion validée: t2')
        self.assertEqual(self.run_cli(plan=name).returncode, 1)
        data = self.journal()
        self.assertEqual(data['tasks']['t1']['state'], 'failed')
        failed = [m for m in moves(data, 't1') if m[1] == 'failed']
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0][2])
        self.assertEqual(data['tasks']['t2']['state'], 'done')


if __name__ == '__main__':
    unittest.main()
