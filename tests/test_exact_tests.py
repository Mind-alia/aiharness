import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / 'template/scripts/lib'
sys.path.insert(0, str(LIB))
import exact_tests  # noqa: E402
import plan_check  # noqa: E402

CORRECTION_TEST = 'tests/test_correction.py'
ORCHESTRATOR_TEST = 'tests/test_orchestrator.py'
FAR_LINES = 5
# The line of tests/test_correction.py that compares the `correction_reverted` detail exactly; computed, so that
# formatting the file never breaks these tests.
EXACT_LINE = next(
    number
    for number, line in enumerate((REPO / CORRECTION_TEST).read_text(encoding='utf-8').splitlines(), 1)
    if "self.assertEqual(found[0]['detail'], {'unresolved': names, 'outcome': 'done', 'cause': 'implementation'})" in line
)
# Deux noms alimentés par le journal des événements (T83) ; toujours cinq lignes avant le corps.
SOURCE_HEAD = 'import unittest\nd = f = events.read(ROOT)\n\nclass T(unittest.TestCase):\n    def test_x(self):\n'


def sheet(allowed, body, acceptance=('tests/test_hidden.py',)):
    lines = ['---', 'id: t1', 'agent: claude-sonnet', 'allowed:'] + ['  - ' + a for a in allowed]
    lines += ['acceptance:'] + ['  - ' + a for a in acceptance]
    lines += ['verify_profile: off', '---', body]
    return '\n'.join(lines)


class NamedStructuresTest(unittest.TestCase):
    def test_event_whole_word(self):
        found = exact_tests.named_structures('Le détail de `correction_reverted` change.')
        self.assertEqual(found, {'correction_reverted': ("'correction_reverted'", '"correction_reverted"')})

    def test_event_inside_longer_word_ignored(self):
        self.assertEqual(exact_tests.named_structures('`x_correction_reverted2` `correction_revertedé`'), {})

    def test_bare_event_or_inside_other_code_ignored(self):
        self.assertEqual(exact_tests.named_structures('correction_reverted'), {})
        self.assertEqual(exact_tests.named_structures('`a correction_reverted b`'), {})

    def test_header_ignored_body_read(self):
        head = '---\nid: t1\nverify: x\nacceptance:\n  - a\n---\n'
        self.assertEqual(exact_tests.named_structures(head + 'rien'), {})
        self.assertEqual(exact_tests.named_structures('---\n`correction_reverted`\n---\nrien'), {})
        self.assertEqual(list(exact_tests.named_structures(head + '`correction_reverted`')), ['correction_reverted'])
        self.assertEqual(list(exact_tests.named_structures('---\n`correction_reverted`')), ['correction_reverted'])

    def test_header_does_not_feed_lock(self):
        self.assertEqual(exact_tests.named_structures('---\nverrou\n---\nrien'), {})
        self.assertIn('verrou', exact_tests.named_structures('---\nid: x\n---\nle verrou'))

    def test_event_after_punctuation(self):
        self.assertIn('correction_reverted', exact_tests.named_structures('(`correction_reverted`)'))

    def test_lock(self):
        for text in ('voir .workspace/orchestrator.json', 'le verrou change'):
            self.assertEqual(exact_tests.named_structures(text), {'verrou': ('orchestrator.json', 'self.lock')})

    def test_lock_word_is_whole(self):
        self.assertEqual(exact_tests.named_structures('verrouiller'), {})

    def test_state(self):
        for text in ('state.json', 'état de la tâche', 'état de tâche'):
            self.assertEqual(exact_tests.named_structures(text)['état'], ('state.json',))
        self.assertNotIn('état', exact_tests.named_structures('state'))

    def test_nothing_named(self):
        self.assertEqual(exact_tests.named_structures('rien de spécial'), {})


class ExactComparisonsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get('WORKSPACE_TEST_TMP'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = Path(self.tmp)

    def write(self, name, body):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SOURCE_HEAD + body, encoding='utf-8')
        return path

    def lines(self, body, markers=('MARK',)):
        return exact_tests.exact_comparisons(self.write('tests/test_a.py', body), markers)

    def test_dict_literal(self):
        self.assertEqual(self.lines("        self.assertEqual(d, {'MARK': 1})\n"), [6])

    def test_nested_dict_literal(self):
        self.assertEqual(self.lines("        self.assertEqual([(1, {'a': 1})], f(),\n                         'MARK')\n"), [6])

    def test_string_list_and_tuple(self):
        self.assertEqual(self.lines("        self.assertListEqual(f(), ['MARK', 'b'])\n"), [6])
        self.assertEqual(self.lines("        self.assertTupleEqual(f(), ('MARK',))\n"), [6])

    def test_mixed_or_empty_sequence_not_exact(self):
        self.assertEqual(self.lines("        self.assertEqual(f(), ['MARK', 1])\n"), [])
        self.assertEqual(self.lines("        self.assertEqual(f(), []) # MARK\n"), [])

    def test_dict_assert_name(self):
        self.assertEqual(self.lines("        self.assertDictEqual(f(), {'MARK': 1})\n"), [6])

    def test_other_asserts_never_kept(self):
        body = (
            "        self.assertIn('MARK', {'MARK': 1})\n        self.assertTrue({'MARK': 1})\n"
            "        self.assertEqual(MARK, other)\n        self.assertEqual(a, b)  # MARK\n"
        )
        self.assertEqual(self.lines(body), [])

    def test_bare_function_name_not_attribute(self):
        self.assertEqual(self.lines("        assertEqual(f(), {'MARK': 1})\n"), [])

    def test_marker_in_call_lines_and_three_lines_above(self):
        near = "        # MARK\n        x = 1\n        y = 2\n        self.assertEqual(f(), {'a': 1})\n"
        self.assertEqual(self.lines(near), [9])
        far = "        # MARK\n" + "        x = 1\n" * (FAR_LINES - 2) + "        self.assertEqual(f(), {'a': 1})\n"
        self.assertEqual(self.lines(far), [])

    def test_marker_on_last_line_of_call(self):
        body = "        self.assertEqual(f(), {\n            'a': 1,\n            'b': 'MARK'})\n"
        self.assertEqual(self.lines(body), [6])

    def test_unreadable_or_invalid_file(self):
        path = self.root / 'tests/test_bad.py'
        path.parent.mkdir(parents=True)
        path.write_text('def (:\n', encoding='utf-8')
        self.assertEqual(exact_tests.exact_comparisons(path, ('x',)), [])
        self.assertEqual(exact_tests.exact_comparisons(self.root / 'absent.py', ('x',)), [])


class FindTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get('WORKSPACE_TEST_TMP'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = Path(self.tmp)
        for name in (CORRECTION_TEST, ORCHESTRATOR_TEST):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / name, target)

    def task(self, allowed=(), acceptance=()):
        return {'allowed': list(allowed), 'acceptance': list(acceptance)}

    def test_correction_reverted_case(self):
        found = exact_tests.find(self.root, self.task(), 'Ajoute une clé au détail de `correction_reverted`.')
        self.assertEqual([item['path'] for item in found], [CORRECTION_TEST])
        self.assertIn(EXACT_LINE, found[0]['lines'])
        self.assertEqual(found[0]['lines'], sorted(found[0]['lines']))
        self.assertEqual(found[0]['structures'], ['correction_reverted'])

    def test_orchestrator_case(self):
        text = '`orchestrator_claimed` et .workspace/orchestrator.json gagnent une clé.'
        found = exact_tests.find(self.root, self.task(), text)
        self.assertEqual([item['path'] for item in found], [ORCHESTRATOR_TEST])
        self.assertEqual(found[0]['structures'], ['orchestrator_claimed', 'verrou'])

    def test_file_in_allowed_or_acceptance_skipped(self):
        text = '`correction_reverted`'
        self.assertEqual(exact_tests.find(self.root, self.task(allowed=[CORRECTION_TEST]), text), [])
        self.assertEqual(exact_tests.find(self.root, self.task(acceptance=['test_correction.py']), text), [])
        self.assertEqual(exact_tests.find(self.root, self.task(acceptance=[CORRECTION_TEST]), text), [])

    def test_nothing_named_nothing_found(self):
        self.assertEqual(exact_tests.find(self.root, self.task(), 'rien'), [])

    def test_symlink_and_nested_files_ignored(self):
        (self.root / 'tests/test_link.py').symlink_to(self.root / CORRECTION_TEST)
        nested = self.root / 'tests/sub/test_deep.py'
        nested.parent.mkdir()
        shutil.copy(self.root / CORRECTION_TEST, nested)
        found = exact_tests.find(self.root, self.task(), '`correction_reverted`')
        self.assertEqual([item['path'] for item in found], [CORRECTION_TEST])

    def test_sorted_by_path_and_never_raises(self):
        found = exact_tests.find(self.root, self.task(), '`correction_reverted` `orchestrator_claimed` verrou')
        self.assertEqual([item['path'] for item in found], [CORRECTION_TEST, ORCHESTRATOR_TEST])
        self.assertEqual(exact_tests.find(self.root / 'absent', self.task(), 'verrou'), [])
        self.assertEqual(exact_tests.find(self.root, None, 'verrou'), [])


class PlanCheckIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get('WORKSPACE_TEST_TMP'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = Path(self.tmp)
        for name in (CORRECTION_TEST, ORCHESTRATOR_TEST):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / name, target)
        hidden = self.root / '.workspace/acceptance/t1/tests/test_hidden.py'
        hidden.parent.mkdir(parents=True)
        hidden.write_text(
            'import unittest\n\n\nclass T(unittest.TestCase):\n' + ''.join('    def test_%d(self):\n        pass\n' % i for i in range(12)),
            encoding='utf-8',
        )

    def check(self, allowed, body):
        path = self.root / '.workspace/tasks/t1.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            sheet(allowed, body).replace('---\n', '---\nacceptance_verify: python3 -m unittest tests.test_hidden\n', 1), encoding='utf-8'
        )
        return plan_check.check(self.root, path)

    def outside(self, result):
        return [d['message'] for d in result['defects'] if d['code'] == 'exact_test_outside']

    def test_defect_message(self):
        result = self.check(['template/scripts/lib/a.py'], '## Besoin\n`correction_reverted` gagne une clé.\n')
        messages = self.outside(result)
        self.assertEqual(len(messages), 1)
        self.assertIn(CORRECTION_TEST, messages[0])
        self.assertIn(str(EXACT_LINE), messages[0])
        self.assertIn('correction_reverted', messages[0])
        self.assertIn('élargir allowed ou reformuler la fiche', messages[0])
        self.assertEqual(result['verdict'], 'à reprendre')

    def test_no_defect_when_file_allowed_or_nothing_named(self):
        self.assertEqual(self.outside(self.check([CORRECTION_TEST], '`correction_reverted`')), [])
        self.assertEqual(self.outside(self.check(['template/scripts/lib/a.py'], 'rien')), [])


if __name__ == '__main__':
    unittest.main()
