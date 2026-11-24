"""Find existing tests outside a task's scope that compare exactly a structure the task sheet names.

Public API: named_structures(text), exact_comparisons(path, markers, fields), find and find_nearby(root, task, text).
A structure is an event type, the lock (verrou) or the task state; a comparison is exact when an assertEqual-like call
holds a dict literal or a non-empty literal list or tuple of strings. A field comparison (find) holds a dict with a
string key or a list of field names AND compares a value that comes from the events journal in the same test function
(events.read, a `.events(` helper, events.jsonl, reconstruct…, directly or through assigned names); the other exact
comparisons, a status.json or an outcome dict among them, are only nearby (find_nearby).
"""

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import events  # noqa: E402

CONTEXT_LINES = 3
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
EXACT_ASSERTS = ('assertEqual', 'assertDictEqual', 'assertListEqual', 'assertTupleEqual')
LOCK_NAME = 'verrou'
LOCK_FILE = 'orchestrator.json'
LOCK_MARKERS = (LOCK_FILE, 'self.lock')
STATE_NAME = 'état'
STATE_FILE = 'state.json'
STATE_PHRASES = (STATE_FILE, 'état de la tâche', 'état de tâche')
TESTS_DIR = 'tests'
# Text of a statement that reads the events journal: the names it assigns carry event details.
EVENT_SOURCES = ('events.read(', '.events(', 'events.jsonl', 'reconstruct', 'read_events(', 'events.record(', 'events._instant(')
ASSIGNMENTS = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For, ast.With, ast.comprehension)
TEST_GLOB = 'test_*.py'
HEADER_FENCE = '---'
CODE_QUOTE = '`'


def _body(text):
    """The text without its leading header (first line and a later line both equal to ---), else the whole text."""
    lines = text.split('\n')
    if lines[0].rstrip() != HEADER_FENCE:
        return text
    for index in range(1, len(lines)):
        if lines[index].rstrip() == HEADER_FENCE:
            return '\n'.join(lines[index + 1 :])
    return text


def named_structures(text) -> dict[str, tuple[str, ...]]:
    """Map each structure the body of the text names to its source markers; event types only between backticks."""
    text = _body(text)
    found: dict[str, tuple[str, ...]] = {}
    for kind in events.KINDS:
        if CODE_QUOTE + kind + CODE_QUOTE in text:
            found[kind] = ("'%s'" % kind, '"%s"' % kind)
    if LOCK_FILE in text or re.search(r'(?<!\w)' + LOCK_NAME + r'(?!\w)', text):
        found[LOCK_NAME] = LOCK_MARKERS
    if any(phrase in text for phrase in STATE_PHRASES):
        found[STATE_NAME] = (STATE_FILE,)
    return found


def _is_string_sequence(node):
    return (
        isinstance(node, (ast.List, ast.Tuple))
        and bool(node.elts)
        and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts)
    )


def _is_exact_argument(node):
    return _is_string_sequence(node) or any(isinstance(child, ast.Dict) for child in ast.walk(node))


def _is_exact_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in EXACT_ASSERTS
        and any(_is_exact_argument(arg) for arg in node.args)
    )


def _is_field_sequence(node):
    return _is_string_sequence(node) and all(IDENTIFIER.fullmatch(e.value) and e.value not in events.KINDS for e in node.elts)


def _is_field_node(node):
    if isinstance(node, ast.Dict):
        return any(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys)
    return _is_field_sequence(node)


def is_field_comparison(call) -> bool:
    """True when an argument of the exact call holds a dict with a string key or a list of field names."""
    return any(_is_field_node(child) for arg in call.args for child in ast.walk(arg))


def _segment(source, node):
    return ast.get_source_segment(source, node) or ''


def _names(node):
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _targets(node):
    if isinstance(node, (ast.Assign, ast.For, ast.comprehension)):
        found = set()
        for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
            found |= _names(target)
        return found
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return _names(node.target)
    return {name for item in node.items if item.optional_vars is not None for name in _names(item.optional_vars)}


