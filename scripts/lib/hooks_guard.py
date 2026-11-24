"""Crochets Claude Code du circuit (Python 3.14, stdlib seule) : refus des écritures protégées, rappel du rapport.

Les modes `pre-tool` et `stop` répondent 0 à toute entrée illisible. La session qui orchestre garde l'exception sur
les écritures protégées, mais ses commandes Bash passent par la liste blanche d'`orchestrator_guard`.
Le mode `executor` (exécutant enfermé dans son worktree) refuse au contraire tout ce qu'il ne comprend pas ;
`orchestrator-check <racine>` rend la décision de la liste blanche sans condition de verrou ;
`orchestrator-session <kit>` l'applique depuis n'importe quel dossier à la session qui tient le verrou du kit.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import events  # noqa: E402
import orchestrator  # noqa: E402

sys.dont_write_bytecode = True

PROTECTED_DIRS = ('scripts', '.workspace')
PROTECTED_FILES = ('project.profile',)
EDIT_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit')
WRITERS = ('rm', 'mv', 'touch', 'chmod', 'chown', 'truncate', 'mkdir', 'rmdir', 'unlink', 'tee')
COPIERS = ('cp', 'rsync', 'install', 'ln')
IN_PLACE = ('sed', 'perl')
WRITE_REDIRECTS = ('>', '>>', '>|', '>&', '&>', '&>>')
READ_REDIRECTS = ('<', '<<', '<<<', '<&', '<>')
REDIRECTS = WRITE_REDIRECTS + READ_REDIRECTS
SEPARATORS = (';', '&&', '||', '|', '|&', '&')
ASSIGNMENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*=')
IN_PLACE_FLAG = re.compile(r'-[A-Za-z]*i|--in-place')
# Préfixes transparents et leurs options à valeur séparée.
PREFIXES = {
    'command': (),
    'builtin': (),
    'nohup': (),
    'time': ('-f', '-o'),
    'exec': ('-a',),
    'doas': ('-u', '-C'),
    'env': ('-u', '-C', '--unset', '--chdir'),
    'sudo': ('-u', '-g', '-h', '-p', '-r', '-t', '-C', '-D', '-R', '-T', '-U', '--user', '--group'),
    'nice': ('-n', '--adjustment'),
    'xargs': ('-n', '-I', '-L', '-P', '-s', '-d', '-E', '-a', '--max-args', '--replace'),
    'timeout': ('-s', '-k', '--signal', '--kill-after'),
    'stdbuf': ('-i', '-o', '-e'),
}
SHELLS = ('sh', 'bash', 'zsh', 'dash')
SHELL_VALUE_OPTIONS = ('-o', '+o', '-O', '+O')
SHELL_COMMAND_FLAG = re.compile(r'-[A-Za-z]*c[A-Za-z]*')
LOOKUP_FLAG = re.compile(r'-[A-Za-z]*[vV][A-Za-z]*')
MAX_SHELL_DEPTH = 5
EXIT_USAGE = 64
TASK_ENV = 'WORKSPACE_TASK_ID'
REFUSAL = 'Refusé : %s est un chemin protégé du circuit (scripts/, project.profile, .workspace/) ; voir AGENTS.md.'
EXECUTOR_REFUSAL = (
    'Refusé (exécutant) : %s — %s. Ton atelier est le worktree et son conteneur ; '
    "pose ta question à l'orchestrateur : bash scripts/ask.sh \"<question>\"."
)
ORCHESTRATOR_REFUSAL = (
    "Refusé (orchestrateur) : %s — liste blanche de l'orchestrateur (docs/ORCHESTRATEUR.md) ; "
    "la commande n'est pas lancée, l'usine continue."
)
ORCHESTRATOR_TASK_ID = 'orchestrator'
TARGET_LIMIT = 200
STOP_MESSAGE = (
    "session %s : le verrou d'orchestration est encore tenu ; écrivez le rapport, puis "
    "libérez-le avec `bash scripts/orchestrator.sh release`."
)


def _project_root(payload):
    """Racine du projet : CLAUDE_PROJECT_DIR, sinon `cwd` du JSON, sinon le dossier courant."""
    cwd = payload.get('cwd')
    raw = os.environ.get('CLAUDE_PROJECT_DIR') or (cwd if isinstance(cwd, str) and cwd else os.getcwd())
    return Path(os.path.realpath(raw))


def _relative(target, root):
    """Chemin de `target` relatif à `root` après normalisation ; `None` hors de la racine."""
    joined = os.path.normpath(os.path.join(str(root), target))
    full = os.path.join(os.path.realpath(os.path.dirname(joined)), os.path.basename(joined))
    rel = os.path.relpath(full, str(root))
    parts = Path(rel).parts
    return rel if parts and parts[0] != os.pardir else None


def _protected(target, root):
    """Chemin relatif si `target` est un chemin protégé, sinon `None`."""
    rel = _relative(target, root)
    if rel is None:
        return None
    return rel if rel in PROTECTED_FILES or Path(rel).parts[0] in PROTECTED_DIRS else None


def tokenize(command):
    """Mots et opérateurs de la commande ; `#` n'ouvre pas de commentaire (le shell n'en voit qu'en début de mot).
    `ValueError` si un guillemet reste ouvert."""
    lexer = shlex.shlex(command.replace('\n', ' ; '), posix=True, punctuation_chars=True)
    lexer.commenters = ''
    lexer.whitespace_split = True  # sinon `:`, `@`, `%`, `+`… deviennent des mots à part (`A=x:y` serait coupé)
    return list(lexer)


def _simple_commands(tokens):
    """Commandes simples : (mots, cibles d'écriture), séparées par les opérateurs de liste et de tube."""
    commands, words, targets = [], [], []
    skip = False
    for index, token in enumerate(tokens):
        if skip:
            skip = False
        elif token in SEPARATORS:
            commands.append((words, targets))
            words, targets = [], []
        elif token in REDIRECTS:
            if token in WRITE_REDIRECTS:
                targets.extend(tokens[index + 1 : index + 2])
            skip = True
        elif token.isdigit() and index + 1 < len(tokens) and tokens[index + 1] in REDIRECTS:
            continue
        else:
            words.append(token)
    commands.append((words, targets))
    return commands


def _strip_prefixes(words):
    """Commande réelle : sans affectations ni préfixes transparents (`env`, `sudo`, `timeout`…) et leurs options."""
    while words:
        if ASSIGNMENT.match(words[0]):
            words = words[1:]
            continue
        name = os.path.basename(words[0])
        if name not in PREFIXES:
            return words
        rest = words[1:]
        while rest and rest[0].startswith('-'):
            option, rest = rest[0], rest[1:]
            if name == 'command' and LOOKUP_FLAG.fullmatch(option):
                return []
            if option == '--':
                break
            if option in PREFIXES[name] and rest:
                rest = rest[1:]
        words = rest[1:] if name == 'timeout' and rest else rest
    return words


def _shell_script(words):
    """Argument de `-c` si la commande est un interpréteur de commandes, sinon `None`."""
    if not words or os.path.basename(words[0]) not in SHELLS:
        return None
    index = 1
    while index < len(words) and words[index].startswith(('-', '+')):
        option = words[index]
        if SHELL_COMMAND_FLAG.fullmatch(option):
            return words[index + 1] if index + 1 < len(words) else None
        index += 2 if option in SHELL_VALUE_OPTIONS else 1
    return None


def _command_candidates(words):
    """Arguments qui désignent une cible écrite selon le premier mot de la commande simple (déjà dépouillée)."""
    if not words:
        return []
    name = os.path.basename(words[0])
    args = [item for item in words[1:] if not item.startswith('-')]
    options = [item for item in words[1:] if item.startswith('-')]
    if name in WRITERS:
        return args
    if name in COPIERS:
        return args[-1:]
    if name in IN_PLACE and any(IN_PLACE_FLAG.match(item) for item in options):
        return args
    if name == 'dd':
        return [item[3:] for item in words[1:] if item.startswith('of=')]
    return []


def _bash_violation(command, root, depth=0):
    """Premier chemin protégé que la commande Bash écrit, sinon `None`."""
    try:
        tokens = tokenize(command)
    except ValueError:
        return None
    for words, targets in _simple_commands(tokens):
        words = _strip_prefixes(words)
        for candidate in targets + _command_candidates(words):
            found = _protected(candidate, root)
            if found is not None:
                return found
        script = _shell_script(words)
        if script is not None and depth < MAX_SHELL_DEPTH:
            found = _bash_violation(script, root, depth + 1)
            if found is not None:
                return found
    return None


def _violation(payload, root):
    """Chemin protégé visé par l'appel d'outil, sinon `None`."""
    tool = payload.get('tool_name')
    tool_input = payload.get('tool_input')
    if not isinstance(tool_input, dict):
        return None
    if tool in EDIT_TOOLS:
        target = tool_input.get('file_path') or tool_input.get('notebook_path')
        return _protected(target, root) if isinstance(target, str) else None
    if tool == 'Bash':
        command = tool_input.get('command')
        return _bash_violation(command, root) if isinstance(command, str) else None
    return None


def _own_lock(root):
    """Verrou tenu par un processus vivant qui est la session du crochet (règle de `claim`) ; `None` sinon."""
    try:
        held = orchestrator._read_lock(orchestrator._lock_path(root))
    except ValueError:
        return None
    pid = held.get('pid') if held else None
    alive = isinstance(pid, int) and not isinstance(pid, bool) and pid > 1 and orchestrator._pid_alive(pid)
    return held if alive and pid == orchestrator._session_pid(os.getppid()) else None


def _read_payload():
    """Objet JSON de l'entrée standard, ou `None` s'il est illisible."""
    try:
        data = json.loads(sys.stdin.read())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _lock_session(root):
    """Session inscrite dans le verrou, vivante ou non ; `orchestrator` si le verrou est absent ou illisible."""
    try:
        held = orchestrator._read_lock(orchestrator._lock_path(root))
    except ValueError:
        held = None
    session = held.get('session') if held else None
    return session if isinstance(session, str) and session else ORCHESTRATOR_TASK_ID


def _orchestrator_reason(payload, root):
    """Raison du refus d'une commande Bash par la liste blanche de l'orchestrateur, `None` si elle est permise."""
    tool_input = payload.get('tool_input') if isinstance(payload, dict) else None
    command = tool_input.get('command') if isinstance(tool_input, dict) else None
    if payload.get('tool_name') != 'Bash' or not isinstance(command, str):
        return None
    try:
        import orchestrator_guard

        return orchestrator_guard.decide(command, root)
    except Exception as error:
        return 'contrôle impossible (%s)' % type(error).__name__


def _refuse_orchestrator(root, payload, reason):
    _journal_refusal(root, _lock_session(root), payload, reason, role='orchestrator')
    print(ORCHESTRATOR_REFUSAL % reason.replace('\n', ' '), file=sys.stderr)
    return 2


def pre_tool(payload, root):
    found = _violation(payload, root)
    if payload.get('tool_name') != 'Bash' and found is None:
        return 0
    if TASK_ENV not in os.environ and _own_lock(root) is not None:
        reason = _orchestrator_reason(payload, root)
        return 0 if reason is None else _refuse_orchestrator(root, payload, reason)
    if found is None:
        return 0
    print(REFUSAL % found, file=sys.stderr)
    return 2


def stop(payload, root):
    if payload.get('stop_hook_active'):
        return 0
    held = _own_lock(root)
    if held is None:
        return 0
    print(STOP_MESSAGE % (held.get('session') or 'inconnue'), file=sys.stderr)
    return 2


def _refused_target(tool_input):
    """Chemin ou commande visés par l'appel refusé, bornés pour le journal."""
    if isinstance(tool_input, dict):
        for key in ('command', 'file_path', 'path', 'notebook_path', 'pattern'):
            if isinstance(tool_input.get(key), str):
                return tool_input[key][:TARGET_LIMIT]
    return ''


def _journal_refusal(root, task_id, payload, reason, role='executor'):
    """Ajoute `tool_refused` au journal ; un journal impossible à écrire ne change pas la décision."""
    tool = payload.get('tool_name') if isinstance(payload, dict) else None
    detail = {
        'role': role,
        'tool': tool if isinstance(tool, str) else '',
        'reason': reason,
        'target': _refused_target(payload.get('tool_input') if isinstance(payload, dict) else None),
    }
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            events.record(root, task_id, 'tool_refused', detail)
    except Exception:
        pass


def executor(args):
    """`executor <racine> <worktree> <task_id>` : 0 si l'appel d'outil est permis, 2 sinon (et si le contrôle échoue)."""
    if len(args) != 3:
        print('usage : hooks_guard.py executor <racine> <worktree> <task_id>', file=sys.stderr)
        return 2
    root, worktree, task_id = args
    payload = _read_payload()
    try:
        import executor_guard

        reason = executor_guard.decide(payload, worktree)
    except Exception as error:
        reason = 'contrôle impossible (%s)' % type(error).__name__
    if reason is None:
        return 0
    tool = payload.get('tool_name') if isinstance(payload, dict) else None
    _journal_refusal(root, task_id, payload, reason)
    print(EXECUTOR_REFUSAL % (tool if isinstance(tool, str) and tool else 'outil inconnu', reason.replace('\n', ' ')), file=sys.stderr)
    return 2


def orchestrator_check(args):
    """`orchestrator-check <racine>` : 0 si la commande Bash est permise à l'orchestrateur, 2 sinon (verrou ignoré)."""
    if len(args) != 1:
        print('usage : hooks_guard.py orchestrator-check <racine>', file=sys.stderr)
        return 2
    root = Path(os.path.realpath(args[0]))
    payload = _read_payload()
    if payload is None:
        payload, reason = {'tool_name': 'Bash', 'tool_input': {}}, 'entrée du crochet illisible'
    else:
        reason = _orchestrator_reason(payload, root)
    return 0 if reason is None else _refuse_orchestrator(root, payload, reason)


def orchestrator_session(args):
    """`orchestrator-session <kit>` : crochet des réglages personnels, actif depuis tout dossier où la session tient le
    verrou du kit. 0 (permis ou hors sujet) ou 2 ; muet hors du kit, sans charger la liste blanche tant qu'il n'y a
    pas de verrou à examiner."""
    if len(args) != 1:
        print('usage : hooks_guard.py orchestrator-session <racine-du-kit>', file=sys.stderr)
        return EXIT_USAGE
    payload = _read_payload()
    if payload is None or payload.get('tool_name') != 'Bash':
        return 0
    tool_input = payload.get('tool_input')
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get('command'), str):
        return 0
    kit = Path(os.path.realpath(args[0]))
    if TASK_ENV in os.environ or _project_root(payload) == kit:
        return 0  # exécutant, ou kit lui-même : le crochet `pre-tool` de ses réglages s'en charge
    if not (kit / orchestrator.LOCK_PATH).is_file():
        return 0
    try:
        if _own_lock(kit) is None:
            return 0
    except Exception:
        return 0
    reason = _orchestrator_reason(payload, kit)
    return 0 if reason is None else _refuse_orchestrator(kit, payload, reason)


RUNNERS = {'pre-tool': pre_tool, 'stop': stop}
KIT_MODES = {'executor': executor, 'orchestrator-check': orchestrator_check, 'orchestrator-session': orchestrator_session}


def main(argv):
    if len(argv) >= 2 and argv[1] in KIT_MODES:
        return KIT_MODES[argv[1]](argv[2:])
    if len(argv) != 2 or argv[1] not in RUNNERS:
        print(
            'usage : hooks_guard.py pre-tool|stop|executor <racine> <worktree> <task_id>|orchestrator-check <racine>'
            '|orchestrator-session <racine-du-kit>',
            file=sys.stderr,
        )
        return EXIT_USAGE
    payload = _read_payload()
    if payload is None:
        return 0
    return RUNNERS[argv[1]](payload, _project_root(payload))


if __name__ == '__main__':
    sys.exit(main(sys.argv))
