"""T123 : `sandbox.sh start <id> --base` monte la base de la tâche à la place de son clone, et `base_worktree` la contrôle.

Les aides (faux Docker, tâche préparée) sont celles de `tests/test_sandbox.py` ; seuls les tests de ce fichier tournent ici."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import unittest

if os.environ.get('WORKSPACE_TESTS') == 'fast':
    raise unittest.SkipTest('module lent : Git réel, sous-processus ou Docker simulé ; suite complète seulement')

try:
    from tests import test_sandbox as helpers
except ImportError:
    import test_sandbox as helpers

ROOT = helpers.ROOT
workdir = helpers.workdir
OWN_TESTS = (
    'test_base_worktree_needs_a_real_folder_without_links',
    'test_start_base_mounts_the_base_read_only_and_marks_the_status',
    'test_start_base_is_refused_without_a_base_or_with_a_running_container',
)


class SandboxBaseTests(helpers.SandboxTests):
    def make_base(self, task_id):
        base = self.root / f'.workspace/orchestration/tasks/{task_id}/base'
        (base / '.git').mkdir(parents=True)
        return base

    def test_base_worktree_needs_a_real_folder_without_links(self):
        spec = importlib.util.spec_from_file_location('sandbox_base', ROOT / 'template/scripts/lib/sandbox.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaises(ValueError):
            module.base_worktree(self.root, 'task-base')
        base = self.make_base('task-base')
        self.assertEqual(module.base_worktree(self.root, 'task-base'), base)
        shutil.rmtree(base)
        base.symlink_to(self.root / 'src')
        with self.assertRaises(ValueError):
            module.base_worktree(self.root, 'task-base')

    def test_start_base_mounts_the_base_read_only_and_marks_the_status(self):
        task_id = self.prepare_task('task-base', profile='python')
        clone, base = workdir.task_worktree(self.root, task_id).resolve(), self.make_base(task_id).resolve()
        res = self.run_sandbox('start', task_id, '--base')
        self.assertEqual(res.returncode, 0, res.stderr)
        run_call = next(c for c in self.get_docker_calls() if c and c[0] == 'run')
        self.assertIn(f'{base}:/work:ro', run_call)
        self.assertNotIn(f'{clone}:/work:ro', run_call)
        self.assertTrue(any(arg.endswith(':/work/.git:ro') for arg in run_call))
        self.assertTrue(any(arg.endswith(':/work/scripts:ro') for arg in run_call))
        status = json.loads((self.root / f'.workspace/orchestration/tasks/{task_id}/sandbox/status.json').read_text())
        self.assertIs(status.get('base'), True)

    def test_start_base_is_refused_without_a_base_or_with_a_running_container(self):
        task_id = self.prepare_task('task-base-refused', profile='python')
        self.assertNotEqual(self.run_sandbox('start', task_id, '--base').returncode, 0)
        self.make_base(task_id)
        res = self.run_sandbox('start', task_id, '--base', env={'FAKE_ASSUME_RUNNING': '1'})
        self.assertEqual(res.returncode, 1)
        self.assertFalse(any(c and c[0] == 'run' for c in self.get_docker_calls()))


def load_tests(loader, tests, pattern):
    """Seuls les tests propres à ce fichier : ceux de `SandboxTests` héritées ne sont pas rejoués."""
    return unittest.TestSuite(SandboxBaseTests(name) for name in OWN_TESTS)


if __name__ == '__main__':
    unittest.main()
