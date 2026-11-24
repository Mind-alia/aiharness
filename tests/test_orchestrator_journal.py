"""Journal du programme d'orchestration : contextes d'étape masqués comme le circuit avant d'entrer dans une consigne
(T100), module du circuit chargé sans masquer le module `secrets` de Python."""

import os
import secrets as python_secrets
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'orchestrator'))

import journal  # noqa: E402

find_secrets = journal.circuit_module(ROOT, 'secrets').find_secrets

# Valeur factice d'un fichier `.env` du projet : le circuit la masque partout où elle apparaît.
KNOWN = 'valeur-connue-du-projet'


class MaskTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=os.environ.get('WORKSPACE_TEST_TMP') or None)
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name))
        (self.root / '.env').write_text(f'SERVICE_URL={KNOWN}\n')

    def test_review_corrections_quoting_an_assignment_pass_the_delegate_guard(self):
        text = '- tests/test_a.py : la ligne `secret = tmp_value` doit appeler safety.write_file_secure\n'
        self.assertTrue(find_secrets(text, []))
        path = journal.write_note(self.root, 'relecture-t1.md', text + f'- valeur vue : {KNOWN}\n')
        written = (self.root / path).read_text()
        self.assertEqual(find_secrets(written, [KNOWN]), [])
        self.assertIn('secret = ***', written)
        self.assertIn('safety.write_file_secure', written)
        self.assertNotIn(KNOWN, written)
        self.assertEqual(oct((self.root / path).stat().st_mode & 0o777), '0o600')

    def test_the_circuit_module_does_not_replace_python_secrets(self):
        module = journal.circuit_module(self.root, 'secrets')
        self.assertTrue(hasattr(module, 'mask_secrets'))
        self.assertIs(sys.modules['secrets'], python_secrets)

    def test_the_project_copy_is_preferred_and_the_kit_is_the_fallback(self):
        lib = self.root / 'scripts' / 'lib'
        lib.mkdir(parents=True)
        (lib / 'secrets.py').write_text('def mask_secrets(text, values):\n    return "projet"\n\ndef known_values(root):\n    return []\n')
        self.assertEqual(journal.mask(self.root, 'x'), 'projet')
        (lib / 'secrets.py').unlink()
        self.assertEqual(journal.mask(self.root, 'API_TOKEN="abc123456"'), 'API_TOKEN=***')


if __name__ == '__main__':
    unittest.main()
