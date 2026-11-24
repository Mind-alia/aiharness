from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
GATE_TEXT = 'echo "gate du projet"\n'
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import workdir  # noqa: E402


class GateSealTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Le clone de la tâche est hors du projet, jamais dans le vrai ~/.workspace-ia.
        self.work_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.work_dir.cleanup)
        patcher = mock.patch.dict(os.environ, {'WORKSPACE_WORK_DIR': os.path.realpath(self.work_dir.name)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = Path(self.temp.name) / 'project with spaces'
        self.scripts = self.root / 'scripts'
        (self.scripts / 'lib').mkdir(parents=True)
        shutil.copy2(ROOT / 'template/scripts/verify-task.sh', self.scripts / 'verify-task.sh')
        for lib_file in (ROOT / 'template/scripts/lib').glob('*.py'):
            shutil.copy2(lib_file, self.scripts / 'lib' / lib_file.name)
        (self.root / 'project.profile').write_text('python\n')
        self.write(self.scripts / 'gate.sh', GATE_TEXT)
        self.write(self.root / '.workspace/tasks/t1.md', '---\nid: t1\nagent: codex\nallowed: []\n---\n')
        # Faux sandbox : il note ses arguments au lieu de lancer un conteneur.
        self.write(self.scripts / 'sandbox.sh', '#!/bin/sh\necho "$@" >> sandbox-calls\n')
        self.worktree_gate = workdir.task_worktree(self.root, 't1') / 'scripts/gate.sh'
        self.worktree_gate.parent.mkdir(parents=True)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def verify(self):
        return subprocess.run(
            ['sh', str(self.scripts / 'verify-task.sh'), '--sandbox', 't1', '.workspace/tasks/t1.md'],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def sandbox_calls(self):
        calls = self.root / 'sandbox-calls'
        return calls.read_text() if calls.exists() else ''

    def test_identical_worktree_gate_runs_in_sandbox(self):
        self.write(self.worktree_gate, GATE_TEXT)

        res = self.verify()

        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('exec t1 -- /bin/sh scripts/gate.sh --quick', self.sandbox_calls())
        self.assertNotIn('control_tampered', res.stderr)

    def test_modified_worktree_gate_is_refused_before_running(self):
        self.write(self.worktree_gate, 'echo "gate modifié"\n')

        res = self.verify()

        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertIn('control_tampered : scripts/gate.sh du worktree diffère de celui du projet', res.stderr)
        self.assertIn('Vérification : échec (code 3).', res.stderr)
        self.assertEqual(self.sandbox_calls(), '')

    def test_missing_worktree_gate_is_refused_before_running(self):
        self.assertFalse(self.worktree_gate.exists())

        res = self.verify()

        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertIn('control_tampered', res.stderr)
        self.assertEqual(self.sandbox_calls(), '')

    def test_symlinked_worktree_gate_is_refused_even_with_same_content(self):
        target = self.root / 'elsewhere-gate.sh'
        self.write(target, GATE_TEXT)
        self.worktree_gate.symlink_to(target)

        res = self.verify()

        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertIn('control_tampered', res.stderr)
        self.assertEqual(self.sandbox_calls(), '')


if __name__ == '__main__':
    unittest.main()
