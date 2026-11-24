"""Alerts use a mocked HTTP transport; no network access."""

import concurrent.futures
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parents[1] / 'template/scripts/lib'
sys.path.insert(0, str(LIB))


class AlertsTests(unittest.TestCase):
    def setUp(self):
        import alerts

        self.alerts = alerts
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'HOME': str(self.root), 'WSC_NTFY_TOPIC': 'test-topic'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ctx = self.root / '.workspace/orchestration/tasks/demo/context'
        (self.ctx / 'questions').mkdir(parents=True)
        (self.ctx / 'answers').mkdir()
        (self.ctx.parent / 'state.json').write_text('{}')
        (self.ctx / 'questions/001.md').write_text('CONFIDENTIAL PROJECT DIAGNOSIS')

    def rows(self):
        return self.alerts.read_alerts(self.root)

    def test_once_concurrent_constant_notification(self):
        with mock.patch.object(self.alerts.urllib.request, 'urlopen') as send:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _: self.alerts.emit(self.root, 'demo', '001', 'unknown'), range(16)))
            self.assertEqual(send.call_count, 1)
            request = send.call_args[0][0]
            self.assertEqual(request.full_url, 'https://ntfy.sh/test-topic')
            self.assertEqual(request.data.decode(), "Une question d'agent attend ta réponse")
            self.assertEqual(request.get_header('Title'), 'Workspace IA')
            self.assertEqual(send.call_args.kwargs['timeout'], 5)
        self.assertEqual(len(self.rows()), 1)
        path = self.root / '.workspace/orchestration/alerts.jsonl'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('CONFIDENTIAL', path.read_text())
        self.assertNotIn('test-topic', path.read_text())

    def test_declarative_secrets_and_priority(self):
        secret = self.root / 'secrets.env'
        sentinel = self.root / 'EXECUTED'
        secret.write_text("OTHER=$(touch %s)\nEVIL=`touch %s`\nWSC_NTFY_TOPIC='from-file'\n" % (sentinel, sentinel))
        secret.chmod(0o600)
        with mock.patch.dict(os.environ, {'WORKSPACE_SECRETS_FILE': str(secret)}):
            os.environ.pop('WSC_NTFY_TOPIC', None)
            self.assertEqual(self.alerts.ntfy_topic(), 'from-file')
            secret.write_text('WSC_NTFY_TOPIC="double-quoted"\n')
            self.assertEqual(self.alerts.ntfy_topic(), 'double-quoted')
            secret.write_text('WSC_NTFY_TOPIC=$(touch %s)\n' % sentinel)
            self.alerts.ntfy_topic()
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.alerts.ntfy_topic(), 'test-topic')

    def test_world_readable_secrets_file_gives_no_topic(self):
        secret = self.root / 'secrets.env'
        secret.write_text("WSC_NTFY_TOPIC='from-file'\n")
        secret.chmod(0o644)
        with mock.patch.dict(os.environ, {'WORKSPACE_SECRETS_FILE': str(secret)}):
            os.environ.pop('WSC_NTFY_TOPIC', None)
            self.assertEqual(self.alerts.ntfy_topic(), '')

    def test_advisor_pass_survives_failed_notification(self):
        import advisor

        with (
            mock.patch.object(advisor, 'Advisor', side_effect=ConnectionError('offline')),
            mock.patch.object(self.alerts.urllib.request, 'urlopen', side_effect=OSError('offline')),
        ):
            self.assertEqual(advisor.run_pass(self.root), 0)
            self.assertEqual(advisor.run_pass(self.root), 0)
        self.assertEqual(len(self.rows()), 2)
        note = json.loads((self.ctx / 'advisor/001.json').read_text())
        self.assertEqual(note['status'], 'unavailable')

    def test_answered_question_never_alerted(self):
        (self.ctx / 'answers/001.md').write_text('resolved')
        with mock.patch.object(self.alerts.urllib.request, 'urlopen') as send:
            self.alerts.emit(self.root, 'demo', '001', 'unknown')
        self.assertEqual(self.rows(), [])
        send.assert_not_called()

    def test_symlink_journal_is_rejected(self):
        target = self.root / 'private'
        target.write_text('do not modify')
        (self.ctx.parents[2] / 'alerts.jsonl').symlink_to(target)
        with mock.patch.object(self.alerts.urllib.request, 'urlopen') as send:
            self.alerts.emit(self.root, 'demo', '001', 'unknown')
        self.assertEqual(target.read_text(), 'do not modify')
        send.assert_not_called()

    def test_offline_logged_without_secret_or_retry(self):
        with mock.patch.object(self.alerts.urllib.request, 'urlopen', side_effect=urllib.error.URLError('test-topic')) as send:
            self.alerts.emit(self.root, 'demo', '001', 'unavailable')
            self.alerts.emit(self.root, 'demo', '001', 'unavailable')
        self.assertEqual(send.call_count, 1)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['reason'], 'ntfy_failed')
        self.assertNotIn('test-topic', json.dumps(rows))
