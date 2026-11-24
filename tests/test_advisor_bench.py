"""Advisor benchmark scoring, with a scripted local model."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import advisor_bench  # noqa: E402


class ScriptedOllama:
    embed_model = 'fake-embed'
    chat_model = 'fake-chat'

    def __init__(self, answers):
        self.answers = answers

    def embed(self, text):
        return [1.0, 0.0]

    def post(self, endpoint, body, timeout):
        question = body['messages'][1]['content'].rsplit('Question : ', 1)[1]
        return {'message': {'content': self.answers[question]}}


class AdvisorBenchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'docs').mkdir()
        (self.root / 'docs/guide.md').write_text('# Guide\nLe port par défaut est 4780.\n')
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)

    def test_scores_correct_wrong_missed_abstained_invented(self):
        questions = [
            dict(id='a', question='Port ?', keywords=['4780'], evidence='Le port par défaut est 4780.'),
            dict(id='b', question='Hôte ?', keywords=['127.0.0.1'], evidence='absent du corpus'),
            dict(id='c', question='Délai ?', keywords=['30']),
            dict(id='d', question='Chat ?'),
            dict(id='e', question='Frigo ?'),
        ]
        answers = {
            'Port ?': 'Le port est 4780 [E1].',
            'Hôte ?': 'C’est localhost [E1].',
            'Délai ?': 'JE NE SAIS PAS',
            'Chat ?': 'JE NE SAIS PAS',
            'Frigo ?': 'Il est blanc [E1].',
        }
        result = advisor_bench.run_bench(self.root, questions, ScriptedOllama(answers))
        self.assertEqual([row['verdict'] for row in result['rows']], ['correct', 'wrong', 'missed', 'abstained', 'invented'])
        self.assertEqual((result['correct'], result['answerable'], result['unanswerable']), (1, 3, 2))
        self.assertAlmostEqual(result['accuracy'], 1 / 3)
        self.assertAlmostEqual(result['abstention_rate'], 1 / 2)
        self.assertAlmostEqual(result['passage_recall'], 1 / 2)
        self.assertEqual(result['chat_model'], 'fake-chat')

    def test_invalid_question_file_is_refused(self):
        bad = self.root / 'bad.json'
        bad.write_text('{"question": "pas une liste"}')
        self.assertEqual(advisor_bench.main([str(self.root), str(bad)]), 64)
        bad.write_text('[{"id": "x"}]')
        self.assertEqual(advisor_bench.main([str(self.root), str(bad)]), 64)
        self.assertFalse((self.root / '.workspace/metrics/advisor-bench.json').exists())

    def test_result_is_written_for_metrics(self):
        questions = self.root / 'bench.json'
        questions.write_text(json.dumps([dict(id='a', question='Port ?', keywords=['4780'])]))
        original = advisor_bench.advisor.Ollama
        advisor_bench.advisor.Ollama = lambda: ScriptedOllama({'Port ?': 'Le port est 4780 [E1].'})
        self.addCleanup(setattr, advisor_bench.advisor, 'Ollama', original)
        self.assertEqual(advisor_bench.main([str(self.root), 'bench.json']), 0)
        target = self.root / '.workspace/metrics/advisor-bench.json'
        self.assertEqual(json.loads(target.read_text())['accuracy'], 1.0)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
