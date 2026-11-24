"""Secret spans contain offsets and types only; values never enter diagnostics."""

import os
import re
import shlex
from pathlib import Path

DEFAULT_SECRETS_PATH = Path.home() / '.config/workspace-ia/secrets.env'
_ENV_LINE = re.compile(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*')
_GENERIC = r'token|key|secret|jeton|clé|cle|password'
_VALUE = r"""(?:"[^"]*"|'[^']*'|[^\s;|&]+)"""
# An assignment only counts with a literal value: a quoted string, or a bare word
# that starts like an env value and is not a call, a common word, or a code opener.
_LITERAL = (
    r"""(?:"[^"]*"|'[^']*'|(?![^\s;|&]*\()(?!(?:true|false|none|null)\b)"""
    r"""[A-Za-z0-9_./+-][^\s;|&]*)"""
)
# Capture the optional generic label for masking, but validate only the value.
_AUTH = re.compile(r'\b(Bearer|Basic)\s+((?:(?:' + _GENERIC + r')\s+)?([A-Za-z0-9._~+/=-]+))', re.I)


_PATTERNS = [
    ('assignment', re.compile(r'\b[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*=\s*(' + _LITERAL + ')', re.I)),
    ('argument', re.compile(r'--(?:password|token|api-key)(?:\s+|=)(' + _VALUE + ')', re.I)),
    ('api_key', re.compile(r'\b(?:sk-[A-Za-z0-9_-]+|ghp_[A-Za-z0-9_]+|xox[bp]-[A-Za-z0-9_-]+|AKIA[A-Z0-9]{16})\b')),
]


def find_secrets(text, known_values):
    hits = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span(1 if pattern.groups else 0)
            hits.append(dict(start=start, end=end, type=kind))
    for match in _AUTH.finditer(text):
        value = match[3]
        if len(value) >= 8 and (re.search(r'[0-9]', value) or (re.search(r'[a-z]', value) and re.search(r'[A-Z]', value))):
            start, end = match.span(2)
            hits.append(dict(start=start, end=end, type=match[1].lower()))
    for value in set(known_values):
        if len(value) < 4 or value.lower() in COMMON_VALUES:
            continue
        offset = 0
        while True:
            start = text.find(value, offset)
            if start < 0:
                break
            hits.append(dict(start=start, end=start + len(value), type='known_value'))
            offset = start + 1
    return sorted(hits, key=lambda hit: (hit['start'], hit['end'], hit['type']))


def mask_secrets(text, known_values):
    spans = []
    for hit in find_secrets(text, known_values):
        start, end = hit['start'], hit['end']
        if spans and start < spans[-1][1]:
            spans[-1][1] = max(end, spans[-1][1])
        else:
            spans.append([start, end])
    for start, end in reversed(spans):
        text = text[:start] + '***' + text[end:]
    return text


# Known values of four characters or more are masked literally, except this
# explicit list of common configuration words (also excluded for long values).
COMMON_VALUES = {'true', 'false', 'none', 'null', 'test', 'development', 'production', 'localhost'}


def secret_files(project_root):
    for directory, dirs, files in os.walk(project_root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', '.workspace') and not (Path(directory) / d).is_symlink()]
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            if (
                (name.startswith('.env') and name != '.env.example')
                or 'secrets' in path.relative_to(project_root).parts
                or name.endswith('.key')
            ):
                yield path


def known_values(project_root):
    """Recursive dotenv/secrets inventory; quotes and escapes follow shlex rules.

    No expansion or execution is performed. Single quotes preserve backslashes;
    double quotes decode escaped quotes and backslashes, plus newline/tab escapes.
    """
    values = set()
    for path in secret_files(Path(project_root)):
        text = path.read_text(encoding='utf-8', errors='replace')
        if not path.name.startswith('.env'):
            candidates = [text.strip()]
        else:
            candidates = []
            for line in text.splitlines():
                match = re.match(r'^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(.*)$', line)
                if not match:
                    continue
                raw = match[1].strip()
                try:
                    if raw.startswith(('"', "'")):
                        tokens = shlex.split(raw, comments=True)
                        value = tokens[0] if tokens else ''
                        if raw.startswith('"'):
                            value = value.replace(r'\n', '\n').replace(r'\t', '\t').replace(r'\r', '\r')
                    else:
                        value = re.split(r'\s+#', raw, maxsplit=1)[0].rstrip()
                    candidates.append(value)
                except ValueError:
                    continue
        values.update(v for v in candidates if len(v) >= 4 and v.lower() not in COMMON_VALUES)
    return sorted(values)


def load_env(path=None) -> dict[str, str]:
    """Single reader of secrets.env: literal values, no expansion, never echoed."""
    if path is None:
        path = os.environ.get('WORKSPACE_SECRETS_FILE') or DEFAULT_SECRETS_PATH
    path = Path(path)
    if path.is_symlink():
        raise ValueError('secrets.env : lien symbolique refusé')
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return {}
    if mode & 0o077:
        raise ValueError('secrets.env : permissions trop larges (attendu 0600)')
    try:
        text = path.read_text(encoding='utf-8')
    except UnicodeError:
        raise ValueError('secrets.env : encodage UTF-8 attendu') from None
    result = {}
    for line in text.splitlines():
        match = _ENV_LINE.fullmatch(line)
        if not match:
            continue
        value = match[2]
        if value[:1] in ('"', "'"):
            if len(value) < 2 or value[-1] != value[0]:
                continue
            value = value[1:-1]
        result[match[1]] = value
    return result
