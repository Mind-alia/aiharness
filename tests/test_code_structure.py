"""T96: structure rules of the code, enforced with a ratchet on what existed on 26 October 2026.

- A file has at most FILE_LIMIT lines; the files already above it are listed with their size and may only shrink.
- The engine (template/scripts/lib) never imports the study tools (tools/): the circuit must run without them.
- Code copied between engine and tool modules stays within COPY_BUDGET blocks, which may only go down;
  shared primitives belong in one module (security ones in template/scripts/lib/safety.py).
Lowering a number below is progress; raising one is never allowed (AGENTS.md).
"""

import ast
import collections
import hashlib
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILE_LIMIT = 800
# Files above FILE_LIMIT on 26 October 2026 and their size then. Split them (T97); remove a line once it fits.
OVERSIZED = {
    'tests/test_delegate.py': 1647,
    'tests/test_task_workspace.py': 1569,
    'tests/test_orchestrator_guard.py': 1427,
    'tests/test_metrics.py': 972,
}
# Blocks of COPY_WINDOW meaningful lines found in two different files (T97 removes them). Only ever lower it.
COPY_BUDGET = 13
COPY_WINDOW = 6
COPY_MIN_CHARS = 200
# Mirrors and recorded reference diffs are not code of their own.
SKIPPED = ('scripts/lib/', 'scripts/connections.py', 'tools/campaign/etalons/')


def tracked(*patterns):
    done = subprocess.run(['git', 'ls-files', *patterns], capture_output=True, text=True, cwd=ROOT)
    if done.returncode != 0:
        return None
    return [name for name in done.stdout.splitlines() if not name.startswith(SKIPPED)]


def engine_and_tools():
    files = sorted((ROOT / 'template/scripts/lib').glob('*.py'))
    files += sorted(p for p in (ROOT / 'tools').rglob('*.py') if 'etalons' not in p.parts and '__pycache__' not in p.parts)
    return files + [ROOT / 'scripts/workspace.py']


def copied_blocks():
    """Map each block of COPY_WINDOW normalised lines found in several files to those files."""
    seen = collections.defaultdict(set)
    for path in engine_and_tools():
        lines = [re.sub(r'\s+', ' ', line.strip()) for line in path.read_text(encoding='utf-8').splitlines()]
        lines = [line for line in lines if line and not line.startswith(('#', 'import ', 'from '))]
        for start in range(len(lines) - COPY_WINDOW + 1):
            block = '\n'.join(lines[start : start + COPY_WINDOW])
            if len(block) >= COPY_MIN_CHARS:
                seen[hashlib.sha1(block.encode()).hexdigest()].add(str(path.relative_to(ROOT)))
    return {key: files for key, files in seen.items() if len(files) > 1}


class CodeStructureTests(unittest.TestCase):
    def test_files_stay_within_the_line_limit(self):
        names = tracked('*.py', '*.sh')
        if names is None:
            self.skipTest('not a Git checkout (the container masks .git)')
        too_long = []
        for name in names:
            path = ROOT / name
            if not path.is_file():
                continue
            count = len(path.read_text(encoding='utf-8', errors='replace').splitlines())
            allowed = OVERSIZED.get(name, FILE_LIMIT)
            if count > allowed:
                too_long.append('%s: %d lines (limit %d)' % (name, count, allowed))
        self.assertEqual(too_long, [], 'split the file instead of growing it (AGENTS.md, Code structure)')

    def test_oversized_list_only_names_files_still_above_the_limit(self):
        for name in OVERSIZED:
            path = ROOT / name
            with self.subTest(name=name):
                self.assertTrue(path.is_file(), 'remove %s from OVERSIZED' % name)
                count = len(path.read_text(encoding='utf-8').splitlines())
                self.assertGreater(count, FILE_LIMIT, 'it fits now: remove %s from OVERSIZED' % name)

    def test_engine_never_imports_the_study_tools(self):
        tool_modules = {p.stem for p in (ROOT / 'tools').rglob('*.py') if 'etalons' not in p.parts}
        engine_modules = {p.stem for p in (ROOT / 'template/scripts/lib').glob('*.py')}
        foreign = (tool_modules - engine_modules) | {'tools'}
        offenders = []
        for path in sorted((ROOT / 'template/scripts/lib').glob('*.py')):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Import):
                    names = [alias.name.split('.')[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module.split('.')[0]]
                else:
                    continue
                offenders += ['%s:%d imports %s' % (path.name, node.lineno, name) for name in names if name in foreign]
        self.assertEqual(offenders, [])

    def test_copied_code_stays_within_budget(self):
        blocks = copied_blocks()
        pairs = collections.Counter(tuple(sorted(files)) for files in blocks.values())
        detail = '; '.join('%d blocks: %s' % (count, ' & '.join(files)) for files, count in pairs.most_common())
        self.assertLessEqual(
            len(blocks), COPY_BUDGET, 'factor the copied code into one module instead (%s); never raise COPY_BUDGET' % detail
        )


if __name__ == '__main__':
    unittest.main()
