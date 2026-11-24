import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.orchestrator import report

FAKE_DEFECTS = '''#!/bin/sh
echo "bruit"
echo "Défauts trouvés après fusion cette semaine : 1"
echo "Défauts trouvés après fusion cette semaine : 3"
'''

FAKE_CHECK = '''#!/bin/sh
printf 'argv=%s\\n' "$*" >> check-record.txt
echo "relecture faite"
exit 3
'''


def run_dict(**changes):
    run = {
        'session': 'S1',
        'by': 'owner',
        'model': 'claude-haiku-5-5',
        'plan': 'docs/PLAN.md',
        'started_at': '2026-10-18T08:00:00Z',
        'finished_at': '2026-10-18T10:00:00Z',
        'status': 'finished',
        'stop_reason': None,
        'resumes': 0,
        'current': None,
        'order': ['T1', 'T2', 'T3'],
        'done': ['T1'],
        'failed': ['T2'],
        'decisions': [],
        'report_check_enabled': True,
        'tasks': {
            'T1': {'fiche': 'f1.md', 'agent': 'bric', 'reason': '', 'merge_commit': 'abcdef0123', 'steps': {'verify': {'status': 'done'}}},
            'T2': {'fiche': 'f2.md', 'agent': 'claude', 'reason': 'tests rouges | deux fois', 'steps': {'verify': {'status': 'failed'}}},
            'T3': {'fiche': 'f3.md', 'agent': 'bric', 'reason': '', 'steps': {}},
        },
    }
    run.update(changes)
    return run


def write_status(root, task_id, data):
    folder = Path(root) / '.workspace' / 'run' / 'delegate' / task_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'status.json').write_text(json.dumps(data), encoding='utf-8')


