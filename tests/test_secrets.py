import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
# Keep fixtures in the repository: system temporary directories may be sandboxed.
TEST_TMP = ROOT / '.workspace/tmp-tests'
TEST_TMP.mkdir(parents=True, exist_ok=True)


class SecretsTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('project_secrets', ROOT / 'template/scripts/lib/secrets.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_reference_patterns_and_positions(self):
        module = self.module()
        for text, expected in [
            ('Bearer token abc123def', 'Bearer ***'),
            ('Basic example', 'Basic example'),
            ('Basic token abc123def', 'Basic ***'),
            ('API_KEY="abc123"', 'API_KEY=***'),
            ('--password abc123', '--password ***'),
            ('sk-abc123', '***'),
            ('ghp_abc123', '***'),
            ('xoxb-abc123', '***'),
            ('AKIAABCDEFGHIJKLMNOP', '***'),  # gitleaks:allow
        ]:
            with self.subTest(text=text):
                self.assertEqual(module.mask_secrets(text, []), expected)
                hits = module.find_secrets(text, [])
                self.assertEqual(bool(hits), expected != text)
                for hit in hits:
                    self.assertEqual(set(hit), {'start', 'end', 'type'})
                    self.assertIsInstance(hit['start'], int)

    def test_auth_mentions_are_not_secrets(self):
        module = self.module()
        for text in (
            'un en-tête « Bearer » suivi d un mot',
            'Bearer suivi',
            'Bearer ou Basic',
            'Bearer',
            'Bearer token',
            'Basic secret',
            'Bearer token documentation',
            'Bearer abc1234',
            'Basic abcdefgh',
            'Bearer DOCUMENTATION',
        ):
            with self.subTest(text=text):
                self.assertEqual(module.find_secrets(text, []), [])
                self.assertEqual(module.mask_secrets(text, []), text)

    def test_auth_token_values_are_masked(self):
        module = self.module()
        for scheme in ('Bearer', 'Basic'):
            for prefix in ('', 'token ', 'key ', 'secret ', 'jeton ', 'clé ', 'password '):
                for value in ('abc123def', 'eyJhbGciOiJI', 'abcDEFgh', 'abc1._~+/=-'):
                    text = scheme + ' ' + prefix + value
                    with self.subTest(text=text):
                        self.assertEqual(module.mask_secrets(text, []), scheme + ' ***')
        self.assertEqual(module.mask_secrets('Authorization: Bearer eyJhbGciOiJI…', []), 'Authorization: Bearer ***…')

    def test_code_assignments_are_not_secrets(self):
        module = self.module()
        for text in (
            'const OBSERVATION_KEYS = ["alpha", "beta", "gamma"] as const;',
            'const hasKey = (value, key) => Object.hasOwn(value, key);',
            'OBSERVATION_KEYS.every((key) => Object.hasOwn(observation, key))',
            'json.dumps(data, sort_keys=True)',
            'TOKEN_CHECK == "abc123def"',
            'mapper(TOKEN_NAME => "abc123def")',
            'SECRET_CALL=fetchSecret(abc123)',
        ):
            with self.subTest(text=text):
                self.assertEqual(module.find_secrets(text, []), [])
                self.assertEqual(module.mask_secrets(text, []), text)

    def test_literal_assignments_are_secrets(self):
        module = self.module()
        for text, expected in [
            ('CLIENT_KEY="abc123def"', 'CLIENT_KEY=***'),
            ('export GITHUB_TOKEN=ghp_abc123def456', 'export GITHUB_TOKEN=***'),  # gitleaks:allow
            ("DB_PASSWORD='hunter2hunter'", 'DB_PASSWORD=***'),
            ('SECRET_VALUE=abc123XYZ', 'SECRET_VALUE=***'),
        ]:
            with self.subTest(text=text):
                self.assertEqual(module.mask_secrets(text, []), expected)
                self.assertTrue(module.find_secrets(text, []))

    def test_overlapping_known_values_are_fully_masked(self):
        module = self.module()
        self.assertEqual(module.mask_secrets('abcdefghijk', ['abcdef', 'defghijk']), '***')

    def test_known_values_and_examples(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            (root / '.env').write_text('export DB="real-value"\nSHORT=abc\n')
            (root / '.env.local').write_text('OTHER=other-value # comment\n')
            (root / '.env.example').write_text('EXAMPLE=example-value\n')
            (root / 'secrets').mkdir()
            (root / 'secrets/key').write_text('file-value\n')
            module = self.module()
            values = module.known_values(root)
            self.assertEqual(set(values), {'real-value', 'other-value', 'file-value'})
            self.assertEqual(module.mask_secrets('real-value other-value file-value example-value', values), '*** *** *** example-value')

    def test_load_env_absent_and_empty(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            module = self.module()
            self.assertEqual(module.load_env(root / 'missing.env'), {})
            (root / 'empty.env').write_text('')
            (root / 'empty.env').chmod(0o600)
            self.assertEqual(module.load_env(root / 'empty.env'), {})

    def test_load_env_refuses_wide_permissions_and_symlinks(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            module = self.module()
            wide = root / 'wide.env'
            wide.write_text('TOKEN_NAME=hunter2-value\n')
            wide.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'permissions trop larges'):
                module.load_env(wide)
            try:
                module.load_env(wide)
            except ValueError as error:
                self.assertNotIn('hunter2-value', str(error))
            target = root / 'target.env'
            target.write_text('A_KEY=alpha\n')
            target.chmod(0o600)
            (root / 'link.env').symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'lien symbolique refusé'):
                module.load_env(root / 'link.env')

    def test_load_env_line_syntax(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            path = root / 'secrets.env'
            path.write_text(
                "# commentaire\n\nexport A_KEY=alpha\nB_KEY = 'beta gamma'\nC_KEY=\"delta\"\n"
                "D_KEY=\nE_KEY=a=b\n1BAD=x\nBAD-KEY=y\nF_KEY='unterminated\n"
            )
            path.chmod(0o600)
            self.assertEqual(
                self.module().load_env(path), {'A_KEY': 'alpha', 'B_KEY': 'beta gamma', 'C_KEY': 'delta', 'D_KEY': '', 'E_KEY': 'a=b'}
            )

    def test_load_env_path_from_environment(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            path = Path(tmp) / 'secrets.env'
            path.write_text('WSC_NTFY_TOPIC=from-env\n')
            path.chmod(0o600)
            with mock.patch.dict(os.environ, {'WORKSPACE_SECRETS_FILE': str(path)}):
                self.assertEqual(self.module().load_env(), {'WSC_NTFY_TOPIC': 'from-env'})

    def test_generation_permissions_and_overwrite(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            script = root / 'scripts/test-secrets.sh'
            script.write_bytes((ROOT / 'template/scripts/test-secrets.sh').read_bytes())

            def run(*args):
                return subprocess.run(['/bin/sh', str(script), *args], capture_output=True)

            self.assertEqual(run().returncode, 0)
            files = sorted((root / '.workspace/secrets-test').iterdir())
            self.assertEqual({p.name for p in files}, {'db_password', 'api_key', 'jwt_secret'})
            before = [p.read_bytes() for p in files]
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in files))
            self.assertNotEqual(run().returncode, 0)
            self.assertEqual(before, [p.read_bytes() for p in files])
            self.assertEqual(run('--force').returncode, 0)
            self.assertTrue(all(a != p.read_bytes() for a, p in zip(before, files, strict=False)))
            self.assertNotEqual(run('../escape').returncode, 0)
            self.assertEqual(run('custom_password').returncode, 0)
            self.assertTrue((root / '.workspace/secrets-test/custom_password').is_file())

    def test_tracing_check(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            script = root / 'scripts/check-secret-tracing.sh'
            script.write_bytes((ROOT / 'template/scripts/check-secret-tracing.sh').read_bytes())
            bad = root / 'bad.sh'
            for trace in ('set -x', 'bash -x child.sh', 'set -o xtrace'):
                bad.write_text(trace + '\ncat secrets/db_password\n')
                result = subprocess.run(['/bin/sh', str(script)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('bad.sh', result.stderr)
            bad.write_text('cat secrets/db_password\n')
            self.assertEqual(subprocess.run(['/bin/sh', str(script)], capture_output=True).returncode, 0)
