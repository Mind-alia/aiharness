"""Documentation check: local Markdown links resolve, docs/LOG.md only grows.

Usage: python3 scripts/lib/docs_links.py <root>
Prints one `DOCS FAIL:` line per problem and exits 1 when there is at least one.
"""

import os
import re
import subprocess
import sys
from urllib.parse import unquote

EXCLUDED_DIRS = {'node_modules', '.git', '.workspace'}
EXCLUDED_PATHS = {'docs/archive'}
LINK = re.compile(r'\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)')
SCHEME = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*:')


def markdown_files(root):
    for folder, dirs, files in os.walk(root):
        rel = os.path.relpath(folder, root).replace(os.sep, '/')
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and ('%s/%s' % (rel, d)).lstrip('./') not in EXCLUDED_PATHS)
        for name in sorted(files):
            if name.endswith('.md'):
                yield os.path.join(folder, name)


def local_targets(path):
    in_code = False
    with open(path, encoding='utf-8', errors='replace') as handle:
        for line in handle:
            if line.lstrip().startswith('```'):
                in_code = not in_code
            elif not in_code:
                for target in LINK.findall(line):
                    if not SCHEME.match(target) and not target.startswith('#'):
                        yield target


def broken_links(root):
    for path in markdown_files(root):
        for target in local_targets(path):
            clean = unquote(re.split(r'[#?]', target, maxsplit=1)[0])
            if not clean:
                continue
            base = root if clean.startswith('/') else os.path.dirname(path)
            if not os.path.exists(os.path.join(base, clean.lstrip('/'))):
                yield 'lien cassé %s -> %s' % (os.path.relpath(path, root).replace(os.sep, '/'), target)


def log_is_append_only(root):
    try:
        shown = subprocess.run(
            ['git', 'show', 'HEAD:./docs/LOG.md'], cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
    except OSError:
        return True
    if shown.returncode != 0:
        return True
    try:
        with open(os.path.join(root, 'docs/LOG.md'), encoding='utf-8', errors='replace') as handle:
            current = handle.read()
    except OSError:
        current = ''
    return current.rstrip().startswith(shown.stdout.rstrip())


def main(argv):
    root = os.path.abspath(argv[1] if len(argv) > 1 else '.')
    problems = list(broken_links(root))
    if not log_is_append_only(root):
        problems.append("docs/LOG.md n'est plus en ajout seul")
    for problem in problems:
        print('DOCS FAIL: ' + problem)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
