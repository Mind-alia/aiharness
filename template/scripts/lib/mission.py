"""Tranche S1 : Préparation sûre d'espace de tâche et registre durable.

Fournit l'implémentation de task-workspace.sh (prepare, merge, close et status).
Python 3.14 standard library uniquement.
"""

from __future__ import annotations

import datetime
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import sys
from pathlib import Path
from typing import Any

# sys.dont_write_bytecode
sys.dont_write_bytecode = True

# Add lib directory to sys.path so we can import task, scope, secrets
LIB_DIR = Path(__file__).resolve().parent
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


import events
import mission_merge
import selfcheck
import state_view
import workdir
from failure_cause import closing_cause
from mission_check import ACTIVE_STATUSES, cmd_check, iter_registry_states, resolve_task_file, validate_state_data
from mission_git import (
    adopt_clone_branch,
    create_task_clone,
    prevalidate_branch_and_worktree,
    remove_legacy_worktree,
    remove_task_clone,
    resolve_base_commit,
    run_git,
    verify_git_head_and_clean,
    verify_git_root,
    verify_task_clone,
)
from mission_merge import read_project_profile, task_uses_exchange
from mission_retry import prepare_retry

# Imports written `name as name` are re-exports kept on purpose: callers and tests reach these rules through `mission`.
from mission_rules import SENSITIVE_PATHS as SENSITIVE_PATHS
from mission_rules import (
    check_infrastructure_symlinks,
    check_path_security,
    patterns_overlap,
    validate_allowed_patterns,
)
from mission_rules import is_relative_to_path as is_relative_to_path
from mission_rules import is_sensitive_pattern as is_sensitive_pattern
from mission_rules import remove_prefix as remove_prefix
from sandbox import full_image_refusal
from task import read_task
from task_cost import print_cost_line


def read_registry_and_check_reservation(root: Path, new_task_id: str, new_allowed: list[str], skip_task_id: str | None = None) -> None:
    for _, state in iter_registry_states(root):
        registered_id = state['task_id']
        if registered_id == new_task_id:
            if registered_id == skip_task_id:
                continue
            raise ValueError(f"Une tâche avec cet identifiant est déjà existante et enregistrée dans le registre : {new_task_id}")

        status = state['status']
        if status in ACTIVE_STATUSES:
            registered_allowed = state['allowed']
            for p_new in new_allowed:
                for p_reg in registered_allowed:
                    if patterns_overlap(p_new, p_reg):
                        raise ValueError(
                            f"Conflit de réservation : le motif '{p_new}' chevauche le périmètre "
                            f"de la tâche active '{registered_id}' ('{p_reg}')."
                        )


