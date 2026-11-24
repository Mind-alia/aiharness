"""Modules a foreign project provides or declares outside the places the kit's own projects use.

`plan_check` asks it which imports of a hidden test are legitimate: packages declared in `setup.py`, `setup.cfg` or a
`requirements/` folder, modules living anywhere in the project's tree (`src/` layout, test fixtures), the runner the
task's commands name (`python -m pytest`), and imports the test makes only under a condition (E18: flask, requests).
"""

import ast
import configparser
import os
import re

SKIPPED_DIRS = {'.git', '.workspace', 'node_modules', '.venv', 'venv', '__pycache__', '.tox', 'build', 'dist'}
SETUP_KEYWORDS = ('install_requires', 'tests_require', 'setup_requires', 'extras_require')
RUNNER = re.compile(r'(?:^|\s)-m\s+([A-Za-z_][A-Za-z0-9_]*)')


def _read(path):
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def _strings(node, assigned):
    """String literals of a list, a tuple, the values of a dict, or a module-level name bound to one of them."""
    if isinstance(node, ast.Name):
        node = assigned.get(node.id)
    if isinstance(node, ast.Dict):
        return [text for value in node.values for text in _strings(value, assigned)]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
    return []


def _setup_py_lines(root):
    try:
        tree = ast.parse(_read(root / 'setup.py'))
    except SyntaxError:
        return []
    assigned = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
    }
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    return [text for call in calls for key in call.keywords if key.arg in SETUP_KEYWORDS for text in _strings(key.value, assigned)]


def _setup_cfg_lines(root):
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(_read(root / 'setup.cfg'))
    except configparser.Error:
        return []
    values = [parser.get('options', key, fallback='') for key in SETUP_KEYWORDS[:3]]
    if parser.has_section('options.extras_require'):
        values.extend(value for _, value in parser.items('options.extras_require'))
    return [line for value in values for line in value.splitlines()]


def _folder_lines(root):
    folder = root / 'requirements'
    paths = sorted(p for pattern in ('*.txt', '*.in') for p in folder.glob(pattern)) if folder.is_dir() else []
    return [line for path in paths for line in _read(path).splitlines() if not line.lstrip().startswith(('#', '-'))]


def requirement_lines(root):
    """Requirement lines of `setup.py`, `setup.cfg` and `requirements/*.txt|*.in`; nothing is executed."""
    return _setup_py_lines(root) + _setup_cfg_lines(root) + _folder_lines(root)


def tree_modules(root):
    """Import names found anywhere in the project: every `.py` file and every folder that holds one."""
    names = set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS]
        stems = {name[:-3] for name in files if name.endswith('.py')}
        if stems and directory != str(root):
            names.add(os.path.basename(directory))
        names.update(stems)
    return names


def runner_modules(task):
    """Modules the task's own commands run with `-m` (the test runner is installed, or the task cannot pass)."""
    commands = '%s\n%s' % (task.get('verify') or '', task.get('acceptance_verify') or '')
    return set(RUNNER.findall(commands))


def conditional_modules(tree):
    """Modules imported only inside an `if` or a `try`: optional by construction (platform or version branches)."""
    names = set()
    for block in ast.walk(tree):
        if not isinstance(block, (ast.If, ast.Try)):
            continue
        for node in ast.walk(block):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module)
    return names
