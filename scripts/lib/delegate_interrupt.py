"""What an interrupted delegation leaves behind, and whether the next one may start on it (part of delegate.py).

Measures the task's clone after an interruption (dirty or not, how many files), decides before any model call
whether a new delegation is refused, accepted with --resume-dirty or ordinary, writes the refusal, and builds
the prompt section that tells the model to reread the files the interrupted attempt left. Run as a script
(`delegate_interrupt.py <root> <task.md>`), it is the same refusal before a `--cascade`, whose restart resets the
clone to its base and would erase what the interruption left.
"""

import datetime
import json
import subprocess
import sys
import types
from pathlib import Path

import events
import safety
import workdir
from delegate_run import REPORT_SECTION

RESUME_OPTION = '--resume-dirty'
USAGE = 'usage: scripts/delegate.sh [--cascade <liste>] <fichier-de-tâche> [--resume-dirty]'
EXIT_REFUSED = 1
EXIT_USAGE = 64
SIGNAL_NAMES = ('SIGTERM', 'SIGINT', 'SIGHUP')
DEFAULT_SIGNAL = 'SIGINT'
INTERRUPTED = 'interrupted'
REFUSED = 'worktree_dirty'
EVENT_KEYS = ('signal', 'worktree_dirty', 'dirty_files')
REFUSAL = (
    'délégation : refusée (clone sale après interruption : %d fichiers) ; '
    'reprendre avec delegate.sh %s --resume-dirty, ou close --abandoned puis prepare --retry'
)
RESUME_TITLE = '## Reprise après interruption'
RESUME_SENTENCE = 'Le travail précédent a été interrompu : relis ces fichiers avant de continuer.'
# HEAD moved without any file listed (an empty commit, a commit then its revert): the section still says it.
NO_FILE_LINE = 'aucun fichier listé : HEAD a changé depuis la base, relis `git log` et `git diff` depuis la base'
GIT_TIMEOUT = 60


def utc_stamp(moment=None):
    """UTC instant as AAAA-MM-JJTHH:MM:SSZ, without fraction."""
    moment = moment or datetime.datetime.now(datetime.UTC)
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def prepared_clone(root, task_id):
    """Return (clone, base commit or None) when task-workspace.sh prepared this task, else None."""
    state_file = Path(root) / f'.workspace/orchestration/tasks/{task_id}/state.json'
    try:
        if not state_file.is_file() or state_file.is_symlink():
            return None
        state = json.loads(state_file.read_text(encoding='utf-8'))
        clone = workdir.state_worktree(root, state)
        if clone != workdir.task_worktree(root, task_id) or not clone.is_dir() or clone.is_symlink() or state.get('task_id') != task_id:
            return None
        base = state.get('base_commit')
        return clone, base if isinstance(base, str) and base else None
    except (OSError, ValueError, AttributeError):
        return None


