"""Check a task sheet (fiche) or the day plan before delegation.

Public API: classify(root, task), check(root, task_path, with_model=False), check_plan(root, plan_path).
CLI: plan_check.py <root> <fiche> [--json] [--with-model] | plan_check.py <root> --plan <plan.md> [--json]
Exit codes: 0 good, 1 to redo, 64 invalid usage. Programs judge first; a model is called only on request.
"""

import ast
import json
import os
import re
import subprocess
import sys
import sysconfig
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import consumption  # noqa: E402
import exact_tests  # noqa: E402
import project_modules  # noqa: E402
import task as task_lib  # noqa: E402

MIN_HIDDEN_TESTS = 12
SMALL_MAX_FILES = 4
MANY_RULES = 8
LARGE_NEW_MODULES = 3
MAX_ATTEMPTS = 2
CLASSES = ('small', 'medium', 'large')
FICHE_MODEL = 'sonnet'
PLAN_MODEL = 'opus'
PLAN_TASK_ID = 'plan'
CLASS_EXECUTOR = {'small': 'claude-haiku', 'medium': 'claude-sonnet', 'large': 'claude-opus'}
ESTIMATE_SECONDS = {'small': 120, 'medium': 900, 'large': 2400}
MODEL_TIMEOUT = 300
SECRET_NAME = re.compile(r'[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Za-z0-9_]*=')
PROTECTED = ('scripts', '.workspace', 'project.profile')
METHOD_TEST = re.compile(r' {4}(?:async +)?def test_')
MODULE_TEST = re.compile(r'(?:async +)?def test_')
USAGE = 'usage: plan_check.py <racine> <fiche> [--json] [--with-model]\n       plan_check.py <racine> --plan <plan.md> [--json]'


def _issue(code, message):
    return {'code': code, 'message': message}


def _sections(text):
    """Map each '## ' heading line to its body lines."""
    sections, current = [], None
    for line in text.splitlines():
        if line.startswith('## '):
            current = (line, [])
            sections.append(current)
        elif current is not None:
            current[1].append(line)
    return sections


def classify(root, task) -> tuple[str, list[str]]:
    """Return the computed class and one reason per retained criterion."""
    root = Path(root)
    allowed = task['allowed']
    new_sources = [p for p in allowed if not (root / p).exists() and not p.startswith('tests/') and not p.endswith('.md')]
    sections = _sections(task.get('body', ''))
    rules = sum(len(re.findall(r'^\d+\. ', '\n'.join(lines), re.M)) for title, lines in sections if title.startswith('## Règles'))
    vague = not any(t.startswith('## Interface') for t, _ in sections) or not any(t.startswith('## Critères') for t, _ in sections)
    reasons = []
    if len(new_sources) >= 1:
        reasons.append('%d fichiers nouveaux' % len(new_sources))
    if len(allowed) > SMALL_MAX_FILES:
        reasons.append('%d entrées dans allowed' % len(allowed))
    if rules >= MANY_RULES:
        reasons.append('%d règles' % rules)
    if vague:
        reasons.append('brief flou')
    if len(new_sources) >= LARGE_NEW_MODULES or vague:
        return 'large', reasons
    if new_sources or len(allowed) > SMALL_MAX_FILES or rules >= MANY_RULES:
        return 'medium', reasons
    return 'small', reasons


def _is_kit(root):
    try:
        text = (root / 'AGENTS.md').read_text(encoding='utf-8')
    except OSError:
        return False
    return any(line.strip() == 'docs-check: kit' for line in text.splitlines())


