"""Échecs de qualité connus de la vérification sur l'hôte (T99, T114, T118) : le format se corrige par programme, le
lint part tel quel dans une ronde de correction courte, sans diagnostic de modèle ; un budget de types dépassé aussi,
avec les erreurs pyright des seuls fichiers de la tâche.

La vérification du kit lance la suite de qualité (`tests/test_code_quality.py`), dont les tests rouges ont un nom
fixe. Quand seuls ces tests sont rouges, la cause est connue : payer un diagnostic Sonnet puis une tentative complète
pour une mise en forme n'apporte rien (E13 bras D, 27/10 : 0,65 $ pour deux erreurs de format).
"""

import json
import os
import re
import shlex
import subprocess

import journal

# Même version épinglée que tests/test_code_quality.py et le Makefile ; ORCHESTRATOR_RUFF la remplace (tests).
RUFF_ENV = 'ORCHESTRATOR_RUFF'
RUFF = 'uvx --offline ruff@0.17.2'
RUFF_TIMEOUT = 300
RUFF_MISSING = 127
# pyright épinglé comme dans tests/test_code_quality.py (1.1.402, vérifié par la suite) ; ORCHESTRATOR_PYRIGHT le remplace.
PYRIGHT_ENV = 'ORCHESTRATOR_PYRIGHT'
PYRIGHT = 'pyright'
PYRIGHT_TIMEOUT = 600
FORMAT, LINT, TYPES = 'format', 'lint', 'types'
LABELS = {FORMAT: 'format ruff', LINT: 'lint ruff', TYPES: 'budget de types pyright'}
# Tests de la suite de qualité dont l'échec a une cause connue, et sa catégorie.
KNOWN = {'test_ruff_format_is_clean': FORMAT, 'test_ruff_lint_is_clean': LINT, 'test_type_errors_stay_within_budget': TYPES}
HEADER = re.compile(r'^(?:FAIL|ERROR): (\w+)', re.M)
SEPARATOR = '=' * 70
OUTPUT_LIMIT = 8000
PYTHON_SUFFIXES = ('.py', '.pyi')
CONTEXT = (
    "Échec de qualité relevé par la vérification sur l'hôte ({}), sortie de l'outil telle quelle (programme, sans "
    'modèle). Corrige exactement ces erreurs dans les fichiers de la tâche, sans rien changer d\'autre, puis relance '
    '`tests.test_code_quality`.'
)


def category(text):
    """FORMAT quand seul le format est rouge, LINT quand le lint l'est (avec ou sans le reste), TYPES quand le budget de
    types l'est sans le lint, None si un autre test est rouge."""
    names = set(HEADER.findall(text or ''))
    if not names or not names <= set(KNOWN):
        return None
    if names == {'test_ruff_format_is_clean'}:
        return FORMAT
    return LINT if 'test_ruff_lint_is_clean' in names else TYPES


def failures(text, limit=OUTPUT_LIMIT):
    """Blocs des tests rouges de la sortie unittest, tels quels, bornés à `limit` caractères."""
    blocks = [block.strip() for block in (text or '').split(SEPARATOR) if HEADER.match(block.strip())]
    joined = ('\n' + SEPARATOR + '\n').join(blocks)
    return joined if len(joined) <= limit else joined[:limit] + '\n[…]'


def python_files(clone, names):
    """Fichiers Python de la tâche présents dans le clone, sans lien symbolique ni chemin qui en sort."""
    kept = []
    for name in names or []:
        if not isinstance(name, str) or not name.endswith(PYTHON_SUFFIXES):
            continue
        try:
            path = journal.inside(clone, name)
        except ValueError:
            continue
        if path.is_file():
            kept.append(name)
    return kept


def format_files(clone, names, log):
    """`ruff format` sur `names` dans le clone de la tâche, sortie ajoutée à `log` ; code de ruff (127 s'il manque)."""
    command = shlex.split(os.environ.get(RUFF_ENV) or RUFF) + ['format', '--no-cache', '--', *names]
    try:
        done = subprocess.run(
            command,
            cwd=str(clone),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=RUFF_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        output, code = f'{command[0]} : {error}', RUFF_MISSING
    else:
        output, code = done.stdout, done.returncode
    with open(str(log), 'a', encoding='utf-8') as stream:
        stream.write(f'\n[programme] ruff format sur {len(names)} fichier(s) : code {code}\n{output.strip()}\n')
    return code


def type_errors(clone, names):
    """T118 : erreurs pyright des fichiers de la tâche (`names`, relatifs au clone), une par ligne, bornées ; pyright
    lit le clone sans rien y exécuter. Rend un texte à donner tel quel à la ronde de correction."""
    kept = set(python_files(clone, names))
    command = shlex.split(os.environ.get(PYRIGHT_ENV) or PYRIGHT) + ['--outputjson']
    try:
        done = subprocess.run(command, cwd=str(clone), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=PYRIGHT_TIMEOUT)
        report = json.loads(done.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return f'pyright indisponible ({error}) : lancer `make typecheck` et corriger les erreurs des fichiers de la tâche.'
    lines, root = [], os.path.realpath(str(clone))
    for item in report.get('generalDiagnostics') or []:
        name = os.path.relpath(os.path.realpath(str(item.get('file') or '')), root)
        if item.get('severity') == 'error' and name in kept:
            start = (item.get('range') or {}).get('start') or {}
            where = '{}:{}:{}'.format(name, int(start.get('line', 0)) + 1, int(start.get('character', 0)) + 1)
            lines.append('{}: error: {} ({})'.format(where, ' '.join(str(item.get('message') or '').split()), item.get('rule') or '-'))
    text = '\n'.join(lines) or 'aucune erreur pyright dans les fichiers de la tâche : lancer `make typecheck`.'
    return text if len(text) <= OUTPUT_LIMIT else text[:OUTPUT_LIMIT] + '\n[…]'


def context(root, task_id, kind, text, details=None):
    """Contexte de la ronde courte : la sortie de l'outil telle quelle, sans diagnostic, et les erreurs détaillées
    (`details`, pyright pour un budget de types) ; chemin relatif à root."""
    body = CONTEXT.format(LABELS.get(kind, kind)) + '\n\n```\n' + failures(text) + '\n```'
    if details:
        body += '\n\nErreurs des fichiers de la tâche :\n\n```\n' + details + '\n```'
    return journal.write_note(root, f'qualite-{task_id}.md', body)


def read_from(path, offset):
    """Texte de `path` à partir de l'octet `offset` (la vérification ajoute au même journal à chaque tentative)."""
    try:
        with open(str(path), 'rb') as stream:
            stream.seek(offset)
            return stream.read().decode('utf-8', 'replace')
    except OSError:
        return ''


def size(path):
    try:
        return os.path.getsize(str(path))
    except OSError:
        return 0
