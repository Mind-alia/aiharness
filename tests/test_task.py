import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Keep fixtures in the repository: system temporary directories may be sandboxed.
TEST_TMP = ROOT / '.workspace/tmp-tests'
TEST_TMP.mkdir(parents=True, exist_ok=True)


class TaskTests(unittest.TestCase):
    def read(self, header):
        spec = importlib.util.spec_from_file_location('task', ROOT / 'template/scripts/lib/task.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            path = Path(folder) / 'task.md'
            path.write_text('---\n' + header + '\n---\nFaire la tâche.\n')
            return module.read_task(path)

    def test_defaults_comments_and_block_list(self):
        task = self.read('id: demo-1\nagent: codex # relay\nallowed:\n  - "src/**"\nverify: echo "# ok" # comment')
        self.assertEqual(task['allowed'], ['src/**'])
        self.assertEqual(task['verify'], 'echo "# ok"')
        self.assertEqual(task['attempts'], 2)
        self.assertEqual(task['mode'], 'write')
        self.assertEqual(task['body'], 'Faire la tâche.\n')

    def test_inline_list(self):
        self.assertEqual(self.read('id: demo\nagent: claude-sonnet\nallowed: ["a", b]')['allowed'], ['a', 'b'])

    def test_invalid_fields_are_rejected(self):
        base = 'id: demo\nagent: codex\nallowed: [src/**]'
        for header in [
            base.replace('id: demo\n', ''),
            base.replace('agent: codex\n', ''),
            'id: demo\nagent: codex',
            base.replace('demo', '../escape'),
            base.replace('codex', 'other'),
            base + '\nmode: unknown',
            base + '\nattempts: 0',
            base.replace('[src/**]', 'src/**'),
            base + '\nid: duplicate',
        ]:
            with self.subTest(header=header), self.assertRaises(ValueError):
                self.read(header)

    def test_verify_profile_and_lint_options(self):
        base = 'id: demo\nagent: codex\nallowed: []'
        task = self.read(base)
        self.assertEqual(task['verify_profile'], 'on')
        self.assertEqual(task['verify_lint'], 'on')
        task = self.read(base + '\nverify_profile: off\nverify_lint: on')
        self.assertEqual(task['verify_profile'], 'off')
        self.assertEqual(task['verify_lint'], 'on')
        self.assertEqual(self.read(base + '\nverify_lint: off')['verify_lint'], 'off')
        for field in ('verify_profile', 'verify_lint'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.read(base + '\n' + field + ': invalid')

    def test_auto_retry(self):
        base = 'id: demo\nagent: codex\nallowed: []'
        self.assertEqual(self.read(base)['auto_retry'], 'off')
        self.assertEqual(self.read(base + '\nauto_retry: on')['auto_retry'], 'on')
        with self.assertRaises(ValueError):
            self.read(base + '\nauto_retry: yes')

    def test_verify_refuses_full_gate(self):
        base = 'id: demo\nagent: codex\nallowed: []'
        refused = [
            'bash scripts/gate.sh',
            'sh scripts/gate.sh',
            './scripts/gate.sh',
            'bash scripts/gate.sh && echo done',
            'echo start; sh scripts/gate.sh',
            'python3 -m unittest tests.test_task; ./scripts/gate.sh',
            'bash scripts/gate.sh extra',
        ]
        for command in refused:
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, 'porte complète'):
                self.read(base + '\nverify: ' + json.dumps(command))

    def test_verify_accepts_targeted_commands(self):
        base = 'id: demo\nagent: codex\nallowed: []'
        accepted = [
            'bash scripts/gate.sh --quick',
            'sh scripts/gate.sh --quick && echo ok',
            './scripts/gate.sh --quick',
            'python3 -m unittest tests.test_task',
            'echo "scripts/gate.sh"',
            'bash scripts/gate.sh.bak',
            'cat scripts/gate.sh',
        ]
        for command in accepted:
            with self.subTest(command=command):
                self.assertEqual(self.read(base + '\nverify: ' + json.dumps(command))['verify'], command)

    def test_acceptance_paths(self):
        base = 'id: demo\nagent: codex\nallowed: []'
        task = self.read(base + '\nacceptance:\n  - tests/hidden.py\nacceptance_verify: python3 tests/hidden.py')
        self.assertEqual(task['acceptance'], ['tests/hidden.py'])
        for path in ('../escape', '/tmp/test', 'a/../test', 'a//test'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.read(base + '\nacceptance: [' + path + ']\nacceptance_verify: true')

    def test_max_turns_defaults_to_none_and_accepts_positive_integers(self):
        base = 'id: demo\nagent: claude-haiku\nallowed: []'
        self.assertIsNone(self.read(base)['max_turns'])
        self.assertEqual(self.read(base + '\nmax_turns: 12')['max_turns'], 12)
        self.assertEqual(self.read(base + '\nmax_turns: 1 # comment')['max_turns'], 1)

    def test_max_turns_refuses_other_values(self):
        base = 'id: demo\nagent: claude-haiku\nallowed: []'
        for value in ('0', 'abc', '-3', '1.5', '007', '', '+4', '4 5'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'max_turns'):
                self.read(base + '\nmax_turns: ' + value)

    def test_type_accepts_each_listed_value(self):
        base = 'id: demo\nagent: codex\nallowed: []'
        for value in ('code', 'docs', 'test', 'security', 'exploration'):
            with self.subTest(type=value):
                self.assertEqual(self.read(base + '\ntype: ' + value)['type'], value)

    def test_type_refuses_unknown_values(self):
        base = 'id: demo\nagent: codex\nallowed: []'
        for value in ('', 'Docs', 'CODE', 'documentation', 'code, docs'):
            with self.subTest(type=value), self.assertRaisesRegex(ValueError, 'type'):
                self.read(base + '\ntype: ' + value)

    def test_type_defaults_from_mode_then_agent(self):
        base = 'id: demo\nallowed: []'
        self.assertEqual(self.read(base + '\nagent: codex\nmode: review')['type'], 'exploration')
        self.assertEqual(self.read(base + '\nagent: antigravity')['type'], 'docs')
        self.assertEqual(self.read(base + '\nagent: claude-haiku')['type'], 'code')
        self.assertEqual(self.read(base + '\nagent: codex\nmode: write')['type'], 'code')

    def test_type_default_priority_review_before_antigravity(self):
        base = 'id: demo\nallowed: []'
        self.assertEqual(self.read(base + '\nagent: antigravity\nmode: review')['type'], 'exploration')

    def test_explicit_type_wins_over_defaults(self):
        base = 'id: demo\nallowed: []'
        self.assertEqual(self.read(base + '\nagent: codex\nmode: review\ntype: code')['type'], 'code')
        self.assertEqual(self.read(base + '\nagent: antigravity\ntype: test')['type'], 'test')
        self.assertEqual(self.read(base + '\nagent: codex\ntype: security')['type'], 'security')

    def test_duplicate_type_is_rejected(self):
        base = 'id: demo\nagent: codex\nallowed: []'
        with self.assertRaisesRegex(ValueError, 'répété'):
            self.read(base + '\ntype: code\ntype: docs')

    def test_direct_executor_has_no_agent_name(self):
        task = self.read('id: demo\nagent: claude-haiku\nallowed: []')
        self.assertEqual(task['agent'], 'claude-haiku')
        self.assertIsNone(task['agent_name'])

    def test_member_name_resolves_to_executor(self):
        task = self.read('id: demo\nagent: Crash\nallowed: []')
        self.assertEqual(task['agent'], 'claude-haiku')
        self.assertEqual(task['agent_name'], 'Crash')
        self.assertEqual(task['type'], 'code')

    def test_member_name_is_case_sensitive(self):
        with self.assertRaisesRegex(ValueError, 'crash'):
            self.read('id: demo\nagent: crash\nallowed: []')

    def test_unknown_agent_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'agent inconnu : Inconnu'):
            self.read('id: demo\nagent: Inconnu\nallowed: []')

    def test_member_name_resolves_from_given_root(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            root = Path(folder)
            (root / 'team.json').write_text(json.dumps({'membres': [{'nom': 'Atlas', 'executant': 'antigravity'}]}), encoding='utf-8')
            fiche = root / 'elsewhere' / 'task.md'
            fiche.parent.mkdir()
            fiche.write_text('---\nid: demo\nagent: Atlas\nallowed: []\n---\nFaire la tâche.\n', encoding='utf-8')
            task = self.load_module().read_task(fiche, root)
        self.assertEqual(task['agent'], 'antigravity')
        self.assertEqual(task['agent_name'], 'Atlas')
        self.assertEqual(task['type'], 'docs')

    def load_module(self):
        spec = importlib.util.spec_from_file_location('task', ROOT / 'template/scripts/lib/task.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