def _field_defects(root, task, text):
    defects = []
    if 'class' in task and task['class'] not in CLASSES:
        defects.append(_issue('class_invalid', 'class doit valoir small, medium ou large : ' + str(task['class'])))
    if task['attempts'] > MAX_ATTEMPTS:
        defects.append(_issue('attempts', 'attempts %d dépasse le maximum de %d' % (task['attempts'], MAX_ATTEMPTS)))
    if _is_kit(root) and task['verify_profile'] != 'off':
        defects.append(_issue('verify_profile', 'dans le kit, verify_profile doit valoir off'))
    for entry in task['allowed']:
        if any(c in entry for c in '*?[') or entry == '.':
            defects.append(_issue('wide_pattern', 'allowed contient un motif large : ' + entry))
        if entry in PROTECTED or entry.startswith(('scripts/', '.workspace/')):
            defects.append(_issue('protected_path', 'allowed contient un chemin protégé : ' + entry))
    match = SECRET_NAME.search(text)
    if match:
        defects.append(_issue('secret_name', 'nom de variable secret suivi de = : ' + match.group(0)))
    return defects


def _threshold(task, defects):
    if 'acceptance_min' not in task:
        return MIN_HIDDEN_TESTS
    raw = task['acceptance_min']
    reason = str(task.get('acceptance_min_reason', '')).strip()
    if not re.fullmatch(r'[0-9]+', raw):
        defects.append(_issue('acceptance_min', 'acceptance_min doit être un entier : ' + raw))
    elif int(raw) > MIN_HIDDEN_TESTS:
        defects.append(_issue('acceptance_min', 'acceptance_min dépasse %d' % MIN_HIDDEN_TESTS))
    elif not reason:
        defects.append(_issue('acceptance_min', 'acceptance_min demande acceptance_min_reason'))
    else:
        return int(raw)
    return MIN_HIDDEN_TESTS


def _hidden_files(root, task, defects):
    """Return (name, text) of each hidden test file and the count of tests.

    A test is a method of a class, `async` or not; pytest also runs the functions at module level."""
    module_level = 'pytest' in task.get('acceptance_verify', '')
    names = task['acceptance']
    if not names:
        defects.append(_issue('hidden_missing', 'aucun test caché (acceptance vide)'))
        return [], 0
    files, count = [], 0
    for name in names:
        path = root / '.workspace' / 'acceptance' / task['id'] / name
        if not path.is_file():
            defects.append(_issue('hidden_missing', 'test caché absent : ' + name))
            continue
        content = path.read_text(encoding='utf-8', errors='replace')
        count += sum(1 for line in content.splitlines() if METHOD_TEST.match(line) or (module_level and MODULE_TEST.match(line)))
        files.append((name, content))
    return files, count


def _stdlib_names():
    names = set(sys.builtin_module_names) | set(sys.stdlib_module_names)
    try:
        folder = sysconfig.get_paths()['stdlib']
        entries = os.listdir(folder) + os.listdir(os.path.join(folder, 'lib-dynload'))
    except OSError:
        entries = []
    for entry in entries:
        names.add(entry.split('.')[0])
    return names


def _toml(path):
    try:
        return tomllib.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _pyproject_requirements(data):
    project = data.get('project')
    project = project if isinstance(project, dict) else {}
    groups = [project.get('dependencies')]
    for table in (project.get('optional-dependencies'), data.get('dependency-groups')):
        groups.extend(table.values() if isinstance(table, dict) else [])
    return [item for group in groups if isinstance(group, list) for item in group if isinstance(item, str)]


def _requirement_lines(root):
    lines = []
    for path in sorted(root.glob('requirements*.txt')):
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        lines.extend(line for line in text.splitlines() if not line.lstrip().startswith(('#', '-')))
    return lines


def _declared_packages(root):
    """Names of the packages the project declares (`uv.lock`, `pyproject.toml`, `requirements*.txt`, and `project_modules`).

    Normalized like an import name (lower case, `_`); a package imported under another name (PyYAML as `yaml`) is not recognized."""
    packages = _toml(root / 'uv.lock').get('package')
    names = [p.get('name') for p in packages if isinstance(p, dict)] if isinstance(packages, list) else []
    for requirement in (
        _pyproject_requirements(_toml(root / 'pyproject.toml')) + _requirement_lines(root) + project_modules.requirement_lines(root)
    ):
        match = re.match(r'\s*([A-Za-z0-9][A-Za-z0-9._-]*)', requirement)
        names.append(match.group(1) if match else None)
    return {re.sub(r'[-_.]+', '_', name).lower() for name in names if isinstance(name, str) and name}


