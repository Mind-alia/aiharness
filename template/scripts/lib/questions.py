"""Collecte et rappel des questions en attente."""

import argparse
import datetime
import json
import re
import sys
import time
from pathlib import Path

import alerts
import context
import events

ANSWER_ID_PATTERN = r'([a-z0-9-]+)/([0-9]{1,6})'
ANSWER_BY_MAX_LENGTH = 64
RESUME_TITLE = '## Réponses à tes questions'
RESUME_EXPLANATION = "Ces réponses ont été données après la fin de ton attente ; ne repose pas ces questions."
READ_DIR = context.READ_DIR
CONTROL_CHAR_LIMIT = 32
DELETE_CHAR = 127


class AnswerError(Exception):
    """Réponse refusée : message d'une ligne et code de sortie."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def check_symlink(path, root=None):
    """Vérifie si le chemin ou un de ses parents jusqu'à root est un lien symbolique."""
    curr = path
    while True:
        if curr.is_symlink():
            return True
        if root is not None and curr == root:
            break
        if curr.parent == curr:
            break
        curr = curr.parent
    return False


def unanswered(root, task_id, since=None, worktree=None):
    """Return unanswered question numbers from both context copies."""
    bases = [Path(root) / '.workspace/orchestration/tasks' / task_id / 'context']
    if worktree is not None:
        bases.append(Path(worktree) / '.workspace/exchange')
    pending, answered = set(), set()
    for base in bases:
        for kind, numbers in (('questions', pending), ('answers', answered)):
            directory = base / kind
            try:
                if check_symlink(directory) or not directory.is_dir():
                    continue
                for path in directory.iterdir():
                    try:
                        if path.is_symlink() or not path.is_file():
                            continue
                        match = re.fullmatch(r'(\d+)\.md', path.name)
                        if match and (kind == 'answers' or since is None or path.stat().st_mtime >= since):
                            numbers.add(match.group(1))
                    except OSError:
                        continue
            except OSError:
                continue
    return sorted(pending - answered)


def numbered_files(directory):
    """Map 'NNN' to the regular files named N.md in a directory (symlinks and odd names ignored)."""
    found = {}
    try:
        if check_symlink(directory) or not directory.is_dir():
            return found
        for path in directory.iterdir():
            match = re.fullmatch(r'(\d{1,3})\.md', path.name)
            if match and not path.is_symlink() and path.is_file():
                found[f"{int(match.group(1)):03d}"] = path
    except OSError:
        return {}
    return found


def read_text(path):
    try:
        return path.read_text(encoding='utf-8').strip()
    except (OSError, ValueError):
        return ''


def unread_answers(root, task_id, worktree=None):
    """Return (number, question, answer) for answers not yet delivered to the agent, sorted by number."""
    context_dir = Path(root) / '.workspace/orchestration/tasks' / task_id / 'context'
    answers = numbered_files(context_dir / 'answers')
    done = set(numbered_files(context_dir / READ_DIR))
    registry_questions = numbered_files(context_dir / 'questions')
    worktree_questions = {}
    if worktree is not None:
        exchange = Path(worktree) / '.workspace/exchange'
        done |= set(numbered_files(exchange / READ_DIR))
        worktree_questions = numbered_files(exchange / 'questions')
    rows = []
    for number in sorted(answers):
        if number in done:
            continue
        question_file = registry_questions.get(number) or worktree_questions.get(number)
        rows.append((number, read_text(question_file) if question_file else '', read_text(answers[number])))
    return rows


def mark_answers_read(root, task_id, numbers):
    """Record in the registry that these answers were delivered; nothing is created for an empty list."""
    numbers = [str(number) for number in numbers]
    for number in numbers:
        if re.fullmatch(r'\d{1,3}', number) is None:
            raise ValueError(f"numéro de réponse invalide : {number}")
    if not numbers:
        return
    root = Path(root)
    read_dir = root / '.workspace/orchestration/tasks' / task_id / 'context' / READ_DIR
    if check_symlink(read_dir, root):
        raise ValueError(f"lien symbolique interdit : {read_dir}")
    for number in numbers:
        context.mark_read(read_dir, f"{int(number):03d}")


def resume_section(root, task_id, worktree=None):
    """Prompt section carrying the answers given after the agent stopped waiting; '' when none."""
    return format_resume(unread_answers(root, task_id, worktree=worktree))


def format_resume(rows):
    """Render (number, question, answer) rows as the resume section; '' for no rows."""
    if not rows:
        return ''
    lines = [RESUME_TITLE, RESUME_EXPLANATION]
    for number, question, answer in rows:
        lines.extend(['', f"Question {number} : {question}", f"Réponse : {answer}"])
    return '\n'.join(lines)


