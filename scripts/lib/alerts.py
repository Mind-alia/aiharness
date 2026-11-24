"""Private, deduplicated orchestrator alerts (Python 3.14, no shell parsing)."""

import fcntl
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from secrets import load_env

from safety import check_no_symlinks

MESSAGE = "Une question d'agent attend ta réponse"


def ntfy_topic():
    if 'WSC_NTFY_TOPIC' in os.environ:
        return os.environ['WSC_NTFY_TOPIC']
    try:
        return load_env().get('WSC_NTFY_TOPIC', '')
    except (OSError, ValueError):
        return ''


def _rows(stream):
    stream.seek(0)
    result = []
    for line in stream:
        try:
            row = json.loads(line)
            if (
                isinstance(row, dict)
                and re.fullmatch(r'[a-z0-9-]+', str(row.get('task_id', '')))
                and re.fullmatch(r'[0-9]{3,}', str(row.get('question', '')))
            ):
                result.append(row)
        except ValueError:
            continue
    return result


def read_alerts(root):
    path = root / '.workspace/orchestration/alerts.jsonl'
    try:
        check_no_symlinks(path, root)
        with path.open(encoding='utf-8') as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            return _rows(stream)
    except (OSError, ValueError):
        return []


def emit(root, task_id, question, reason):
    """Claim before delivery: at most one notification attempt per question."""
    question = '%03d' % int(question)
    if not re.fullmatch(r'[a-z0-9-]+', task_id):
        return
    directory = root / '.workspace/orchestration'
    path = directory / 'alerts.jsonl'
    try:
        check_no_symlinks(path, root)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a+', encoding='utf-8') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            os.fchmod(stream.fileno(), 0o600)
            if any(r['task_id'] == task_id and r['question'] == question for r in _rows(stream)):
                return
            answer = directory / 'tasks' / task_id / 'context/answers' / (question + '.md')
            check_no_symlinks(answer, root)
            if answer.is_file():
                return
            row = dict(at=datetime.now(UTC).isoformat(), task_id=task_id, question=question, reason=reason)
            stream.write(json.dumps(row) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        topic = ntfy_topic()
        if not topic:
            return
        try:
            request = urllib.request.Request(
                'https://ntfy.sh/' + urllib.parse.quote(topic, safe=''),
                data=MESSAGE.encode('utf-8'),
                headers={'Title': 'Workspace IA', 'Content-Type': 'text/plain; charset=utf-8'},
                method='POST',
            )
            with urllib.request.urlopen(request, timeout=5):
                pass
        except Exception:
            # Do not persist exception messages: they can contain the topic.
            row.update(at=datetime.now(UTC).isoformat(), reason='ntfy_failed')
            with path.open('a', encoding='utf-8') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                stream.write(json.dumps(row) + '\n')
    except (OSError, ValueError):
        # Alert delivery must never break the advisor pass or question listing.
        return


def pending_alerts(root):
    result = []
    seen = set()
    for row in read_alerts(root):
        key = (row['task_id'], row['question'])
        if key in seen or row.get('reason') == 'ntfy_failed':
            continue
        seen.add(key)
        ctx = root / '.workspace/orchestration/tasks' / key[0] / 'context'
        question = ctx / 'questions' / (key[1] + '.md')
        answer = ctx / 'answers' / (key[1] + '.md')
        try:
            check_no_symlinks(question, root)
            check_no_symlinks(answer, root)
            if question.is_file() and not answer.is_file():
                result.append(row)
        except ValueError:
            continue
    return result