def _is_import_module(node):
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr == 'import_module') or (isinstance(func, ast.Name) and func.id == 'import_module')


def _imported_modules(tree):
    """Absolute module names imported by a hidden test, literals only."""
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
            if node.module == 'tests':
                modules.extend('tests.' + alias.name for alias in node.names)
        elif (
            isinstance(node, ast.Call)
            and _is_import_module(node)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            modules.append(node.args[0].value)
    return modules


def _is_name(node, name):
    return isinstance(node, ast.Name) and node.id == name


def _dirname_arg(node):
    """True for __file__ wrapped or not by abspath/realpath/Path(...).resolve()."""
    if _is_name(node, '__file__'):
        return True
    if not isinstance(node, ast.Call) or node.keywords:
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in ('resolve', 'absolute') and not node.args:
        return _dirname_arg(func.value)
    wrapper = (isinstance(func, ast.Attribute) and func.attr in ('abspath', 'realpath')) or (
        isinstance(func, ast.Name) and func.id in ('abspath', 'realpath', 'Path')
    )
    return wrapper and len(node.args) == 1 and _dirname_arg(node.args[0])


def _literal_parts(node):
    """Path parts of a string literal, or None when it leaves the root."""
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or node.value.startswith('/'):
        return None
    parts = [p for p in node.value.split('/') if p not in ('', '.')]
    return None if '..' in parts else tuple(parts)


def _anchor_state(node, anchors, file_parts):  # noqa: C901
    """Evaluate node to (repo parts, is_bare_file) or None when it is not an anchor expression."""
    if isinstance(node, ast.Name):
        return (anchors[node.id], False) if node.id in anchors else None
    if isinstance(node, ast.Call):
        func = node.func
        if _is_name(func, 'Path') and len(node.args) == 1 and not node.keywords:
            if _is_name(node.args[0], '__file__'):
                return file_parts, True
            return _anchor_state(node.args[0], anchors, file_parts)
        if isinstance(func, ast.Attribute) and func.attr in ('resolve', 'absolute') and not node.args:
            state = _anchor_state(func.value, anchors, file_parts)
            return state if state is not None and state[1] else None
        is_dirname = (isinstance(func, ast.Attribute) and func.attr == 'dirname') or _is_name(func, 'dirname')
        if is_dirname and len(node.args) == 1 and _dirname_arg(node.args[0]):
            return file_parts[:-1], False
        return None
    pops = 0
    if isinstance(node, ast.Attribute) and node.attr == 'parent':
        base, pops = node.value, 1
    elif (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == 'parents'
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, int)
        and not isinstance(node.slice.value, bool)
        and node.slice.value >= 0
    ):
        base, pops = node.value.value, node.slice.value + 1
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        state = _anchor_state(node.left, anchors, file_parts)
        tail = _literal_parts(node.right)
        if state is None or state[1] or tail is None:
            return None
        return state[0] + tail, False
    else:
        return None
    state = _anchor_state(base, anchors, file_parts)
    if state is None or pops > len(state[0]):
        return None
    return state[0][: len(state[0]) - pops], False


def _anchored_paths(tree, name):
    """Repo-relative paths built as <anchor> / 'literal' in a hidden test saved at repo path name."""
    file_parts = tuple(name.split('/'))
    anchors = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, value = node.target.id, node.value
        else:
            continue
        state = _anchor_state(value, anchors, file_parts)
        if state is not None and not state[1]:
            anchors[target] = state[0]
        else:
            anchors.pop(target, None)
    paths, inner = [], set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)) or id(node) in inner:
            continue
        state = _anchor_state(node, anchors, file_parts)
        if state is None or state[1]:
            continue
        child = node.left
        while isinstance(child, ast.BinOp):
            inner.add(id(child))
            child = child.left
        if state[0]:
            paths.append('/'.join(state[0]))
    return paths