class ReportTests(unittest.TestCase):
    def setUp(self):
        base = os.environ.get('WORKSPACE_TEST_TMP') or None
        self.tmp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'depot'
        scripts = self.root / 'scripts'
        scripts.mkdir(parents=True)
        (scripts / 'report.sh').write_text(FAKE_DEFECTS, encoding='utf-8')
        # Le dépôt de test ne doit jamais tomber sur un dépôt Git englobant (ceiling : au-dessus de base).
        identity = {
            'GIT_AUTHOR_NAME': 'Test',
            'GIT_AUTHOR_EMAIL': 'test@example.org',
            'GIT_COMMITTER_NAME': 'Test',
            'GIT_COMMITTER_EMAIL': 'test@example.org',
            'GIT_CEILING_DIRECTORIES': str(self.base),
        }
        env = mock.patch.dict(os.environ, identity)
        env.start()
        self.addCleanup(env.stop)

    def git(self, *args):
        subprocess.run(['git', *args], cwd=str(self.root), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def init_repo(self):
        self.git('init', '-b', 'main')
        self.git('add', '-A')
        self.git('commit', '-m', 'base')

    def sections(self, text):
        return [line for line in text.splitlines() if line.startswith('## ')]

    def test_structure_and_defects_line(self):
        text = report.build(self.root, run_dict())
        lines = [line for line in text.splitlines() if line]
        self.assertTrue(lines[0].startswith("# Rapport de l'orchestrateur — S1 (2026-10-18)"))
        self.assertEqual(lines[1], report.DEFECTS_PREFIX + '3')
        self.assertEqual(
            self.sections(text),
            [
                '## Travaux',
                '## Délégations',
                "## Défauts rencontrés et décisions prises à la place du propriétaire",
                '## Tokens par fournisseur et par rôle',
                '## État des dépôts',
                "## Ce que l'orchestrateur ferait ensuite",
            ],
        )
        self.assertTrue(text.endswith('\n'))

    def test_defects_missing_script_or_failure(self):
        (self.root / 'scripts' / 'report.sh').unlink()
        self.assertIn(report.DEFECTS_PREFIX + 'non mesuré', report.build(self.root, run_dict()))
        (self.root / 'scripts' / 'report.sh').write_text('exit 2\n', encoding='utf-8')
        self.assertIn(report.DEFECTS_PREFIX + 'non mesuré', report.build(self.root, run_dict()))

    def test_defects_timeout(self):
        (self.root / 'scripts' / 'report.sh').write_text('sleep 5\n', encoding='utf-8')
        with mock.patch.object(report, 'DEFECTS_TIMEOUT', 0.5):
            self.assertIn(report.DEFECTS_PREFIX + 'non mesuré', report.build(self.root, run_dict()))

    def test_identity_paragraph_with_stop(self):
        run = run_dict(status='stopped', stop_reason='quota', finished_at=None, resumes=2)
        text = report.build(self.root, run)
        self.assertIn(
            "Programme `tools/orchestrator/run.py`, session `S1` lancée par `owner`, modèle "
            "`claude-haiku-5-5`, plan `docs/PLAN.md`. Début 2026-10-18T08:00:00Z, fin non terminé, "
            "reprises après interruption : 2. Issue : stopped (quota).",
            text,
        )

    def test_travaux_rows(self):
        run = run_dict(current={'task': 'T3', 'step': 'delegate'})
        text = report.build(self.root, run)
        self.assertIn('| T1 | bric | `abcdef0` | délégation `non mesuré`, vérification hôte verte, fusion `abcdef0` |', text)
        self.assertIn('| T2 | claude | aucune | échec : tests rouges \\| deux fois |', text)
        self.assertIn('| T3 | bric | aucune | non terminée (étape delegate) |', text)

    def test_travaux_not_started_and_skipped_verify(self):
        run = run_dict(done=['T1'], failed=[], current=None)
        run['tasks']['T1']['steps'] = {'verify': {'status': 'skipped'}}
        text = report.build(self.root, run)
        self.assertIn('vérification hôte sautée', text)
        self.assertIn('| T2 | claude | aucune | non terminée (étape non commencée) |', text)

    def test_delegation_table_and_total(self):
        write_status(
            self.root,
            'T1',
            {
                'agent': 'bric',
                'outcome': 'merged',
                'attempts': 1,
                'metrics': {
                    'attempts': 2,
                    'duration_seconds': 983.6,
                    'tokens': {'input': 32, 'cached_input': 984797, 'cache_creation': 53690, 'output': 25077},
                },
            },
        )
        text = report.build(self.root, run_dict())
        self.assertIn('| T1 | bric | merged | 2 | 984 | 32 / 984 797 / 53 690 / 25 077 |', text)
        self.assertIn('| T2 | claude | non mesuré | non mesuré | non mesuré | non mesuré |', text)
        self.assertIn('| **Total** |  | 1 mesurées | 2 | 984 | 32 / 984 797 / 53 690 / 25 077 |', text)

    def test_delegation_without_metrics_uses_top_level_attempts(self):
        write_status(self.root, 'T1', {'outcome': 'merged', 'attempts': 3})
        text = report.build(self.root, run_dict())
        self.assertIn('| T1 | bric | merged | 3 | non mesuré | 0 / 0 / 0 / 0 |', text)

    def test_symlinked_status_is_absent(self):
        real = self.root / 'real.json'
        real.write_text(json.dumps({'outcome': 'merged', 'attempts': 1}), encoding='utf-8')
        folder = self.root / '.workspace' / 'run' / 'delegate' / 'T1'
        folder.mkdir(parents=True)
        os.symlink(str(real), str(folder / 'status.json'))
        text = report.build(self.root, run_dict())
        self.assertIn('| T1 | bric | non mesuré | non mesuré | non mesuré | non mesuré |', text)

    def test_default_order_and_decisions(self):
        run = run_dict(order=None, decisions=['relancé T2 à la main', 'fusion sans preuve'])
        text = report.build(self.root, run)
        self.assertLess(text.index('| T1 |'), text.index('| T3 |'))
        self.assertIn('1. relancé T2 à la main\n2. fusion sans preuve', text)
        self.assertIn("Aucune décision prise à la place du propriétaire par le programme.", report.build(self.root, run_dict()))

    def test_tokens_section(self):
        write_status(self.root, 'T1', {'metrics': {'tokens': {'output': 25077}}})
        text = report.build(self.root, run_dict())
        self.assertIn('Claude, exécution : sortie 25 077 tokens sur 1 délégations mesurées.', text)
        self.assertIn('Codex, relecture : coût dans le bloc ajouté en tête par `report-check.sh`.', text)
        off = report.build(self.root, run_dict(report_check_enabled=False))
        self.assertIn('Codex, relecture : vérification du rapport non lancée.', off)

    def test_repos_without_upstream(self):
        self.init_repo()
        text = report.build(self.root, run_dict())
        self.assertIn('Branche `main`, tête `', text)
        self.assertIn('0 fichiers modifiés non commités', text)
        self.assertIn('Écart avec la branche suivie : pas de branche suivie.', text)

    def test_repos_with_upstream_gap(self):
        self.init_repo()
        remote = self.base / 'remote.git'
        subprocess.run(
            ['git', 'init', '--bare', '-b', 'main', str(remote)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.git('remote', 'add', 'origin', str(remote))
        self.git('push', '-u', 'origin', 'main')
        (self.root / 'b.txt').write_text('b', encoding='utf-8')
        self.git('add', 'b.txt')
        self.git('commit', '-m', 'local')
        (self.root / 'c.txt').write_text('c', encoding='utf-8')
        text = report.build(self.root, run_dict())
        self.assertIn('1 fichiers modifiés non commités', text)
        self.assertIn('Écart avec la branche suivie : 0 en retard, 1 en avance.', text)

    def test_repos_outside_git_is_not_measured(self):
        text = report.build(self.root, run_dict())
        self.assertIn('Branche `non mesuré`, tête `non mesuré`, non mesuré fichiers modifiés', text)
        self.assertIn('Écart avec la branche suivie : pas de branche suivie.', text)

    def test_next_steps_when_stopped(self):
        run = run_dict(status='stopped', stop_reason='quota', current={'task': 'T3', 'step': 'delegate'}, done=[], failed=['T2'])
        text = report.build(self.root, run)
        self.assertIn('- reprendre `T2` : tests rouges | deux fois', text)
        self.assertIn("- relancer `python3 tools/orchestrator/run.py docs/PLAN.md` : il reprendra à l'étape delegate de `T3`", text)

    def test_next_steps_when_finished(self):
        text = report.build(self.root, run_dict(failed=[]))
        self.assertTrue(text.endswith('- Plan terminé ; rien à reprendre.\n'))

    def test_write_creates_parents_and_returns_absolute(self):
        target = report.write(self.root, run_dict(), 'docs/RAPPORTS/jour.md')
        self.assertEqual(target, self.root / 'docs' / 'RAPPORTS' / 'jour.md')
        self.assertTrue(target.read_text(encoding='utf-8').startswith('# Rapport'))
        report.write(self.root, run_dict(), str(target))
        self.assertTrue(target.is_file())

    def test_write_refuses_outside_and_symlink(self):
        with self.assertRaises(ValueError):
            report.write(self.root, run_dict(), '../hors.md')
        outside = self.base / 'hors-depot'
        outside.mkdir()
        os.symlink(str(outside), str(self.root / 'lien'))
        with self.assertRaises(ValueError):
            report.write(self.root, run_dict(), 'lien/rapport.md')
        self.assertFalse((outside / 'rapport.md').exists())

    def test_check_without_script_returns_127(self):
        log = self.root / '.workspace' / 'orchestrator-run' / 'report-check.log'
        self.assertEqual(report.check(self.root, 'docs/r.md'), 127)
        self.assertFalse(log.exists())

    def test_check_runs_script_and_appends_log(self):
        scripts = self.root / 'scripts'
        (scripts / 'report-check.sh').write_text(FAKE_CHECK, encoding='utf-8')
        report.write(self.root, run_dict(), 'docs/RAPPORTS/jour.md')
        log = self.root / '.workspace' / 'orchestrator-run' / 'report-check.log'
        self.assertEqual(report.check(self.root, 'docs/RAPPORTS/jour.md'), 3)
        self.assertEqual(report.check(self.root, 'docs/RAPPORTS/jour.md'), 3)
        self.assertEqual(log.read_text(encoding='utf-8').count('relecture faite'), 2)
        record = (self.root / 'check-record.txt').read_text(encoding='utf-8')
        self.assertIn('argv=docs/RAPPORTS/jour.md', record)
        self.assertEqual(oct(log.parent.stat().st_mode & 0o777), '0o700')

    def test_check_timeout_kills_script(self):
        (self.root / 'scripts' / 'report-check.sh').write_text('sleep 30\n', encoding='utf-8')
        report.write(self.root, run_dict(), 'docs/r.md')
        with mock.patch.dict(os.environ, {'ORCHESTRATOR_REPORT_CHECK_TIMEOUT': '0.5'}):
            self.assertEqual(report.check(self.root, 'docs/r.md'), 124)


if __name__ == '__main__':
    unittest.main()
