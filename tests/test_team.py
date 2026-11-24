import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
# Keep fixtures in the repository: system temporary directories may be sandboxed.
TEST_TMP = ROOT / '.workspace/tmp-tests'
TEST_TMP.mkdir(parents=True, exist_ok=True)
MEMBER_NAMES = ['Bric', 'Crash', 'Garde', 'Fiole', 'Plume', 'Loupe', 'Atlas']


def load_team():
    spec = importlib.util.spec_from_file_location('team', ROOT / 'template/scripts/lib/team.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def member(name, executant='codex'):
    return {'nom': name, 'pingouin': name.lower(), 'piece': 'atelier', 'executant': executant, 'role': 'essai', 'type': 'code'}


class TeamRegistryTests(unittest.TestCase):
    def setUp(self):
        self.team = load_team()

    def test_kit_registry_is_valid_and_matches_template(self):
        kit = (ROOT / 'team.json').read_text(encoding='utf-8')
        template = (ROOT / 'template/team.json').read_text(encoding='utf-8')
        self.assertEqual(kit, template)
        self.assertEqual(json.loads(kit), json.loads(template))
        self.assertEqual(kit, json.dumps(json.loads(kit), ensure_ascii=False, indent=2) + '\n')
        members = self.team.load(ROOT / 'template/team.json')
        self.assertEqual([m['nom'] for m in members], MEMBER_NAMES)
        self.assertEqual(members[0]['executant'], 'claude-sonnet')
        self.assertEqual(members[3]['executant'], 'antigravity')
        self.assertIn('provisoires', json.loads(template)['note'])

    def test_resolve_kit_members_to_executors(self):
        self.assertEqual(self.team.resolve('Crash', ROOT), 'claude-haiku')
        self.assertEqual(self.team.resolve('Loupe', ROOT), 'codex')
        self.assertEqual(self.team.resolve('Atlas', ROOT), 'antigravity')

    def test_resolve_is_case_sensitive_and_rejects_unknown_names(self):
        for name in ('crash', 'Inconnu', 'codex'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'agent inconnu : ' + name):
                self.team.resolve(name, ROOT)

    def test_resolve_without_registry_raises(self):
        # TEST_TMP sits under the repository, whose team.json would be found: stub the search.
        with mock.patch.object(self.team, 'find_registry', return_value=None):
            with self.assertRaisesRegex(ValueError, 'agent inconnu : Crash'):
                self.team.resolve('Crash', TEST_TMP)

    def test_find_registry_walks_up_to_parents(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            root = Path(folder)
            (root / 'team.json').write_text(json.dumps({'membres': [member('Ada', 'codex')]}), encoding='utf-8')
            run = root / '.workspace' / 'run' / 'delegate' / 'demo'
            run.mkdir(parents=True)
            self.assertEqual(self.team.find_registry(run), root / 'team.json')
            self.assertEqual(self.team.resolve('Ada', run), 'codex')

    def test_find_registry_prefers_nearest_file(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            root = Path(folder)
            (root / 'team.json').write_text(json.dumps({'membres': [member('Ada', 'codex')]}), encoding='utf-8')
            inner = root / 'inner'
            inner.mkdir()
            (inner / 'team.json').write_text(json.dumps({'membres': [member('Ada', 'claude-opus')]}), encoding='utf-8')
            self.assertEqual(self.team.resolve('Ada', inner), 'claude-opus')

    def test_find_registry_refuses_symbolic_link(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            root = Path(folder)
            target = root / 'real.json'
            target.write_text(json.dumps({'membres': [member('Ada')]}), encoding='utf-8')
            child = root / 'child'
            child.mkdir()
            (child / 'team.json').symlink_to(target)
            # The link is skipped: the search goes on to the parents, never returns the link itself.
            self.assertNotEqual(self.team.find_registry(child), child / 'team.json')

    def test_invalid_registries_are_rejected(self):
        invalid = [
            [],
            {'membres': 'Ada'},
            {'membres': ['Ada']},
            {'membres': [{'nom': '', 'executant': 'codex'}]},
            {'membres': [{'executant': 'codex'}]},
            {'membres': [member('Ada', 'gemini')]},
            {'membres': [member('Ada'), member('Ada')]},
        ]
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            path = Path(folder) / 'team.json'
            for data in invalid:
                with self.subTest(data=data):
                    path.write_text(json.dumps(data), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'team.json invalide'):
                        self.team.load(path)
            path.write_text('{pas du json', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'team.json invalide'):
                self.team.load(path)

    def test_load_returns_members_in_order(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            path = Path(folder) / 'team.json'
            path.write_text(json.dumps({'membres': [member('Ada'), member('Bob', 'codex')]}), encoding='utf-8')
            self.assertEqual([m['nom'] for m in self.team.load(path)], ['Ada', 'Bob'])


if __name__ == '__main__':
    unittest.main()