def _scope_defects(root, task, files):  # noqa: C901
    defects, seen = [], set()
    allowed = set(task['allowed'])
    own = {name for name, _ in files}
    allowed_names = {p.rsplit('/', 1)[-1] for p in allowed}
    allowed_dirs = {os.path.dirname(p) for p in allowed if p.endswith('.py')}
    stdlib = _stdlib_names()
    declared = _declared_packages(root)
    provided = project_modules.tree_modules(root) | project_modules.runner_modules(task)

    def add(code, message):
        if (code, message) not in seen:
            seen.add((code, message))
            defects.append(_issue(code, message))

    def known_module(name):
        first = name.split('.')[0]
        file_name = first + '.py'
        if first in stdlib or first.lower() in declared or first in provided or file_name in allowed_names:
            return True
        if (root / 'template/scripts/lib' / file_name).is_file() or (root / file_name).is_file():
            return True
        if (root / first).is_dir() or (root / 'tests' / file_name).is_file():
            return True
        if 'tests/' + file_name in own or any(os.path.basename(n) == file_name for n in own):
            return True
        return any((root / folder / file_name).is_file() for folder in allowed_dirs)

    for name, content in files:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for value in _anchored_paths(tree, name):
            if not (
                value in allowed
                or value in own
                or (root / value).exists()
                or any(a.startswith(value + '/') or (a.endswith('/') and value.startswith(a)) for a in allowed)
            ):
                add('hidden_outside_scope', 'chemin absent de la racine et de allowed : ' + value)
        optional = project_modules.conditional_modules(tree)
        for module in _imported_modules(tree):
            parts = module.split('.')
            if parts[0] == 'tests' and len(parts) > 1:
                candidate = 'tests/' + parts[1] + '.py'
            elif parts[0].startswith('test_') and not (root / 'template/scripts/lib' / (parts[0] + '.py')).is_file():
                candidate = 'tests/' + parts[0] + '.py'
            else:
                candidate = None
            if candidate in allowed:
                add('hidden_borrows', 'le test caché emprunte %s, modifiable par la tâche : il porte ses propres aides' % candidate)
            elif candidate is None and module not in optional and not known_module(module):
                add('hidden_outside_scope', 'module hors du périmètre : ' + module)
    return defects


def _acceptance_verify_defects(task):
    defects = []
    verify = task.get('acceptance_verify', '')
    for name in task['acceptance']:
        if not name.endswith('.py'):
            continue
        module = name[:-3].replace('/', '.')
        if not re.search(r'(?<![\w.])' + re.escape(module) + r'(?![\w.])', verify):
            defects.append(_issue('acceptance_verify', 'acceptance_verify ne lance pas le module ' + module))
    return defects


def _model_call(root, model, prompt, task_id):
    """Return (parsed object, None) or (None, reason); the call leaves a consumption row."""
    command = [
        'claude',
        '-p',
        '--model',
        model,
        '--output-format',
        'json',
        '--tools',
        '',
        '--setting-sources',
        'project,local',
        '--strict-mcp-config',
    ]
    started = time.monotonic()
    try:
        done = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=MODEL_TIMEOUT, cwd=str(root))
    except (OSError, subprocess.SubprocessError) as error:
        return None, 'appel de claude impossible : ' + type(error).__name__
    elapsed = time.monotonic() - started
    try:
        envelope = json.loads(done.stdout)
    except (ValueError, TypeError):
        envelope = None
    if isinstance(envelope, dict):
        consumption.record_claude(root, 'conseil', model, task_id, envelope, duration=elapsed, exit=done.returncode)
    if done.returncode != 0:
        return None, 'claude a rendu le code %d' % done.returncode
    try:
        result = json.loads(done.stdout)['result']
        decoder = json.JSONDecoder()
        for match in re.finditer(r'\{', result):
            try:
                value, _ = decoder.raw_decode(result[match.start() :])
            except ValueError:
                continue
            if isinstance(value, dict):
                return value, None
    except (ValueError, KeyError, TypeError):
        pass
    return None, 'réponse de claude illisible'


