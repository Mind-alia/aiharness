import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))
import prepare_task
from prepare_task import MIN_HIDDEN_TESTS, REASON_MAX, count_tests, prompt_for, validate
from task import read_task


def tests_block(count, offset=0):
    return 'class Cases:\n' + ''.join('    def test_n%d(self):\n        pass\n' % (offset + i) for i in range(count))


def plan_with(count, **extra):
    task = {'suffix': '', 'class': 'medium', 'allowed': ['src/a.py'], 'verify': 'python3 -m unittest', 'body': 'Fiche'}
    task.update(extra)
    return {
        'tasks': [task],
        'acceptance': [{'task_suffix': '', 'path': 'tests/test_a.py', 'content': tests_block(count)}],
        'acceptance_verify': {'': 'python3 tests/test_a.py'},
    }


class ClassAndTwelveTests(unittest.TestCase):
    def accepted(self, plan):
        return validate(plan, 'a', 'claude-haiku')

    def rejected(self, plan):
        with self.assertRaises(ValueError):
            self.accepted(plan)

    def test_constants(self):
        self.assertEqual(prepare_task.CLASSES, ('small', 'medium', 'large'))
        self.assertEqual((MIN_HIDDEN_TESTS, REASON_MAX, prepare_task.TEST_PREFIX), (12, 512, '    def test_'))

    def test_count_tests(self):
        self.assertEqual(count_tests([tests_block(3), tests_block(4), 'def test_top():\n  pass\n']), 7)
        self.assertEqual(count_tests([]), 0)

    def test_header_has_class_after_mode(self):
        document = self.accepted(plan_with(12))[0][1]
        lines = document.splitlines()
        self.assertEqual(lines.index('class: "medium"'), lines.index('mode: "write"') + 1)

    def test_invalid_class(self):
        for value in ('huge', 1, None, True, ['small']):
            with self.subTest(value=value):
                self.rejected(plan_with(12, **{'class': value}))
        plan = plan_with(12)
        del plan['tasks'][0]['class']
        self.rejected(plan)

    def test_twelve_over_two_files(self):
        plan = plan_with(7)
        plan['acceptance'].append({'task_suffix': '', 'path': 'tests/test_b.py', 'content': tests_block(5)})
        self.assertEqual(len(self.accepted(plan)), 1)

    def test_eleven_without_waiver_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.accepted(plan_with(11))
        self.assertEqual(str(caught.exception), 'a : 11 tests cachés, il en faut au moins 12')

    def test_waiver_accepted(self):
        outputs = self.accepted(plan_with(11, acceptance_min=11, acceptance_min_reason='Module minuscule'))
        lines = outputs[0][1].splitlines()
        verify = [i for i, line in enumerate(lines) if line.startswith('acceptance_verify:')][0]
        self.assertTrue(lines[verify + 1].startswith('acceptance_min: 11'))
        self.assertTrue(lines[verify + 2].startswith('acceptance_min_reason:'))
        task = read_task_from(outputs[0][1])
        self.assertEqual(task['acceptance_min'], '11')

    def test_waiver_count_below_minimum(self):
        self.rejected(plan_with(4, acceptance_min=5, acceptance_min_reason='Raison'))

    def test_waiver_invalid_values(self):
        for value in (12, 0, -1, True, '5', 5.0, None):
            with self.subTest(value=value):
                self.rejected(plan_with(11, acceptance_min=value, acceptance_min_reason='Raison'))

    def test_waiver_invalid_reason(self):
        for reason in ('', '   ', 'x' * (REASON_MAX + 1), 3, None):
            with self.subTest(reason=reason):
                self.rejected(plan_with(11, acceptance_min=11, acceptance_min_reason=reason))
        self.accepted(plan_with(11, acceptance_min=11, acceptance_min_reason='x' * REASON_MAX))

    def test_waiver_keys_go_together(self):
        self.rejected(plan_with(11, acceptance_min=11))
        self.rejected(plan_with(12, acceptance_min_reason='Raison'))

    def test_unknown_task_key_refused(self):
        self.rejected(plan_with(12, extra=1))

    def test_rule_applies_per_task(self):
        plan = plan_with(12)
        second = copy.deepcopy(plan['tasks'][0])
        plan['tasks'][0]['suffix'], second['suffix'] = '-1', '-2'
        plan['tasks'].append(second)
        plan['acceptance'][0]['task_suffix'] = '-1'
        plan['acceptance'].append({'task_suffix': '-2', 'path': 'tests/test_b.py', 'content': tests_block(11)})
        plan['acceptance_verify'] = {'-1': 'python3 tests/test_a.py', '-2': 'python3 tests/test_b.py'}
        self.rejected(plan)
        plan['acceptance'][1]['content'] = tests_block(12)
        self.assertEqual(len(self.accepted(plan)), 2)

    def test_prompt_mentions_class_and_twelve(self):
        prompt = prompt_for(ROOT / 'template', 'Demande')
        for fragment in ('"class":"small|medium|large"', 'douze', 'def test_', 'acceptance_min', 'acceptance_min_reason'):
            self.assertIn(fragment, prompt)


def read_task_from(document):
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'task.md'
        path.write_text(document, encoding='utf-8')
        return read_task(path)


if __name__ == '__main__':
    unittest.main()


class KitProfileTests(unittest.TestCase):
    """T86 : dans le kit, la proposition générée porte `verify_profile: off` ; ailleurs, rien n'est ajouté."""

    def test_kit_header_has_verify_profile_off_after_verify(self):
        document = validate(plan_with(12), 'a', 'claude-haiku', kit=True)[0][1]
        lines = document.splitlines()
        self.assertEqual(lines.index('verify_profile: "off"'), lines.index('verify: "python3 -m unittest"') + 1)
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.md'
            path.write_text(document, encoding='utf-8')
            self.assertEqual(read_task(path)['verify_profile'], 'off')

    def test_other_project_header_unchanged(self):
        document = validate(plan_with(12), 'a', 'claude-haiku')[0][1]
        self.assertNotIn('verify_profile', document)
        self.assertEqual(validate(plan_with(12), 'a', 'claude-haiku', kit=False)[0][1], document)

    def test_is_kit_reads_agents_marker(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(prepare_task.is_kit(root))
            (root / 'AGENTS.md').write_text('# Projet\n', encoding='utf-8')
            self.assertFalse(prepare_task.is_kit(root))
            (root / 'AGENTS.md').write_text('# Kit\n\n' + prepare_task.KIT_MARKER + '\n', encoding='utf-8')
            self.assertTrue(prepare_task.is_kit(root))

    def test_constants(self):
        self.assertEqual((prepare_task.KIT_MARKER, prepare_task.KIT_VERIFY_PROFILE), ('docs-check: kit', 'off'))
