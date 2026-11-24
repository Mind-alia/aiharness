"""Liste blanche des commandes Bash de l'orchestrateur (Python 3.14, stdlib seule).

`decide(command, root)` rend `None` si la commande est permise, sinon une raison courte en français.
Le découpage en commandes simples, les préfixes transparents et le `sh -c` imbriqué sont ceux de `hooks_guard`.
Garde-fou contre l'accident, pas barrière contre un attaquant : l'orchestrateur écrit déjà les fiches et le plan.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile

import hooks_guard
import workdir

sys.dont_write_bytecode = True

SUBSTITUTIONS = ('seq', 'date', 'pwd')
# Seules ces commandes reçoivent une expansion ($x, $1, $(…)) dans leurs arguments : elles ne font que lire ou afficher.
PURE_READ = (
    'echo',
    'printf',
    'test',
    '[',
    'cat',
    'ls',
    'wc',
    'head',
    'tail',
    'grep',
    'basename',
    'dirname',
    'realpath',
    'stat',
    'du',
    'jq',
    'seq',
    'sleep',
    'date',
    'pwd',
    'true',
    'false',
)
# Remplace, dans le texte lu par le tokeniseur, un `$` littéral (apostrophes, `\$`) : seul un vrai `$` reste une expansion.
LITERAL_DOLLAR = ''
SUBSTITUTED = '$x'
LOOP_VARIABLE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
LOOP_LEVEL = re.compile(r'[1-9][0-9]*')
# Affectations qui changent ce que le shell lance (chemin de recherche, chargement, éditeur, pager, git).
DANGEROUS_ASSIGNMENT = re.compile(
    r'(PATH|IFS|SHELL|ENV|BASH_ENV|PS4|PROMPT_COMMAND|EDITOR|VISUAL|LD_[A-Z_]*|DYLD_[A-Z_]*|PYTHON(PATH|HOME|STARTUP|INSPECT|USERBASE)|'
    r'GIT_[A-Z_]*|[A-Z_]*PAGER)='
)
# Préfixes transparents admis et leurs seules options connues (celles dont `hooks_guard` sait lire la valeur).
PREFIX_OPTIONS = {
    'env': ('-i', '-', '-u', '--unset', '--ignore-environment'),
    'timeout': ('-s', '-k', '--signal', '--kill-after', '--preserve-status', '--foreground'),
    'nice': ('-n', '--adjustment'),
    'time': ('-p',),
    'command': ('-p', '-v', '-V'),
    'nohup': (),
    'builtin': (),
    'exec': (),
}
ENV_BINARY = '/usr/bin/env'
CIRCUIT_SCRIPT = re.compile(r'scripts/[A-Za-z0-9_.-]+\.sh')
CIRCUIT_PYTHON = ('scripts/workspace.py', 'tools/orchestrator/run.py', 'tools/campaign/campaign.py')
MIRROR = ['rsync', '-a', '--exclude', '__tests__', '--exclude', '__pycache__', 'template/scripts/', 'scripts/']
GIT_READING = (
    'status',
    'log',
    'diff',
    'show',
    'rev-parse',
    'rev-list',
    'ls-files',
    'ls-tree',
    'cat-file',
    'for-each-ref',
    'show-ref',
    'merge-base',
    'describe',
    'blame',
    'grep',
)
# Sous-commandes qui écrivent : refusées dans un clone de tâche, dont les crochets git feraient lancer le code de l'exécutant.
GIT_WRITING = ('add', 'commit', 'merge', 'worktree', 'branch', 'mv')
GIT_ALLOWED = GIT_READING + GIT_WRITING
# Chemins du circuit que `git mv` ne déplace pas (premier composant, ou fichier à la racine).
PROTECTED_FOLDERS = ('scripts', '.claude', '.workspace', '.git')
PROTECTED_FILES = ('project.profile', 'agents.md')
GIT_NETWORK = ('push', 'fetch', 'pull', 'clone', 'ls-remote')
GIT_GLOBAL_OPTIONS = ('--no-pager', '--no-optional-locks')
# Options qui font lancer une commande ou écrire un fichier à git.
GIT_RUNNERS = re.compile(r'--(output|open-files-in-pager|ext-diff|exec-path|upload-pack|receive-pack)(=|$)')
READ_ONLY = (
    'ls',
    'cat',
    'head',
    'tail',
    'wc',
    'grep',
    'rg',
    'sort',
    'uniq',
    'cut',
    'tr',
    'diff',
    'date',
    'pwd',
    'echo',
    'printf',
    'test',
    '[',
    'true',
    'false',
    'which',
    'stat',
    'du',
    'ps',
    'pgrep',
    'sleep',
    'seq',
    'basename',
    'dirname',
    'realpath',
    'jq',
    'find',
    'sed',
    'awk',
)
FIND_ACTIONS = ('-delete', '-exec', '-execdir', '-ok', '-okdir', '-fprint', '-fprint0', '-fprintf', '-fls')
FORBIDDEN_OPTIONS = {
    'rg': re.compile(r'--(pre|hostname-bin)(=|$)'),
    'date': re.compile(r'-s|--set'),
}
# sed : deux formes d'affichage, `sed -n '<script>' [fichier…]` ; le `sed` BSD lit les crochets autrement que GNU.
SED_SCRIPT = re.compile(r'[0-9]+p|[0-9]+,[0-9]+p|[0-9]+,\$p|\$p')
# sort : seules ces options courtes ; `-k` et `-t` prennent une valeur. Une option longue GNU s'abrège (`--out=`).
SORT_FLAGS = 'bdfghiMnrsuV'
SORT_VALUED = 'kt'
NETWORK = ('curl', 'wget', 'nc', 'ssh', 'scp', 'sftp', 'ftp', 'telnet', 'npx', 'npm', 'pip', 'brew', 'gh')
CLOSE_SCRIPT = 'scripts/task-workspace.sh'
TASK_ID = re.compile(r'[a-z0-9-]+')
FORCE_LONG = re.compile(r'--force(=|-|$)')
FORCE_SHORT = re.compile(r'-[A-Za-z]*[fD][A-Za-z]*')
ILLEGIBLE = 'commande vide ou illisible'
DELETION = "suppression : le propriétaire l'exécute"
NETWORK_REASON = 'réseau hors des relais connus'
FORCE_REASON = '--force hors de .workspace/'
OUTSIDE_REASON = 'hors liste blanche'
SED_REASON = "sed : seules sed -n 'N,Mp' et sed -n '$p' sont admises"
SUBSTITUTION_REASON = 'substitution de commande : seules $(seq …), $(date …), $(pwd) et une commande de lecture sont admises'
CP_REASON = 'cp : seulement vers un dossier temporaire'
IF_REASON = 'if mal formé'
TEMP_CWD_REASON = 'dossier temporaire : lecture et copie seulement'
UNITTEST_REASON = 'tests : seulement ceux du projet'
# Commandes dont `-v` évalue un indice de tableau : aucune expansion ne doit alors atteindre leurs arguments.
VALUE_EVALUATORS = ('test', '[', 'printf')
VERBOSE_PREFIX = '-v'
UNITTEST_PYTHONS = ('python3', 'python3.14', '/usr/bin/python3')
UNITTEST_MODULE = ['-m', 'unittest']
# unittest : options sans valeur, à valeur chemin, à valeur motif (jamais un chemin) ; débuts de chemin refusés.
UNITTEST_FLAGS = ('-v', '-q', '-f', '-c', '-b', '--locals')
UNITTEST_PATH_OPTIONS = ('-s', '-t', '--start-directory', '--top-level-directory')
UNITTEST_PATTERN_OPTIONS = ('-p', '-k', '--pattern')
UNITTEST_PATTERN_FORBIDDEN = '/$'
UNITTEST_PATH_PREFIXES = ('/', '~', '=')
TEMP_COMMANDS = ('cp', 'cd', 'set')
AWK_REASON = 'awk : écriture ou commande interdite'
# `if` : l'état de chaque `if` ouvert (condition, branche `then`, branche `else`).
IF_KEYWORDS = ('if', 'then', 'elif', 'else', 'fi')
IF_CONDITION, IF_THEN, IF_ELSE = 'condition', 'then', 'else'
IF_NEXT_STATE = {'then': IF_THEN, 'else': IF_ELSE, 'elif': IF_CONDITION}
NEGATION = '!'
CP_OPTIONS = ('-p', '-r', '-R')
CP_MIN_OPERANDS = 2
CP_FORBIDDEN_CHARS = '*?[{'
SET_POSITIONAL = '--'
# awk : `-v` et `-F` prennent une valeur attachée ou séparée ; mots et signes qui lancent une commande ou écrivent.
AWK_VALUED = ('-v', '-F')
AWK_FORBIDDEN = re.compile(r'\b(system|getline|close|fflush)\b|\||@(include|load)')
AWK_PRINT = re.compile(r'\bprint')
AWK_STATEMENT_BREAKS = re.compile(r'[;{}]')
WORKSPACE_DIR = '.workspace'
LOOP_EXIT_REASON = "break ou continue hors d'une boucle for"
CD_REASON = 'cd : un seul dossier de la racine est admis'
MKDIR_REASON = 'mkdir : seulement sous un dossier temporaire'
CLONE_REASON = 'dans un clone de tâche : tests et lecture seulement'
GIT_MV_REASON = 'git mv : chemin du circuit protégé'


class _Refuse(Exception):
    """La commande est refusée ; le message est la raison."""


def _matching_paren(command: str, start: int) -> int:
    """Indice de la parenthèse fermante qui clôt la substitution ouverte juste avant `start`."""
    depth, quote, index = 1, None, start
    while index < len(command):
        char = command[index]
        if char == '\\' and quote != "'":
            index += 1
        elif quote:
            quote = None if char == quote else quote
        elif char in ('"', "'"):
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise _Refuse('substitution non fermée')


def _scan(command: str) -> tuple[str, list[str]]:  # noqa: C901
    """Commande où chaque `$(…)` est remplacé par un mot neutre, et les intérieurs des `$(…)` trouvés.

    Refuse ce que le découpage en mots ne montre pas : accents graves, `$'…'`, parenthèses hors substitution
    (sous-shell, substitution de processus), fin de ligne échappée."""
    kept, inner = [], []
    quote, index = None, 0
    while index < len(command):
        char = command[index]
        if char == '\\' and quote != "'":
            if command[index + 1 : index + 2] == '\n':
                raise _Refuse(ILLEGIBLE)
            kept.append(command[index : index + 2].replace('$', LITERAL_DOLLAR))
            index += 2
            continue
        if quote == "'":
            quote = None if char == "'" else quote
            char = LITERAL_DOLLAR if char == '$' else char
        elif char == '`':
            raise _Refuse('accent grave : ' + SUBSTITUTION_REASON)
        elif char == '$' and command.startswith('$(', index):
            if command.startswith('$((', index):
                raise _Refuse(SUBSTITUTION_REASON)
            end = _matching_paren(command, index + 2)
            inner.append(command[index + 2 : end])
            kept.append(SUBSTITUTED)
            index = end + 1
            continue
        elif char == '$' and quote is None and command[index + 1 : index + 2] in ('"', "'"):
            raise _Refuse(ILLEGIBLE)
        elif quote == '"':
            quote = None if char == '"' else quote
        elif char in ('"', "'"):
            quote = char
        elif char in '()':
            raise _Refuse('parenthèse hors substitution (sous-shell ou substitution de processus)')
        kept.append(char)
        index += 1
    return ''.join(kept), inner


def _path_like(word: str) -> bool:
    return os.sep in word or word.startswith('.')


def _temp_roots() -> list[str]:
    """Dossiers temporaires où une redirection est admise : `/tmp` et le dossier temporaire du système."""
    return sorted({os.path.realpath('/tmp'), os.path.realpath(tempfile.gettempdir())})


def _sed_prints_only(args: list[str]) -> bool:
    """Vrai pour `-n '<N|N,M|N,$|$>p'` suivi de fichiers seulement (aucun mot d'option après le script)."""
    return (
        len(args) >= 2
        and args[0] == '-n'
        and SED_SCRIPT.fullmatch(args[1]) is not None
        and not any(item.startswith('-') for item in args[2:])
    )


def _sort_options_allowed(args: list[str]) -> bool:
    """Vrai si `sort` n'a que des options courtes admises (avec la valeur de `-k` et de `-t`)."""
    index = 0
    while index < len(args):
        item = args[index]
        index += 1
        if item == '--':
            return True
        if item.startswith('--'):
            return False
        if item.startswith('-') and item != '-':
            for position, char in enumerate(item[1:], 1):
                if char in SORT_VALUED:
                    index += 0 if item[position + 1 :] else 1
                    break
                if char not in SORT_FLAGS:
                    return False
    return True


def _evaluates_names(name: str, args: list[str]) -> bool:
    """`printf -v`, `test -v`, `[ -v` : bash y évalue un indice de tableau, `$(…)` compris, même entre guillemets simples."""
    if name == 'printf':
        options = []
        for item in args:
            if not item.startswith('-') or item in ('-', '--'):
                break
            options.append(item)
        return any(item.startswith('-v') for item in options)
    return name in ('test', '[') and '-v' in args


def _unittest_paths(args: list[str]) -> list[str]:
    """Chemins cités par `python3 -m unittest …` ; refuse toute option ou valeur hors de la liste admise.

    La valeur séparée de `-p` et de `-k` est un motif (ni `/` ni `$`) ; celle de `-s` et de `-t` est un chemin."""
    paths: list[str] = []
    index = 0
    while index < len(args):
        item = args[index]
        index += 1
        if item.startswith('-') and item != '-':
            if item in UNITTEST_FLAGS:
                continue
            if item not in UNITTEST_PATH_OPTIONS + UNITTEST_PATTERN_OPTIONS or index >= len(args):
                raise _Refuse(UNITTEST_REASON)
            value, index = args[index], index + 1
            if item in UNITTEST_PATTERN_OPTIONS:
                if any(char in value for char in UNITTEST_PATTERN_FORBIDDEN):
                    raise _Refuse(UNITTEST_REASON)
                continue
            item = value
        if item.startswith(UNITTEST_PATH_PREFIXES) or '$' in item or '..' in item.split(os.sep):
            raise _Refuse(UNITTEST_REASON)
        paths.append(item)
    return paths


class _Judge:
    def __init__(self, root) -> None:
        self.project = os.path.realpath(str(root))
        self.root = self.project
        self.cwd = self.root
        self.temp_roots = _temp_roots()

    # Chemins ---------------------------------------------------------------------------------------------------

    def _relative(self, word: str, base: str | None = None) -> str | None:
        """Chemin de `word` relatif à `base` (la racine par défaut), `None` s'il en sort ou s'il n'est pas lisible."""
        base = base or self.root
        if '$' in word or word.startswith('~'):
            return None
        absolute = os.path.join(self.cwd, word)
        rel = hooks_guard._relative(absolute, base)
        if rel is None and os.path.realpath(os.path.normpath(absolute)) == base:
            return '.'
        return rel

    def _redirect(self, target: str) -> None:
        if target == '/dev/null':
            return
        if any(self._relative(target, base) is not None for base in [self.root, *self.temp_roots]):
            return
        raise _Refuse("redirection hors de la racine, de /tmp et du dossier temporaire : %s" % target)

    # Découpage ---------------------------------------------------------------------------------------------------

    def _parse(self, command: str):
        kept, inner = _scan(command)
        try:
            tokens = hooks_guard.tokenize(kept)
        except ValueError:
            raise _Refuse(ILLEGIBLE)
        return [item for item in hooks_guard._simple_commands(tokens) if item[0] or item[1]], inner

    def command(self, command: str, depth: int) -> None:  # noqa: C901
        if depth > hooks_guard.MAX_SHELL_DEPTH:
            raise _Refuse('imbrication trop profonde')
        commands, inner = self._parse(command)
        for script in inner:
            self._substitution(script, depth)
        open_loops, expect_do, branches = 0, False, []
        for words, targets in commands:
            if expect_do:
                if words[:1] != ['do']:
                    raise _Refuse('boucle for mal formée')
                words, expect_do, open_loops = words[1:], False, open_loops + 1
            elif words[:1] == ['do']:
                raise _Refuse('« do » sans boucle for')
            words = self._keywords(words, branches)
            if words[:1] in (['while'], ['until']):
                raise _Refuse('boucle %s : attente sans borne (docs/ORCHESTRATEUR.md §5)' % words[0])
            if words[:1] in (['break'], ['continue']):
                if not open_loops or len(words) > 2 or (len(words) == 2 and not LOOP_LEVEL.fullmatch(words[1])):
                    raise _Refuse(LOOP_EXIT_REASON)
            elif words[:1] == ['for']:
                self._loop_header(words)
                expect_do = True
            elif words == ['done']:
                if not open_loops:
                    raise _Refuse('« done » sans boucle for')
                open_loops -= 1
            else:
                self._simple(words, depth)
            for target in targets:
                self._redirect(target.replace(LITERAL_DOLLAR, '$'))
        if expect_do or open_loops:
            raise _Refuse('boucle for non fermée')
        if branches:
            raise _Refuse(IF_REASON)

    @staticmethod
    def _keywords(words: list[str], branches: list[str]) -> list[str]:
        """`words` sans les mots-clés `if` en tête puis sans `!` ; `branches` suit les `if` ouverts."""
        while words[:1] and words[0] in IF_KEYWORDS:
            word, words = words[0], words[1:]
            if word == 'if':
                branches.append(IF_CONDITION)
            elif word == 'fi' and branches and branches[-1] != IF_CONDITION:
                branches.pop()
            elif word in IF_NEXT_STATE and branches and branches[-1] == (IF_CONDITION if word == 'then' else IF_THEN):
                branches[-1] = IF_NEXT_STATE[word]
            else:
                raise _Refuse(IF_REASON)
        return words[1:] if words[:1] == [NEGATION] else words

    def _substitution(self, script: str, depth: int) -> None:
        commands, _ = self._parse(script)
        if len(commands) != 1 or commands[0][1] or commands[0][0][:1] not in [[name] for name in SUBSTITUTIONS + READ_ONLY]:
            raise _Refuse(SUBSTITUTION_REASON)
        self.command(script, depth + 1)

    @staticmethod
    def _loop_header(words: list[str]) -> None:
        if len(words) < 3 or words[2] != 'in' or not LOOP_VARIABLE.fullmatch(words[1]):
            raise _Refuse('boucle for mal formée')
        if DANGEROUS_ASSIGNMENT.match(words[1] + '='):
            raise _Refuse('variable de boucle dangereuse : ' + words[1])

    # Commandes simples -------------------------------------------------------------------------------------------

    @staticmethod
    def _prefix_problem(removed: list[str]) -> str | None:
        name = None
        for word in removed:
            if hooks_guard.ASSIGNMENT.match(word):
                if DANGEROUS_ASSIGNMENT.match(word):
                    return "affectation d'environnement dangereuse : " + word.split('=')[0]
            elif word == ENV_BINARY or word in PREFIX_OPTIONS:
                name = os.path.basename(word)
            elif os.path.basename(word) in hooks_guard.PREFIXES:
                return OUTSIDE_REASON
            elif word.startswith('-') and word != '--' and (name is None or word.split('=')[0] not in PREFIX_OPTIONS[name]):
                return OUTSIDE_REASON
        return None

    def _simple(self, words: list[str], depth: int) -> None:  # noqa: C901
        stripped = hooks_guard._strip_prefixes(words)
        problem = self._prefix_problem(words[: len(words) - len(stripped)])
        if problem:
            raise _Refuse(problem)
        if not stripped:
            return
        if os.path.basename(stripped[0]) == 'rm':
            raise _Refuse(DELETION)
        if os.path.basename(stripped[0]) in NETWORK:
            raise _Refuse(NETWORK_REASON)
        self._expansions(words[: len(words) - len(stripped)], stripped)
        stripped = [item.replace(LITERAL_DOLLAR, '$') for item in stripped]
        script = hooks_guard._shell_script(stripped)
        if script is not None:
            self.command(script, depth + 1)
            return
        name, args = stripped[0], stripped[1:]
        base = os.path.basename(name)
        if base == 'rm':
            raise _Refuse(DELETION)
        if base in NETWORK:
            raise _Refuse(NETWORK_REASON)
        if self._temporary(self.cwd) and not self._temp_allowed(words[: len(words) - len(stripped)], stripped):
            raise _Refuse(TEMP_CWD_REASON)
        if name == 'git':
            paths = self._git(args)
        elif name == 'cd':
            self._cd(args)
            return
        elif name == 'mkdir':
            self._mkdir(args)
            return
        elif name == 'cp':
            self._cp(args)
            return
        elif name == 'set':
            if args[:1] != [SET_POSITIONAL]:
                raise _Refuse(OUTSIDE_REASON)
            return
        elif self._circuit(name, args, self.project) or self._circuit(name, args, self.root):
            if self.root != self.project:
                raise _Refuse(CLONE_REASON)
            paths = [item for item in args if _path_like(item)]
        elif self._tests(name, args) or stripped == MIRROR or self._docker(name, args):
            if name in UNITTEST_PYTHONS:
                self._unittest_inside(args[len(UNITTEST_MODULE) :])
            paths = [item for item in args if _path_like(item)]
        elif name in READ_ONLY:
            self._read_only(name, args)
            paths = [item for item in args if _path_like(item)]
        else:
            raise _Refuse(OUTSIDE_REASON)
        self._force(name, args, paths)

    @staticmethod
    def _expansions(removed: list[str], stripped: list[str]) -> None:
        """Une expansion (`$x`, `$1`, `$(…)`) n'atteint que les commandes de PURE_READ ; en préfixe, seule l'affectation passe."""
        if not any('$' in item for item in removed + stripped):
            return
        if (
            stripped[0] in VALUE_EVALUATORS
            and any(item.startswith(VERBOSE_PREFIX) for item in stripped[1:])
            and any('$' in item for item in stripped[1:])
        ):
            raise _Refuse('expansion dans les arguments de %s' % stripped[0])
        if stripped[0] not in PURE_READ or any('$' in item and not hooks_guard.ASSIGNMENT.match(item) for item in removed):
            raise _Refuse('expansion dans les arguments de %s' % stripped[0].replace(LITERAL_DOLLAR, '$'))

    def _circuit(self, name: str, args: list[str], base: str) -> bool:
        """Vrai si la commande lance un script du circuit situé dans `base`."""
        if name in ('bash', 'sh'):
            script = args[0] if args else ''
            return bool(CIRCUIT_SCRIPT.fullmatch(self._relative(script, base) or ''))
        if name == 'python3':
            return bool(args) and self._relative(args[0], base) in CIRCUIT_PYTHON
        rel = self._relative(name, base) if os.sep in name else None
        return rel == 'workspace' or bool(CIRCUIT_SCRIPT.fullmatch(rel or ''))

    def _tests(self, name: str, args: list[str]) -> bool:
        if name in UNITTEST_PYTHONS:
            return args[:2] == ['-m', 'unittest']
        return name in ('sh', 'bash') and len(args) == 2 and self._relative(args[0]) == 'tests/run.sh' and args[1] in ('fast', 'full')

    def _inside_root(self, real: str) -> bool:
        return real == self.root or real.startswith(self.root + os.sep)

    def _unittest_inside(self, args: list[str]) -> None:
        """`python3 -m unittest` : lancé depuis le projet ou le clone, et chaque chemin cité s'y résout."""
        if not self._inside_root(self.cwd) or not all(
            self._inside_root(os.path.realpath(os.path.join(self.cwd, item))) for item in _unittest_paths(args)
        ):
            raise _Refuse(UNITTEST_REASON)

    @staticmethod
    def _temp_allowed(removed: list[str], stripped: list[str]) -> bool:
        """Depuis un dossier temporaire : lecture, `cp`, `cd`, `set --` seulement, sans préfixe transparent."""
        if not all(hooks_guard.ASSIGNMENT.match(item) for item in removed):
            return False
        name = stripped[0]
        return name in READ_ONLY or (name in TEMP_COMMANDS and (name != 'set' or stripped[1:2] == [SET_POSITIONAL]))

    @staticmethod
    def _docker(name: str, args: list[str]) -> bool:
        return name == 'docker' and args[:1] in (['ps'], ['stop'])

    def _cd(self, args: list[str]) -> None:
        word = args[0] if len(args) == 1 else '-'
        if word.startswith(('-', '~')) or '$' in word:
            raise _Refuse(CD_REASON)
        target = os.path.realpath(os.path.join(self.cwd, word))
        if target == self.project or target.startswith(self.project + os.sep):
            self.root = self.project
        elif self._temporary(target) and not self.project.startswith(target + os.sep):
            self.root = self.project
        else:
            self.root = self._task_clone(target)
        self.cwd = target

    def _temporary(self, real: str) -> bool:
        """Vrai si `real` est une racine temporaire ou un de ses sous-dossiers, hors du projet."""
        return (
            real != self.project
            and not real.startswith(self.project + os.sep)
            and any(real == base or real.startswith(base + os.sep) for base in self.temp_roots)
        )

    def _cp(self, args: list[str]) -> None:
        """`cp [-p|-r|-R]… SOURCE… DESTINATION` : la destination se résout dans un dossier temporaire, hors du projet."""
        operands = [item for item in args if not item.startswith('-')]
        if len(operands) < CP_MIN_OPERANDS or any(item.startswith('-') and item not in CP_OPTIONS for item in args):
            raise _Refuse(CP_REASON)
        destination = operands[-1]
        if (
            destination.startswith('~')
            or '$' in destination
            or '..' in destination.split(os.sep)
            or any(char in destination for char in CP_FORBIDDEN_CHARS)
        ):
            raise _Refuse(CP_REASON)
        parent = os.path.join(self.cwd, destination)
        while not os.path.lexists(parent):
            parent = os.path.dirname(parent)
        real = os.path.realpath(parent)
        if not self._temporary(real) or real == self.root or real.startswith(self.root + os.sep):
            raise _Refuse(CP_REASON)
        full = os.path.join(self.cwd, destination)
        if os.path.islink(full.rstrip(os.sep)):
            raise _Refuse(CP_REASON)
        if os.path.isdir(full):
            for source in operands[:-1]:
                target = os.path.join(full, os.path.basename(source.rstrip(os.sep)))
                resolved = os.path.realpath(target)
                if os.path.islink(target) or not self._temporary(resolved) or self._inside_root(resolved):
                    raise _Refuse(CP_REASON)

    def _task_clone(self, target: str) -> str:
        """Dossier de la tâche qui contient `target`, sous-dossier strict de `workdir.work_base`."""
        try:
            base = os.path.realpath(str(workdir.work_base(self.project)))
        except ValueError:
            raise _Refuse(CD_REASON)
        if not target.startswith(base + os.sep):
            raise _Refuse(CD_REASON)
        return os.path.join(base, target[len(base) + 1 :].split(os.sep)[0])

    def _mkdir(self, args: list[str]) -> None:
        folders = [item for item in args if item != '-p']
        if not folders or any(
            item.startswith('-') or '$' in item or item.startswith('~') or '..' in item.split(os.sep) for item in folders
        ):
            raise _Refuse(MKDIR_REASON)
        for item in folders:
            parent = os.path.join(self.cwd, item)
            while not os.path.lexists(parent):
                parent = os.path.dirname(parent)
            real = os.path.realpath(parent)
            # Un projet posé sous /tmp n'est pas pour autant un dossier temporaire.
            if (
                real == self.root
                or real.startswith(self.root + os.sep)
                or not any(real == base or real.startswith(base + os.sep) for base in self.temp_roots)
            ):
                raise _Refuse(MKDIR_REASON)

    def _read_only(self, name: str, args: list[str]) -> None:
        forbidden = FORBIDDEN_OPTIONS.get(name)
        options = [item for item in args if item.startswith('-')]
        if forbidden and any(forbidden.match(item) for item in options):
            raise _Refuse('option interdite pour %s : %s' % (name, ', '.join(options)))
        if name == 'sed' and not _sed_prints_only(args):
            raise _Refuse(SED_REASON)
        if name == 'sort' and not _sort_options_allowed(args):
            raise _Refuse('option interdite pour sort : seules les options courtes -b -d -f -g -h -i -k -M -n -r -s -t -u -V sont admises')
        if _evaluates_names(name, args):
            raise _Refuse('%s -v évalue un indice de tableau, $(…) compris' % name)
        if name == 'awk':
            self._awk(args)
        if name == 'find' and any(item in FIND_ACTIONS for item in args):
            raise _Refuse('find avec une action qui lance ou écrit')
        if name == 'uniq' and len([item for item in args if not item.startswith('-')]) > 1:
            raise _Refuse('uniq écrirait dans un fichier')

    @staticmethod
    def _awk(args: list[str]) -> None:
        """awk en lecture : ni `-f`, ni option inconnue, ni programme qui lance une commande ou écrit."""
        index = 0
        while index < len(args) and args[index].startswith('-') and args[index] not in ('-', '--'):
            item = args[index]
            if item in AWK_VALUED:
                index += 1
            elif not item.startswith(AWK_VALUED):
                raise _Refuse(AWK_REASON)
            index += 1
        index += 1 if args[index : index + 1] == ['--'] else 0
        program = args[index] if index < len(args) else ''
        if AWK_FORBIDDEN.search(program) or '@' in program:
            raise _Refuse(AWK_REASON)
        for statement in AWK_STATEMENT_BREAKS.split(program):
            printed = AWK_PRINT.search(statement)
            if printed and '>' in statement[printed.start() :]:
                raise _Refuse(AWK_REASON)

    def _git(self, args: list[str]) -> list[str]:  # noqa: C901
        paths = []
        while args and args[0].startswith('-'):
            if args[0] == '-C' and len(args) > 1:
                paths.append(args[1])
                args = args[2:]
            elif args[0] in GIT_GLOBAL_OPTIONS:
                args = args[1:]
            else:
                raise _Refuse(OUTSIDE_REASON)
        if not args:
            raise _Refuse(OUTSIDE_REASON)
        sub, rest = args[0], args[1:]
        if sub in GIT_NETWORK:
            raise _Refuse(NETWORK_REASON)
        if sub == 'config':
            if rest[:1] != ['--get']:
                raise _Refuse(OUTSIDE_REASON)
        elif sub not in GIT_ALLOWED:
            raise _Refuse(OUTSIDE_REASON)
        elif sub in GIT_WRITING and self.root != self.project:
            raise _Refuse(CLONE_REASON)
        elif sub in GIT_WRITING:
            if len(paths) > 1:
                raise _Refuse(CLONE_REASON)
            self._git_writes_here(paths)
            if sub == 'mv':
                self._git_mv(rest, bool(paths))
        if any(GIT_RUNNERS.match(item) or (sub == 'grep' and item.startswith('-O')) for item in rest):
            raise _Refuse('option de git qui lance une commande ou écrit un fichier')
        return paths + [item for item in rest if _path_like(item)]

    def _git_writes_here(self, folders: list[str]) -> None:
        """Un `-C` d'une sous-commande d'écriture reste dans la racine du projet, hors des clones de tâche."""
        if not folders:
            return
        try:
            base = os.path.realpath(str(workdir.work_base(self.project)))
        except ValueError:
            raise _Refuse(CLONE_REASON)
        for word in folders:
            if '$' in word or word.startswith('~'):
                raise _Refuse(CLONE_REASON)
            target = os.path.realpath(os.path.join(self.cwd, word))
            inside = target == self.project or target.startswith(self.project + os.sep)
            if not inside or target == base or target.startswith(base + os.sep):
                raise _Refuse(CLONE_REASON)

    def _git_mv(self, args: list[str], redirected: bool) -> None:
        """`git mv` ne touche ni les dossiers du circuit ni `project.profile` et `AGENTS.md` (source ou destination)."""
        if redirected:
            raise _Refuse(GIT_MV_REASON)
        for word in (item for item in args if not item.startswith('-') or item == '-'):
            if '$' in word or word.startswith('~'):
                raise _Refuse(GIT_MV_REASON)
            rel = self._relative(word)
            if rel is None:
                continue
            first = rel.split(os.sep)[0].lower()
            if rel == '.' or first in PROTECTED_FOLDERS or (first in PROTECTED_FILES and os.sep not in rel):
                raise _Refuse(GIT_MV_REASON)

    def _close_abandoned(self, name: str, args: list[str]) -> bool:
        """Vrai pour `task-workspace.sh close <id> --abandoned [--keep-branch] --force`, depuis la racine du projet."""
        if self.root != self.project or name not in ('bash', 'sh') or len(args) < 4:
            return False
        if self._relative(args[0], self.project) != CLOSE_SCRIPT or args[1] != 'close' or not TASK_ID.fullmatch(args[2]):
            return False
        options = sorted(args[3:])
        return options in (['--abandoned', '--force'], ['--abandoned', '--force', '--keep-branch'])

    def _force(self, name: str, args: list[str], paths: list[str]) -> None:
        if self._close_abandoned(name, args):
            return
        short = name == 'git' and any(FORCE_SHORT.fullmatch(item) for item in args)
        if not short and not any(FORCE_LONG.match(item) for item in args):
            return
        inside = [self._relative(item) for item in paths]
        if inside and all(item is not None and item.split(os.sep)[0] == WORKSPACE_DIR and item != WORKSPACE_DIR for item in inside):
            return
        raise _Refuse(FORCE_REASON)


def decide(command, root) -> str | None:
    """`None` si la commande Bash est permise à l'orchestrateur, sinon une raison courte en français."""
    if not isinstance(command, str) or not command.strip():
        return ILLEGIBLE
    try:
        _Judge(root).command(command, 0)
    except _Refuse as refusal:
        return str(refusal)
    return None
