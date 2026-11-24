"""Protocole d'échange de contexte (questions/réponses) par fichiers.

Structure :
.workspace/orchestration/tasks/<id>/context/questions/NNN.md
.workspace/orchestration/tasks/<id>/context/answers/NNN.md

Règles de sécurité :
- Permissions strictes : répertoires en 0700, fichiers en 0600.
- Refus absolu de tout lien symbolique (symlink).
- Validation de l'identifiant de tâche et vérification de son existence dans le registre S1.
- Numérotation séquentielle NNN (001, 002, ...).
- Délai d'attente borné (ASK_TIMEOUT, défaut 300s).
"""

from __future__ import annotations

import datetime
import os
import re
import sys
import time
from pathlib import Path

import workdir
from safety import check_no_symlinks, write_file_secure

sys.dont_write_bytecode = True

READ_DIR = 'read'


def get_context_dirs(root: Path, task_id: str) -> tuple[Path, Path, Path]:
    """Valide et retourne (context_dir, questions_dir, answers_dir)."""
    if not isinstance(task_id, str) or not re.fullmatch(r'[a-z0-9-]+', task_id):
        raise ValueError(f"Identifiant de tâche invalide : '{task_id}'")

    check_no_symlinks(root, root)

    task_dir = root / '.workspace/orchestration/tasks' / task_id
    state_file = task_dir / 'state.json'

    # Vérification d'existence de la tâche dans le registre S1
    if not task_dir.is_dir() or not state_file.is_file():
        raise ValueError(f"Tâche inconnue ou non enregistrée dans le registre S1 : {task_id}")

    check_no_symlinks(state_file, root)

    context_dir = task_dir / 'context'
    questions_dir = context_dir / 'questions'
    answers_dir = context_dir / 'answers'

    for d in (context_dir, questions_dir, answers_dir):
        if d.is_symlink():
            raise ValueError(f"Lien symbolique (symlink) interdit sur le répertoire de contexte : {d}")
        d.mkdir(mode=0o700, exist_ok=True)
        d.chmod(0o700)
        check_no_symlinks(d, root)

    return context_dir, questions_dir, answers_dir


def is_in_worktree(root: Path) -> tuple[bool, Path | None]:
    """Détecte si l'appel est fait depuis ou dans un worktree de tâche du projet `root`."""
    if (root / '.workspace/exchange').is_dir():
        return True, root
    root = root.resolve()
    cwd = Path.cwd().resolve()
    try:
        clone = workdir.containing_task(root, cwd)
    except ValueError:
        clone = None
    if clone is not None and (clone / '.workspace/exchange').is_dir():
        return True, clone
    try:
        rel = cwd.relative_to(root)
    except ValueError:
        return False, None
    for candidate in (cwd, *cwd.parents):
        if candidate == root:
            break
        if (candidate / '.workspace/exchange').is_dir():
            return True, candidate
    parts = rel.parts
    if len(parts) >= 3 and parts[:2] == ('.workspace', 'worktrees'):
        wt_path = root.joinpath(*parts[:3])
        if (wt_path / '.workspace/exchange').is_dir():
            return True, wt_path
    return False, None


def worktree_exchange_dir(root: Path, task_id: str) -> Path:
    """Dossier exchange du clone de la tâche (ou du worktree d'une tâche ancienne s'il existe)."""
    legacy = root / f'.workspace/worktrees/{task_id}/.workspace/exchange'
    try:
        clone = workdir.task_worktree(root, task_id) / '.workspace/exchange'
    except ValueError:
        return legacy
    return legacy if not clone.is_dir() and legacy.is_dir() else clone