def cmd_prepare(  # noqa: C901, PLR0913, PLR0915
    root: Path, task_file_arg: str, base_ref: str | None, caller_cwd: Path, retry: bool = False, allow_slim_image: bool = False
) -> int:
    task_path = resolve_task_file(root, task_file_arg, caller_cwd)

    task_data = read_task(task_path, root)
    if not allow_slim_image:
        refusal = full_image_refusal(root, read_project_profile(root), task_uses_exchange(root, task_data))
        if refusal is not None:
            print(refusal, file=sys.stderr)
            return 3
    task_id = task_data['id']
    agent = task_data['agent']
    allowed = task_data['allowed']

    if not re.fullmatch(r'[a-z0-9-]+', task_id):
        raise ValueError(f"Identifiant de tâche invalide (id) : {task_id}")

    validate_allowed_patterns(root, allowed)

    verify_git_root(root)
    head_commit = verify_git_head_and_clean(root)
    base_commit = resolve_base_commit(root, base_ref, head_commit)

    check_infrastructure_symlinks(root)
    # Refus avant toute création : WORKSPACE_WORK_DIR relatif ou situé dans le projet.
    workdir.task_worktree(root, task_id)

    workspace_dir = root / '.workspace'
    orch_dir = workspace_dir / 'orchestration'
    tasks_dir = orch_dir / 'tasks'

    workspace_dir.mkdir(mode=0o700, exist_ok=True)
    workspace_dir.chmod(0o700)
    orch_dir.mkdir(mode=0o700, exist_ok=True)
    orch_dir.chmod(0o700)
    tasks_dir.mkdir(mode=0o700, exist_ok=True)
    tasks_dir.chmod(0o700)

    lock_file_path = orch_dir / '.lock'
    if lock_file_path.is_symlink():
        raise ValueError("Lien symbolique (symlink) interdit pour le fichier de verrou : .lock")

    open_flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, 'O_NOFOLLOW'):
        open_flags |= os.O_NOFOLLOW

    try:
        lock_fd = os.open(lock_file_path, open_flags, 0o600)
    except OSError as exc:
        if lock_file_path.is_symlink() or exc.errno == getattr(errno, 'ELOOP', None):
            raise ValueError("Lien symbolique (symlink) interdit pour le fichier de verrou : .lock") from exc
        raise

    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise ValueError("Une autre opération prepare est en cours d'exécution.") from exc

        retry_attempt = None
        if retry:
            retry_attempt = prepare_retry(
                root,
                task_id,
                lambda: load_task_state(root, task_id),
                lambda: read_registry_and_check_reservation(root, task_id, allowed, skip_task_id=task_id),
            )
        else:
            read_registry_and_check_reservation(root, task_id, allowed)

        branch_name, worktree_path, state_file = prevalidate_branch_and_worktree(root, task_id)
        target_existed_before = worktree_path.exists() or worktree_path.is_symlink()

        branch_created = False
        try:
            res_branch = run_git(root, ['branch', '--end-of-options', branch_name, base_commit], check=False)
            if res_branch.returncode != 0:
                raise ValueError(f"Échec de la création de la branche {branch_name} : {res_branch.stderr.strip()}")
            branch_created = True

            create_task_clone(root, branch_name, worktree_path)

            task_sha256 = hashlib.sha256(task_path.read_bytes()).hexdigest()
            created_at = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
            rel_task = task_path.resolve().relative_to(root.resolve()).as_posix()

            state_data = {
                "schema_version": 1,
                "task_id": task_id,
                "agent": agent,
                "agent_name": task_data['agent_name'],
                "type": task_data['type'],
                "allowed": allowed,
                "base_commit": base_commit,
                "branch": branch_name,
                "worktree": str(worktree_path),
                "status": "prepared",
                "phase": "prepared",
                "created_at": created_at,
                "task_sha256": task_sha256,
                "source_task": rel_task,
            }
            if retry_attempt is not None:
                state_data["retry_of"] = f"{task_id}-tentative{retry_attempt}"
            if allow_slim_image:
                state_data["allow_slim_image"] = True

            task_state_dir = tasks_dir / task_id
            task_state_dir.mkdir(mode=0o700, exist_ok=True)
            task_state_dir.chmod(0o700)

            state_view.write(root, task_id, state_data, 'prepare')
            prepared_detail = {'branch': branch_name, 'base_commit': base_commit}
            if retry_attempt is not None:
                prepared_detail['retry'] = retry_attempt
            events.record(root, task_id, 'prepared', prepared_detail)

            print(json.dumps(state_data, indent=2, ensure_ascii=False))
            return 0

        except Exception:
            if not target_existed_before:
                try:
                    remove_task_clone(worktree_path)
                except OSError:
                    shutil.rmtree(worktree_path, ignore_errors=True)

            if branch_created:
                # Conditional atomic branch deletion: delete only if branch still points to base_commit
                run_git(root, ['update-ref', '-d', f'refs/heads/{branch_name}', base_commit], check=False)

            task_state_dir = tasks_dir / task_id
            if task_state_dir.exists() and not task_state_dir.is_symlink():
                shutil.rmtree(task_state_dir, ignore_errors=True)
                state_view.removed(root, task_id, 'cleanup')

            raise
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except Exception:
            pass


def load_task_state(root: Path, task_id: str) -> dict[str, Any]:
    if not re.fullmatch(r'[a-z0-9-]+', task_id):
        raise ValueError(f"Identifiant de tâche invalide (id) : {task_id}")

    check_infrastructure_symlinks(root)

    orch_tasks = root / '.workspace/orchestration/tasks'
    if not orch_tasks.exists():
        raise ValueError(f"Tâche inconnue ou état introuvable : {task_id}")

    task_dir = orch_tasks / task_id
    if task_dir.is_symlink():
        raise ValueError(f"Lien symbolique (symlink) interdit sur le dossier de tâche : {task_id}")

    state_file = task_dir / 'state.json'
    if not state_file.is_file():
        raise ValueError(f"Tâche inconnue ou état introuvable : {task_id}")
    if state_file.is_symlink():
        raise ValueError(f"Lien symbolique (symlink) interdit sur l'état de tâche : {task_id}")

    try:
        data = json.loads(state_file.read_text(encoding='utf-8'))
    except Exception as exc:
        raise ValueError(f"État de tâche corrompu ou invalide pour {task_id}") from exc

    try:
        validate_state_data(data, expected_task_id=task_id)
    except Exception as exc:
        raise ValueError(f"État de tâche corrompu ou invalide pour {task_id} : {exc}") from exc

    return data


