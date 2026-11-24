"""T123 : un `verify_failed` de la délégation, rouge sur la base aussi dans le conteneur, n'est pas une erreur de l'exécutant.

Faux circuit de test_orchestrator_run, plus un faux `scripts/sandbox.sh` qui journalise ses appels dans
`.workspace/fake/calls.log` et imprime un bloc `FAIL: …` tant que le marqueur `env-broken` existe."""

import json
import os
import sys
import unittest
from pathlib import Path

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel et sous-processus ; suite complète seulement')

try:
    from tests.test_orchestrator_run import ProjectCase
except ImportError:
    from test_orchestrator_run import ProjectCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'orchestrator'))

import base_replay  # noqa: E402
import triage  # noqa: E402

RED_TEST = 'tests.test_sandbox.SandboxTests.test_default_image'
RED_BLOCK = f'======\nFAIL: test_default_image ({RED_TEST})\n------\nAssertionError: rouge\n\nRan 4 tests in 0.1s\n\nFAILED (failures=1)\n'
VERIFY = 'python3 -m unittest tests.test_sandbox'
FICHE = f'---\nid: t1\nagent: bric\nallowed:\n  - src/**\nverify: {VERIFY}\n---\n# Fiche t1\n'
# Le conteneur de la base : la base doit exister pendant `exec`, `start` échoue si `start-fails` existe.
FAKE_SANDBOX = r"""#!/bin/bash
d=$(cd "$(dirname "$0")/.." && pwd)
fake="$d/.workspace/fake"
base="$d/.workspace/orchestration/tasks/$2/base"
mkdir -p "$fake"
case "$1" in
  start)
    echo "sandbox start $2 $3" >> "$fake/calls.log"
    if [ -e "$fake/start-fails" ]; then echo "docker indisponible" >&2; exit 1; fi
    [ -d "$base/.git" ] || { echo "base absente" >&2; exit 1; } ;;
  exec)
    echo "sandbox exec $2 :: $6 (base $([ -d "$base/.git" ] && echo presente || echo absente))" >> "$fake/calls.log"
    if [ -e "$fake/env-broken" ]; then
      if [ -e "$fake/silent" ]; then echo "python3: command not found"; else printf '%s' "$(cat "$fake/red-block")"; fi
      exit 1
    fi
    echo "Ran 4 tests in 0.1s"; echo "OK" ;;
  stop)
    echo "sandbox stop $2" >> "$fake/calls.log" ;;
esac
"""


class EnvFailureCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write('scripts/sandbox.sh', FAKE_SANDBOX)
        self.write('fiches/t1.md', FICHE)
        self.write('docs/QUESTIONS.md', '# Questions\n\n')
        self.write('.workspace/fake/red-block', RED_BLOCK)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'faux conteneur')
        self.marker = self.root / '.workspace' / 'fake' / 'env-broken'
        self.control(outcomes={'t1': ['verify_failed']})

    def verify_txt(self, text=RED_BLOCK):
        self.write('.workspace/run/delegate/t1/verify.txt', text)

    def sandbox_calls(self):
        return [line for line in self.calls() if line.startswith('sandbox ')]

    def steps_called(self):
        return [line for line in self.calls() if not line.startswith(('sandbox ', 'report', 'claude'))]

    def base_dir(self):
        return self.root / '.workspace' / 'orchestration' / 'tasks' / 't1' / 'base'

    def record_state(self):
        """État de tâche à la main (sans faux circuit) : base = HEAD."""
        state = {'status': 'prepared', 'base_commit': self.git('rev-parse', 'HEAD')}
        self.write('.workspace/orchestration/tasks/t1/state.json', json.dumps(state))

    def replay(self, command=VERIFY):
        log = self.root / '.workspace' / 'replay.log'
        return base_replay.replay(self.root, 't1', command, log, lambda pid: None), log


