"""Décision du crochet `executor` : l'exécutant Claude ne voit que son worktree (Python 3.14, stdlib seule).

`decide(payload, worktree)` rend `None` si l'appel d'outil est permis, sinon la raison du refus.
Le processus de l'agent tourne sur l'hôte : seul ce crochet l'empêche d'y lire, d'y écrire ou d'y lancer quoi que ce soit.
"""

from __future__ import annotations

import os
import re
import shlex
import sys

import hooks_guard

sys.dont_write_bytecode = True

# Les sept outils offerts à l'exécutant (`--tools`) ; les trois suivants sont d'anciens noms, contrôlés comme chemins.
EXECUTOR_TOOLS = ('Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep', 'NotebookEdit')
ALLOWED_TOOLS = EXECUTOR_TOOLS + ('MultiEdit', 'LS', 'NotebookRead')
CUT_TOOLS = ('WebFetch', 'WebSearch', 'Agent', 'Task')
CUT_PREFIX = 'mcp__'
PATH_TOOLS = ('Read', 'Glob', 'Grep', 'LS', 'NotebookRead') + hooks_guard.EDIT_TOOLS
PATH_KEYS = ('file_path', 'path', 'notebook_path')
BASH_SCRIPTS = ('scripts/run-in-sandbox.sh', 'scripts/ask.sh')
# Filtres d'affichage admis après `run-in-sandbox.sh -- <mots>` : `2>&1` puis un seul `| tail|head -n N` ou `-N` (N : 1 à 5 chiffres).
DISPLAY_FILTER = re.compile(r'(.*?)[ ]+\|[ ]+(?:tail|head)[ ]+(?:-n[ ]+[0-9]{1,5}|-[0-9]{1,5})[ ]*', re.DOTALL)
MERGED_STDERR = re.compile(r'(.*?)[ ]+2>&1[ ]*', re.DOTALL)
GLOB_CHARS = '*?[{'
# Opérateurs de liste, de tube et de redirection du crochet `pre-tool`, plus parenthèses et fins de ligne.
SHELL_OPERATORS = frozenset(''.join(hooks_guard.SEPARATORS + hooks_guard.REDIRECTS) + '()\n\r')
# Évalués par le shell de l'hôte dès qu'ils ne sont ni échappés par une barre oblique inverse ni entre guillemets simples.
EVALUATED = '$`'
# `.claude/` : des réglages de projet écrits par l'agent pourraient ajouter un crochet exécuté sur l'hôte.
WRITE_PROTECTED_DIRS = ('scripts', '.workspace', '.git', '.claude')
WRITE_PROTECTED_FILES = ('project.profile',)
# Sorties longues de la session, relues par `Read` seulement : `<racine>/<id de session>/<dossier>/<fichier>`.
TMP_PARENT = '/tmp'
TMP_PREFIX = 'claude-'
TMP_OUTPUT_DIRS = ('tasks', 'tool-results')
CONFIG_OUTPUT_DIRS = ('tool-results',)
CONFIG_DIR_VARIABLE = 'CLAUDE_CONFIG_DIR'
DEFAULT_CONFIG_DIR = '~/.claude'
CONFIG_PROJECTS_DIR = 'projects'
SESSION_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
NON_SLUG_CHARS = re.compile(r'[^A-Za-z0-9]')
MIN_OUTPUT_PARTS = 3  # id de session, dossier, fichier


def _unsafe_shell_syntax(command):
    """Raison du refus si le shell de l'hôte évaluerait autre chose que des mots littéraux, sinon `None`."""
    quote, escaped = None, False
    for char in command:
        if escaped:
            escaped = False
        elif quote == "'":
            quote = None if char == "'" else quote
        elif char == '\\':
            escaped = True
        elif quote == '"':
            if char in EVALUATED:
                return "substitution évaluée sur l'hôte"
            quote = None if char == '"' else quote
        elif char in ("'", '"'):
            quote = char
        elif char in EVALUATED:
            return "substitution évaluée sur l'hôte"
        elif char in SHELL_OPERATORS:
            return 'séparateur, redirection ou saut de ligne'
    return 'guillemet non fermé' if quote else None


def _without_display_filters(command):
    """`command` sans son `2>&1` et son filtre `| tail|head` finaux, `None` s'il n'en a aucun."""
    stripped = False
    match = DISPLAY_FILTER.fullmatch(command)
    if match:
        command, stripped = match.group(1), True
    match = MERGED_STDERR.fullmatch(command)
    if match:
        command, stripped = match.group(1), True
    return command if stripped else None


def _filtered_sandbox_command(command):
    """Vrai si `command` est `bash scripts/run-in-sandbox.sh -- <mots>` suivi de filtres d'affichage admis."""
    base = _without_display_filters(command)
    if base is None or _unsafe_shell_syntax(base):
        return False
    try:
        words = shlex.split(base)
    except ValueError:
        return False
    return words[:3] == ['bash', 'scripts/run-in-sandbox.sh', '--']


