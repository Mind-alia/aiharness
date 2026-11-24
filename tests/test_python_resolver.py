"""T94: every circuit script and hook runs the interpreter resolved by template/scripts/python.sh (Python 3.14).

An older interpreter is refused with exit code 2 and a clear message (a hook then blocks), never a cryptic import error;
python3.14 is preferred when present; no script runs a bare `python3` behind the resolver's back (a script copied
without the resolver, as some test fixtures do, falls back to `python3`).
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / 'template/scripts/python.sh'
BARE_PYTHON = re.compile(r'(?<![\w/.$"-])python3(?![\w.])')


class PythonResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bin = Path(self.temp.name) / 'bin'
        self.bin.mkdir()
        self.old = self.bin / 'old-python'
        self.old.write_text('#!/bin/sh\nexit 1\n')
        self.old.chmod(0o755)

    def env(self, **extra):
        return {'PATH': '%s:/usr/bin:/bin' % self.bin, 'HOME': self.temp.name, **extra}

    def test_old_interpreter_is_refused_by_a_circuit_script(self):
        done = subprocess.run(
            ['sh', str(ROOT / 'scripts/statusline.sh')],
            input='{}',
            capture_output=True,
            text=True,
            env=self.env(WORKSPACE_PYTHON=str(self.old)),
        )
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn('Python 3.14 requis', done.stderr)
        self.assertNotIn('Traceback', done.stderr)

    def test_old_interpreter_makes_the_guard_hook_block(self):
        for settings in (ROOT / '.claude/settings.json', ROOT / 'template/.claude/settings.json'):
            hooks = json.loads(settings.read_text(encoding='utf-8'))['hooks']
            for event in ('PreToolUse', 'Stop'):
                command = hooks[event][0]['hooks'][0]['command']
                with self.subTest(settings=settings.name, event=event):
                    done = subprocess.run(
                        ['sh', '-c', command],
                        input='{}',
                        capture_output=True,
                        text=True,
                        env=self.env(WORKSPACE_PYTHON=str(self.old), CLAUDE_PROJECT_DIR=str(ROOT)),
                    )
                    self.assertEqual(done.returncode, 2, done.stderr)
                    self.assertIn('Python 3.14 requis', done.stderr)

    def test_python314_is_preferred_and_exported(self):
        (self.bin / 'python3.14').write_text('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
        (self.bin / 'python3.14').chmod(0o755)
        done = subprocess.run(['sh', '-c', '. "%s" && echo "$WORKSPACE_PYTHON"' % RESOLVER], capture_output=True, text=True, env=self.env())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), str(self.bin / 'python3.14'))

    def test_override_is_honoured(self):
        done = subprocess.run(
            ['sh', '-c', '. "%s" && echo "$WORKSPACE_PYTHON"' % RESOLVER],
            capture_output=True,
            text=True,
            env=self.env(WORKSPACE_PYTHON=sys.executable),
        )
        self.assertEqual((done.returncode, done.stdout.strip()), (0, sys.executable), done.stderr)

    def test_no_circuit_script_runs_a_bare_python3(self):
        for script in sorted(path for path in (ROOT / 'template/scripts').glob('*.sh') if path != RESOLVER):
            text = script.read_text(encoding='utf-8')
            with self.subTest(script=script.name):
                for line in text.splitlines():
                    if line.lstrip().startswith('#') or 'command -v python3' in line:
                        continue
                    self.assertIsNone(BARE_PYTHON.search(line), line)
                if '"${WORKSPACE_PYTHON:-python3}"' in text:
                    self.assertIn('[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"', text)

    def test_resolver_is_not_executable_on_its_own(self):
        # Mode bits, not os.access: some container mounts report every file as executable.
        self.assertEqual(RESOLVER.stat().st_mode & 0o111, 0)


if __name__ == '__main__':
    unittest.main()