def cmd_status(root: Path, task_id: str | None = None) -> int:
    if task_id is not None:
        data = load_task_state(root, task_id)
    else:
        check_infrastructure_symlinks(root)
        data = {"active": [], "closed": []}
        tasks_dir = root / '.workspace/orchestration/tasks'
        if tasks_dir.exists():
            for entry in sorted(tasks_dir.iterdir()):
                state = load_task_state(root, entry.name)
                group = 'closed' if state['status'] in ('merged', 'abandoned') else 'active'
                data[group].append(state)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def cmd_close(root: Path, task_id: str, outcome: str, keep_branch: bool, force: bool, registry_locked: bool = False) -> int:  # noqa: C901, PLR0913, PLR0915
    verify_git_root(root)
    # Validate before opening the existing lock: close never creates a registry.
    load_task_state(root, task_id)
    lock_path = root / '.workspace/orchestration/.lock'
    flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    lock_fd = os.open(lock_path, flags)
    try:
        try:
            if not registry_locked:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("Une autre opération sur le registre est en cours.") from exc
        state = load_task_state(root, task_id)
        branch = 'workspace-task/' + task_id
        clone_based = workdir.is_clone_state(state)
        if clone_based:
            worktree = workdir.state_worktree(root, state)
            consistent = worktree == workdir.task_worktree(root, task_id)
        else:
            relative_worktree = '.workspace/worktrees/' + task_id
            worktree = root / relative_worktree
            consistent = state.get('worktree') == relative_worktree
        if state.get('branch') != branch or not consistent:
            raise ValueError("État corrompu : branche ou chemin de worktree incohérent.")
        if not clone_based:
            check_path_security(worktree, root, 'worktree')
        check_path_security(root / '.git', root, 'répertoire Git')
        if state['status'] in ('merged', 'abandoned'):
            print(f"Tâche {task_id} déjà close ({state['status']}).")
            return 0

        ref = 'refs/heads/' + branch
        present = os.path.lexists(str(worktree))
        if clone_based and present:
            # Check identity and cleanliness first, then bring the validated work home before removing anything.
            verify_task_clone(root, task_id, worktree, branch, walk=True)
            # Ignored files (build outputs, caches) are not work to protect.
            dirty = run_git(worktree, ['status', '--porcelain=v1', '--untracked-files=all']).stdout
            if dirty.strip() and not force:
                raise ValueError("Modifications non validées dans le worktree ; --force requis.")
            adopt_clone_branch(root, worktree, branch)
        tip = run_git(root, ['rev-parse', '--verify', ref]).stdout.strip()
        if outcome == 'merged':
            run_git(root, ['symbolic-ref', '--quiet', 'HEAD'])
            ancestor = run_git(root, ['merge-base', '--is-ancestor', tip, 'HEAD'], check=False)
            if ancestor.returncode != 0:
                raise ValueError("Branche de tâche non entièrement fusionnée dans la branche courante.")

        if present:
            if force:
                print(f"Clôture forcée (--force) de {task_id}.", file=sys.stderr)
            if clone_based:
                remove_task_clone(worktree)
            else:
                remove_legacy_worktree(root, worktree, ref, force)
        if outcome == 'merged' and not keep_branch:
            run_git(root, ['update-ref', '-d', ref, tip])

        state['status'] = outcome
        state['phase'] = 'abandoned' if outcome == 'abandoned' else 'done'
        state['closed_at'] = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        state['close_force'] = force
        state['branch_kept'] = outcome == 'abandoned' or keep_branch
        state_view.write(root, task_id, state, 'close')
        closed = {'outcome': outcome, 'branch_kept': state['branch_kept']}
        if outcome == 'abandoned':
            closed['cause'] = closing_cause(root, task_id)
        events.record(root, task_id, 'closed', closed)
        print(json.dumps(state, indent=2, ensure_ascii=False))
        return 0
    finally:
        os.close(lock_fd)


