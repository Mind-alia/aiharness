"""Read the deliberately small task front matter (no YAML dependency).

Public API: read_task(path) -> dict, including defaults and Markdown body.
Supported YAML subset: scalar fields, comments, block/inline string lists.
CLI: python3 scripts/lib/task.py task.md prints the same dictionary as JSON.
"""

import json
import re
import sys
from pathlib import Path


def _split(text, delimiter):
    parts, current, quote, escaped = [], [], None, False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == '\\' and quote == '"':
            current.append(char)
            escaped = True
            continue
        if char in "\"'":
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        if char == delimiter and quote is None:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(char)
    if quote:
        raise ValueError('Guillemet non fermé dans l’en-tête')
    parts.append(''.join(current).strip())
    return parts


def _comment(text):
    # A YAML comment begins at # only when preceded by whitespace.
    quote = None
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == '\\' and quote == '"':
            escaped = True
        elif char in "\"'":
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif char == '#' and quote is None and (index == 0 or text[index - 1].isspace()):
            return text[:index].strip()
    return text.strip()


def _scalar(value):
    value = value.strip()
    if value.startswith('"'):
        try:
            return json.loads(value)
        except ValueError as exc:
            raise ValueError('Chaîne invalide dans l’en-tête') from exc
    if value.startswith("'"):
        if not value.endswith("'") or len(value) < 2:
            raise ValueError('Chaîne invalide dans l’en-tête')
        return value[1:-1].replace("''", "'")
    return value


EXECUTORS = ('codex', 'antigravity', 'claude-haiku', 'claude-sonnet', 'claude-opus')
TASK_TYPES = ('code', 'docs', 'test', 'security', 'exploration')
FULL_GATE_MESSAGE = (
    'verify ne doit pas lancer la porte complète (scripts/gate.sh) : elle tourne à la fusion ; '
    'indiquer une commande ciblée, par exemple les tests des fichiers touchés, '
    'ou scripts/gate.sh --quick.'
)
GATE_CALL = re.compile(r'(?:bash\s+|sh\s+|\./)?scripts/gate\.sh(\s.*)?')


def runs_full_gate(command):
    """True when a verify command chain calls scripts/gate.sh without --quick."""
    for segment in re.split(r'&&|\|\||[;&|\n]', command):
        match = GATE_CALL.fullmatch(segment.strip())
        if match and '--quick' not in (match.group(1) or '').split():
            return True
    return False


def resolve_agent(name, start):
    """Map a member name of team.json to its executor; team is imported only when needed."""
    lib = str(Path(__file__).parent)
    if lib not in sys.path:
        sys.path.append(lib)
    import team

    return team.resolve(name, start)