def git_output(clone, *args):
    """Standard output of a hardened git command run in the clone, or None when it fails."""
    try:
        done = subprocess.run(
            workdir.git_command(*args, cwd=clone),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.decode('utf-8', errors='replace') if done.returncode == 0 else None


def measure(root, task_id):
    """Measure the task's clone: porcelain lines, files committed since the base, and whether it is dirty.

    Without a prepared clone, or when git cannot answer, nothing is measured: clean, zero files.
    """
    lines, committed, moved = [], [], False
    prepared = prepared_clone(root, task_id)
    if prepared is not None and not workdir.unsafe_clone_config(prepared[0]):
        clone, base = prepared
        status = git_output(clone, 'status', '--porcelain=v1', '--untracked-files=all')
        lines = [line for line in (status or '').split('\n') if line]
        if base is not None:
            head = git_output(clone, 'rev-parse', '--verify', '-q', 'HEAD')
            start = git_output(clone, 'rev-parse', '--verify', '-q', base + '^{commit}')
            moved = head is not None and start is not None and head != start
            names = git_output(clone, 'diff', '--name-only', base, 'HEAD') if moved else None
            committed = [name for name in (names or '').split('\n') if name]
    return types.SimpleNamespace(lines=lines, committed=committed, dirty=bool(lines) or moved, count=len(lines) + len(committed))


def interruption(root, task_id, signal_name):
    """The status.json keys of an interruption, measured now (call it once the model's group is dead)."""
    found = measure(root, task_id)
    name = signal_name if signal_name in SIGNAL_NAMES else DEFAULT_SIGNAL
    return dict(interrupted_at=utc_stamp(), signal=name, worktree_dirty=found.dirty, dirty_files=found.count)


def event_detail(status):
    """The keys an interrupted status adds to its outcome event; nothing for any other outcome."""
    if status.get('outcome') != INTERRUPTED:
        return {}
    return {key: status[key] for key in EVENT_KEYS if key in status}


def read_previous(root, run):
    """The previous status.json of the task as a dict (empty when absent or unreadable); a symbolic link on the way from
    `root` to the file, the run folder included, is refused by `safety.check_no_symlinks`."""
    path = Path(run) / 'status.json'
    safety.check_no_symlinks(path, Path(root))
    try:
        previous = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return previous if isinstance(previous, dict) else {}


def left_dirty(previous):
    """True when the previous delegation was interrupted, or refused, on a dirty clone."""
    return previous.get('outcome') in (INTERRUPTED, REFUSED) and previous.get('worktree_dirty') is True


def sheet_name(root, task_path):
    """The sheet path relative to the project root, or absolute when it lies outside, links resolved on both sides."""
    resolved = Path(task_path).resolve()
    try:
        return str(resolved.relative_to(Path(root).resolve()))
    except ValueError:
        return str(resolved)


def write_refusal(d, previous, count, sheet):
    """Keep the previous status.json but for the outcome, the new count and the end; journal it and say why on stdout."""
    status = dict(previous, outcome=REFUSED, dirty_files=count, ended_at=datetime.datetime.now(datetime.UTC).isoformat())
    safety.check_no_symlinks(d.run, Path(d.root))
    safety.write_file_secure(d.run / 'status.json', json.dumps(status, ensure_ascii=False, indent=2) + '\n')
    events.record(d.root, d.task['id'], 'outcome', {'outcome': REFUSED, 'attempts': status.get('attempts'), 'dirty_files': count})
    print(REFUSAL % (count, sheet))


def gate(d, task_path, requested):
    """Decide, before any model call, how a delegation starts on what an interruption left.

    Return None to go on (d.resumed_dirty and d.resume_lines say whether it is a resumption), or the exit code
    of the refused delegation (already explained). Sets d.run, which open_run sets again to the same folder.
    """
    d.resumed_dirty, d.resume_lines = False, []
    d.run = d.root / '.workspace/run/delegate' / d.task['id']
    previous = read_previous(d.root, d.run)
    if not left_dirty(previous):
        if requested:
            print(USAGE, file=sys.stderr)
            return EXIT_USAGE
        return None
    found = measure(d.root, d.task['id'])
    if not found.dirty:
        return None
    if not requested:
        write_refusal(d, previous, found.count, sheet_name(d.root, task_path))
        return EXIT_REFUSED
    d.resumed_dirty, d.resume_lines = True, found.lines + found.committed
    return None


def resume_section(lines):
    """The prompt section naming the files the interrupted attempt left (a line says so when none is listed)."""
    return '\n'.join([RESUME_TITLE, '', *('- ' + line for line in lines or [NO_FILE_LINE]), '', RESUME_SENTENCE])


def with_resume(prompt, lines):
    """The prompt with the resume section just before the report section (at the end if that one is missing); an empty
    list of lines still gets the section, with NO_FILE_LINE."""
    head, marker, tail = prompt.rpartition(REPORT_SECTION)
    return head + resume_section(lines) + '\n\n' + marker + tail if marker else prompt + '\n\n' + resume_section(lines)


def cascade_gate(root, task_path):
    """Exit code before a `--cascade` (which never takes --resume-dirty): 1 with the refusal of `gate` when an
    interruption left the clone dirty, else 0. A sheet it cannot read is left to cascade.py, which checks it with
    the model list and says what is wrong; a symbolic link on the way to the run folder is refused (64)."""
    try:
        from task import read_task

        task = read_task(Path(task_path), Path(root))
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    try:
        refused = gate(types.SimpleNamespace(root=Path(root), task=task), task_path, False)
    except (OSError, ValueError) as error:
        print('delegate_interrupt.py: ' + str(error), file=sys.stderr)
        return EXIT_USAGE
    return 0 if refused is None else refused


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(USAGE, file=sys.stderr)
        sys.exit(EXIT_USAGE)
    sys.exit(cascade_gate(sys.argv[1], sys.argv[2]))
