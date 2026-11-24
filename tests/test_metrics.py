"""Tests unitaires pour template/scripts/lib/metrics.py et metrics.sh."""

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
METRICS_PY = ROOT / 'template/scripts/lib/metrics.py'
METRICS_SH = ROOT / 'template/scripts/metrics.sh'


class MetricsTests(unittest.TestCase):
    def setUp(self):
        env_patch = mock.patch.dict(os.environ, {'WSC_NTFY_TOPIC': ''})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        # Copie de template/scripts dans root/scripts pour tester metrics.sh
        shutil.copytree(ROOT / 'template/scripts', self.root / 'scripts')

    def run_metrics(self, *args, claude_dir=None, env=None):
        cmd = [sys.executable, str(METRICS_PY), str(self.root)] + list(args)
        if claude_dir is not None:
            cmd.extend(['--claude-projects-dir', str(claude_dir)])
        run_env = env if env is not None else os.environ.copy()
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=run_env)
        return res

    def run_metrics_sh(self, *args):
        script = self.root / 'scripts/metrics.sh'
        cmd = ['bash', str(script)] + list(args)
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=self.root)
        return res

    def test_closed_questions_and_waiting_answer_outcome(self):
        for name, state in (('open', 'prepared'), ('closed', 'abandoned')):
            folder = self.root / '.workspace/orchestration/tasks' / name
            (folder / 'context/questions').mkdir(parents=True)
            (folder / 'context/questions/001.md').write_text('question')
            (folder / 'state.json').write_text(json.dumps({'status': state}))
        run = self.root / '.workspace/run/delegate/open'
        run.mkdir(parents=True)
        (run / 'status.json').write_text(json.dumps({'outcome': 'waiting_answer', 'attempts': 1}))
        result = self.run_metrics('--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['advisor']['pending_for_orchestrator'], 2)
        self.assertEqual(data['advisor']['unanswered_in_closed_tasks'], 1)
        self.assertEqual(data['delegation']['outcomes']['waiting_answer'], 1)

    def test_cache_creation_and_unknown_calls_survive_consumption_aggregation(self):
        folder = self.root / '.workspace/metrics'
        folder.mkdir(parents=True)
        records = [
            dict(
                at='2026-10-17T12:00:00Z',
                role='execution',
                provider='claude',
                tokens=dict(input=12, cached_input=7, cache_creation=5, output=3),
            ),
            dict(at='2026-10-17T12:00:01Z', role='execution', provider='claude', tokens=dict(input=12, cached_input=7, output=3)),
            dict(
                at='2026-10-17T12:00:02Z', role='execution', provider='codex', tokens=dict(input=12, cached_input=7, output=3, reasoning=2)
            ),
        ]
        (folder / 'consumption.jsonl').write_text('\n'.join(json.dumps(row) for row in records))
        result = self.run_metrics('--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        consumption = json.loads(result.stdout)['consumption']
        claude = consumption['by_provider']['claude']
        self.assertIsNone(claude['cache_creation'])
        self.assertIsNone(claude['total_tokens'])
        self.assertEqual(claude['unknown_token_calls'], 1)
        codex = consumption['by_provider']['codex']
        self.assertEqual(codex['cache_creation'], 0)
        self.assertEqual(codex['total_tokens'], 15)
        self.assertEqual(codex['unknown_token_calls'], 0)
        execution = consumption['by_role']['execution']
        self.assertIsNone(execution['total_tokens'])
        self.assertEqual(execution['unknown_token_calls'], 1)
        since = self.run_metrics('--json', '--since', '2026-10-18')
        self.assertEqual(json.loads(since.stdout)['consumption']['events'], 0)
        for flags in ((), ('--markdown',)):
            rendered = self.run_metrics(*flags)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertIn('Cache création', rendered.stdout)
            self.assertIn('Incomplets', rendered.stdout)
            if flags:
                self.assertIn('| execution | 3 | 36 | 21 | - | 9 | - | 1 | 0 | 0.0 |', rendered.stdout)

    def test_delegation_total_remains_unknown_if_one_claude_task_is_incomplete(self):
        for name, tokens in [
            ('complete', dict(input=12, cached_input=7, cache_creation=5, output=3)),
            ('legacy', dict(input=12, cached_input=7, output=3)),
        ]:
            folder = self.root / '.workspace/run/delegate' / name
            folder.mkdir(parents=True)
            (folder / 'status.json').write_text(
                json.dumps(
                    dict(
                        id=name,
                        agent='claude-haiku',
                        attempts=1,
                        outcome='done',
                        started_at='2026-10-17T12:00:00Z',
                        metrics=dict(duration_seconds=1, tokens=tokens),
                    )
                )
            )
        result = self.run_metrics('--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        agent = json.loads(result.stdout)['delegation']['by_agent']['claude-haiku']
        self.assertIsNone(agent['tokens'])

    def test_unanswered_closed_tasks_and_alerts(self):
        for name, status in [('open', 'active'), ('closed', 'merged'), ('gone', 'abandoned')]:
            task = self.root / '.workspace/orchestration/tasks' / name
            (task / 'context/questions').mkdir(parents=True)
            (task / 'context/questions/001.md').write_text('question')
            (task / 'state.json').write_text(json.dumps({'status': status}))
        result = self.run_metrics('--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        adv = json.loads(result.stdout)['advisor']
        self.assertEqual(adv['pending_for_orchestrator'], 3)
        self.assertEqual(adv['unanswered_in_closed_tasks'], 2)
        self.assertEqual(adv['alerts'], 3)

    def test_empty_project(self):  # noqa: PLR0915
        res = self.run_metrics('--json')
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)

        # Vérification exhaustive de l'interface commune
        self.assertEqual(data['project'], self.root.name)
        self.assertTrue(isinstance(data['generated_at'], str))
        self.assertIsNone(data['since'])

        # Délégation vide
        d = data['delegation']
        self.assertEqual(d['tasks'], 0)
        self.assertIsNone(d['first_try_success_rate'])
        self.assertIsNone(d['mean_attempts'])
        self.assertIsNone(d['mean_duration_seconds'])
        self.assertEqual(d['merged'], 0)
        self.assertEqual(d['abandoned'], 0)
        self.assertEqual(d['outcomes'], {})
        self.assertEqual(d['final_states'], {})
        self.assertEqual(d['by_agent'], {})

        # Conseiller vide
        adv = data['advisor']
        self.assertEqual(adv['questions'], 0)
        self.assertEqual(adv['answered'], 0)
        self.assertEqual(adv['unknown'], 0)
        self.assertEqual(adv['unavailable'], 0)
        self.assertEqual(adv['research'], 0)
        self.assertIsNone(adv['answer_rate'])
        self.assertIsNone(adv['mean_seconds'])
        self.assertEqual(adv['pending_for_orchestrator'], 0)

        # Tokens vides
        tok = data['tokens']
        cl = tok['claude']
        self.assertEqual(cl['messages'], 0)
        self.assertEqual(cl['input'], 0)
        self.assertEqual(cl['cache_creation'], 0)
        self.assertEqual(cl['cache_read'], 0)
        self.assertEqual(cl['output'], 0)
        self.assertEqual(cl['by_model'], {})

        cl_orch = tok['claude_orchestrator']
        self.assertEqual(cl_orch['messages'], 0)
        self.assertEqual(cl_orch['input'], 0)
        self.assertEqual(cl_orch['cache_creation'], 0)
        self.assertEqual(cl_orch['cache_read'], 0)
        self.assertEqual(cl_orch['output'], 0)
        self.assertEqual(cl_orch['by_model'], {})

        cx = tok['codex']
        self.assertEqual(cx['turns'], 0)
        self.assertEqual(cx['input'], 0)
        self.assertEqual(cx['cached_input'], 0)
        self.assertEqual(cx['output'], 0)
        self.assertEqual(cx['reasoning_output'], 0)

        # Consommation vide
        cons = data['consumption']
        self.assertEqual(cons['events'], 0)
        self.assertEqual(cons['by_role'], {})
        self.assertEqual(cons['by_provider'], {})
        self.assertEqual(cons['quota_limits'], 0)
        self.assertIsNone(cons['last_quota_limit'])

        # Git vide
        self.assertEqual(data['git'], {'commits': 0, 'merges': 0})

        # Banc d'essai vide
        self.assertEqual(data['bench'], {'advisor': None})

        # Vérification affichage texte et markdown sans plantage
        res_txt = self.run_metrics()
        self.assertEqual(res_txt.returncode, 0)
        self.assertIn("Tableau de bord", res_txt.stdout)

        res_md = self.run_metrics('--markdown')
        self.assertEqual(res_md.returncode, 0)
        self.assertIn("# Tableau de bord", res_md.stdout)

    def test_delegations(self):
        # Configuration de tâches de délégation
        # Tâche 1 : codex, 1 essai, done, durée par metrics (10s), tokens dict input/output, état merged
        # Tâche 2 : codex, 2 essais, done_with_warning, durée par ended_at - started_at (20s), tokens total (50), état abandoned
        # Tâche 3 : antigravity, 1 essai, failed, durée 30s, tokens None, pas d'état registre
        tasks_data = [
            {
                'id': 'task-1',
                'agent': 'codex',
                'attempts': 1,
                'outcome': 'done',
                'started_at': '2026-10-01T10:00:00Z',
                'ended_at': '2026-10-01T10:00:10Z',
                'metrics': {'duration_seconds': 10.0, 'tokens': {'input': 50, 'output': 50}},
                'registry_status': 'merged',
            },
            {
                'id': 'task-2',
                'agent': 'codex',
                'attempts': 2,
                'outcome': 'done_with_warning',
                'started_at': '2026-10-02T10:00:00Z',
                'ended_at': '2026-10-02T10:00:20Z',
                # pas de duration_seconds dans metrics -> calcul par ended_at - started_at = 20s
                'metrics': {'tokens': {'total': 50}},
                'registry_status': 'abandoned',
            },
            {
                'id': 'task-3',
                'agent': 'antigravity',
                'attempts': 1,
                'outcome': 'failed',
                'started_at': '2026-10-03T10:00:00Z',
                'ended_at': '2026-10-03T10:00:30Z',
                'metrics': {
                    'duration_seconds': 30.0,
                },
                'registry_status': None,
            },
        ]

        for item in tasks_data:
            d_dir = self.root / '.workspace/run/delegate' / item['id']
            d_dir.mkdir(parents=True, exist_ok=True)
            status_content = {
                'id': item['id'],
                'agent': item['agent'],
                'attempts': item['attempts'],
                'outcome': item['outcome'],
                'started_at': item['started_at'],
                'ended_at': item['ended_at'],
                'metrics': item['metrics'],
            }
            (d_dir / 'status.json').write_text(json.dumps(status_content), encoding='utf-8')

            if item['registry_status']:
                reg_dir = self.root / '.workspace/orchestration/tasks' / item['id']
                reg_dir.mkdir(parents=True, exist_ok=True)
                (reg_dir / 'state.json').write_text(
                    json.dumps({'task_id': item['id'], 'status': item['registry_status']}), encoding='utf-8'
                )

        res = self.run_metrics('--json')
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)['delegation']

        self.assertEqual(data['tasks'], 3)
        # Réussite 1er essai : task-1 réussit (attempts 1, done) ; task-2 a 2 tentatives ; task-3 a échoué
        # Donc 1 / 3
        self.assertAlmostEqual(data['first_try_success_rate'], 1 / 3, places=4)
        # Tentatives moyennes : (1 + 2 + 1) / 3 = 1.3333
        self.assertAlmostEqual(data['mean_attempts'], 4 / 3, places=4)
        # Durées moyennes : (10.0 + 20.0 + 30.0) / 3 = 20.0s
        self.assertAlmostEqual(data['mean_duration_seconds'], 20.0, places=4)
        self.assertEqual(data['merged'], 1)
        self.assertEqual(data['abandoned'], 1)
        self.assertEqual(data['outcomes'], {'done': 1, 'done_with_warning': 1, 'failed': 1})
        self.assertEqual(data['final_states'], {'abandoned': 1, 'merged': 1})

        # Détail par agent
        codex_ag = data['by_agent']['codex']
        self.assertEqual(codex_ag['tasks'], 2)
        self.assertAlmostEqual(codex_ag['first_try_success_rate'], 0.5)
        self.assertAlmostEqual(codex_ag['mean_attempts'], 1.5)
        self.assertAlmostEqual(codex_ag['mean_duration_seconds'], 15.0)
        self.assertEqual(codex_ag['tokens'], 150)  # 100 + 50

        anti_ag = data['by_agent']['antigravity']
        self.assertEqual(anti_ag['tasks'], 1)
        self.assertAlmostEqual(anti_ag['first_try_success_rate'], 0.0)
        self.assertAlmostEqual(anti_ag['mean_attempts'], 1.0)
        self.assertAlmostEqual(anti_ag['mean_duration_seconds'], 30.0)
        self.assertIsNone(anti_ag['tokens'])

    def test_advisor_and_answer_rate(self):
        task_dir = self.root / '.workspace/orchestration/tasks/task-a'
        adv_dir = task_dir / 'context/advisor'
        q_dir = task_dir / 'context/questions'
        a_dir = task_dir / 'context/answers'
        adv_dir.mkdir(parents=True, exist_ok=True)
        q_dir.mkdir(parents=True, exist_ok=True)
        a_dir.mkdir(parents=True, exist_ok=True)

        (adv_dir / '001.json').write_text(json.dumps({'status': 'answered', 'seconds': 1.0, 'at': '2026-10-01T10:00:00Z'}))
        (adv_dir / '002.json').write_text(json.dumps({'status': 'unknown', 'seconds': 2.0, 'at': '2026-10-01T10:05:00Z'}))
        (adv_dir / '003.json').write_text(json.dumps({'status': 'unavailable', 'seconds': 0.5, 'at': '2026-10-01T10:10:00Z'}))
        (adv_dir / '004.json').write_text(json.dumps({'status': 'research', 'seconds': 4.5, 'at': '2026-10-01T10:15:00Z'}))
        (adv_dir / '005.json').write_text(json.dumps({'status': 'error', 'seconds': 1.0, 'at': '2026-10-01T10:20:00Z'}))

        # Questions en attente : 001 répondu, 002 non répondu
        (q_dir / '001.md').write_text('Question 1')
        (a_dir / '001.md').write_text('Réponse 1')
        (q_dir / '002.md').write_text('Question 2 en attente')

        res = self.run_metrics('--json')
        self.assertEqual(res.returncode, 0)
        adv = json.loads(res.stdout)['advisor']

        self.assertEqual(adv['questions'], 5)
        self.assertEqual(adv['answered'], 1)
        self.assertEqual(adv['unknown'], 1)
        self.assertEqual(adv['unavailable'], 1)
        self.assertEqual(adv['research'], 1)
        # answer_rate = answered / (answered + unknown) = 1 / (1 + 1) = 0.5
        self.assertAlmostEqual(adv['answer_rate'], 0.5)
        # mean_seconds = (1.0 + 2.0 + 0.5 + 4.5 + 1.0) / 5 = 1.8
        self.assertAlmostEqual(adv['mean_seconds'], 1.8)
        self.assertEqual(adv['pending_for_orchestrator'], 1)

    def test_codex_tokens(self):
        cx_dir = self.root / '.workspace/run/codex'
        cx_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(
                {
                    'type': 'turn.completed',
                    'usage': {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 30, 'reasoning_output_tokens': 10},
                }
            ),
            'ligne illisible non json',
            json.dumps({'type': 'other.event', 'usage': {'input_tokens': 500}}),
            json.dumps(
                {
                    'type': 'turn.completed',
                    'usage': {'input_tokens': 200, 'cached_input_tokens': 40, 'output_tokens': 70, 'reasoning_output_tokens': 20},
                }
            ),
        ]
        (cx_dir / 'events.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')

        res = self.run_metrics('--json')
        self.assertEqual(res.returncode, 0)
        cx = json.loads(res.stdout)['tokens']['codex']

        self.assertEqual(cx['turns'], 2)
        self.assertEqual(cx['input'], 300)
        self.assertEqual(cx['cached_input'], 60)
        self.assertEqual(cx['output'], 100)
        self.assertEqual(cx['reasoning_output'], 30)

    def test_claude_tokens_dedup_cwd_and_secrecy(self):
        fake_claude = self.root / 'fake_claude_projects'
        p_dir = fake_claude / 'my_project'
        p_dir.mkdir(parents=True, exist_ok=True)

        canary_secret = "ULTRA_CONFIDENTIAL_KEY_123456789"

        # Ligne 1 : assistant, cwd dans self.root, msg_1
        # Ligne 2 : assistant, même msg_1 (dédoublonnage stream), usage identique
        # Ligne 3 : assistant, sous-dossier de self.root, msg_2, modèle différent
        # Ligne 4 : assistant, projet étranger -> ignoré
        # Ligne 5 : message utilisateur -> ignoré
        lines = [
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(self.root),
                    'message': {
                        'id': 'msg_1',
                        'model': 'claude-3-5-sonnet-20241022',
                        'usage': {
                            'input_tokens': 100,
                            'cache_creation_input_tokens': 10,
                            'cache_read_input_tokens': 20,
                            'output_tokens': 30,
                        },
                        'content': canary_secret,
                    },
                }
            ),
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(self.root),
                    'message': {
                        'id': 'msg_1',
                        'model': 'claude-3-5-sonnet-20241022',
                        'usage': {
                            'input_tokens': 100,
                            'cache_creation_input_tokens': 10,
                            'cache_read_input_tokens': 20,
                            'output_tokens': 30,
                        },
                        'content': canary_secret,
                    },
                }
            ),
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(self.root / 'sub_dir'),
                    'message': {
                        'id': 'msg_2',
                        'model': 'claude-3-5-haiku-20241022',
                        'usage': {'input_tokens': 50, 'cache_creation_input_tokens': 5, 'cache_read_input_tokens': 0, 'output_tokens': 15},
                        'content': 'un autre texte secret',
                    },
                }
            ),
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': '/tmp/unrelated_other_project',
                    'message': {
                        'id': 'msg_alien',
                        'model': 'claude-3-5-sonnet-20241022',
                        'usage': {'input_tokens': 1000, 'output_tokens': 1000},
                    },
                }
            ),
            json.dumps({'type': 'user', 'cwd': str(self.root), 'message': {'content': 'bonjour'}}),
        ]
        (p_dir / 'transcript.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')

        # Test sortie JSON
        res_json = self.run_metrics('--json', claude_dir=fake_claude)
        self.assertEqual(res_json.returncode, 0)
        data = json.loads(res_json.stdout)['tokens']['claude']

        # Dédoublonnage : msg_1 compté une seule fois, msg_2 compté une fois = 2 messages
        self.assertEqual(data['messages'], 2)
        self.assertEqual(data['input'], 150)
        self.assertEqual(data['cache_creation'], 15)
        self.assertEqual(data['cache_read'], 20)
        self.assertEqual(data['output'], 45)

        # Vérification par modèle
        self.assertEqual(data['by_model']['claude-3-5-sonnet-20241022']['messages'], 1)
        self.assertEqual(data['by_model']['claude-3-5-sonnet-20241022']['input'], 100)
        self.assertEqual(data['by_model']['claude-3-5-haiku-20241022']['messages'], 1)
        self.assertEqual(data['by_model']['claude-3-5-haiku-20241022']['input'], 50)

        # CONFIDENTIALITÉ STRICTE : le secret de contenu ne doit figurer dans AUCUNE sortie
        self.assertNotIn(canary_secret, res_json.stdout)

        res_txt = self.run_metrics(claude_dir=fake_claude)
        self.assertNotIn(canary_secret, res_txt.stdout)

        res_md = self.run_metrics('--markdown', claude_dir=fake_claude)
        self.assertNotIn(canary_secret, res_md.stdout)

    def test_since_filter(self):
        # Initialisation git
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), 'config', 'user.name', 'Test'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'config', 'user.email', 'test@test.com'], check=True)
        (self.root / 'dummy.txt').write_text('init')
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(
            ['git', '-C', str(self.root), 'commit', '-m', 'commit 1', '--date=2026-10-01T10:00:00Z'],
            check=True,
            env=dict(os.environ, GIT_COMMITTER_DATE='2026-10-01T10:00:00Z'),
        )
        (self.root / 'dummy.txt').write_text('update')
        subprocess.run(
            ['git', '-C', str(self.root), 'commit', '-am', 'commit 2', '--date=2026-10-03T10:00:00Z'],
            check=True,
            env=dict(os.environ, GIT_COMMITTER_DATE='2026-10-03T10:00:00Z'),
        )

        # Délégation : 1 ancienne (2026-10-01), 1 récente (2026-10-03)
        for name, dt in [('t-old', '2026-10-01T12:00:00Z'), ('t-new', '2026-10-03T12:00:00Z')]:
            d = self.root / '.workspace/run/delegate' / name
            d.mkdir(parents=True, exist_ok=True)
            (d / 'status.json').write_text(
                json.dumps(
                    {
                        'id': name,
                        'agent': 'codex',
                        'attempts': 1,
                        'outcome': 'done',
                        'started_at': dt,
                        'ended_at': dt,
                        'metrics': {'duration_seconds': 5.0},
                    }
                )
            )

        # Conseiller : 1 ancienne note, 1 récente
        adv_dir = self.root / '.workspace/orchestration/tasks/t-new/context/advisor'
        adv_dir.mkdir(parents=True, exist_ok=True)
        (adv_dir / '001.json').write_text(json.dumps({'status': 'answered', 'seconds': 1.0, 'at': '2026-10-01T12:00:00Z'}))
        (adv_dir / '002.json').write_text(json.dumps({'status': 'answered', 'seconds': 2.0, 'at': '2026-10-03T12:00:00Z'}))

        res = self.run_metrics('--json', '--since', '2026-10-02')
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)

        self.assertEqual(data['since'], '2026-10-02')
        self.assertEqual(data['delegation']['tasks'], 1)
        self.assertEqual(data['advisor']['questions'], 1)
        self.assertEqual(data['git']['commits'], 1)

    def test_snapshot_and_history(self):
        # 1er instantané
        res1 = self.run_metrics('--snapshot')
        self.assertEqual(res1.returncode, 0)
        path1_str = res1.stdout.strip()
        path1 = self.root / path1_str
        self.assertTrue(path1.is_file())
        self.assertFalse(path1.is_symlink())

        today = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d')
        self.assertIn(f"{today}.md", path1_str)

        # 2nd instantané : doit créer un fichier avec suffixe -2 sans écraser
        res2 = self.run_metrics('--snapshot')
        self.assertEqual(res2.returncode, 0)
        path2_str = res2.stdout.strip()
        path2 = self.root / path2_str
        self.assertTrue(path2.is_file())
        self.assertIn(f"{today}-2.md", path2_str)
        self.assertNotEqual(path1, path2)

        # Vérification permissions de history.jsonl et son dossier
        hist_dir = self.root / '.workspace/metrics'
        hist_file = hist_dir / 'history.jsonl'
        self.assertTrue(hist_dir.is_dir())
        self.assertTrue(hist_file.is_file())
        self.assertEqual(hist_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(hist_file.stat().st_mode & 0o777, 0o600)

        lines = hist_file.read_text(encoding='utf-8').strip().splitlines()
        self.assertEqual(len(lines), 2)
        rec = json.loads(lines[0])
        self.assertEqual(rec['project'], self.root.name)

        # Test affichage --history
        res_hist = self.run_metrics('--history')
        self.assertEqual(res_hist.returncode, 0)
        self.assertIn("Date", res_hist.stdout)
        self.assertIn("Tâches", res_hist.stdout)
        self.assertIn("Claude out", res_hist.stdout)
        self.assertIn("Quotas", res_hist.stdout)

        # Test affichage --history --markdown
        res_hist_md = self.run_metrics('--history', '--markdown')
        self.assertEqual(res_hist_md.returncode, 0)
        self.assertIn("| Date | Tâches |", res_hist_md.stdout)
        self.assertIn("Quotas", res_hist_md.stdout)

    def test_bench_advisor(self):
        bench_dir = self.root / '.workspace/metrics'
        bench_dir.mkdir(parents=True, exist_ok=True)
        bench_data = {'accuracy': 0.88, 'tested': 25}
        (bench_dir / 'advisor-bench.json').write_text(json.dumps(bench_data))

        res = self.run_metrics('--json')
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data['bench']['advisor'], bench_data)

    def test_consumption_journal(self):  # noqa: PLR0915
        metrics_dir = self.root / '.workspace/metrics'
        metrics_dir.mkdir(parents=True, exist_ok=True)
        consumption_file = metrics_dir / 'consumption.jsonl'

        # Journal factice :
        # - lignes corrompues (non JSON, JSON non-dict, missing role/provider)
        # - plusieurs rôles : orchestration, execution, conseil
        # - plusieurs fournisseurs : claude, codex, antigravity
        # - null et proxy : appel où tous les tokens sont null -> proxy_calls
        # - limites de quota : plusieurs atteintes, vérification de last_quota_limit
        # - horodatages pour test --since
        lines = [
            "ligne non JSON corrompue",
            "",
            "   ",
            json.dumps([1, 2, 3]),
            json.dumps({"at": "2026-10-01T10:00:00Z", "role": None, "provider": "codex"}),
            # 1: 2026-10-01 - orchestration / claude (tokens normaux)
            json.dumps(
                {
                    "at": "2026-10-01T10:00:00Z",
                    "role": "orchestration",
                    "provider": "claude",
                    "model": "claude-3-5-sonnet",
                    "tokens": {"input": 150, "cached_input": 30, "output": 50, "reasoning": None},
                    "proxy": {"turns": 1, "duration_seconds": 12.0},
                    "quota": {"limit_reached": False, "detail": None},
                }
            ),
            # 2: 2026-10-02 - execution / codex (tokens avec reasoning)
            json.dumps(
                {
                    "at": "2026-10-02T10:00:00Z",
                    "role": "execution",
                    "provider": "codex",
                    "model": "o3-mini",
                    "tokens": {"input": 200, "cached_input": 0, "output": 80, "reasoning": 20},
                    "proxy": {"turns": 1, "duration_seconds": 10.5},
                    "quota": {"limit_reached": False, "detail": None},
                }
            ),
            # 3: 2026-10-03 - execution / antigravity (tokens null -> compté en proxy_calls, quota atteint)
            json.dumps(
                {
                    "at": "2026-10-03T10:00:00Z",
                    "role": "execution",
                    "provider": "antigravity",
                    "model": None,
                    "tokens": {"input": None, "cached_input": None, "output": None, "reasoning": None},
                    "proxy": {"turns": 2, "duration_seconds": 7.5},
                    "quota": {"limit_reached": True, "detail": "Rate limit 429"},
                }
            ),
            # 4: 2026-10-04 - conseil / claude (quota atteint plus récent)
            json.dumps(
                {
                    "at": "2026-10-04T10:00:00Z",
                    "role": "conseil",
                    "provider": "claude",
                    "model": "claude-3-5-haiku",
                    "tokens": {"input": 300, "cached_input": 50, "output": 100, "reasoning": None},
                    "proxy": {"turns": 1, "duration_seconds": 5.0},
                    "quota": {"limit_reached": True, "detail": "Daily quota exceeded"},
                }
            ),
        ]
        consumption_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 1. Test JSON global
        res = self.run_metrics('--json')
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        cons = data['consumption']

        self.assertEqual(cons['events'], 4)
        self.assertEqual(cons['quota_limits'], 2)
        self.assertEqual(cons['last_quota_limit'], {"at": "2026-10-04T10:00:00Z", "provider": "claude", "detail": "Daily quota exceeded"})

        by_role = cons['by_role']
        self.assertIn('orchestration', by_role)
        self.assertIn('execution', by_role)
        self.assertIn('conseil', by_role)

        self.assertEqual(by_role['orchestration']['calls'], 1)
        self.assertEqual(by_role['orchestration']['input'], 150)
        self.assertEqual(by_role['orchestration']['cached_input'], 30)
        self.assertEqual(by_role['orchestration']['output'], 50)
        self.assertEqual(by_role['orchestration']['reasoning'], 0)
        self.assertEqual(by_role['orchestration']['proxy_calls'], 0)
        self.assertAlmostEqual(by_role['orchestration']['duration_seconds'], 12.0)

        self.assertEqual(by_role['execution']['calls'], 2)
        self.assertEqual(by_role['execution']['input'], 200)
        self.assertEqual(by_role['execution']['cached_input'], 0)
        self.assertEqual(by_role['execution']['output'], 80)
        self.assertEqual(by_role['execution']['reasoning'], 20)
        # 1 des 2 appels execution avait tous ses tokens null -> proxy_calls == 1
        self.assertEqual(by_role['execution']['proxy_calls'], 1)
        self.assertAlmostEqual(by_role['execution']['duration_seconds'], 18.0)

        self.assertEqual(by_role['conseil']['calls'], 1)
        self.assertEqual(by_role['conseil']['input'], 300)
        self.assertEqual(by_role['conseil']['cached_input'], 50)
        self.assertEqual(by_role['conseil']['output'], 100)
        self.assertEqual(by_role['conseil']['reasoning'], 0)
        self.assertEqual(by_role['conseil']['proxy_calls'], 0)
        self.assertAlmostEqual(by_role['conseil']['duration_seconds'], 5.0)

        by_provider = cons['by_provider']
        self.assertEqual(by_provider['claude']['calls'], 2)
        self.assertEqual(by_provider['claude']['input'], 450)
        self.assertEqual(by_provider['claude']['cached_input'], 80)
        self.assertEqual(by_provider['claude']['output'], 150)
        self.assertEqual(by_provider['claude']['proxy_calls'], 0)
        self.assertAlmostEqual(by_provider['claude']['duration_seconds'], 17.0)

        self.assertEqual(by_provider['codex']['calls'], 1)
        self.assertEqual(by_provider['codex']['input'], 200)
        self.assertEqual(by_provider['codex']['output'], 80)
        self.assertEqual(by_provider['codex']['reasoning'], 20)
        self.assertEqual(by_provider['codex']['proxy_calls'], 0)
        self.assertAlmostEqual(by_provider['codex']['duration_seconds'], 10.5)

        self.assertEqual(by_provider['antigravity']['calls'], 1)
        self.assertEqual(by_provider['antigravity']['input'], 0)
        self.assertEqual(by_provider['antigravity']['output'], 0)
        self.assertEqual(by_provider['antigravity']['proxy_calls'], 1)
        self.assertAlmostEqual(by_provider['antigravity']['duration_seconds'], 7.5)

        # 2. Test filtre --since
        res_since = self.run_metrics('--json', '--since', '2026-10-03')
        self.assertEqual(res_since.returncode, 0)
        data_since = json.loads(res_since.stdout)['consumption']
        self.assertEqual(data_since['events'], 2)
        self.assertEqual(sorted(data_since['by_role'].keys()), ['conseil', 'execution'])
        self.assertEqual(data_since['quota_limits'], 2)
        self.assertEqual(data_since['last_quota_limit']['provider'], 'claude')

        # 3. Test affichage texte
        res_txt = self.run_metrics()
        self.assertEqual(res_txt.returncode, 0)
        self.assertIn("Consommation par rôle :", res_txt.stdout)
        self.assertIn("orchestration", res_txt.stdout)
        self.assertIn("execution", res_txt.stdout)
        self.assertIn("conseil", res_txt.stdout)
        self.assertIn("Limites de quota atteintes : 2 (dernière : 2026-10-04, claude)", res_txt.stdout)

        # 4. Test affichage Markdown
        res_md = self.run_metrics('--markdown')
        self.assertEqual(res_md.returncode, 0)
        self.assertIn("## Consommation par rôle", res_md.stdout)
        self.assertIn(
            "| Rôle | Appels | Entrée native | Cache lecture | Cache création | Sortie | Total tokens | Incomplets | Proxy | Durée (s) |",
            res_md.stdout,
        )
        self.assertIn("| orchestration |", res_md.stdout)
        self.assertIn("| execution |", res_md.stdout)
        self.assertIn("| conseil |", res_md.stdout)
        self.assertIn("- **Limites de quota atteintes** : 2 (dernière : 2026-10-04, claude)", res_md.stdout)

    def test_claude_root_dedup_and_orchestrator(self):
        fake_claude = self.root / 'fake_claude_projects'
        p_dir = fake_claude / 'proj'
        p_dir.mkdir(parents=True, exist_ok=True)

        # Outside the project root: otherwise the project itself would count these messages.
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        orch_dir = Path(outside.name) / 'orchestrator_root'
        orch_dir.mkdir(parents=True, exist_ok=True)
        other_dir = Path(outside.name) / 'other_root'
        other_dir.mkdir(parents=True, exist_ok=True)

        lines = [
            # Message 1 : projet uniquement
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(self.root / 'subdir'),
                    'message': {'id': 'msg_proj', 'model': 'claude-3-5-sonnet', 'usage': {'input_tokens': 100, 'output_tokens': 20}},
                }
            ),
            # Message 2 : orchestrateur uniquement
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(orch_dir / 'subdir'),
                    'message': {'id': 'msg_orch', 'model': 'claude-3-5-sonnet', 'usage': {'input_tokens': 200, 'output_tokens': 40}},
                }
            ),
            # Message 3 : présent à la fois chez l'orchestrateur et chez le projet -> SANS DOUBLE COMPTE, D'ABORD AU PROJET
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(orch_dir),
                    'message': {'id': 'msg_both', 'model': 'claude-3-5-sonnet', 'usage': {'input_tokens': 300, 'output_tokens': 60}},
                }
            ),
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(self.root),
                    'message': {'id': 'msg_both', 'model': 'claude-3-5-sonnet', 'usage': {'input_tokens': 300, 'output_tokens': 60}},
                }
            ),
            # Message 4 : dossier étranger non compté
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(other_dir),
                    'message': {'id': 'msg_alien', 'model': 'claude-3-5-sonnet', 'usage': {'input_tokens': 1000, 'output_tokens': 1000}},
                }
            ),
        ]
        (p_dir / 'transcript.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')

        # Test avec option CLI --claude-root
        res_cli = self.run_metrics('--json', '--claude-root', str(orch_dir), claude_dir=fake_claude)
        self.assertEqual(res_cli.returncode, 0, res_cli.stderr)
        data_cli = json.loads(res_cli.stdout)['tokens']

        # tokens.claude (projet) : msg_proj (100 in, 20 out) + msg_both (300 in, 60 out) = 2 msgs, 400 in, 80 out
        self.assertEqual(data_cli['claude']['messages'], 2)
        self.assertEqual(data_cli['claude']['input'], 400)
        self.assertEqual(data_cli['claude']['output'], 80)

        # tokens.claude_orchestrator (racine ajoutée sans double compte) : msg_orch seulement (200 in, 40 out)
        self.assertEqual(data_cli['claude_orchestrator']['messages'], 1)
        self.assertEqual(data_cli['claude_orchestrator']['input'], 200)
        self.assertEqual(data_cli['claude_orchestrator']['output'], 40)

        # Test avec variable d'environnement WORKSPACE_ORCHESTRATOR_ROOTS
        env = dict(os.environ, WORKSPACE_ORCHESTRATOR_ROOTS=f"/nonexistent:{orch_dir}")
        res_env = self.run_metrics('--json', claude_dir=fake_claude, env=env)
        self.assertEqual(res_env.returncode, 0, res_env.stderr)
        data_env = json.loads(res_env.stdout)['tokens']
        self.assertEqual(data_env['claude']['messages'], 2)
        self.assertEqual(data_env['claude_orchestrator']['messages'], 1)
        self.assertEqual(data_env['claude_orchestrator']['input'], 200)

        # Test affichage texte et markdown pour Claude orchestrateur
        res_txt = self.run_metrics('--claude-root', str(orch_dir), claude_dir=fake_claude)
        self.assertEqual(res_txt.returncode, 0)
        self.assertIn("Claude orch.", res_txt.stdout)

        res_md = self.run_metrics('--markdown', '--claude-root', str(orch_dir), claude_dir=fake_claude)
        self.assertEqual(res_md.returncode, 0)
        self.assertIn("Claude (orchestrateur)", res_md.stdout)

    def _roots_fixture(self):
        """Transcrit projet, orchestrateur, racine `~` et un dossier étranger ; retourne le dossier Claude, l'environnement et la racine orchestrateur."""
        fake_claude = self.root / 'fake_claude_projects' / 'proj'
        fake_claude.mkdir(parents=True, exist_ok=True)
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        base = Path(outside.name)
        orch_dir = base / 'orchestrator_root'
        home_dir = base / 'home'
        home_orch = home_dir / 'orch_home'
        for d in (orch_dir / 'subdir', home_orch, base / 'other_root'):
            d.mkdir(parents=True, exist_ok=True)

        lines = [
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(self.root / 'subdir'),
                    'message': {'id': 'msg_proj', 'model': 'claude-3-5-sonnet', 'usage': {'input_tokens': 100, 'output_tokens': 20}},
                }
            ),
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(orch_dir / 'subdir'),
                    'message': {'id': 'msg_orch', 'model': 'claude-3-5-sonnet', 'usage': {'input_tokens': 200, 'output_tokens': 40}},
                }
            ),
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(home_orch),
                    'message': {'id': 'msg_home', 'model': 'claude-3-5-sonnet', 'usage': {'input_tokens': 500, 'output_tokens': 50}},
                }
            ),
            json.dumps(
                {
                    'type': 'assistant',
                    'cwd': str(base / 'other_root'),
                    'message': {'id': 'msg_alien', 'model': 'claude-3-5-sonnet', 'usage': {'input_tokens': 1000, 'output_tokens': 1000}},
                }
            ),
        ]
        (fake_claude / 'transcript.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        env = dict(os.environ, HOME=str(home_dir), WORKSPACE_ORCHESTRATOR_ROOTS='')
        return fake_claude, env, orch_dir

    def test_claude_roots_file_counts_listed_roots(self):
        fake_claude, env, orch_dir = self._roots_fixture()
        roots_dir = self.root / '.workspace' / 'metrics'
        roots_dir.mkdir(parents=True)
        rel_orch = os.path.relpath(orch_dir, self.root)

        res_none = self.run_metrics('--json', claude_dir=fake_claude, env=env)
        self.assertEqual(res_none.returncode, 0, res_none.stderr)
        self.assertEqual(json.loads(res_none.stdout)['tokens']['claude_orchestrator']['messages'], 0)

        # Commentaires, lignes vides, espaces, chemin relatif, `~` ; la racine absolue est aussi citée une fois en doublon
        (roots_dir / 'claude-roots.txt').write_text(
            f"# racines de l'orchestrateur\n\n   {rel_orch}   \n{orch_dir}\n   # commentaire indenté\n~/orch_home\n", encoding='utf-8'
        )
        res_file = self.run_metrics('--json', claude_dir=fake_claude, env=env)
        self.assertEqual(res_file.returncode, 0, res_file.stderr)
        tokens = json.loads(res_file.stdout)['tokens']
        self.assertEqual(tokens['claude']['messages'], 1)
        self.assertEqual(tokens['claude_orchestrator']['messages'], 2)
        self.assertEqual(tokens['claude_orchestrator']['input'], 700)
        self.assertEqual(tokens['claude_orchestrator']['output'], 90)

        # Même racine aussi passée par --claude-root : comptée une seule fois
        res_both = self.run_metrics('--json', '--claude-root', str(orch_dir), claude_dir=fake_claude, env=env)
        self.assertEqual(res_both.returncode, 0, res_both.stderr)
        tokens_both = json.loads(res_both.stdout)['tokens']
        self.assertEqual(tokens_both['claude_orchestrator']['messages'], 2)
        self.assertEqual(tokens_both['claude_orchestrator']['input'], 700)

    def _reset_workspace(self):
        ws = self.root / '.workspace'
        if ws.is_symlink():
            ws.unlink()
        elif ws.exists():
            shutil.rmtree(ws)

    def _assert_roots_ignored(self, fake_claude, env):
        res = self.run_metrics('--json', claude_dir=fake_claude, env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)['tokens']['claude_orchestrator']['messages'], 0)
        self.assertTrue(any(line.startswith('metrics: ') for line in res.stderr.splitlines()), res.stderr)

    def test_claude_roots_file_refused_with_warning(self):
        fake_claude, env, orch_dir = self._roots_fixture()
        ws = self.root / '.workspace'
        listed = f"{orch_dir}\n"

        def file_is_link():
            (ws / 'metrics').mkdir(parents=True)
            real = ws / 'metrics' / 'real-roots.txt'
            real.write_text(listed, encoding='utf-8')
            os.symlink(real, ws / 'metrics' / 'claude-roots.txt')

        def metrics_dir_is_link():
            (ws / 'metrics-real').mkdir(parents=True)
            (ws / 'metrics-real' / 'claude-roots.txt').write_text(listed, encoding='utf-8')
            os.symlink(ws / 'metrics-real', ws / 'metrics')

        def workspace_is_link():
            (self.root / 'ws_real' / 'metrics').mkdir(parents=True)
            (self.root / 'ws_real' / 'metrics' / 'claude-roots.txt').write_text(listed, encoding='utf-8')
            os.symlink(self.root / 'ws_real', ws)

        def file_too_big():
            (ws / 'metrics').mkdir(parents=True)
            (ws / 'metrics' / 'claude-roots.txt').write_text(listed + '#' + 'x' * (64 * 1024 + 1) + '\n', encoding='utf-8')

        def file_not_utf8():
            (ws / 'metrics').mkdir(parents=True)
            (ws / 'metrics' / 'claude-roots.txt').write_bytes(b'\xff\xfe' + listed.encode('utf-8'))

        for build in (file_is_link, metrics_dir_is_link, workspace_is_link, file_too_big, file_not_utf8):
            with self.subTest(case=build.__name__):
                self._reset_workspace()
                build()
                self._assert_roots_ignored(fake_claude, env)

    def test_metrics_sh(self):
        res = self.run_metrics_sh('--json')
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data['project'], self.root.name)


if __name__ == '__main__':
    unittest.main()
