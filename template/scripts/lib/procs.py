"""Process groups of the circuit: liveness, bounded wait, kill with a grace period, and a guardian process that
empties the group if the circuit itself is killed.
"""

import os
import signal
import subprocess
import sys
import time


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_empty(pgid, timeout):
    deadline = time.monotonic() + timeout
    while group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.02)


def kill_group(pgid, grace=1.0):
    """SIGTERM the group, wait up to `grace` seconds for it to empty, then SIGKILL it."""
    if pgid <= 1:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    _wait_empty(pgid, grace)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return
    # SIGKILL is asynchronous: let the group disappear before returning (an unreaped leader stays a zombie, hence the bound)
    _wait_empty(pgid, min(grace, 0.5))


def start_guardian(pgid):
    """Start a watcher in its own session that kills `pgid` unless the caller releases it.

    The watcher outlives a SIGKILL sent to the caller's group: it sees its stdin
    close without the `release` line and then empties the group.
    """
    return subprocess.Popen(
        [sys.executable, '-I', os.path.abspath(__file__), '--guard', str(pgid)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def release_guardian(guardian):
    try:
        try:
            guardian.stdin.write(b'release\n')
        finally:
            guardian.stdin.close()
    except (OSError, ValueError):
        pass
    try:
        guardian.wait(timeout=2)
    except subprocess.TimeoutExpired:
        guardian.kill()
        guardian.wait()


if __name__ == '__main__' and sys.argv[1:2] == ['--guard']:
    if b'release' not in sys.stdin.buffer.read().split():
        kill_group(int(sys.argv[2]))