def write_question_dir(questions_dir: Path, question: str) -> tuple[int, Path]:
    """Enregistre une nouvelle question dans un répertoire de questions donné."""
    questions_dir.mkdir(mode=0o700, exist_ok=True)
    questions_dir.chmod(0o700)

    existing_nums = []
    for entry in questions_dir.iterdir():
        if entry.is_symlink():
            raise ValueError(f"Lien symbolique (symlink) interdit dans questions : {entry}")
        m = re.fullmatch(r'(\d+)\.md', entry.name)
        if m:
            existing_nums.append(int(m.group(1)))

    next_num = max(existing_nums, default=0) + 1
    num_str = f"{next_num:03d}"
    q_file = questions_dir / f"{num_str}.md"
    write_file_secure(q_file, question)
    return next_num, q_file


def write_question(root: Path, task_id: str, question: str) -> tuple[int, Path]:
    """Enregistre une nouvelle question dans context/questions/NNN.md."""
    _, questions_dir, _ = get_context_dirs(root, task_id)
    return write_question_dir(questions_dir, question)


def write_answer(root: Path, task_id: str, num: int, answer: str) -> Path:
    """Enregistre une réponse dans context/answers/NNN.md et dans exchange le cas échéant."""
    _, questions_dir, answers_dir = get_context_dirs(root, task_id)
    num_str = f"{num:03d}"
    q_file = questions_dir / f"{num_str}.md"

    # Vérification éventuelle dans le worktree de tâche
    worktree_exchange = worktree_exchange_dir(root, task_id)
    wt_q_file = worktree_exchange / f'questions/{num_str}.md' if worktree_exchange.is_dir() else None

    if not q_file.is_file():
        if wt_q_file and wt_q_file.is_file():
            write_file_secure(q_file, wt_q_file.read_text(encoding='utf-8'))
        else:
            raise ValueError(f"Question {num_str} introuvable pour la tâche {task_id}.")

    if q_file.is_symlink():
        raise ValueError(f"Lien symbolique (symlink) interdit pour la question : {q_file}")

    a_file = answers_dir / f"{num_str}.md"
    write_file_secure(a_file, answer)

    # Si le worktree existe et possède exchange, y copier la réponse également
    if worktree_exchange.is_dir():
        wt_answers = worktree_exchange / 'answers'
        wt_answers.mkdir(mode=0o700, exist_ok=True)
        wt_answers.chmod(0o700)
        wt_a_file = wt_answers / f"{num_str}.md"
        write_file_secure(wt_a_file, answer)

    return a_file


def wait_for_answer(answers_dir: Path, num: int, timeout: float) -> str | None:
    """Attend l'apparition du fichier de réponse answers/NNN.md dans la limite du timeout."""
    num_str = f"{num:03d}"
    a_file = answers_dir / f"{num_str}.md"
    start_time = time.monotonic()

    while True:
        if a_file.is_symlink():
            raise ValueError(f"Lien symbolique (symlink) interdit pour la réponse : {a_file}")
        if a_file.is_file():
            return a_file.read_text(encoding='utf-8')

        elapsed = time.monotonic() - start_time
        if elapsed >= timeout:
            return None

        # Pause courte avant de re-tester
        remaining = timeout - elapsed
        time.sleep(min(0.1, max(0.01, remaining)))


def mark_read(read_dir: Path, num_str: str) -> None:
    """Écrit le marqueur read/NNN.md (date ISO UTC) : la réponse NNN est déjà parvenue à l'agent."""
    if read_dir.is_symlink():
        raise ValueError(f"Lien symbolique (symlink) interdit : {read_dir}")
    read_dir.mkdir(mode=0o700, exist_ok=True)
    read_dir.chmod(0o700)
    write_file_secure(read_dir / f"{num_str}.md", datetime.datetime.now(datetime.UTC).isoformat())


def registry_root(root: Path) -> Path:
    """Racine du dépôt principal quand le script est la copie d'un worktree ou d'un clone de tâche."""
    parts = root.parts
    if len(parts) > 3 and parts[-3] == '.workspace' and parts[-2] == 'worktrees':
        return Path(*parts[:-3])
    return workdir.project_of(root) or root