def _orchestrator_sections(root, numbers):
    try:
        text = (Path(root) / 'docs/ORCHESTRATEUR.md').read_text(encoding='utf-8')
    except OSError:
        return ''
    picked = []
    for title, lines in _sections(text):
        if any(title.startswith('## %d.' % n) for n in numbers):
            picked.append('\n'.join([title] + lines))
    return '\n\n'.join(picked)


def _model_issues(root, model, prompt, task_id, expected_class=None):
    answer, reason = _model_call(root, model, prompt, task_id)
    if answer is None:
        return [], [_issue('model_unavailable', reason)]
    defects = []
    if answer.get('realisable') is False:
        defects.append(_issue('model_unfeasible', 'le modèle juge la fiche irréalisable'))
    contradictions = answer.get('contradictions')
    for item in contradictions if isinstance(contradictions, list) else []:
        defects.append(_issue('model_contradiction', str(item)))
    if expected_class is not None and isinstance(answer.get('classe'), str) and answer['classe'] != expected_class:
        defects.append(
            _issue(
                'model_class',
                'classe retenue %s, classe du modèle %s : trancher par class: dans l\'en-tête' % (expected_class, answer['classe']),
            )
        )
    return defects, []


def check(root, task_path, with_model=False):
    root = Path(root)
    result = {
        'verdict': 'bon',
        'defects': [],
        'warnings': [],
        'class': 'large',
        'class_source': 'calculée',
        'executor': '',
        'hidden_tests': 0,
        'estimate_seconds': 0,
    }
    try:
        task = task_lib.read_task(task_path, root)
    except ValueError as error:
        result['defects'].append(_issue('header', str(error)))
        result['verdict'] = 'à reprendre'
        return result
    text = Path(task_path).read_text(encoding='utf-8')
    defects = _field_defects(root, task, text)
    computed, _ = classify(root, task)
    if task.get('class') in CLASSES:
        retained, source = task['class'], 'en-tête'
    else:
        retained, source = computed, 'calculée'
    executor, warnings = task['agent'], []
    if retained == 'large' and executor != 'claude-opus':
        defects.append(_issue('large_not_opus', 'fiche de classe large : la découper ou la confier à claude-opus'))
    elif retained != 'large' and executor != CLASS_EXECUTOR[retained]:
        warnings.append(_issue('executor_class', 'la classe %s se confie à %s, pas à %s' % (retained, CLASS_EXECUTOR[retained], executor)))
    threshold = _threshold(task, defects)
    files, hidden = _hidden_files(root, task, defects)
    if task['acceptance'] and hidden < threshold:
        defects.append(_issue('hidden_count', '%d tests cachés, il en faut au moins %d' % (hidden, threshold)))
    defects.extend(_acceptance_verify_defects(task))
    defects.extend(_scope_defects(root, task, files))
    for item in exact_tests.find(root, task, text):
        defects.append(
            _issue(
                'exact_test_outside',
                '%s compare exactement (lignes %s) %s hors de allowed : '
                'élargir allowed ou reformuler la fiche'
                % (item['path'], ', '.join(str(n) for n in item['lines']), ', '.join(item['structures'])),
            )
        )
    for item in exact_tests.find_nearby(root, task, text):
        warnings.append(
            _issue(
                'exact_test_nearby',
                '%s compare exactement (lignes %s) une liste de noms ou de valeurs '
                'liée à %s hors de allowed : à vérifier'
                % (item['path'], ', '.join(str(n) for n in item['lines']), ', '.join(item['structures'])),
            )
        )
    if with_model:
        prompt = (
            'Tu juges une fiche de tâche avant délégation.\n\n# Fiche\n'
            + text
            + '\n\n# Classe retenue\n'
            + retained
            + '\n\n# Consigne de l\'orchestrateur (sections 3 et 4)\n'
            + _orchestrator_sections(root, (3, 4))
            + '\n\nRéponds par un seul objet JSON : '
            '{"realisable": bool, "contradictions": [str], "classe": "small|medium|large"}. '
            '"realisable" : la fiche peut-elle être satisfaite dans son périmètre ; "contradictions" : '
            'ce qui contredit la consigne ; "classe" : ta classe de la fiche.'
        )
        more, unavailable = _model_issues(root, FICHE_MODEL, prompt, task['id'], retained)
        defects.extend(more)
        warnings.extend(unavailable)
    result.update(
        {
            'verdict': 'à reprendre' if defects else 'bon',
            'defects': defects,
            'warnings': warnings,
            'class': retained,
            'class_source': source,
            'executor': executor,
            'hidden_tests': hidden,
            'estimate_seconds': ESTIMATE_SECONDS[retained] * task['attempts'],
        }
    )
    return result


