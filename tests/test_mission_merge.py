"""Steps of task-workspace.sh merge (mission_merge.py), and the shape of cmd_merge once split (T14 phase A)."""

import ast
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import mission_merge

CHAIN_LIMIT = 80
# The steps of the merge, in the order cmd_merge chains them.
STEPS = [
    'open_merge',
    'read_sheet',
    'check_changes',
    'commit_leftovers',
    'merge_target',
    'verify_in_workshop',
    'check_after_verify',
    'integrate',
]


class RecordPathsTests(unittest.TestCase):
    def test_both_ends_of_a_rename_or_copy_count(self):
        records = ['R  new.py', 'old.py', ' M kept.py', 'C  copy.py', 'origin.py', '?? added.txt', '']
        self.assertEqual(mission_merge.record_paths(records), {'new.py', 'old.py', 'kept.py', 'copy.py', 'origin.py', 'added.txt'})

    def test_a_clean_worktree_names_nothing(self):
        self.assertEqual(mission_merge.record_paths(['']), set())


class IntegrationCommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_no_gate_configured(self):
        self.assertIsNone(mission_merge.integration_command(self.root))

    def test_the_kit_gate_by_default(self):
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/gate.sh').write_text('exit 0\n')
        self.assertEqual(mission_merge.integration_command(self.root), 'bash scripts/gate.sh')

    def test_the_configuration_line_wins(self):
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/gate.sh').write_text('exit 0\n')
        (self.root / '.workspace-integration').write_text('  make check  \n')
        self.assertEqual(mission_merge.integration_command(self.root), 'make check')

    def test_an_empty_or_multi_line_configuration_is_refused(self):
        for text in ('\n', 'make a\nmake b\n'):
            with self.subTest(text=text):
                (self.root / '.workspace-integration').write_text(text)
                with self.assertRaises(ValueError):
                    mission_merge.integration_command(self.root)


class CommitLeftoversTests(unittest.TestCase):
    def merge(self, body, records):
        merge = mission_merge.Merge(Path('/r'), 't-demo', {}, 'workspace-task/t-demo', Path('/w'), False)
        merge.data = {'body': body}
        merge.records = records
        return merge

    def committed(self, merge, message=None):
        with mock.patch.object(mission_merge, 'run_git') as run_git:
            mission_merge.commit_leftovers(merge, message)
        return [call.args[1] for call in run_git.call_args_list]

    def test_nothing_left_means_no_commit(self):
        self.assertEqual(self.committed(self.merge('# Faire A\n', [''])), [])

    def test_the_sheet_title_becomes_a_conventional_message(self):
        calls = self.committed(self.merge('\n# Faire A\n', [' M a.py', '']))
        self.assertEqual(calls, [['add', '--all'], ['commit', '-m', 'feat: Faire A']])

    def test_a_conventional_title_is_kept_as_is(self):
        calls = self.committed(self.merge('# fix(merge): garder le titre\n', [' M a.py', '']))
        self.assertEqual(calls[-1], ['commit', '-m', 'fix(merge): garder le titre'])

    def test_an_empty_message_is_refused(self):
        with self.assertRaises(ValueError):
            self.committed(self.merge('# Faire A\n', [' M a.py', '']), message='  ')


def function(tree, name):
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)


class SplitShapeTests(unittest.TestCase):
    """cmd_merge only holds the lock and chains the named steps of mission_merge.py."""

    def setUp(self):
        self.source = (ROOT / 'template/scripts/lib/mission.py').read_text(encoding='utf-8')
        self.cmd_merge = function(ast.parse(self.source), 'cmd_merge')

    def test_cmd_merge_stays_a_short_chain_without_waivers(self):
        node = self.cmd_merge
        assert node.end_lineno is not None
        self.assertLessEqual(node.end_lineno - node.lineno + 1, CHAIN_LIMIT)
        self.assertNotIn('noqa', self.source.splitlines()[node.lineno - 1])

    def test_the_steps_are_chained_in_order(self):
        calls = [
            node
            for node in ast.walk(self.cmd_merge)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
            if node.func.value.id == 'mission_merge'
        ]
        self.assertEqual([node.func.attr for node in sorted(calls, key=lambda node: node.lineno)], STEPS)


if __name__ == '__main__':
    unittest.main()