def read_task(path, root=None):  # noqa: C901, PLR0915
    lines = Path(path).read_text(encoding='utf-8').splitlines(keepends=True)
    if not lines or lines[0].strip() != '---':
        raise ValueError('En-tête de tâche attendu (---)')
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == '---'), None)
    if end is None:
        raise ValueError('En-tête de tâche non terminé (---)')
    data = {}
    list_key = None
    for line in lines[1:end]:
        value = _comment(line)
        if not value:
            continue
        if line[0].isspace():
            if list_key not in ('allowed', 'acceptance') or not value.startswith('- '):
                raise ValueError('Liste allowed invalide')
            data[list_key].append(_scalar(value[2:]))
            continue
        match = re.fullmatch(r'([a-z_]+):\s*(.*)', value)
        if not match:
            raise ValueError('Champ d’en-tête invalide: ' + value)
        key, value = match.groups()
        if key in data:
            raise ValueError('Champ répété: ' + key)
        list_key = None
        if key in ('allowed', 'acceptance'):
            if not value:
                data[key] = []
                list_key = key
            elif value.startswith('[') and value.endswith(']'):
                data[key] = [_scalar(item) for item in _split(value[1:-1], ',')] if value[1:-1].strip() else []
            else:
                raise ValueError('allowed doit être une liste')
        else:
            data[key] = _scalar(value)
    for key in ('id', 'agent', 'allowed'):
        if key not in data or (key != 'allowed' and not data[key]):
            raise ValueError('Champ obligatoire manquant: ' + key)
    if not isinstance(data['id'], str) or not re.fullmatch(r'[a-z0-9-]+', data['id']):
        raise ValueError('id doit respecter [a-z0-9-]+')
    data['agent_name'] = None
    if data['agent'] not in EXECUTORS:
        data['agent_name'] = data['agent']
        data['agent'] = resolve_agent(data['agent'], root if root is not None else Path(path).parent)
    data.setdefault('mode', 'write')
    if data['mode'] not in ('write', 'review'):
        raise ValueError('mode doit être write ou review')
    if 'type' in data:
        if data['type'] not in TASK_TYPES:
            raise ValueError('type doit être code, docs, test, security ou exploration')
    elif data['mode'] == 'review':
        data['type'] = 'exploration'
    elif data['agent'] == 'antigravity':
        data['type'] = 'docs'
    else:
        data['type'] = 'code'
    if any(not isinstance(item, str) or not item for item in data['allowed']):
        raise ValueError('allowed doit contenir des chemins non vides')
    attempts = data.get('attempts', '2')
    if not isinstance(attempts, str) or not re.fullmatch(r'[1-9][0-9]*', attempts):
        raise ValueError('attempts doit être un entier positif')
    data['attempts'] = int(attempts)
    max_turns = data.get('max_turns')
    if max_turns is not None and (not isinstance(max_turns, str) or not re.fullmatch(r'[1-9][0-9]*', max_turns)):
        raise ValueError('max_turns doit être un entier positif')
    data['max_turns'] = None if max_turns is None else int(max_turns)
    if 'verify' in data and not isinstance(data['verify'], str):
        raise ValueError('verify doit être une commande')
    if 'verify' in data and runs_full_gate(data['verify']):
        raise ValueError(FULL_GATE_MESSAGE)
    for field, default in (('verify_profile', 'on'), ('verify_lint', 'on'), ('auto_retry', 'off')):
        data.setdefault(field, default)
        if data[field] not in ('on', 'off'):
            raise ValueError(field + ' doit être on ou off')
    if 'isolation' in data:
        if data['isolation'] not in ('none', 'exchange', 'full'):
            raise ValueError('isolation doit être none, exchange ou full')
    else:
        data['isolation'] = None
    data.setdefault('network', 'off')
    if data['network'] not in ('on', 'off'):
        raise ValueError('network doit être on ou off')
    data.setdefault('acceptance', [])
    for name in data['acceptance']:
        validate_acceptance_name(name)
    if data['acceptance'] and not data.get('acceptance_verify', '').strip():
        raise ValueError('acceptance_verify obligatoire avec acceptance')
    if data.get('acceptance_verify') and not data['acceptance']:
        raise ValueError('acceptance obligatoire avec acceptance_verify')
    data['body'] = ''.join(lines[end + 1 :])
    return data


def validate_acceptance_name(name):
    if (
        not isinstance(name, str)
        or not name
        or name.startswith('/')
        or any(part in ('', '.', '..', '.git', '.workspace') for part in name.split('/'))
        or '\\' in name
        or any(ord(c) < 32 for c in name)
    ):
        raise ValueError('Chemin acceptance invalide')


def acceptance_feedback(output):
    """Allow only test identifiers and assertion messages, never source excerpts."""
    output = re.sub(r'\x1b\[[0-9;]*m', '', output)
    lines = []
    for line in output.splitlines():
        line = line.strip()
        if re.match(r'^(?:FAIL(?:ED)?[: ]|[×✕❯] )', line):
            lines.append(line)
        elif re.match(r'^(?:AssertionError|Error:|Expected:|Received:|Expected |Received )', line):
            lines.append(line)
    return '\n'.join(lines) or 'Acceptation échouée (aucun diagnostic structuré).'


def run_acceptance(task, root, worktree, execute, exchange=False):  # noqa: C901
    """Mount hidden fixtures only while trusted verification runs; always remove them."""
    import shutil

    source = safe_path(Path(root) / '.workspace/acceptance' / task['id'])
    worktree = Path(worktree)
    files, directories = [], []
    try:
        # Validate all inputs before copying any test.
        pairs = []
        for name in task['acceptance']:
            validate_acceptance_name(name)
            src = safe_path(source / name)
            dst = safe_path(worktree / name)
            if not src.is_file() or dst.exists():
                raise ValueError('Fichier acceptance absent ou destination occupée : ' + name)
            pairs.append((src, dst))
        for src, dst in pairs:
            missing = []
            parent = dst.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for parent in reversed(missing):
                parent.mkdir()
                directories.append(parent)
            with dst.open('xb') as stream:
                files.append(dst)
                stream.write(src.read_bytes())
        args = ['/bin/sh', '-c', task['acceptance_verify']]
        if exchange:
            args = ['bash', str(Path(root) / 'scripts/sandbox.sh'), 'exec', task['id'], '--'] + args
        code, out, err = execute(args, cwd=worktree)
        return code, acceptance_feedback(out + '\n' + err)
    finally:
        for path in reversed(files):
            # Refuse following any link introduced by the command during cleanup.
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        for path in reversed(directories):
            try:
                path.rmdir()
            except OSError:
                pass


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError('usage: task.py <fichier-de-tâche>')
        print(json.dumps(read_task(sys.argv[1]), ensure_ascii=False))
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(64)


def safe_path(path):
    """Reject symbolic components, including existing parent directories."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Chemin symbolique interdit : ' + str(part))
    return path
