"""Local advisor integration, using only a loopback fake Ollama."""

import http.server
import io
import json
import os
import runpy
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel, sous-processus ou Docker simulé ; suite complète seulement')

ROOT = Path(__file__).resolve().parents[1]
ADVISOR = ROOT / 'template/scripts/lib/advisor.py'
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import workdir  # noqa: E402


class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Le clone de tâche est hors du projet (HOME est le projet dans ce test).
        self.work_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.work_dir.cleanup)
        env_patch = mock.patch.dict(
            os.environ, {'HOME': str(self.root), 'WSC_NTFY_TOPIC': '', 'WORKSPACE_WORK_DIR': os.path.realpath(self.work_dir.name)}
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.track('guide.md', '# Installation\nLa commande est installer.\n')
        self.ctx = self.root / '.workspace/orchestration/tasks/demo/context'
        (self.ctx / 'questions').mkdir(parents=True)
        (self.ctx.parent / 'state.json').write_text('{}')
        (self.ctx / 'questions/001.md').write_text('Quelle commande installer ?')
        self.requests = []
        self.answer = 'Utiliser installer [E1].'
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append((self.path, body))
                if self.path == '/api/embed':
                    inputs = body['input']
                    if isinstance(inputs, str):
                        inputs = [inputs]
                    result = {'embeddings': [[1.0, float(len(t) % 7 + 1)] for t in inputs]}
                else:
                    result = {'message': {'role': 'assistant', 'content': owner.answer}, 'prompt_eval_count': 17, 'eval_count': 6}
                data = json.dumps(result).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.transport_fallback = False
        self.is_offline = False
        try:
            self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        except PermissionError:
            self.transport_fallback = True
            self.env = dict(os.environ, OLLAMA_HOST='http://127.0.0.1:1')
            self.env.pop('WORKSPACE_AGENT_ROOT', None)
            return
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.env = dict(os.environ, OLLAMA_HOST='http://127.0.0.1:%s' % self.server.server_port)
        self.env.pop('WORKSPACE_AGENT_ROOT', None)

    def track(self, name, text):
        (self.root / name).write_text(text)
        subprocess.run(['git', '-C', str(self.root), 'add', '--', name], check=True)

    def run_advisor(self, *args):
        if self.transport_fallback:
            # The managed sandbox forbids even loopback bind(). Keep the real
            # urllib request boundary, replacing only the socket transport.
            def open_fake(opener, request, timeout=None):
                if self.is_offline:
                    raise urllib.error.URLError('Connection refused')
                body = json.loads(request.data)
                endpoint = request.full_url.rsplit(':1', 1)[-1]
                self.requests.append((endpoint, body))
                if endpoint == '/api/embed':
                    inputs = body['input']
                    if isinstance(inputs, str):
                        inputs = [inputs]
                    value = {'embeddings': [[1.0, float(len(t) % 7 + 1)] for t in inputs]}
                else:
                    value = {'message': {'role': 'assistant', 'content': self.answer}, 'prompt_eval_count': 17, 'eval_count': 6}
                return io.BytesIO(json.dumps(value).encode())

            output, errors = io.StringIO(), io.StringIO()
            saved = {name: sys.modules.pop(name, None) for name in ('context', 'secrets')}
            old_umask = os.umask(0o077)
            old_bytecode = sys.dont_write_bytecode
            old_path = sys.path[:]
            sys.path.insert(0, str(ADVISOR.parent))
            try:
                with (
                    mock.patch.dict(os.environ, self.env, clear=True),
                    mock.patch.object(urllib.request.OpenerDirector, 'open', open_fake),
                    mock.patch.object(sys, 'argv', [str(ADVISOR), str(self.root)] + list(args)),
                    redirect_stdout(output),
                    redirect_stderr(errors),
                ):
                    try:
                        runpy.run_path(str(ADVISOR), run_name='__main__')
                    except SystemExit as exc:
                        code = exc.code
            finally:
                os.umask(old_umask)
                sys.dont_write_bytecode = old_bytecode
                sys.path[:] = old_path
                for name, module in saved.items():
                    sys.modules.pop(name, None)
                    if module is not None:
                        sys.modules[name] = module
            return subprocess.CompletedProcess(args, code, output.getvalue(), errors.getvalue())
        return subprocess.run([sys.executable, str(ADVISOR), str(self.root)] + list(args), env=self.env, capture_output=True, text=True)

    def note(self):
        return json.loads((self.ctx / 'advisor/001.json').read_text())

    def test_consumption_and_write_failure(self):
        self.run_advisor('--task', 'demo')
        path = self.root / '.workspace/metrics/consumption.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['tokens']['input'], 17)
        self.assertEqual(rows[0]['tokens']['output'], 6)
        self.assertEqual(rows[0]['task_id'], 'demo')
        self.assertEqual(rows[0]['role'], 'conseil')
        path.unlink()
        path.mkdir()
        (self.ctx / 'questions/002.md').write_text('Quelle commande installer ?')
        self.assertEqual(self.run_advisor('--task', 'demo').returncode, 0)
        self.assertTrue((self.ctx / 'answers/002.md').exists())

    def test_cited_answer_and_permissions_and_cache(self):
        result = self.run_advisor('--task', 'demo')
        self.assertEqual(result.returncode, 0, result.stderr)
        answer = (self.ctx / 'answers/001.md').read_text()
        self.assertIn('Sources :', answer)
        self.assertIn('guide.md#Installation', answer)
        self.assertEqual(self.note()['status'], 'answered')
        for path in [self.ctx / 'advisor/001.json', self.root / '.workspace/advisor/embeddings.json']:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        chat = [b for p, b in self.requests if p == '/api/chat'][0]
        self.assertFalse(chat['think'])
        self.assertFalse(chat['stream'])
        self.assertEqual(chat['options']['temperature'], 0)

    def test_abstention_and_invalid_citations(self):
        for answer in ['JE NE SAIS PAS', 'installer', 'installer [E0] [E99]', 'installer [E01]']:
            with self.subTest(answer=answer):
                self.answer = answer
                note = self.ctx / 'advisor/001.json'
                if note.exists():
                    note.unlink()
                result = self.run_advisor()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.note()['status'], 'unknown')
                self.assertFalse((self.ctx / 'answers/001.md').exists())
                self.assertIn("transmis à l'orchestrateur", result.stdout)

    def offline(self):
        if self.transport_fallback:
            self.is_offline = True
            return
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.env['OLLAMA_HOST'] = 'http://127.0.0.1:%s' % sock.getsockname()[1]

    def test_unavailable_does_not_block(self):
        self.offline()
        result = self.run_advisor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.note()['status'], 'unavailable')
        self.assertFalse((self.ctx / 'answers/001.md').exists())

    def test_unknown_and_existing_notes_alert_once(self):
        self.answer = 'JE NE SAIS PAS'
        self.env['HOME'] = str(self.root)
        self.env.pop('WSC_NTFY_TOPIC', None)
        for status in ('unknown', 'unavailable', 'error'):
            (self.ctx / 'advisor').mkdir(exist_ok=True)
            (self.ctx / 'advisor/001.json').write_text(json.dumps({'status': status}))
            self.assertEqual(self.run_advisor().returncode, 0)
        path = self.root / '.workspace/orchestration/alerts.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['question'], '001')

    def test_ask_initialization_failure_records_consumption(self):
        self.offline()
        self.env['WORKSPACE_TASK_ID'] = 'offline-task'
        self.env['WORKSPACE_ADVISOR_CHAT_MODEL'] = 'offline-chat'
        result = self.run_advisor('--ask', 'installer ?')
        self.assertEqual(result.returncode, 4, result.stderr)
        path = self.root / '.workspace/metrics/consumption.jsonl'
        self.assertTrue(path.exists(), 'Missing consumption event for failed initialization')
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            (row['role'], row['provider'], row['model'], row['task_id']), ('conseil', 'ollama', 'offline-chat', 'offline-task')
        )
        self.assertEqual(row['exit'], 1)
        self.assertTrue(all(value is None for value in row['tokens'].values()))
        self.assertGreaterEqual(row['proxy']['duration_seconds'], 0)
        self.assertNotIn('installer', json.dumps(row))
        path.unlink()
        path.mkdir()
        result = self.run_advisor('--ask', 'installer ?')
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn('Consumption', result.stderr)

    def test_already_answered_or_noted_skips_all_calls(self):
        for directory, suffix in [('answers', 'md'), ('advisor', 'json')]:
            path = self.ctx / directory / ('001.' + suffix)
            path.parent.mkdir(exist_ok=True)
            path.write_text('{}')
            result = self.run_advisor()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.requests, [])
            path.unlink()

    def test_secret_files_and_passages_never_sent(self):
        self.track('.env.md', '# Private\nENV_FILE_CANARY\n')
        self.track('notes.md', '# Private\nAPI_TOKEN=FAKE_PRIVATE_CANARY_123\n')
        self.track('known.md', '# Private\nKNOWN_PRIVATE_CANARY_456\n')
        (self.root / '.env').write_text('PASSWORD=KNOWN_PRIVATE_CANARY_456\n')
        result = self.run_advisor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.requests)
        payload = json.dumps(self.requests)
        for marker in ['ENV_FILE_CANARY', 'FAKE_PRIVATE_CANARY_123', 'KNOWN_PRIVATE_CANARY_456']:
            self.assertNotIn(marker, payload)

    def test_remote_host_refused(self):
        self.env['OLLAMA_HOST'] = 'http://example.com:11434'
        result = self.run_advisor('--ask', 'installer ?')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('local', result.stderr.lower())
        self.assertEqual(self.requests, [])

    def test_ask_exit_codes(self):
        result = self.run_advisor('--ask', 'installer ?')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('guide.md#Installation', result.stdout)
        self.answer = 'JE NE SAIS PAS'
        self.assertEqual(self.run_advisor('--ask', 'installer ?').returncode, 3)
        self.offline()
        self.assertEqual(self.run_advisor('--ask', 'installer ?').returncode, 4)

    def test_answer_copied_to_worktree(self):
        exchange = workdir.task_worktree(self.root, 'demo') / '.workspace/exchange'
        (exchange / 'questions').mkdir(parents=True)
        result = self.run_advisor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((exchange / 'answers/001.md').read_text(), (self.ctx / 'answers/001.md').read_text())

    def test_symlink_worktree_answers_refused(self):
        exchange = workdir.task_worktree(self.root, 'demo') / '.workspace/exchange'
        exchange.mkdir(parents=True)
        outside = self.root / 'outside'
        outside.mkdir()
        (exchange / 'answers').symlink_to(outside, target_is_directory=True)
        result = self.run_advisor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.note()['status'], 'error')
        self.assertFalse((self.ctx / 'answers/001.md').exists())

    def test_cache_reuses_document_embeddings(self):
        self.assertEqual(self.run_advisor('--ask', 'installer ?').returncode, 0)
        self.requests.clear()
        self.assertEqual(self.run_advisor('--ask', 'installer ?').returncode, 0)
        embeds = [b['input'] for p, b in self.requests if p == '/api/embed']
        self.assertEqual(embeds, ['installer ?'])

    def test_symlink_note_directory_refused(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.ctx / 'advisor').symlink_to(outside, target_is_directory=True)
        result = self.run_advisor()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.requests, [])


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        old_bytecode = sys.dont_write_bytecode
        with mock.patch.object(sys, 'path', [str(ADVISOR.parent)] + sys.path), mock.patch.dict(sys.modules):
            sys.modules.pop('context', None)
            sys.modules.pop('secrets', None)
            self.api = runpy.run_path(str(ADVISOR))
        sys.dont_write_bytecode = old_bytecode

    def test_chunk_overlap_and_fenced_headings(self):
        chunks = list(self.api['chunk_markdown']('# Guide\n' + 'a' * 900 + '\n```md\n# code heading\n' + 'b' * 900 + '\n```\nEnd\n'))
        self.assertEqual(len(chunks[0][1]), 800)
        self.assertEqual(len(chunks[1][1]), 200)
        self.assertTrue(all(title == 'Guide' for title, text in chunks))
        code = [text for title, text in chunks if text.startswith('```')]
        self.assertEqual(len(code), 1)
        self.assertIn('# code heading', code[0])
        self.assertTrue(code[0].endswith('```'))

    def test_section_is_the_full_heading_path(self):
        chunks = list(self.api['chunk_markdown']('# Guide\n## Installer\nun\n### Mac\ndeux\n## Lancer\ntrois\n'))
        self.assertEqual([title for title, text in chunks], ['Guide > Installer', 'Guide > Installer > Mac', 'Guide > Lancer'])

    def make_repo(self, files):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
        return root

    def test_noisy_folders_left_out_of_the_corpus(self):
        root = self.make_repo(
            {
                'docs/guide.md': '# Guide\nun\n',
                'README.md': '# Lisez\ndeux\n',
                '.claude/rules/style.md': '# Style\ntrois\n',
                'docs/design/maquette.md': '# Maquette\nquatre\n',
                'docs/journal/e1.md': '# Journal\ncinq\n',
                'docs/plans/done/p.md': '# Plan\nsix\n',
                '_bmad-output/brief.md': '# Brouillon\nsept\n',
            }
        )
        files = sorted({chunk['file'] for chunk in self.api['corpus'](root)})
        self.assertEqual(files, ['README.md', 'docs/guide.md'])

    def test_embeddings_include_the_heading_path(self):
        root = self.make_repo({'docs/guide.md': '# Guide\n## Installer\nla commande\n'})
        texts = []

        class Local:
            embed_model = 'fake'

            def embed(self, text):
                texts.append(text)
                return [1.0, 0.0]

        self.api['Advisor'](root, Local()).close()
        self.assertEqual(texts, ['Guide > Installer\nla commande'])

    def test_weighted_rrf_and_eight_passages(self):
        advisor = object.__new__(self.api['Advisor'])
        advisor.chunks = [dict(file='%d.md' % i, section='', text='text') for i in range(10)]
        advisor.vectors = [[1, 0]] * 10
        advisor.fts = True
        captured = {}

        class Lexical:
            def execute(self, sql, params):
                captured['query'] = params[0]
                return [(10,), (9,)]

        class Local:
            chat_model = 'fake'

            def embed(self, text):
                return [1, 0]

            def post(self, endpoint, body, timeout):
                captured['prompt'] = body['messages'][1]['content']
                return {'message': {'content': 'Réponse [E1] [E8]'}}

        advisor.db, advisor.ollama = Lexical(), Local()
        answer, sources = advisor.ask('le déploiement et la commande')
        self.assertEqual(captured['query'], '"déploiement" OR "commande"')
        # Hand calculation: doc9 = 1/70 + .5/61; doc8 = 1/69 + .5/62.
        # doc8 wins, followed by doc9, then the semantic-only docs 0..5.
        self.assertEqual([source['file'] for source in sources], ['8.md', '5.md'])
        self.assertIn('[E8]', captured['prompt'])
        self.assertNotIn('[E9]', captured['prompt'])

    def test_semantic_only_without_fts(self):
        advisor = object.__new__(self.api['Advisor'])
        advisor.chunks = [dict(file='irrelevant.md', section='', text='other'), dict(file='guide.md', section='Guide', text='installer')]
        advisor.vectors = [[0, 1], [1, 0]]
        advisor.fts = False

        class Local:
            chat_model = 'fake'

            def embed(self, text):
                return [1, 0]

            def post(self, endpoint, body, timeout):
                return {'message': {'content': 'Installer [E1]'}}

        advisor.ollama = Local()
        answer, sources = advisor.ask('installer')
        self.assertEqual(sources[0]['file'], 'guide.md')


if __name__ == '__main__':
    unittest.main()
