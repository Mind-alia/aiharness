#!/bin/sh
# Static conservative check: tracing and secret-file references in the same script.
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
root=$(CDPATH= cd -- "${0%/*}/.." && pwd) || exit 2
exec "${WORKSPACE_PYTHON:-python3}" - "$root" <<'PY'
import os
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
trace = re.compile(r'\bbash\s+-x\b|\bset\s+-x\b|\bset\s+-o\s+xtrace\b')
secret = re.compile(r'secrets/|\.env\b|[\w.-]*_(?:password|secret)\b|[\w.*-]+\.key\b', re.I)
bad = []
for directory, dirs, files in os.walk(root, followlinks=False):
    dirs[:] = [name for name in dirs if name not in ('node_modules', '.workspace', '.git')]
    for name in files:
        if not (name.endswith('.sh') or name == 'Makefile'):
            continue
        path = Path(directory) / name
        if path.is_symlink():
            continue
        # Comments do not execute and must not make the checker reject itself.
        text = '\n'.join(line for line in path.read_text(errors='replace').splitlines()
                         if not line.lstrip().startswith('#'))
        if trace.search(text) and secret.search(text):
            bad.append(str(path.relative_to(root)))
for name in sorted(bad):
    print(name, file=sys.stderr)
sys.exit(bool(bad))
PY