def resolve_task_id(root: Path, explicit_id: str | None = None) -> str:
    """Résout le task_id à partir de l'argument, de l'environnement ou du dossier courant."""
    if explicit_id:
        return explicit_id

    env_id = os.environ.get('TASK_ID') or os.environ.get('WORKSPACE_TASK_ID')
    if env_id:
        return env_id

    cwd = Path.cwd().resolve()
    # Anchor to this script's project registry, not the first worktrees segment
    # in an ancestor path (the kit itself may be running inside a task worktree).
    worktrees_dir = (registry_root(root) / '.workspace/worktrees').resolve()
    try:
        rel = cwd.relative_to(worktrees_dir)
        if rel.parts:
            return rel.parts[0]
    except (ValueError, Exception):
        pass
    try:
        clone = workdir.containing_task(registry_root(root), cwd)
    except ValueError:
        clone = None
    if clone is not None:
        return clone.name

    if (root / '.workspace/exchange').is_dir():
        return root.name

    raise ValueError("Identifiant de tâche non spécifié (passer <task-id> ou définir TASK_ID).")


def main() -> int:  # noqa: C901, PLR0915
    os.umask(0o077)
    if len(sys.argv) < 3:
        print("Usage : context.py <root> ask [<task-id>] \"<question>\" | answer <task-id> <NNN> \"<réponse>\"", file=sys.stderr)
        return 64

    root = Path(sys.argv[1]).resolve()
    action = sys.argv[2]

    try:
        if action == 'ask':
            if len(sys.argv) == 4:
                question = sys.argv[3]
                explicit_id = None
            elif len(sys.argv) == 5:
                explicit_id = sys.argv[3]
                question = sys.argv[4]
            else:
                print("Usage : ask.sh [<task-id>] \"<question>\"", file=sys.stderr)
                return 64

            in_wt, wt_path = is_in_worktree(root)
            if in_wt and wt_path is not None:
                q_dir = wt_path / '.workspace/exchange/questions'
                a_dir = wt_path / '.workspace/exchange/answers'
                q_dir.mkdir(mode=0o700, exist_ok=True)
                a_dir.mkdir(mode=0o700, exist_ok=True)
                check_no_symlinks(q_dir, wt_path)
                check_no_symlinks(a_dir, wt_path)
                num, _ = write_question_dir(q_dir, question)
                answers_dir = a_dir
            else:
                task_id = resolve_task_id(root, explicit_id)
                main_root = registry_root(root)
                num, _ = write_question(main_root, task_id, question)
                _, _, answers_dir = get_context_dirs(main_root, task_id)

            timeout = float(os.environ.get('ASK_TIMEOUT', '300'))
            ans = wait_for_answer(answers_dir, num, timeout)
            if ans is not None:
                try:
                    mark_read(answers_dir.parent / READ_DIR, f"{num:03d}")
                except (OSError, ValueError):
                    pass  # the answer is still delivered; at worst it is shown again on resume
                print(ans, end='' if ans.endswith('\n') else '\n')
                return 0
            else:
                print("Délai d'attente dépassé sans réponse de l'orchestrateur.", file=sys.stderr)
                print("Aucune réponse reçue (délai dépassé).", file=sys.stdout)
                return 1

        elif action == 'answer':
            if len(sys.argv) != 6:
                print("Usage : answer.sh <task-id> <NNN> \"<réponse>\"", file=sys.stderr)
                return 64

            task_id = sys.argv[3]
            try:
                num = int(sys.argv[4])
            except ValueError:
                print(f"Numéro de question invalide : {sys.argv[4]}", file=sys.stderr)
                return 64
            answer_text = sys.argv[5]

            write_answer(registry_root(root), task_id, num, answer_text)
            return 0

        else:
            print(f"Action de contexte inconnue : {action}", file=sys.stderr)
            return 64

    except Exception as exc:
        print(f"Erreur contexte : {exc}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