def format_agent_age(mtime, now=None):
    if now is None:
        now = time.time()
    elapsed = max(0, int(now - mtime))
    if elapsed < 3600:
        minutes = max(1, elapsed // 60)
        return f"il y a {minutes} min"
    elif elapsed < 86400:
        hours = elapsed // 3600
        return f"il y a {hours} h"
    else:
        days = elapsed // 86400
        return f"il y a {days} j"


def format_doc_age(date_val, today=None):
    if today is None:
        today = datetime.date.today()
    diff = (today - date_val).days
    if diff <= 0:
        return "aujourd'hui"
    return f"il y a {diff} j"


def read_doc_questions(root):  # noqa: C901
    docs_dir = root / 'docs'
    q_file = docs_dir / 'QUESTIONS.md'
    if check_symlink(docs_dir, root) or check_symlink(q_file, root):
        return []
    if not q_file.is_file():
        return []
    try:
        content = q_file.read_text(encoding='utf-8')
    except OSError:
        return []

    questions = []
    today = datetime.date.today()
    for line_idx, line in enumerate(content.splitlines()):
        stripped = line.strip()
        if re.match(r'^-\s*\[[xX]\]', stripped):
            continue
        m_open = re.match(r'^-\s*\[\s*\]\s*(.*)$', stripped)
        if not m_open:
            continue
        rest = m_open.group(1).strip()
        if not rest:
            continue

        parts = [p.strip() for p in rest.split('·')]
        if len(parts) >= 3 and re.fullmatch(r'\d{4}-\d{2}-\d{2}', parts[0]):
            date_str = parts[0]
            source = parts[1]
            text = ' · '.join(parts[2:])
        elif len(parts) == 2 and re.fullmatch(r'\d{4}-\d{2}-\d{2}', parts[0]):
            date_str = parts[0]
            source = None
            text = parts[1]
        elif len(parts) == 2 and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', parts[0]):
            date_str = None
            source = parts[0]
            text = parts[1]
        else:
            date_str = None
            source = None
            text = rest

        age_str = None
        q_date = None
        if date_str:
            try:
                q_date = datetime.date.fromisoformat(date_str)
                age_str = format_doc_age(q_date, today)
            except ValueError:
                q_date = None

        one_line_text = ' '.join(text.split())
        if len(one_line_text) > 200:
            one_line_text = one_line_text[:200]

        questions.append(
            {
                'type': 'doc',
                'date': q_date,
                'date_str': date_str,
                'source': source,
                'text': one_line_text,
                'age': age_str,
                'line_idx': line_idx,
            }
        )

    questions.sort(
        key=lambda q: (0 if q['date'] is not None else 1, q['date'] if q['date'] is not None else datetime.date.max, q['line_idx'])
    )
    return questions


def read_agent_questions(root):  # noqa: C901, PLR0915
    agent_questions = []
    closed_questions = []

    tasks_dir = root / '.workspace/orchestration/tasks'
    if check_symlink(tasks_dir, root) or not tasks_dir.is_dir():
        return agent_questions, closed_questions

    try:
        task_entries = sorted(tasks_dir.iterdir())
    except OSError:
        return agent_questions, closed_questions

    for task_dir in task_entries:
        if task_dir.is_symlink() or not task_dir.is_dir():
            continue
        task_id = task_dir.name
        if not re.fullmatch(r'^[a-z0-9-]+$', task_id):
            continue

        state_file = task_dir / 'state.json'
        task_status = None
        if not state_file.is_symlink() and state_file.is_file():
            try:
                data = json.loads(state_file.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    task_status = data.get('status')
            except (OSError, ValueError):
                task_status = None

        is_closed = task_status in ('merged', 'abandoned')
        suspended = False
        status_file = root / '.workspace/run/delegate' / task_id / 'status.json'
        try:
            if not check_symlink(status_file, root):
                status = json.loads(status_file.read_text(encoding='utf-8'))
                suspended = isinstance(status, dict) and status.get('outcome') == 'waiting_answer'
        except (OSError, ValueError):
            pass

        context_dir = task_dir / 'context'
        if context_dir.is_symlink() or not context_dir.is_dir():
            continue
        questions_dir = context_dir / 'questions'
        if questions_dir.is_symlink() or not questions_dir.is_dir():
            continue

        answers_dir = context_dir / 'answers'
        advisor_dir = context_dir / 'advisor'

        try:
            q_files = sorted(questions_dir.iterdir())
        except OSError:
            continue

        for q_file in q_files:
            if q_file.is_symlink() or not q_file.is_file():
                continue
            m = re.fullmatch(r'(\d+)\.md', q_file.name)
            if not m:
                continue
            num_str = m.group(1)

            has_answer = False
            if not answers_dir.is_symlink() and answers_dir.is_dir():
                a_file = answers_dir / f"{num_str}.md"
                if not a_file.is_symlink() and a_file.is_file():
                    has_answer = True

            if has_answer:
                continue

            try:
                raw_text = q_file.read_text(encoding='utf-8')
            except OSError:
                raw_text = ""
            one_line_text = ' '.join(raw_text.split())
            if len(one_line_text) > 200:
                one_line_text = one_line_text[:200]

            try:
                mtime = q_file.stat().st_mtime
            except OSError:
                mtime = time.time()

            reason = 'no_advisor'
            adv_file = advisor_dir / f"{num_str}.json"
            if not check_symlink(adv_file, root) and adv_file.is_file():
                try:
                    note = json.loads(adv_file.read_text(encoding='utf-8'))
                    reason = note.get('status') if isinstance(note, dict) else 'error'
                except (OSError, ValueError):
                    reason = 'error'
            if reason in ('no_advisor', 'unknown', 'unavailable', 'error'):
                alerts.emit(root, task_id, num_str, reason)

            if is_closed:
                closed_questions.append(
                    {
                        'type': 'closed_agent',
                        'task_id': task_id,
                        'num': num_str,
                        'mtime': mtime,
                        'text': one_line_text,
                    }
                )
            else:
                advisor_unknown = False
                if not advisor_dir.is_symlink() and advisor_dir.is_dir():
                    adv_file = advisor_dir / f"{num_str}.json"
                    if not adv_file.is_symlink() and adv_file.is_file():
                        try:
                            adv_data = json.loads(adv_file.read_text(encoding='utf-8'))
                            if isinstance(adv_data, dict) and adv_data.get('status') == 'unknown':
                                advisor_unknown = True
                        except (OSError, ValueError):
                            pass

                agent_questions.append(
                    {
                        'type': 'agent',
                        'task_id': task_id,
                        'num': num_str,
                        'mtime': mtime,
                        'text': one_line_text,
                        'age': format_agent_age(mtime),
                        'advisor_unknown': advisor_unknown,
                        'suspended': suspended,
                    }
                )

    agent_questions.sort(key=lambda q: (not q['suspended'], q['mtime'], q['task_id'], q['num']))
    closed_questions.sort(key=lambda q: (q['mtime'], q['task_id'], q['num']))
    return agent_questions, closed_questions


class UsageArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f"{self.prog}: erreur: {message}\n")
        sys.exit(64)


def check_answer_args(ident, text, by):
    """Valide les arguments de `answer` ; renvoie (tâche, numéro sur trois chiffres)."""
    match = re.fullmatch(ANSWER_ID_PATTERN, ident)
    if match is None:
        raise AnswerError("identifiant mal formé : attendu <tâche>/<numéro>", 64)
    if not text.strip():
        raise AnswerError("texte de réponse vide", 64)
    if by is None or not by.strip():
        raise AnswerError("--by absent ou vide", 64)
    has_control = any(ord(char) < CONTROL_CHAR_LIMIT or ord(char) == DELETE_CHAR for char in by)
    if len(by) > ANSWER_BY_MAX_LENGTH or has_control:
        raise AnswerError("--by : 64 caractères au plus, sans caractère de contrôle", 64)
    return match.group(1), f"{int(match.group(2)):03d}"


def answer_paths(root, task_id, num_str):
    """Question, copie de la question dans le worktree et réponse ; refus si un lien symbolique les touche."""
    task_dir = root / '.workspace/orchestration/tasks' / task_id
    worktree_exchange = context.worktree_exchange_dir(root, task_id)
    question = task_dir / 'context/questions' / f'{num_str}.md'
    worktree_question = worktree_exchange / 'questions' / f'{num_str}.md'
    answer = task_dir / 'context/answers' / f'{num_str}.md'
    for path in (question, worktree_question, answer):
        if check_symlink(path, root):
            raise AnswerError(f"lien symbolique interdit : {path}")
    return question, worktree_question, answer


def write_reply(root, task_id, num_str, text, by, force):  # noqa: PLR0913
    """Écrit la réponse, puis journalise `question_answered`."""
    question, worktree_question, answer = answer_paths(root, task_id, num_str)
    if not (question.is_file() or worktree_question.is_file()):
        raise AnswerError(f"question inconnue : {task_id}/{num_str}")
    if answer.is_file() and not force:
        raise AnswerError(f"déjà répondue ; --force pour remplacer : {task_id}/{num_str}")
    try:
        context.write_answer(root, task_id, int(num_str), text)
    except (OSError, ValueError) as error:
        raise AnswerError(f"écriture impossible : {error}") from error
    events.record(root, task_id, 'question_answered', {'question': num_str, 'by': by, 'forced': force})
    print(f"réponse écrite : {task_id}/{num_str} (par {by})")


def parse_answer_args(argv):
    parser = UsageArgumentParser(prog='questions.sh answer', description="Répond à une question d'agent")
    parser.add_argument('ident', help="<tâche>/<numéro>")
    parser.add_argument('text', help="Texte de la réponse")
    parser.add_argument('--by', help="Auteur de la réponse (64 caractères au plus)")
    parser.add_argument('--force', action='store_true', help="Remplace une réponse existante")
    return parser.parse_args(argv)


def answer_main(root, argv):
    """Sous-commande `answer` ; `root` est la racine transmise par questions.sh."""
    args = parse_answer_args(argv)
    registry = context.registry_root(Path(root).resolve())
    try:
        task_id, num_str = check_answer_args(args.ident, args.text, args.by)
        write_reply(registry, task_id, num_str, args.text, args.by, args.force)
    except AnswerError as error:
        print(f"questions.sh answer : {error}", file=sys.stderr)
        return error.code
    return 0


def parse_args(argv=None):
    parser = UsageArgumentParser(description="Rappel des questions en attente")
    parser.add_argument('roots', nargs='*', type=Path, help="Racines des projets")
    parser.add_argument('--alerts', action='store_true', help='Alertes non résolues avec leur âge')
    parser.add_argument('--short', action='store_true', help="Affichage court sur une ligne")
    parser.add_argument('--check', action='store_true', help="Code 1 si questions en attente, 0 sinon")
    return parser.parse_args(argv)


def main(argv=None):  # noqa: C901, PLR0915
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) >= 2 and argv[0] == 'answer':
        return answer_main(argv[1], argv[2:])
    args = parse_args(argv)
    roots = args.roots if args.roots else [Path.cwd()]

    if args.alerts:
        waiting = 0
        for root in roots:
            read_agent_questions(root)
            for row in alerts.pending_alerts(root):
                try:
                    at = datetime.datetime.fromisoformat(row['at']).timestamp()
                except (KeyError, TypeError, ValueError):
                    continue
                print(f"{root.name} : {row['task_id']}/{row['question']} ({format_agent_age(at)}) {row.get('reason', '')}")
                waiting += 1
        if not waiting:
            print('Aucune alerte en attente.')
        return 1 if args.check and waiting else 0

    projects = []
    total_waiting = 0
    total_agent = 0
    total_closed = 0

    for root in roots:
        doc_q = read_doc_questions(root)
        agent_q, closed_q = read_agent_questions(root)

        total_waiting += len(agent_q) + len(doc_q)
        total_agent += len(agent_q)
        total_closed += len(closed_q)

        lines = []
        for q in agent_q:
            adv_str = " (le conseiller local ne sait pas)" if q['advisor_unknown'] else ""
            if q['suspended']:
                adv_str += (
                    " (tâche suspendue)"
                    f" répondre : bash scripts/questions.sh answer {q['task_id']}/{q['num']}"
                    " \"<texte>\" --by <auteur>, puis relancer delegate.sh"
                )
            lines.append(f"  - [agent {q['task_id']}] ({q['age']}) {q['text']}{adv_str}")

        for q in doc_q:
            src_str = f"[{q['source']}] " if q['source'] else ""
            age_str = f"({q['age']}) " if q['age'] else ""
            lines.append(f"  - {src_str}{age_str}{q['text']}")

        for q in closed_q:
            txt_str = f" : {q['text']}" if q['text'] else ""
            lines.append(f"  - [{q['task_id']}] question restée sans réponse dans une tâche close{txt_str}")

        proj_name = root.resolve().name or str(root)
        projects.append(
            {
                'name': proj_name,
                'lines': lines,
                'has_questions': bool(agent_q or doc_q or closed_q),
            }
        )

    if args.short:
        if total_waiting == 0:
            print("Aucune question en attente.")
        else:
            print(f"Questions en attente : {total_waiting} (dont {total_agent} d'agents qui attendent)")
    else:
        if total_waiting == 0 and total_closed == 0:
            print("Aucune question en attente.")
        else:
            for idx, p in enumerate(projects):
                if idx > 0:
                    print()
                print(f"{p['name']} :")
                if p['lines']:
                    for line in p['lines']:
                        print(line)
                else:
                    print("  Aucune question en attente.")

    if args.check:
        return 1 if total_waiting > 0 else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
