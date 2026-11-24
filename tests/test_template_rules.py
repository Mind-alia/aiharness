"""T25, kit part: the template carries one short rules README, no ECC rules."""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r'tdd-guide|ecc|bmad', re.I)


class TemplateRulesTests(unittest.TestCase):
    def test_no_forbidden_word_anywhere_under_template_claude(self):
        hits = []
        for path in (ROOT / 'template/.claude').rglob('*'):
            if path.is_file():
                text = path.read_text(encoding='utf-8', errors='replace')
                if FORBIDDEN.search(text) or FORBIDDEN.search(path.name):
                    hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [])

    def test_rules_folder_holds_only_a_short_readme_pointing_to_the_real_rules(self):
        rules = ROOT / 'template/.claude/rules'
        entries = sorted(p.relative_to(rules).as_posix() for p in rules.rglob('*'))
        self.assertEqual(entries, ['README.md'])
        text = (rules / 'README.md').read_text(encoding='utf-8')
        self.assertLessEqual(len(text.splitlines()), 10)
        for needle in ('AGENTS.md', 'docs/ORCHESTRATEUR.md', 'team.json'):
            self.assertIn(needle, text)
        self.assertIsNone(re.search(r'\[[^\]]*\]\([^)]*\)', text))

    def test_settings_untouched_by_this_task(self):
        self.assertTrue((ROOT / 'template/.claude/settings.json').is_file())

    def test_new_project_gets_only_the_readme(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(
                os.environ,
                GIT_AUTHOR_NAME='T',
                GIT_AUTHOR_EMAIL='t@example.invalid',
                GIT_COMMITTER_NAME='T',
                GIT_COMMITTER_EMAIL='t@example.invalid',
            )
            done = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/workspace.py'), 'new', 'demo', '--parent', tmp],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            rules = Path(tmp) / 'demo/.claude/rules'
            self.assertEqual(sorted(p.relative_to(rules).as_posix() for p in rules.rglob('*')), ['README.md'])


if __name__ == '__main__':
    unittest.main()
