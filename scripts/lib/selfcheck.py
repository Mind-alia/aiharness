"""Contrôle préalable du test caché : il doit échouer sur la base de la tâche (Python 3.14, stdlib seule).

Un test caché qui passe avant tout travail ne prouve rien. `delegate.py` l'exécute dans le conteneur
de la tâche, worktree encore à la base ; `task-workspace.sh prepare --selfcheck` fait de même à la préparation.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import breaker
import events
import workdir
from task import read_task, safe_path

MESSAGE = (
    'acceptance_vacuous : le test caché passe déjà sur la base de la tâche ; corriger le test caché avant de déléguer '
    '(WORKSPACE_NO_SELFCHECK=1 pour passer outre).'
)
VACUOUS_CODE = 3
SUMMARY_MAX_LINES = 12
SUMMARY_LINE_WIDTH = 200
SUMMARY_TITLE = 'selfcheck : résumé des échecs sur la base :'
NO_LOG = "selfcheck : aucun journal d'exécution du test caché"
LOG_MAX_BYTES = 1024 * 1024
FALLBACK_TAIL_LINES = 5
LOG_DIR = '.workspace/orchestration/tasks/{}/sandbox'
LOG_NAME = re.compile(r'^exec-(\d+)\.log$')
FAIL_PREFIXES = ('FAIL: ', 'ERROR: ')
EXCEPTION_LINE = re.compile(r'^[A-Za-z_][\w.]*(Error|Exception)\b(?=:|$)')
SYNTAX_LINE = re.compile(r'^(SyntaxError|IndentationError)\b')
FILE_LINE = re.compile(r'^File ".*", line \d+')
RAN_LINE = re.compile(r'^Ran \d+ tests? in ')


def _git(worktree, *args):
    result = subprocess.run(workdir.git_command(*args, cwd=worktree), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.returncode, result.stdout


def at_base(root, task_id, worktree):
    """Vrai si le worktree est propre et son HEAD égal au `base_commit` de l'état de la tâche."""
    try:
        state = json.loads(safe_path(Path(root) / '.workspace/orchestration/tasks' / task_id / 'state.json').read_text(encoding='utf-8'))
        base = state.get('base_commit')
    except (OSError, ValueError, AttributeError):
        return False
    if not isinstance(base, str) or not base:
        return False
    code, out = _git(worktree, 'status', '--porcelain', '--untracked-files=all')
    if code or out.strip():
        return False
    code, head = _git(worktree, 'rev-parse', 'HEAD')
    return code == 0 and head.strip() == base


def exec_logs(root, task_id):
    """Chemins des `exec-NNN.log` du bac à sable de la tâche, triés par numéro ; liens symboliques ignorés."""
    folder = Path(root) / LOG_DIR.format(task_id)
    found = []
    try:
        for path in folder.iterdir():
            match = LOG_NAME.match(path.name)
            if match and not path.is_symlink() and path.is_file():
                found.append((int(match.group(1)), path))
    except OSError:
        return []
    return [path for _, path in sorted(found)]


def failure_summary(text):
    """Lignes utiles d'une sortie `unittest` : échecs, exceptions, ligne de fichier d'une erreur de syntaxe, bilan."""
    kept = []

    def keep(line):
        line = line[:SUMMARY_LINE_WIDTH].strip()
        if line and line not in kept:
            kept.append(line)

    last_file = None
    for raw in text.splitlines():
        line = raw.strip()
        if FILE_LINE.match(line):
            last_file = line
        if line.startswith(FAIL_PREFIXES) or RAN_LINE.match(line) or line == 'OK' or line.startswith('FAILED ('):
            keep(line)
        elif EXCEPTION_LINE.match(line):
            if SYNTAX_LINE.match(line) and last_file:
                keep(last_file)
            keep(line)
    if len(kept) > SUMMARY_MAX_LINES:
        kept = kept[: SUMMARY_MAX_LINES - 1] + kept[-1:]
    return kept


def _read_log(path):
    with open(path, 'rb') as handle:
        return handle.read(LOG_MAX_BYTES).decode('utf-8', errors='replace')


def _print_summary(root, task_id, before):
    new = [path for path in exec_logs(root, task_id) if path not in before]
    try:
        text = _read_log(new[-1]) if new else None
    except OSError:
        text = None
    if text is None:
        print(NO_LOG)
        return
    lines = failure_summary(text)
    if not lines:
        lines = [line.strip()[:SUMMARY_LINE_WIDTH] for line in text.splitlines() if line.strip()][-FALLBACK_TAIL_LINES:]
    if not lines:
        print(NO_LOG)
        return
    print(SUMMARY_TITLE)
    for line in lines:
        print('  ' + line)


def run(root, task, worktree, execute):
    """Exécute l'acceptation en conteneur, enregistre l'événement `selfcheck` et retourne le code."""
    code, _ = breaker.run_acceptance(task, root, worktree, execute, True)
    events.record(root, task['id'], 'selfcheck', {'code': code})
    return code


def prepare_selfcheck(root, task_id):
    """Contrôle d'une tâche préparée (`prepare --selfcheck`) : 3 si le test caché passe déjà, sinon 0."""
    root = Path(root)
    state = json.loads(safe_path(root / '.workspace/orchestration/tasks' / task_id / 'state.json').read_text(encoding='utf-8'))
    task = read_task(safe_path(root / state['source_task']))
    if not breaker.has_acceptance(task, root):
        print('selfcheck : aucun test caché, rien à contrôler')
        return 0
    sandbox = ['bash', str(root / 'scripts/sandbox.sh')]

    def execute(args, cwd=None):
        result = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.returncode, result.stdout, result.stderr

    before = set(exec_logs(root, task_id))
    code, out, err = execute(sandbox + ['start', task_id])
    if code:
        raise ValueError('selfcheck : démarrage du conteneur impossible : ' + (out + err).strip())
    try:
        code = run(root, task, workdir.state_worktree(root, state), execute)
    finally:
        execute(sandbox + ['stop', task_id])
    if code == 0:
        print(MESSAGE, file=sys.stderr)
        return VACUOUS_CODE
    print('selfcheck : le test caché échoue sur la base (code %d), délégation possible' % code)
    _print_summary(root, task_id, before)
    return 0
