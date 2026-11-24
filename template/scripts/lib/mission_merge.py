"""Steps of task-workspace.sh merge, chained by cmd_merge in mission.py.

In order: open_merge checks the registry state and the task's workshop, read_sheet reads the task sheet,
check_changes refuses files outside the scope or a branch without implementation, commit_leftovers commits what
the executor left uncommitted, merge_target brings the target into the task branch, verify_in_workshop runs the
verify command, the hidden tests and the breaker instruments, check_after_verify makes sure nothing moved, and
integrate merges into the target, through the integration gate when one is configured. Any refusal raises
ValueError and leaves the target unchanged.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from secrets import known_values, mask_secrets
from typing import Any

import correction
import events
import integration_logs
import state_view
import task_phase
import workdir
from breaker import has_acceptance, instrument_names, merge_refusal, run_acceptance, run_instruments
from isolation import choose_isolation
from mission_git import adopt_clone_branch, fetch_target_head, run_git, verify_git_head_and_clean, verify_task_clone
from mission_rules import check_path_security
from scope import matches
from task import read_task


def read_project_profile(root: Path) -> str:
    profile = 'docs'
    profile_path = root / 'project.profile'
    if profile_path.is_file() and not profile_path.is_symlink():
        try:
            profile = profile_path.read_text(encoding='utf-8').strip()
        except OSError:
            profile = 'docs'
    return profile


def task_uses_exchange(root: Path, data: dict[str, Any]) -> bool:
    """Same decision as delegate.sh: explicit header, else the project profile."""
    profile = read_project_profile(root)
    try:
        return choose_isolation(data, profile)[0] == 'exchange'
    except ValueError:
        return data.get('isolation') == 'exchange'


def sandbox_command(root: Path) -> list[str]:
    return ['bash', str(root / 'scripts/sandbox.sh')]


@dataclass
class Merge:
    """What one merge knows: the registry state, the task branch and workshop, the sheet and the target head."""

    root: Path
    task_id: str
    state: dict[str, Any]
    branch: str
    worktree: Path
    clone_based: bool
    recorded_worktree: Any = None
    task_path: Path = Path()
    data: dict[str, Any] = field(default_factory=dict)
    target_head: str = ''
    records: list[str] = field(default_factory=list)

    def set_phase(self, phase: str) -> None:
        # The in-memory state is dumped later: keep it in step with the file.
        self.state['phase'] = phase
        task_phase.set_phase(self.root, self.task_id, phase)

    def save_state(self) -> None:
        state_view.write(self.root, self.task_id, self.state, 'integration')

    def uses_exchange(self) -> bool:
        return task_uses_exchange(self.root, self.data)


@dataclass
class Gate:
    """The integration gate of one merge: its throwaway worktree, its command, its time limit and its sandbox."""

    worktree: Path
    command: str
    timeout: float
    exchange: bool


def open_merge(root: Path, task_id: str, state: dict[str, Any]) -> Merge | None:
    """Check the registry state and the task's workshop; return None when the clone's git config is refused."""
    if state['status'] in ('merged', 'abandoned'):
        raise ValueError("Tâche déjà close.")
    branch = 'workspace-task/' + task_id
    recorded_worktree = state.get('worktree')
    clone_based = workdir.is_clone_state(state)
    if clone_based:
        worktree = workdir.state_worktree(root, state)
        consistent = worktree == workdir.task_worktree(root, task_id)
    else:
        worktree = root / '.workspace/worktrees' / task_id
        consistent = recorded_worktree == '.workspace/worktrees/' + task_id
    if state.get('branch') != branch or not consistent:
        raise ValueError("État corrompu : branche ou chemin de worktree incohérent.")
    verify_git_head_and_clean(root)
    run_git(root, ['symbolic-ref', '--quiet', 'HEAD'])
    if clone_based:
        try:
            workdir.check_clone_config(worktree)
        except ValueError as exc:
            print('task-workspace.sh: ' + str(exc), file=sys.stderr)
            return None
        verify_task_clone(root, task_id, worktree, branch)
    else:
        check_path_security(worktree, root, 'worktree')
        if not worktree.is_dir():
            raise ValueError("Worktree de tâche introuvable.")
        if run_git(worktree, ['symbolic-ref', '--quiet', 'HEAD']).stdout.strip() != 'refs/heads/' + branch:
            raise ValueError("Branche du worktree incohérente.")
    return Merge(root, task_id, state, branch, worktree, clone_based, recorded_worktree)


def read_sheet(m: Merge) -> None:
    m.set_phase('merging')
    m.task_path = m.root / m.state['source_task']
    check_path_security(m.task_path, m.root)
    m.data = read_task(m.task_path)


def record_paths(records: list[str]) -> set[str]:
    """Paths named by `git status --porcelain=v1 -z`, both ends of a rename or copy included."""
    paths = set()
    index = 0
    while index < len(records) and records[index]:
        record = records[index]
        paths.add(record[3:])
        index += 1
        if 'R' in record[:2] or 'C' in record[:2]:
            paths.add(records[index])
            index += 1
    return paths


def check_changes(m: Merge) -> None:
    """Refuse files outside the allowed scope, committed or not, and a branch that implements nothing."""
    root, worktree = m.root, m.worktree
    # Measured from the common ancestor with the target, so target changes already
    # integrated by an earlier merge attempt never count as task changes.
    # A clone has no history: it fetches the target head first (the combined version is checked there).
    m.target_head = fetch_target_head(root, worktree) if m.clone_based else run_git(root, ['rev-parse', 'HEAD']).stdout.strip()
    scope_base = run_git(worktree, ['merge-base', 'HEAD', m.target_head]).stdout.strip()
    paths = set(run_git(worktree, ['diff', '--name-only', '-z', '--no-renames', scope_base, 'HEAD', '--']).stdout.split('\0'))
    m.records = run_git(worktree, ['status', '--porcelain=v1', '-z', '--untracked-files=all']).stdout.split('\0')
    paths |= record_paths(m.records)
    violations = sorted(path for path in paths if path and not any(matches(path, pattern) for pattern in m.state['allowed']))
    if violations:
        raise ValueError("Fichiers hors périmètre : " + ', '.join(violations))
    refusal = merge_refusal(root, m.task_id, worktree, m.state['base_commit'])
    if refusal:
        events.record(root, m.task_id, 'outcome', {'outcome': 'no_implementation'})
        raise ValueError(refusal)


def commit_leftovers(m: Merge, message: str | None) -> None:
    """Commit what the executor left uncommitted, titled after the sheet unless a message is given."""
    if not m.records[0]:
        return
    if message is None:
        title = next((line.strip().lstrip('#').strip() for line in m.data['body'].splitlines() if line.strip()), m.task_id)
        message = title if re.match(r'^[a-z]+(?:\([^\n]+\))?: .+', title) else 'feat: ' + title
    if not message.strip():
        raise ValueError("Message de commit vide.")
    run_git(m.worktree, ['add', '--all'])
    run_git(m.worktree, ['commit', '-m', message])


def merge_failure(m: Merge, merge_root: Path, result: subprocess.CompletedProcess) -> None:
    """Abort a failed merge; record conflicts in the registry; always raise."""
    conflicts = run_git(merge_root, ['diff', '--name-only', '--diff-filter=U', '-z']).stdout
    merge_head = run_git(merge_root, ['rev-parse', '--verify', 'MERGE_HEAD'], check=False)
    if merge_head.returncode == 0:
        run_git(merge_root, ['merge', '--abort'])
    if conflicts:
        m.state['status'] = 'merge_conflict'
        m.state['conflict_files'] = [path for path in conflicts.split('\0') if path]
        m.set_phase('failed')
        state_view.write(m.root, m.task_id, m.state, 'merge')
        events.record(m.root, m.task_id, 'outcome', {'outcome': 'merge_conflict'})
    names = ', '.join(path for path in conflicts.split('\0') if path)
    raise ValueError("Fusion refusée" + (" (conflits : " + names + ")" if names else "") + " : " + result.stderr + result.stdout)


def merge_target(m: Merge) -> None:
    result = run_git(m.worktree, ['merge', '--no-edit', m.target_head], check=False)
    if result.returncode:
        merge_failure(m, m.worktree, result)


def acceptance_execute(args, cwd):
    result = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.returncode, result.stdout, result.stderr


def run_verify(m: Merge, exchange: bool) -> None:
    verify = m.root if exchange else m.worktree
    args = ['bash', str(verify / 'scripts/verify-task.sh')]
    if exchange:
        args += ['--sandbox', m.task_id]
    subprocess.run(args + [str(m.task_path)], cwd=m.worktree, check=True)


def hidden_tests(m: Merge, exchange: bool) -> None:
    """Run the hidden tests (a failure refuses the merge) and the breaker instruments (only reported)."""
    root, task_id, data = m.root, m.task_id, m.data
    skip = correction.unresolved(root, task_id)
    if skip:
        print('ronde de correction rétablie : %d faille(s) non corrigée(s) : %s' % (len(skip), ', '.join(skip)))
    if has_acceptance(data, root):
        code, feedback = run_acceptance(data, root, m.worktree, acceptance_execute, exchange, skip=skip)
        if code:
            m.state['outcome'] = 'acceptance_failed'
            m.set_phase('failed')
            state_view.write(root, task_id, m.state, 'acceptance')
            events.record(root, task_id, 'outcome', {'outcome': 'acceptance_failed'})
            raise ValueError('acceptance_failed : ' + mask_secrets(feedback, known_values(root)))
    if instrument_names(data, root):
        failed = [
            r['test']
            for r in run_instruments(data, root, m.worktree, acceptance_execute, exchange)
            if not r['passed'] and r['test'] not in skip
        ]
        if failed:
            print('casseur (instrument) : %d échec(s)' % len(failed))
            for name in failed:
                print(name)


def verify_in_workshop(m: Merge, no_verify: bool) -> None:
    """Verify the merged task branch in its workshop (its sandbox in exchange mode)."""
    if no_verify and not has_acceptance(m.data, m.root) and not instrument_names(m.data, m.root):
        return
    sandbox = sandbox_command(m.root)
    exchange = m.uses_exchange()
    try:
        if exchange:
            subprocess.run(sandbox + ['start', m.task_id], cwd=m.root, check=True)
        if not no_verify:
            run_verify(m, exchange)
        hidden_tests(m, exchange)
    finally:
        if exchange:
            subprocess.run(sandbox + ['stop', m.task_id], cwd=m.root, check=True)


def check_after_verify(m: Merge) -> None:
    # Verification must not leave changes which would be lost at closure.
    # Ignored files are build outputs of the verification (dist/, caches), not lost work.
    if run_git(m.worktree, ['status', '--porcelain=v1', '--untracked-files=all']).stdout.strip():
        raise ValueError("Modifications non validées après vérification dans le worktree.")
    verify_git_head_and_clean(m.root)
    if run_git(m.root, ['rev-parse', 'HEAD']).stdout.strip() != m.target_head:
        raise ValueError("la cible a changé pendant la vérification, relancer merge")
    if m.clone_based:
        adopt_clone_branch(m.root, m.worktree, m.branch)


def integration_command(root: Path) -> str | None:
    """The command of the integration gate: .workspace-integration (one line), else scripts/gate.sh, else none."""
    configuration = root / '.workspace-integration'
    if configuration.is_file():
        lines = configuration.read_text(encoding='utf-8').splitlines()
        if len(lines) != 1 or not lines[0].strip():
            raise ValueError("Commande .workspace-integration vide ou invalide (une ligne attendue).")
        return lines[0].strip()
    if (root / 'scripts/gate.sh').is_file():
        return 'bash scripts/gate.sh'
    return None


def run_gate_process(args: list[str], gate: Gate) -> tuple[bytes, int]:
    process = subprocess.Popen(args, cwd=gate.worktree, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        try:
            output, _ = process.communicate(timeout=gate.timeout)
            code = process.returncode
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate()
            output += b'\nIntegration timeout\n'
            code = 124
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    return output, code


def keep_gate_logs(m: Merge, gate: Gate, output: bytes, values: list[str]) -> None:
    try:
        integration_logs.keep(
            m.root,
            m.task_id,
            gate.worktree,
            output.decode('utf-8', errors='replace'),
            sorted(set(values) | set(known_values(gate.worktree))),
        )
    except Exception as error:
        print("Journaux d'intégration non copiés : " + mask_secrets(str(error), values), file=sys.stderr)


def gate_failed(m: Merge, gate: Gate, output: bytes, values: list[str], code: int) -> None:
    """Record a red gate in the registry with the end of its output, masked; always raise."""
    m.state['status'] = 'integration_failed'
    m.set_phase('failed')
    values = sorted(set(values) | set(known_values(m.root)) | set(known_values(gate.worktree)))
    m.state['integration']['output'] = '\n'.join(mask_secrets(output.decode('utf-8', errors='replace'), values).splitlines()[-40:])
    events.record(m.root, m.task_id, 'outcome', {'outcome': 'integration_failed'})
    raise ValueError("Porte d'intégration rouge (code " + str(code) + ") ; cible inchangée.")


def gate_in(m: Merge, gate: Gate, values: list[str]) -> None:
    """Merge into a throwaway worktree of the target, run the gate there, then fast-forward the target to it."""
    root, task_id = m.root, m.task_id
    run_git(root, ['worktree', 'add', '--detach', str(gate.worktree), m.target_head])
    result = run_git(gate.worktree, ['merge', '--no-ff', '-m', 'merge: task ' + task_id, m.branch], check=False)
    if result.returncode:
        merge_failure(m, gate.worktree, result)
    checked_commit = run_git(gate.worktree, ['rev-parse', 'HEAD']).stdout.strip()
    args = ['bash', '-c', gate.command]
    if gate.exchange:
        # sandbox.sh derives its bind mount from the registry worktree.
        m.state['worktree'] = str(gate.worktree.relative_to(root))
        m.save_state()
        subprocess.run(sandbox_command(root) + ['start', task_id], cwd=root, check=True)
        args = sandbox_command(root) + ['exec', task_id, '--'] + args
    started = time.monotonic()
    output, code = run_gate_process(args, gate)
    keep_gate_logs(m, gate, output, values)
    m.state['integration'].update(exit=code, seconds=round(time.monotonic() - started, 3), status='passed' if code == 0 else 'failed')
    if code:
        gate_failed(m, gate, output, values, code)
    if (
        run_git(gate.worktree, ['rev-parse', 'HEAD']).stdout.strip() != checked_commit
        or run_git(gate.worktree, ['status', '--porcelain', '--untracked-files=all']).stdout.strip()
    ):
        raise ValueError("La porte d'intégration a modifié le résultat fusionné.")
    verify_git_head_and_clean(root)
    if run_git(root, ['rev-parse', 'HEAD']).stdout.strip() != m.target_head:
        raise ValueError("la cible a changé pendant la porte d'intégration, relancer merge")
    run_git(root, ['merge', '--ff-only', checked_commit])


def run_gate(m: Merge, command: str, values: list[str]) -> None:
    """Run the integration gate; whatever happens, stop its sandbox, restore the registry and drop its worktree."""
    timeout = float(os.environ.get('WORKSPACE_INTEGRATION_TIMEOUT', '1800'))
    if not 0 < timeout < float('inf'):
        raise ValueError('WORKSPACE_INTEGRATION_TIMEOUT doit être positif et fini.')
    integration = m.root / '.workspace/integration' / m.task_id
    check_path_security(integration, m.root, 'worktree intégration')
    if integration.exists():
        raise ValueError("Worktree d'intégration déjà présent.")
    integration.parent.mkdir(parents=True, exist_ok=True)
    gate = Gate(integration, command, timeout, m.uses_exchange())
    try:
        gate_in(m, gate, values)
    finally:
        try:
            if gate.exchange:
                subprocess.run(sandbox_command(m.root) + ['stop', m.task_id], cwd=m.root, check=True)
        finally:
            m.state['worktree'] = m.recorded_worktree
            try:
                m.save_state()
            finally:
                if integration.exists():
                    run_git(m.root, ['worktree', 'remove', '--force', '--', str(integration)])


def integrate(m: Merge, no_integration: bool) -> None:
    """Merge the task branch into the target, through the integration gate unless there is none or it is skipped."""
    # project.profile is a single profile name for all existing readers.
    command = integration_command(m.root)
    values = known_values(m.root)
    m.state['integration'] = dict(
        command=mask_secrets(command, values) if command else None, exit=None, seconds=0, status='skipped' if no_integration else 'none'
    )
    if not no_integration and command is not None:
        run_gate(m, command, values)
        return
    print("Porte d'intégration sautée (--no-integration)." if no_integration else "aucune porte d'intégration configurée")
    m.save_state()
    result = run_git(m.root, ['merge', '--no-ff', '-m', 'merge: task ' + m.task_id, m.branch], check=False)
    if result.returncode:
        merge_failure(m, m.root, result)