def _bash_decision(tool_input):
    command = tool_input.get('command')
    if not isinstance(command, str) or not command.strip():
        return 'commande Bash illisible'
    if _filtered_sandbox_command(command):
        return None
    problem = _unsafe_shell_syntax(command)
    if problem:
        return 'commande Bash refusée (%s) : une seule commande `bash scripts/run-in-sandbox.sh -- ...` est permise' % problem
    try:
        words = shlex.split(command)
    except ValueError:
        return 'commande Bash illisible'
    if len(words) < 2 or words[0] != 'bash' or words[1] not in BASH_SCRIPTS:
        return 'commande Bash refusée : seules `bash scripts/run-in-sandbox.sh -- <commande>` et `bash scripts/ask.sh "<question>"` sont permises'
    return None


def _resolve(raw, worktree):
    """Chemin réel (liens suivis) de `raw`, relatif au worktree s'il ne l'est pas déjà."""
    return os.path.realpath(os.path.join(worktree, os.path.expanduser(raw) if raw.startswith('~') else raw))


def _inside(path, worktree):
    return path == worktree or path.startswith(worktree.rstrip(os.sep) + os.sep)


def project_slug(path):
    """`path` où chaque caractère qui n'est ni une lettre ASCII ni un chiffre devient `-`."""
    return NON_SLUG_CHARS.sub('-', path)


def session_output_roots(worktree):
    """Les deux dossiers parents des sorties de session : sous `/tmp` puis sous la configuration de Claude."""
    slug = project_slug(os.path.realpath(worktree))
    config = os.environ.get(CONFIG_DIR_VARIABLE, '')
    if not config or not os.path.isabs(config):
        config = os.path.expanduser(DEFAULT_CONFIG_DIR)
    tmp_root = os.path.join(os.path.realpath(TMP_PARENT), '%s%d' % (TMP_PREFIX, os.getuid()), slug)
    return [tmp_root, os.path.join(os.path.realpath(config), CONFIG_PROJECTS_DIR, slug)]


def is_session_output(path, worktree, session_id=None):
    """Vrai si le chemin réel `path` est strictement sous `<racine>/<id de session>/<dossier de sorties>/`.

    `<id de session>` doit être celui de l'appel (`session_id`), lui-même un identifiant de session valide."""
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        return False
    roots = session_output_roots(worktree)
    for root, allowed in zip(roots, (TMP_OUTPUT_DIRS, CONFIG_OUTPUT_DIRS), strict=False):
        if not path.startswith(root + os.sep):
            continue
        parts = path[len(root) + 1 :].split(os.sep)
        if len(parts) >= MIN_OUTPUT_PARTS and parts[0] == session_id and parts[1] in allowed and all(parts[2:]):
            return True
    return False


def _glob_prefix(pattern):
    """Partie de `pattern` avant le premier joker, `None` si elle n'a pas à être contrôlée, `''` si elle est irrecevable."""
    cut = min([pattern.index(char) for char in GLOB_CHARS if char in pattern] or [len(pattern)])
    prefix, rest = pattern[:cut], pattern[cut:]
    if os.pardir in rest.replace('\\', '/').split('/'):
        return ''
    if os.path.isabs(prefix) or prefix.startswith('~') or os.pardir in prefix.replace('\\', '/').split('/'):
        return prefix or os.sep
    return None


def _path_decision(tool, tool_input, worktree, session_id=None):  # noqa: C901
    candidates = []
    for key in PATH_KEYS:
        value = tool_input.get(key)
        if value is None or value == '':
            continue
        if not isinstance(value, str) or '\0' in value:
            return 'chemin illisible (%s)' % key
        candidates.append(value)
    if tool == 'Glob':
        pattern = tool_input.get('pattern')
        if isinstance(pattern, str):
            prefix = _glob_prefix(pattern)
            if prefix == '':
                return 'motif Glob qui remonte hors du worktree'
            if prefix is not None:
                candidates.append(prefix)
    for raw in candidates:
        resolved = _resolve(raw, worktree)
        if not _inside(resolved, worktree):
            if tool == 'Read' and is_session_output(resolved, worktree, session_id):
                continue
            return 'chemin hors du worktree : %s' % raw
        if tool in hooks_guard.EDIT_TOOLS and resolved != worktree:
            parts = [part.lower() for part in os.path.relpath(resolved, worktree).split(os.sep)]
            if parts[0] in WRITE_PROTECTED_DIRS or '.git' in parts or parts == list(WRITE_PROTECTED_FILES):
                return 'écriture refusée sur un chemin protégé du circuit : %s' % raw
    return None


def decide(payload, worktree) -> str | None:
    """`None` si l'appel d'outil est permis, sinon une raison courte en français."""
    if not isinstance(payload, dict):
        return "entrée du crochet illisible"
    tool, tool_input = payload.get('tool_name'), payload.get('tool_input')
    if not isinstance(tool, str) or not tool or not isinstance(tool_input, dict):
        return "entrée du crochet illisible"
    if tool in CUT_TOOLS or tool.startswith(CUT_PREFIX):
        return "outil coupé pour l'exécutant"
    if tool not in ALLOWED_TOOLS:
        return 'outil %s hors liste blanche (permis : %s)' % (tool, ', '.join(EXECUTOR_TOOLS))
    worktree = os.path.realpath(str(worktree))
    if tool == 'Bash':
        return _bash_decision(tool_input)
    return _path_decision(tool, tool_input, worktree, payload.get('session_id'))