def check_plan(root, plan_path):
    plan = Path(plan_path).read_text(encoding='utf-8')
    prompt = (
        'Tu juges le plan du jour avant la première délégation.\n\n# Plan\n'
        + plan
        + '\n\n# Consigne de l\'orchestrateur (sections 3, 4 et 6)\n'
        + _orchestrator_sections(root, (3, 4, 6))
        + '\n\nRéponds par un seul objet JSON : {"realisable": bool, "contradictions": [str]}. '
        '"realisable" : le plan peut-il être exécuté tel quel ; "contradictions" : ce qui contredit la consigne.'
    )
    defects, warnings = _model_issues(Path(root), PLAN_MODEL, prompt, PLAN_TASK_ID)
    return {'verdict': 'à reprendre' if defects else 'bon', 'defects': defects, 'warnings': warnings}


def _lines(result, extra):
    lines = ['défaut [%s] : %s' % (d['code'], d['message']) for d in result['defects']]
    lines += ['avertissement [%s] : %s' % (w['code'], w['message']) for w in result['warnings']]
    lines += extra
    if result['defects']:
        lines.append('verdict : à reprendre (%d défauts)' % len(result['defects']))
    else:
        lines.append('verdict : bon')
    return lines


def main(argv):
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 64
    root, args = argv[0], argv[1:]
    as_json = '--json' in args
    args = [a for a in args if a != '--json']
    plan: str | None = None
    with_model = False
    positional: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == '--with-model':
            with_model = True
        elif arg == '--plan' and index + 1 < len(args):
            plan = args[index + 1]
            index += 1
        elif arg.startswith('--'):
            print('option inconnue : ' + arg + '\n' + USAGE, file=sys.stderr)
            return 64
        else:
            positional.append(arg)
        index += 1
    target = plan if plan is not None else (positional[0] if len(positional) == 1 else None)
    if target is None or (plan is not None and (positional or with_model)) or not Path(target).is_file():
        print(USAGE, file=sys.stderr)
        return 64
    if plan is not None:
        result = check_plan(root, plan)
        extra: list[str] = []
    else:
        result = check(root, target, with_model)
        extra = [
            'classe : %s (%s)' % (result['class'], result['class_source']),
            'exécutant : ' + result['executor'],
            'tests cachés : %d' % result['hidden_tests'],
            'durée estimée : %d min' % -(-result['estimate_seconds'] // 60),
        ]
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print('\n'.join(_lines(result, extra)))
    return 0 if result['verdict'] == 'bon' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
