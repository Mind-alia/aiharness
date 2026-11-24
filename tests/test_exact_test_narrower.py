import ast
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'template/scripts/lib'))
import exact_tests  # noqa: E402

HEAD = 'import unittest\na = b = row = rows = events.read(ROOT)\n\nclass T(unittest.TestCase):\n    def test_x(self):\n'
TEXT = 'Les événements `outcome` et `closed` gagnent un champ.'


def call(source):
    return ast.parse(source).body[0].value


class IsFieldComparisonTest(unittest.TestCase):
    def test_field_sets(self):
        for source in (
            "self.assertEqual(found[0]['detail'], {'unresolved': n, 'outcome': 'done'})",
            "self.assertEqual(sorted(data), ['launched_by', 'model', 'pid'])",
            "self.assertEqual(list(row), ['at', 'detail', 'event', 'task_id'])",
            "self.assertEqual(rows, [('t', 'outcome', {'outcome': 'interrupted'})])",
        ):
            self.assertTrue(exact_tests.is_field_comparison(call(source)), source)

    def test_values_and_types(self):
        for source in (
            "self.assertEqual(events.KINDS, ('prepared', 'outcome'))",
            "self.assertEqual([e['o'] for e in rows], ['usine-1', 'usine-1'])",
            "self.assertEqual(x['waiting_questions'], ['001'])",
            "self.assertEqual(f(), {})",
        ):
            self.assertFalse(exact_tests.is_field_comparison(call(source)), source)


class NarrowerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get('WORKSPACE_TEST_TMP'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = Path(self.tmp)
        (self.root / 'tests').mkdir()
        self.task = {'allowed': [], 'acceptance': []}

    def write(self, body):
        (self.root / 'tests/test_a.py').write_text(HEAD + body, encoding='utf-8')

    def test_split_between_find_and_nearby(self):
        self.write(
            "        self.assertEqual(events.KINDS, ('prepared', 'outcome'))\n"
            "        self.assertEqual(list(row), ['at', 'detail', 'event'])  # 'outcome'\n"
            "        self.assertEqual(rows, [('t', 'outcome', {'outcome': 'x'})])\n"
        )
        found = exact_tests.find(self.root, self.task, TEXT)
        nearby = exact_tests.find_nearby(self.root, self.task, TEXT)
        self.assertEqual(found[0]['lines'], [7, 8])
        self.assertEqual(nearby[0]['lines'], [6])
        self.assertNotIn(6, found[0]['lines'])
        self.assertEqual(nearby[0]['path'], 'tests/test_a.py')
        self.assertEqual(nearby[0]['structures'], ['outcome'])

    def test_line_never_in_both(self):
        self.write("        self.assertEqual(a, ['closed']); self.assertEqual(b, {'closed': 1})\n")
        found = exact_tests.find(self.root, self.task, TEXT)
        nearby = exact_tests.find_nearby(self.root, self.task, TEXT)
        self.assertEqual(found[0]['lines'], [6])
        self.assertEqual(nearby, [])

    def test_nearby_respects_scope_and_never_raises(self):
        self.write("        self.assertEqual(x['q'], ['001'])  # 'outcome'\n")
        self.assertEqual(len(exact_tests.find_nearby(self.root, self.task, TEXT)), 1)
        self.task['allowed'] = ['tests/test_a.py']
        self.assertEqual(exact_tests.find_nearby(self.root, self.task, TEXT), [])
        self.assertEqual(exact_tests.find_nearby(self.root, None, TEXT), [])
        self.assertEqual(exact_tests.find_nearby(self.root / 'absent', self.task, TEXT), [])

    def test_exact_comparisons_fields_flag(self):
        self.write("        self.assertEqual(x, ['001'])  # 'outcome'\n")
        path = self.root / 'tests/test_a.py'
        self.assertEqual(exact_tests.exact_comparisons(path, ('outcome',)), [])
        self.assertEqual(exact_tests.exact_comparisons(path, ('outcome',), fields=False), [6])


class EventSourcedTest(unittest.TestCase):
    """T83 (reste du 24/10) : un ensemble de champs n'est un défaut que si la valeur comparée vient du journal."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get('WORKSPACE_TEST_TMP'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = Path(self.tmp)
        (self.root / 'tests').mkdir()
        self.task = {'allowed': [], 'acceptance': []}
        self.plain = 'import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n'

    def write(self, body, head=None):
        (self.root / 'tests/test_a.py').write_text((head or self.plain) + body, encoding='utf-8')

    def found_and_nearby(self):
        return (exact_tests.find(self.root, self.task, TEXT), exact_tests.find_nearby(self.root, self.task, TEXT))

    def test_status_json_dict_is_only_nearby(self):
        self.write(
            "        status = json.loads((root / 'status.json').read_text())\n"
            "        self.assertEqual(status['outcome'], {'outcome': 'running'})\n"
        )
        found, nearby = self.found_and_nearby()
        self.assertEqual(found, [])
        self.assertEqual(nearby[0]['lines'], [7])

    def test_value_read_from_events_is_a_defect(self):
        self.write(
            "        rows = events.read(root)\n        first = rows[0]\n        self.assertEqual(first['detail'], {'outcome': 'done'})\n"
        )
        found, nearby = self.found_and_nearby()
        self.assertEqual(found[0]['lines'], [8])
        self.assertEqual(nearby, [])

    def test_helper_call_in_the_arguments(self):
        self.write("        self.assertEqual(self.events('outcome'), [{'exit': 1}, {'exit': 0}])\n")
        found, _ = self.found_and_nearby()
        self.assertEqual(found[0]['lines'], [6])

    def test_loop_and_jsonl_path_taint(self):
        self.write(
            "        path = root / '.workspace/events.jsonl'\n        lines = path.read_text().splitlines()\n"
            "        for line in lines:\n            item = json.loads(line)\n"
            "            self.assertEqual(list(item), ['at', 'detail', 'event'])  # 'outcome'\n"
        )
        found, _ = self.found_and_nearby()
        self.assertEqual(found[0]['lines'], [10])

    def test_other_dict_in_an_event_reading_function_is_nearby(self):
        self.write(
            "        rows = events.read(root)\n        status = self.status()\n"
            "        self.assertEqual(status['verify'], {'exit': 0, 'outcome': 'x'})\n"
        )
        found, nearby = self.found_and_nearby()
        self.assertEqual(found, [])
        self.assertEqual(nearby[0]['lines'], [8])

    def test_module_level_fixture_taints(self):
        self.write(
            "        self.assertEqual(ROWS[0], {'outcome': 'done'})\n",
            head='import unittest\nROWS = events.read(ROOT)\n\nclass T(unittest.TestCase):\n    def test_x(self):\n',
        )
        found, _ = self.found_and_nearby()
        self.assertEqual(found[0]['lines'], [6])

    def test_sources_constant(self):
        self.assertIn('events.read(', exact_tests.EVENT_SOURCES)
        self.assertIn('.events(', exact_tests.EVENT_SOURCES)
        self.assertIn('events.jsonl', exact_tests.EVENT_SOURCES)


if __name__ == '__main__':
    unittest.main()