class PlanTests(EnvFailureCase):
    def test_red_on_the_base_too_counts_nothing_and_waits_for_owner(self):
        self.marker.write_text('1\n')
        self.verify_txt()
        done = self.run_cli()
        self.assertEqual(done.returncode, 4, done.stderr + done.stdout)
        entry = self.journal()['tasks']['t1']
        self.assertEqual((entry['failures'], entry['verify_category'], entry['state']), (0, 'environnement', 'awaiting_user'))
        # Pas de diagnostic ni de seconde délégation ; le clone est fermé (`close`), la fiche repart de zéro à la relance.
        self.assertEqual(self.steps_called(), ['prepare t1', 'delegate t1', 'close t1'])
        self.assertNotIn('claude sonnet', self.calls())
        self.assertEqual(
            self.sandbox_calls(),
            ['sandbox start t1 --base', f'sandbox exec t1 :: {VERIFY} (base presente)', 'sandbox stop t1'],
        )
        self.assertFalse(self.base_dir().exists())
        self.assertEqual(entry['notice'], '.workspace/orchestrator-run/ENVIRONNEMENT-t1.md')
        notice = (self.root / entry['notice']).read_text()
        self.assertIn(RED_TEST, notice)
        self.assertIn('t1-verify_base_container.log', notice)
        self.assertTrue((self.root / entry['patch']).is_file())
        self.assertEqual(entry['steps'].get('prepare'), None)
        log = self.root / '.workspace' / 'orchestrator-run' / 't1-verify_base_container.log'
        self.assertIn(RED_TEST, log.read_text())

    def test_rerun_after_the_fix_prepares_and_delegates_again(self):
        self.marker.write_text('1\n')
        self.verify_txt()
        self.assertEqual(self.run_cli().returncode, 4)
        self.marker.unlink()
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.steps_called(), ['prepare t1', 'delegate t1', 'close t1', 'prepare t1 retry', 'delegate t1', 'merge t1'])
        self.assertEqual(self.journal()['tasks']['t1']['failures'], 0)

    def test_green_base_stays_the_executors_error(self):
        self.verify_txt()
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        entry = self.journal()['tasks']['t1']
        self.assertEqual(entry['failures'], 1)
        self.assertIn('claude sonnet', self.calls())
        self.assertEqual([c.split()[1] for c in self.sandbox_calls()], ['start', 'exec', 'stop'])
        self.assertFalse(self.base_dir().exists())

    def test_new_red_test_is_the_executors_error(self):
        self.marker.write_text('1\n')
        self.verify_txt(RED_BLOCK.replace('test_default_image', 'test_new_one').replace(RED_TEST, 'tests.test_x.A.test_new_one'))
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.journal()['tasks']['t1']['failures'], 1)

    def test_without_verify_txt_no_container_is_started(self):
        self.marker.write_text('1\n')
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.sandbox_calls(), [])
        self.assertEqual(self.journal()['tasks']['t1']['failures'], 1)

    def test_a_fiche_without_verify_field_is_left_alone(self):
        self.write('fiches/t1.md', FICHE.replace(f'verify: {VERIFY}\n', ''))
        self.git('commit', '-q', '-am', 'sans verify')
        self.marker.write_text('1\n')
        self.verify_txt()
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(self.sandbox_calls(), [])


