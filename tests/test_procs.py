import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'template/scripts/lib'))
import procs

GROUP = 'sleep 60 & echo $!; sleep 60'


def running(pid):
    """A zombie is not a survivor: an orphan may stay defunct until its reaper runs."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except OSError:
        return True


def gone(pid, timeout=5):
    deadline = time.monotonic() + timeout
    while running(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    return not running(pid)


class ProcsTests(unittest.TestCase):
    def start_group(self, script=GROUP):
        leader = subprocess.Popen(['sh', '-c', script], stdout=subprocess.PIPE, start_new_session=True)
        self.addCleanup(lambda: (procs.kill_group(leader.pid, 0), leader.wait(), leader.stdout.close()))
        return leader, int(leader.stdout.readline())

    def test_kill_group_on_missing_group_does_nothing(self):
        done = subprocess.Popen(['sh', '-c', 'true'], start_new_session=True)
        done.wait()
        procs.kill_group(done.pid)
        procs.kill_group(done.pid, grace=0)
        self.assertFalse(procs.group_alive(done.pid))

    def test_kill_group_never_targets_the_callers_group(self):
        procs.kill_group(0)
        procs.kill_group(1)

    def test_group_alive_follows_the_group(self):
        leader, _ = self.start_group()
        self.assertTrue(procs.group_alive(leader.pid))
        procs.kill_group(leader.pid, grace=0.2)
        leader.wait()
        self.assertTrue(gone(leader.pid))

    def test_kill_group_empties_the_group_and_is_idempotent(self):
        leader, background = self.start_group()
        procs.kill_group(leader.pid, grace=0.2)
        procs.kill_group(leader.pid, grace=0.2)
        self.assertNotEqual(leader.wait(timeout=5), 0)
        self.assertTrue(gone(background))

    def test_kill_group_escalates_when_sigterm_is_ignored(self):
        leader, background = self.start_group("trap '' TERM; " + GROUP)
        procs.kill_group(leader.pid, grace=0.2)
        self.assertEqual(leader.wait(timeout=5), -9)
        self.assertTrue(gone(background))

    def test_guardian_kills_the_group_when_its_input_closes_without_release(self):
        leader, background = self.start_group()
        guardian = procs.start_guardian(leader.pid)
        guardian.stdin.close()
        self.assertEqual(guardian.wait(timeout=10), 0)
        leader.wait(timeout=5)
        self.assertTrue(gone(background))

    def test_guardian_has_emptied_the_group_by_the_time_it_exits(self):
        leader, background = self.start_group()
        guardian = procs.start_guardian(leader.pid)
        guardian.stdin.close()
        self.assertEqual(guardian.wait(timeout=10), 0)
        self.assertFalse(running(background))

    def test_released_guardian_leaves_the_group_alone(self):
        leader, background = self.start_group()
        guardian = procs.start_guardian(leader.pid)
        procs.release_guardian(guardian)
        self.assertEqual(guardian.returncode, 0)
        time.sleep(0.2)
        self.assertTrue(running(background))
        self.assertIsNone(leader.poll())

    def test_release_guardian_tolerates_a_dead_guardian(self):
        guardian = procs.start_guardian(2**22 + 12345)
        guardian.kill()
        guardian.wait()
        procs.release_guardian(guardian)
        procs.release_guardian(guardian)

    def test_guardian_survives_sigkill_of_the_callers_group(self):
        leader, background = self.start_group()
        caller = subprocess.Popen(
            [
                sys.executable,
                '-c',
                'import sys, time; sys.path.insert(0, %r); import procs; '
                'g = procs.start_guardian(%d); print(g.pid, flush=True); time.sleep(60)' % (str(Path(procs.__file__).parent), leader.pid),
            ],
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        self.addCleanup(caller.stdout.close)
        guardian_pid = int(caller.stdout.readline())
        os.killpg(caller.pid, 9)
        caller.wait()
        self.assertTrue(gone(background, timeout=10))
        self.assertTrue(gone(guardian_pid, timeout=10))


if __name__ == '__main__':
    unittest.main()
