"""Tranche S2 : Courtier d'exécution et protocole d'échange (Exchange).

Agent :
- scripts/run-in-sandbox.sh -- <commande...>
  Écrit dans <worktree>/.workspace/exchange/requests/NNN.json
  Attend <worktree>/.workspace/exchange/results/NNN.json
  Affiche la sortie et sort avec le code de la commande.

Orchestrateur :
- Thread broker dans delegate.sh
  Surveille requests/
  Exécute chaque demande par sandbox.sh exec <id> -- ...
  Écrit le résultat atomiquement dans results/NNN.json
  Journalise dans .workspace/orchestration/tasks/<id>/exchange/
  Synchronise questions et réponses avec context/
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

LIB_DIR = Path(__file__).resolve().parent
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import workdir

try:
    from safety import check_no_symlinks, write_file_secure
except ImportError:
    pass

LIMIT_OUTPUT = 200 * 1024  # 200 Ko


def write_json_atomic(target_path: Path, data: Any) -> None:
    if target_path.is_symlink():
        raise ValueError(f"Lien symbolique interdit : {target_path}")
    parent = target_path.parent
    check_no_symlinks(parent, parent)
    tmp_path = parent / f"{target_path.name}.tmp.{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp_path, flags, 0o600)
    with open(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    tmp_path.chmod(0o600)
    os.replace(tmp_path, target_path)


def find_exchange_dir(root: Path) -> Path:  # noqa: C901
    if (root / '.workspace/exchange').is_dir():
        return root / '.workspace/exchange'

    cwd = Path.cwd().resolve()
    if (cwd / '.workspace/exchange').is_dir():
        return cwd / '.workspace/exchange'
    for parent in cwd.parents:
        if (parent / '.workspace/exchange').is_dir():
            return parent / '.workspace/exchange'

    agent_root = os.environ.get('WORKSPACE_AGENT_ROOT')
    if agent_root:
        p = Path(agent_root).resolve()
        if (p / '.workspace/exchange').is_dir():
            return p / '.workspace/exchange'

    task_id = os.environ.get('TASK_ID') or os.environ.get('WORKSPACE_TASK_ID')
    if task_id:
        try:
            p = workdir.task_worktree(root, task_id) / '.workspace/exchange'
        except ValueError:
            p = None
        if p is not None and p.is_dir():
            return p

    try:
        wt_bases = [workdir.work_base(root), root / '.workspace/worktrees']
    except ValueError:
        wt_bases = [root / '.workspace/worktrees']
    for wt_base in wt_bases:
        if wt_base.is_dir():
            for wt in wt_base.iterdir():
                if wt.is_dir() and (wt / '.workspace/exchange').is_dir():
                    return wt / '.workspace/exchange'

    raise ValueError(f"Dossier exchange introuvable depuis {root} ou {cwd}")


def cmd_request(root: Path, args: list[str]) -> int:  # noqa: C901
    if not args:
        print("Usage : run-in-sandbox.sh -- <commande...>", file=sys.stderr)
        return 64

    if args[0] == '--':
        args = args[1:]

    if not args:
        print("Usage : run-in-sandbox.sh -- <commande...>", file=sys.stderr)
        return 64

    exchange_dir = find_exchange_dir(root)
    requests_dir = exchange_dir / 'requests'
    results_dir = exchange_dir / 'results'

    for d in (requests_dir, results_dir):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        d.chmod(0o700)
        check_no_symlinks(d, exchange_dir)

    existing_nums = []
    for entry in requests_dir.iterdir():
        if entry.is_symlink():
            raise ValueError(f"Lien symbolique interdit dans requests : {entry}")
        m = re.fullmatch(r'(\d+)\.json', entry.name)
        if m:
            existing_nums.append(int(m.group(1)))

    next_num = max(existing_nums, default=0) + 1
    num_str = f"{next_num:03d}"

    req_file = requests_dir / f"{num_str}.json"
    req_data = {"command": args, "timestamp": datetime.datetime.now(datetime.UTC).isoformat()}

    write_json_atomic(req_file, req_data)

    res_file = results_dir / f"{num_str}.json"
    timeout = float(os.environ.get('RUN_TIMEOUT', '600'))
    start_time = time.monotonic()

    while True:
        if res_file.is_symlink():
            raise ValueError(f"Lien symbolique interdit pour le résultat : {res_file}")

        if res_file.is_file():
            try:
                res_data = json.loads(res_file.read_text(encoding='utf-8'))
                exit_code = int(res_data.get('exit_code', res_data.get('code', 0)))
                output = res_data.get('output', '')
                if output:
                    print(output, end='' if output.endswith('\n') else '\n')
                return exit_code
            except Exception as exc:
                print(f"Erreur à la lecture du résultat {res_file} : {exc}", file=sys.stderr)
                return 1

        elapsed = time.monotonic() - start_time
        if elapsed >= timeout:
            print(f"run-in-sandbox: Délai d'attente dépassé ({timeout}s) sans résultat du courtier.", file=sys.stderr)
            return 124

        remaining = timeout - elapsed
        time.sleep(min(0.05, max(0.01, remaining)))


def execute_request(root: Path, task_id: str, req_file: Path, req_num: int, log_dir: Path) -> tuple[int, str]:
    req_name = f"{req_num:03d}.json"
    raw_content = ""
    try:
        raw_content = req_file.read_text(encoding='utf-8')
        log_req = log_dir / f"request-{req_name}"
        write_file_secure(log_req, raw_content)
    except Exception:
        pass

    try:
        data = json.loads(raw_content)
    except Exception:
        return 64, f"Erreur : demande {req_name} malformée (JSON invalide)."

    if not isinstance(data, dict):
        return 64, f"Erreur : demande {req_name} malformée (doit être un objet JSON)."

    command = data.get('command')
    if not isinstance(command, list) or not command or not all(isinstance(c, str) and c for c in command):
        return 64, f"Erreur : demande {req_name} malformée (le champ 'command' doit être une liste non vide de chaînes)."

    sandbox_script = root / 'scripts/sandbox.sh'
    cmd = ['bash', str(sandbox_script), 'exec', task_id, '--'] + command

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        exit_code = proc.returncode
        output = proc.stdout
        if proc.stderr:
            if output and not output.endswith('\n'):
                output += '\n'
            output += proc.stderr
    except Exception as exc:
        exit_code = 1
        output = f"Erreur d'exécution de la commande : {exc}"

    try:
        from secrets import known_values, mask_secrets

        output = mask_secrets(output, known_values(root))
    except Exception:
        pass

    raw_bytes = output.encode('utf-8', errors='replace')
    if len(raw_bytes) > LIMIT_OUTPUT:
        output = raw_bytes[:LIMIT_OUTPUT].decode('utf-8', errors='replace') + '\n[TRUNCATED]\n'

    return exit_code, output


def run_broker(root: Path, task_id: str, stop_event: threading.Event, poll_interval: float = 0.05) -> None:  # noqa: C901, PLR0915
    check_no_symlinks(root, root)
    task_dir = root / f'.workspace/orchestration/tasks/{task_id}'
    state_file = task_dir / 'state.json'
    if not state_file.is_file():
        return
    try:
        state = json.loads(state_file.read_text(encoding='utf-8'))
        worktree = workdir.state_worktree(root, state)
    except Exception:
        return

    exchange_dir = worktree / '.workspace/exchange'
    requests_dir = exchange_dir / 'requests'
    results_dir = exchange_dir / 'results'
    questions_dir = exchange_dir / 'questions'
    answers_dir = exchange_dir / 'answers'

    for d in (requests_dir, results_dir, questions_dir, answers_dir):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        d.chmod(0o700)

    context_dir = task_dir / 'context'
    ctx_questions = context_dir / 'questions'
    ctx_answers = context_dir / 'answers'
    for d in (context_dir, ctx_questions, ctx_answers):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        d.chmod(0o700)

    task_exchange_log = task_dir / 'exchange'
    task_exchange_log.mkdir(parents=True, exist_ok=True, mode=0o700)
    task_exchange_log.chmod(0o700)

    def skip_answered(number: int) -> int:
        # Une requête qui a déjà un résultat (délégation précédente de la même tâche) n'est jamais rejouée.
        while True:
            answered = results_dir / f"{number:03d}.json"
            if not answered.is_file() or answered.is_symlink():
                return number
            number += 1

    next_req = skip_answered(1)

    def sync_context() -> None:  # noqa: C901
        try:
            if questions_dir.is_dir():
                for qf in sorted(questions_dir.iterdir()):
                    if qf.is_file() and not qf.is_symlink() and re.fullmatch(r'\d+\.md', qf.name):
                        target = ctx_questions / qf.name
                        if not target.exists():
                            write_file_secure(target, qf.read_text(encoding='utf-8'))
        except Exception:
            pass

        try:
            if ctx_answers.is_dir():
                for af in sorted(ctx_answers.iterdir()):
                    if af.is_file() and not af.is_symlink() and re.fullmatch(r'\d+\.md', af.name):
                        target = answers_dir / af.name
                        if not target.exists():
                            write_file_secure(target, af.read_text(encoding='utf-8'))
            if answers_dir.is_dir():
                for af in sorted(answers_dir.iterdir()):
                    if af.is_file() and not af.is_symlink() and re.fullmatch(r'\d+\.md', af.name):
                        target = ctx_answers / af.name
                        if not target.exists():
                            write_file_secure(target, af.read_text(encoding='utf-8'))
        except Exception:
            pass

    while True:
        sync_context()

        req_name = f"{next_req:03d}.json"
        req_file = requests_dir / req_name
        res_file = results_dir / req_name

        if req_file.is_file() and not req_file.is_symlink():
            exit_code, output = execute_request(root, task_id, req_file, next_req, task_exchange_log)
            res_data = {"exit_code": exit_code, "code": exit_code, "output": output}
            write_json_atomic(res_file, res_data)

            try:
                log_res = task_exchange_log / f"result-{req_name}"
                write_json_atomic(log_res, res_data)
            except Exception:
                pass

            next_req = skip_answered(next_req + 1)
            continue

        if stop_event.is_set():
            sync_context()
            break

        stop_event.wait(poll_interval)


def main() -> int:
    os.umask(0o077)
    if len(sys.argv) < 3:
        print("Usage : exchange.py <root> request [--] <commande...>", file=sys.stderr)
        return 64

    root = Path(sys.argv[1]).resolve()
    action = sys.argv[2]

    try:
        if action == 'request':
            return cmd_request(root, sys.argv[3:])
        else:
            print(f"Action exchange inconnue : {action}", file=sys.stderr)
            return 64
    except Exception as exc:
        print(f"Erreur exchange : {exc}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