class ReplayTests(EnvFailureCase):
    def test_replay_runs_start_exec_stop_and_always_removes_the_base(self):
        self.record_state()
        self.marker.write_text('1\n')
        result, _log = self.replay()
        self.assertEqual(result[0], 1)
        self.assertIn(RED_TEST, result[1])
        self.assertEqual([c.split()[1] for c in self.sandbox_calls()], ['start', 'exec', 'stop'])
        self.assertFalse(self.base_dir().exists())
        self.assertEqual(base_replay.base_dir(self.root, 't1'), self.base_dir())

    def test_unknown_base_runs_nothing(self):
        result, _log = self.replay()
        self.assertIsNone(result)
        self.assertEqual(self.sandbox_calls(), [])
        self.assertFalse(self.base_dir().exists())

    def test_a_start_that_fails_gives_none_without_stop(self):
        self.record_state()
        (self.root / '.workspace' / 'fake' / 'start-fails').write_text('1\n')
        result, log = self.replay()
        self.assertIsNone(result)
        self.assertEqual(self.sandbox_calls(), ['sandbox start t1 --base'])
        self.assertIn('docker indisponible', log.read_text())
        self.assertFalse(self.base_dir().exists())

    def test_a_leftover_base_folder_is_replaced_and_removed(self):
        self.record_state()
        self.write('.workspace/orchestration/tasks/t1/base/reste.txt', 'ancien\n')
        result, _log = self.replay()
        self.assertEqual(result[0], 0)
        self.assertFalse(self.base_dir().exists())

    def test_category_without_verify_txt_is_the_executor_and_starts_nothing(self):
        self.record_state()
        log = self.root / '.workspace' / 'replay.log'
        found, detail = base_replay.category(self.root, 't1', 'fiches/t1.md', VERIFY, log, None)
        self.assertEqual((found, detail['red']), (triage.EXECUTOR, []))
        self.verify_txt('   \n')
        self.assertEqual(base_replay.category(self.root, 't1', 'fiches/t1.md', VERIFY, log, None)[0], triage.EXECUTOR)
        self.assertEqual(self.sandbox_calls(), [])

    def test_category_is_the_environment_when_every_red_test_is_red_on_the_base(self):
        self.record_state()
        self.marker.write_text('1\n')
        self.verify_txt()
        log = self.root / '.workspace' / 'replay.log'
        found, detail = base_replay.category(self.root, 't1', 'fiches/t1.md', VERIFY, log, None)
        self.assertEqual((found, detail['container'], detail['base_red']), (triage.ENVIRONMENT, True, [RED_TEST]))

    def test_a_base_that_fails_without_a_named_test_is_the_environment(self):
        self.record_state()
        self.marker.write_text('1\n')
        (self.root / '.workspace' / 'fake' / 'silent').write_text('1\n')
        self.verify_txt()
        log = self.root / '.workspace' / 'replay.log'
        self.assertEqual(base_replay.category(self.root, 't1', 'fiches/t1.md', VERIFY, log, None)[0], triage.ENVIRONMENT)

    def test_the_fiche_category_of_the_host_is_not_kept(self):
        # Un test rouge neuf dans un fichier figé : `triage.classify` dirait « fiche » ; ici c'est l'exécutant.
        self.record_state()
        self.write('tests/test_frozen.py', '# test figé\n')
        self.verify_txt('FAIL: test_report_order (tests.test_frozen.FrozenTests.test_report_order)\n')
        log = self.root / '.workspace' / 'replay.log'
        self.assertEqual(base_replay.category(self.root, 't1', 'fiches/t1.md', VERIFY, log, None)[0], triage.EXECUTOR)

    def test_verify_command_reads_the_header_field(self):
        self.assertEqual(base_replay.verify_command(self.root, 'fiches/t1.md'), VERIFY)
        self.assertIsNone(base_replay.verify_command(self.root, 'fiches/absente.md'))


class NoticeTests(EnvFailureCase):
    def test_notice_cites_the_container_log_only_for_a_container_replay(self):
        detail = {'red': [], 'base_red': [], 'new': [], 'frozen': []}
        plain = (self.root / triage.notice(self.root, 't1', triage.ENVIRONMENT, detail, 'fiches/t1.md')).read_text()
        self.assertNotIn('verify_base_container', plain)
        container = (
            self.root / triage.notice(self.root, 't1', triage.ENVIRONMENT, dict(detail, container=True), 'fiches/t1.md')
        ).read_text()
        self.assertIn('t1-verify_base_container.log', container)
        self.assertIn('t1-verify_base.log', container)


if __name__ == '__main__':
    unittest.main()
