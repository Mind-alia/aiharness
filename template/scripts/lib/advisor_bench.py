"""Advisor benchmark: replay questions with known answers and record the score.

Usage: advisor_bench.py <root> <questions.json>
Each question: {"id", "question", "keywords": [...], "evidence": "..."} ; a question
without keywords has no answer in the docs and must get « je ne sais pas ».
The result goes to .workspace/metrics/advisor-bench.json, read by metrics.sh.
"""

import datetime
import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import advisor  # noqa: E402


class RecordingOllama:
    """Keeps the last prompt so the bench can tell retrieval misses from answer errors."""

    def __init__(self, ollama):
        self.ollama = ollama
        self.embed_model = ollama.embed_model
        self.chat_model = ollama.chat_model
        self.last_prompt = ''

    def embed(self, text):
        return self.ollama.embed(text)

    def post(self, endpoint, body, timeout):
        if 'messages' in body:
            self.last_prompt = body['messages'][1]['content']
        return self.ollama.post(endpoint, body, timeout)


def normalize(text):
    return ' '.join(text.casefold().split())


def load_questions(path):
    questions = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(questions, list) or not questions:
        raise ValueError('Fichier de questions vide ou invalide : liste attendue.')
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get('question'), str):
            raise ValueError('Chaque question doit avoir un champ « question ».')
        if not isinstance(question.get('keywords', []), list):
            raise ValueError('« keywords » doit être une liste.')
    return questions


def run_bench(root, questions, ollama):
    recorder = RecordingOllama(ollama)
    started = time.monotonic()
    engine = advisor.Advisor(root, recorder)
    index_seconds = time.monotonic() - started
    rows = []
    try:
        for question in questions:
            recorder.last_prompt = ''
            began = time.monotonic()
            answer, _ = engine.ask(question['question'])
            seconds = time.monotonic() - began
            keywords = question.get('keywords') or []
            evidence = question.get('evidence') or ''
            if keywords:
                correct = answer is not None and all(k.casefold() in answer.casefold() for k in keywords)
                verdict = 'correct' if correct else ('wrong' if answer is not None else 'missed')
            else:
                verdict = 'abstained' if answer is None else 'invented'
            rows.append(
                dict(
                    id=question.get('id'),
                    verdict=verdict,
                    seconds=round(seconds, 3),
                    passage_found=bool(evidence) and normalize(evidence) in normalize(recorder.last_prompt),
                )
            )
    finally:
        engine.close()
    answerable = [row for row in rows if row['verdict'] in ('correct', 'wrong', 'missed')]
    unanswerable = [row for row in rows if row['verdict'] in ('abstained', 'invented')]
    with_evidence = [row for row, q in zip(rows, questions, strict=False) if q.get('evidence')]

    def count(verdict):
        return sum(row['verdict'] == verdict for row in rows)

    return dict(
        at=datetime.datetime.now(datetime.UTC).isoformat(),
        chat_model=ollama.chat_model,
        embed_model=ollama.embed_model,
        passages=len(engine.chunks),
        index_seconds=round(index_seconds, 3),
        questions=len(rows),
        answerable=len(answerable),
        unanswerable=len(unanswerable),
        correct=count('correct'),
        wrong=count('wrong'),
        missed=count('missed'),
        abstained=count('abstained'),
        invented=count('invented'),
        accuracy=count('correct') / len(answerable) if answerable else None,
        abstention_rate=count('abstained') / len(unanswerable) if unanswerable else None,
        passage_recall=(sum(row['passage_found'] for row in with_evidence) / len(with_evidence) if with_evidence else None),
        mean_seconds=sum(row['seconds'] for row in rows) / len(rows) if rows else None,
        rows=rows,
    )


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print('Usage : advise.sh --bench <questions.json>', file=sys.stderr)
        return 64
    root = Path(argv[0]).resolve()
    try:
        questions = load_questions((root / argv[1]) if not Path(argv[1]).is_absolute() else Path(argv[1]))
        result = run_bench(root, questions, advisor.Ollama())
    except (OSError, ValueError) as exc:
        print('Erreur banc d’essai : %s' % exc, file=sys.stderr)
        return 64 if isinstance(exc, ValueError) else 4
    target = root / '.workspace/metrics/advisor-bench.json'
    advisor.secure_dir(target.parent, root)
    advisor.atomic_json(target, result, root)
    target.chmod(0o600)
    print(
        'Banc d’essai du conseiller : %d/%d justes, %d fausses, %d sans réponse ; '
        '« je ne sais pas » justes %d/%d ; passage fourni %s ; %.1f s par question.'
        % (
            result['correct'],
            result['answerable'],
            result['wrong'],
            result['missed'],
            result['abstained'],
            result['unanswerable'],
            '—' if result['passage_recall'] is None else '%d %%' % round(100 * result['passage_recall']),
            result['mean_seconds'] or 0,
        )
    )
    print('Résultat : %s' % target)
    return 0


if __name__ == '__main__':
    sys.exit(main())