def cmd_merge(root: Path, task_id: str, no_verify: bool, message: str | None, no_integration: bool = False) -> int:
    """Merge a finished task into the target under the registry lock; each step lives in mission_merge.py."""
    verify_git_root(root)
    load_task_state(root, task_id)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def interrupted(signum, frame):
        raise KeyboardInterrupt("Fusion interrompue")

    signal.signal(signal.SIGTERM, interrupted)
    lock_fd = os.open(root / '.workspace/orchestration/.lock', os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("Une autre opération sur le registre est en cours.") from exc
        merge = mission_merge.open_merge(root, task_id, load_task_state(root, task_id))
        if merge is None:
            return 1
        mission_merge.read_sheet(merge)
        mission_merge.check_changes(merge)
        mission_merge.commit_leftovers(merge, message)
        mission_merge.merge_target(merge)
        mission_merge.verify_in_workshop(merge, no_verify)
        mission_merge.check_after_verify(merge)
        mission_merge.integrate(merge, no_integration)
        events.record(root, task_id, 'merged', {'commit': run_git(root, ['rev-parse', 'HEAD']).stdout.strip()})
        merge.set_phase('done')
        print_cost_line(root, task_id)
        return cmd_close(root, task_id, 'merged', False, False, registry_locked=True)
    finally:
        os.close(lock_fd)
        signal.signal(signal.SIGTERM, previous_sigterm)


def main() -> int:  # noqa: C901, PLR0915
    os.umask(0o077)
    if len(sys.argv) < 3:
        print(
            "Usage : task-workspace.sh prepare <task.md> [--base REF] [--retry] [--selfcheck] [--allow-slim-image] | check <task.md> | status [<task-id>] | merge <id> [--no-verify-again] [--no-integration] [--message TEXT] | close <id> --merged|--abandoned [--keep-branch] [--force]",
            file=sys.stderr,
        )
        return 64

    root = Path(sys.argv[1]).resolve()
    action = sys.argv[2]
    caller_cwd = Path.cwd()

    try:
        if action == 'prepare':
            task_file_arg = None
            base_ref = None
            retry = False
            check_base = False
            allow_slim_image = False
            idx = 3
            while idx < len(sys.argv):
                arg = sys.argv[idx]
                if arg == '--base':
                    if idx + 1 >= len(sys.argv):
                        print("Erreur : argument manquant pour --base", file=sys.stderr)
                        return 64
                    base_ref = sys.argv[idx + 1]
                    idx += 2
                elif arg.startswith('--base='):
                    base_ref = arg.partition('=')[2]
                    idx += 1
                elif arg == '--retry':
                    retry = True
                    idx += 1
                elif arg == '--selfcheck':
                    check_base = True
                    idx += 1
                elif arg == '--allow-slim-image':
                    allow_slim_image = True
                    idx += 1
                elif task_file_arg is None:
                    task_file_arg = arg
                    idx += 1
                else:
                    print(
                        "Usage : task-workspace.sh prepare <task.md> [--base REF] [--retry] [--selfcheck] [--allow-slim-image]",
                        file=sys.stderr,
                    )
                    return 64
            if task_file_arg is None:
                print(
                    "Usage : task-workspace.sh prepare <task.md> [--base REF] [--retry] [--selfcheck] [--allow-slim-image]", file=sys.stderr
                )
                return 64
            code = cmd_prepare(root, task_file_arg, base_ref, caller_cwd, retry, allow_slim_image)
            if code == 0 and check_base:
                code = selfcheck.prepare_selfcheck(root, read_task(resolve_task_file(root, task_file_arg, caller_cwd))['id'])
            return code
        elif action == 'check':
            if len(sys.argv) != 4:
                print("Usage : task-workspace.sh check <task.md>", file=sys.stderr)
                return 64
            return cmd_check(root, sys.argv[3], caller_cwd)
        elif action == 'merge':
            import argparse

            parser = argparse.ArgumentParser(prog='task-workspace.sh merge')
            parser.add_argument('task_id')
            parser.add_argument('--no-verify-again', action='store_true')
            parser.add_argument('--message')
            parser.add_argument('--no-integration', action='store_true')
            args = parser.parse_args(sys.argv[3:])
            return cmd_merge(root, args.task_id, args.no_verify_again, args.message, args.no_integration)
        elif action == 'close':
            options = sys.argv[4:]
            modes = [option for option in options if option in ('--merged', '--abandoned')]
            if (
                len(sys.argv) < 5
                or len(modes) != 1
                or any(option not in ('--merged', '--abandoned', '--keep-branch', '--force') for option in options)
            ):
                print('Usage : task-workspace.sh close <id> --merged|--abandoned [--keep-branch] [--force]', file=sys.stderr)
                return 64
            return cmd_close(root, sys.argv[3], modes[0][2:], '--keep-branch' in options, '--force' in options)
        elif action == 'status':
            if len(sys.argv) not in (3, 4):
                print(
                    "Usage : task-workspace.sh status [<task-id>] | close <id> --merged|--abandoned [--keep-branch] [--force]",
                    file=sys.stderr,
                )
                return 64
            return cmd_status(root, sys.argv[3] if len(sys.argv) == 4 else None)
        else:
            print(f"Action inconnue : {action}", file=sys.stderr)
            return 64
    except Exception as exc:
        print(f"Erreur task-workspace : {exc}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