def _reads_events(source, node, tainted):
    text = _segment(source, node)
    return any(marker in text for marker in EVENT_SOURCES) or bool(_names(node) & tainted)


def _tainted_names(source, nodes, seed):
    """Names assigned, directly or through other tainted names, from a statement that reads the events journal."""
    tainted, grown = set(seed), True
    while grown:
        grown = False
        for node in nodes:
            if isinstance(node, ASSIGNMENTS):
                value = (
                    node.iter
                    if isinstance(node, (ast.For, ast.comprehension))
                    else (node.items if isinstance(node, ast.With) else node.value)
                )
                if value is None:
                    continue
                values = value if isinstance(value, list) else [value]
                if any(_reads_events(source, item, tainted) for item in values):
                    new = _targets(node) - tainted
                    if new:
                        tainted |= new
                        grown = True
    return tainted


def event_sourced_lines(source, tree) -> set:
    """First lines of the exact calls whose compared values come from the events journal: in their own function,
    or in a module-level fixture (a name assigned at the top of the file)."""
    found = set()
    module_level = _tainted_names(source, [node for node in tree.body if isinstance(node, ASSIGNMENTS)], set())
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tainted = _tainted_names(source, list(ast.walk(function)), module_level)
        for node in ast.walk(function):
            if _is_exact_call(node) and any(_reads_events(source, arg, tainted) for arg in node.args):
                found.add(node.lineno)
    return found


def exact_comparisons(path, markers, fields=True) -> list[int]:
    """First lines of the exact comparisons in the file that carry one of the markers and match the fields kind:
    fields=True keeps the field comparisons of event-sourced values, fields=False every other exact comparison."""
    try:
        source = Path(path).read_text(encoding='utf-8')
        tree = ast.parse(source)
    except (OSError, ValueError, SyntaxError, RecursionError):
        return []
    lines = source.splitlines()
    sourced = event_sourced_lines(source, tree)
    kept = set()
    for node in ast.walk(tree):
        if not _is_exact_call(node) or (is_field_comparison(node) and node.lineno in sourced) != fields:
            continue
        first, last = node.lineno, getattr(node, 'end_lineno', None) or node.lineno
        window = '\n'.join(lines[max(first - 1 - CONTEXT_LINES, 0) : last])
        if any(marker in window for marker in markers):
            kept.add(first)
    return sorted(kept)


def _candidates(root, task):
    folder = root / TESTS_DIR
    if folder.is_symlink() or not folder.is_dir():
        return []
    skipped = set(task.get('allowed', [])) | set(task.get('acceptance', []))
    names = []
    for path in sorted(folder.glob(TEST_GLOB)):
        rel = TESTS_DIR + '/' + path.name
        if path.is_symlink() or not path.is_file() or rel in skipped or path.name in skipped:
            continue
        names.append((rel, path))
    return names


def _collect(root, task, text, fields):
    structures = named_structures(text)
    if not structures:
        return []
    found = []
    for rel, path in _candidates(Path(root), task):
        lines, names = set(), []
        for name in sorted(structures):
            hits = exact_comparisons(path, structures[name], fields)
            if hits:
                lines.update(hits)
                names.append(name)
        if not fields:
            lines -= set(n for name in structures for n in exact_comparisons(path, structures[name], True))
        if lines:
            found.append({'path': rel, 'lines': sorted(lines), 'structures': names})
    return sorted(found, key=lambda item: item['path'])


def find(root, task, text) -> list[dict]:
    """Existing tests outside the task scope that exactly compare a set of fields of a structure named by the sheet."""
    try:
        return _collect(root, task, text, True)
    except Exception:  # find never raises: any trouble drops the file
        return []


def find_nearby(root, task, text) -> list[dict]:
    """Exact comparisons of names or values near a named structure, outside the task scope (warning only)."""
    try:
        return _collect(root, task, text, False)
    except Exception:  # never raises
        return []
