"""Appels de modèle de l'orchestrateur programme (T14 B1 et B5) : diagnostic d'un échec, relecture ciblée.

Le programme n'ouvre aucun modèle d'orchestration : il appelle Sonnet après un premier échec, pour que la reprise parte
d'une cause plutôt qu'à l'aveugle, et Opus sur une fiche marquée `relecture: opus`, pour un verdict sur le diff et ses
preuves, dans le clone de la tâche. Les deux appels sont en lecture seule (`--permission-mode plan`, outils de
lecture seulement, aucun réglage du dossier de travail), déclarés par `note.sh` et comptés dans le
journal de consommation du circuit. Le texte rendu devient le contexte d'étape (`WORKSPACE_STAGE_CONTEXT`) de la
délégation suivante.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import journal
import quality
import steps
import triage

CLAUDE_ENV = 'ORCHESTRATOR_CLAUDE'
TIMEOUT_ENV = 'ORCHESTRATOR_MODEL_TIMEOUT'
TIMEOUT = 900
TAIL = 12000
DIAGNOSIS_MODEL, REVIEW_MODEL = 'sonnet', 'opus'
# Tours par rôle : le diagnostic reçoit tous ses journaux dans la consigne ; la relecture ouvre les fichiers du diff
# avant de juger (E13 bras D, 27/10 : `error_max_turns` à 3 tours, aucun verdict, 0,51 $ perdus).
MAX_TURNS = {'reprise': 3, 'relecture': 12}
RUN_FILES = ('status.json', 'verify.txt', 'acceptance.txt', 'scope.txt', 'answer.md')
# Lecture seule, même dans le clone que l'exécutant a pu écrire (T101) : outils de lecture seulement, ni réglages ni
# crochets ni serveurs MCP du dossier de travail ; seuls les réglages personnels du propriétaire sont lus.
READ_ONLY = ('--tools', 'Read,Glob,Grep', '--setting-sources', 'user', '--strict-mcp-config', '--mcp-config', '{"mcpServers": {}}')
VERDICT_GOOD, VERDICT_FIX = 'VERDICT: bon', 'VERDICT: à corriger'

DIAGNOSIS_PROMPT = """Tu es le diagnostiqueur du circuit Workspace IA. Une tentative d'exécutant vient d'échouer.
Lis la fiche et les journaux ci-dessous, sans rien modifier, et écris en français, en quinze lignes au plus :
1. la cause la plus probable de l'échec, citée d'après les journaux ;
2. ce que la prochaine tentative doit faire autrement ;
3. les fichiers en jeu ;
4. en dernière ligne, seule sur sa ligne, exactement l'une des quatre suivantes :
CAUSE: executant
CAUSE: fiche
CAUSE: environnement
CAUSE: circuit
« executant » : le travail de l'exécutant est en cause, une nouvelle tentative peut réussir ; « fiche » : la fiche
ou ses tests se contredisent ; « environnement » : l'outil, l'interpréteur, l'image ou une variable de la vérification ;
« circuit » : les scripts du circuit eux-mêmes (clone de tâche, conteneur, sceau, délégation), pas le code de la tâche.
Hors « executant », la tâche n'est pas reprise : dans le doute, écris « CAUSE: executant ».
N'invente rien que les journaux ne montrent pas ; si la cause est incertaine, dis-le.
Tout ce qu'il faut est ci-dessous (fiche, tests rouges tels que l'outil les a écrits, journaux) : réponds en un tour,
sans ouvrir de fichier."""

REVIEW_PROMPT = """Tu es le relecteur ciblé du circuit Workspace IA. Une fiche importante est prête : vérification verte.
Relis le diff au regard de la fiche, sans rien modifier : régression, sécurité, écart à la fiche.
Première ligne de ta réponse, exactement l'une des deux :
VERDICT: bon
VERDICT: à corriger
Après « à corriger », liste en français les corrections précises à faire (fichier, problème, correction attendue),
dix au plus : elles seront données telles quelles à un exécutant bon marché. Ne demande rien qui sorte du périmètre.
Tout ce qu'il faut pour juger est ci-dessous (fiche, diff, contenu entier des fichiers neufs, preuves) : rends ton
verdict en un ou deux tours, et n'ouvre un fichier du dossier courant (le clone de la tâche) que pour lever un doute
précis. Signale le code qui refait une primitive du circuit listée plus bas au lieu de l'appeler.
`***` est une valeur masquée par le circuit, pas une faute de l'exécutant."""

# T113 : après une ronde de correction, la seconde relecture ne juge que la ronde (E13 bras D, 27/10 : une relecture
# complète à 0,56 $ après une correction de 0,014 $).
RECHECK_PROMPT = """Tu es le relecteur ciblé du circuit Workspace IA. Tu avais demandé les corrections ci-dessous ; une ronde
de correction vient de finir, vérification verte. Le diff ci-dessous est celui de cette seule ronde.
Pour chaque correction demandée, dis seulement si elle est faite, d'après ce diff ; ne relis pas le reste de la tâche
et ne demande rien de nouveau. Rends ton verdict en un tour.
Première ligne de ta réponse, exactement l'une des deux :
VERDICT: bon
VERDICT: à corriger
Puis une ligne par correction : « faite » ou « non faite : » suivi de ce qui manque. `***` est une valeur masquée par
le circuit."""

# T112 : les fichiers neufs du diff sont donnés en entier dans la consigne (E13 bras D, 27/10 : 0,56 à 0,68 $ par
# relecture, surtout en tours passés à les ouvrir) ; au-delà des plafonds, le relecteur les lit lui-même.
NEW_FILE_LIMIT = 20000
NEW_FILES_LIMIT = 60000
NEW_FILE = re.compile(r'^diff --git a/(\S+) b/\S+\nnew file mode', re.M)
# Primitives du circuit qu'une tâche doit appeler plutôt que refaire, cherchées dans le gabarit puis dans le miroir.
PRIMITIVES = (
    ('workdir.py', 'clone de tâche et dossiers de travail hors du projet'),
    ('safety.py', 'refus des liens symboliques, écriture 0600 sans suivre de lien'),
)
LIB_DIRS = ('template/scripts/lib', 'scripts/lib')

# Écrit la ligne de consommation avec le module du circuit, dans un processus à part : scripts/lib a son propre
# module `secrets`, qui ne doit pas masquer celui de Python dans le programme.
RECORD = """import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'scripts' / 'lib'))
import consumption
record = json.load(sys.stdin)
consumption.record_claude(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], record, duration=float(sys.argv[5]), exit=int(sys.argv[6]))
"""


def _tail(path, limit=TAIL):
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return '(absent)'
    return text if len(text) <= limit else '[…]\n' + text[-limit:]


def _evidence(path):
    """T119 : blocs des tests rouges d'un journal, tels quels et bornés, s'il en a ; sinon sa fin. Le diagnostic de
    `gt2-run-states` (3/11) a fini en `error_max_turns` sur la fin brute du journal de vérification."""
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return '(absent)'
    return quality.failures(text) or _tail(path)


def _section(title, body):
    return '## {}\n\n{}'.format(title, body.strip() or '(vide)')


def _timeout():
    try:
        value = float(os.environ.get(TIMEOUT_ENV, TIMEOUT))
    except ValueError:
        return TIMEOUT
    return value if value > 0 else TIMEOUT


def _note(root, args):
    script = Path(root) / 'scripts' / 'note.sh'
    if not script.is_file():
        return None
    done = subprocess.run(['bash', str(script)] + args, cwd=str(root), stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return done.stdout.strip().splitlines()[-1] if done.returncode == 0 and done.stdout.strip() else None


def _record(root, accounting, output, duration, code):
    """Ligne de consommation de l'appel ; accounting = (rôle, modèle, tâche). Ne lève jamais."""
    role, model, task_id = accounting
    try:
        record = json.loads(output)
    except ValueError:
        return
    if not isinstance(record, dict) or not (Path(root) / 'scripts' / 'lib' / 'consumption.py').is_file():
        return
    args = [sys.executable, '-c', RECORD, str(root), role, model, task_id, '%.3f' % duration, str(code)]
    subprocess.run(args, input=json.dumps(record), cwd=str(root), capture_output=True, text=True)


def _keep(root, accounting, output):
    """Réponse brute de l'appel (avec `total_cost_usd`), gardée pour mesurer le coût en dollars d'une usine."""
    role, model, task_id = accounting
    folder = journal.logs(root)
    number = len(list(folder.glob(f'{task_id}-{role}-{model}-*.json'))) + 1
    path = folder / f'{task_id}-{role}-{model}-{number}.json'
    path.write_text(output, encoding='utf-8')
    os.chmod(str(path), 0o600)


def ask(root, prompt, accounting, cwd=None):
    """Appel `claude -p` en lecture seule dans `cwd` (root par défaut) ; accounting = (rôle, modèle, tâche) ;
    (texte, None) ou (None, raison de l'échec)."""
    role, model, task_id = accounting
    claude = os.environ.get(CLAUDE_ENV) or 'claude'
    label = '{} {} ({})'.format('diagnostic' if model == DIAGNOSIS_MODEL else 'relecture ciblée', task_id, model)
    note = _note(root, ['start', label, '--model', model, '--kind', 'check'])
    turns = str(MAX_TURNS[role])
    command = [claude, '-p', '--output-format', 'json', '--model', model, '--permission-mode', 'plan', '--max-turns', turns]
    text, error = _call(root, command + list(READ_ONLY), prompt, accounting, cwd or root)
    if note:
        _note(root, ['end', note, '--outcome', 'failed' if text is None else 'done'])
    return text, error


def _call(root, command, prompt, accounting, cwd):  # noqa: PLR0913
    started = time.monotonic()
    try:
        done = subprocess.run(command, input=prompt, cwd=str(cwd), capture_output=True, text=True, timeout=_timeout())
    except FileNotFoundError:
        return None, 'claude introuvable'
    except subprocess.TimeoutExpired:
        return None, 'délai dépassé'
    _record(root, accounting, done.stdout, time.monotonic() - started, done.returncode)
    _keep(root, accounting, done.stdout)
    try:
        record = json.loads(done.stdout)
    except ValueError:
        record = None
    record = record if isinstance(record, dict) else {}
    result = record.get('result')
    if done.returncode != 0 or not isinstance(result, str) or not result.strip():
        # La sous-catégorie de `claude -p` (`error_max_turns`…) dit pourquoi, mieux que le code de sortie.
        subtype = record.get('subtype')
        return None, subtype if isinstance(subtype, str) and subtype.startswith('error') else f'code {done.returncode}'
    return result.strip(), None


def _run_files(root, task_id):
    run = Path(root) / '.workspace' / 'run' / 'delegate' / task_id
    return [_section(name, _evidence(run / name) if name == 'verify.txt' else _tail(run / name)) for name in RUN_FILES]


def diagnose(root, item, why, log):
    """Diagnostic Sonnet d'un premier échec ; (chemin du diagnostic relatif à root, None, cause de sa ligne `CAUSE:`),
    ou (chemin, raison, exécutant) quand le modèle échoue mais que des tests rouges sont relevés (T119 : la reprise les
    reçoit tels quels), ou (None, raison, exécutant)."""
    task_id = item['id']
    parts = [DIAGNOSIS_PROMPT, _section('Échec relevé par le programme', why), _section('Fiche', _tail(Path(root) / item['fiche']))]
    parts += _run_files(root, task_id) + [_section("Journal de l'étape", _evidence(log))]
    text, error = ask(root, '\n\n'.join(parts), ('reprise', DIAGNOSIS_MODEL, task_id))
    if text is not None:
        body = "Diagnostic de l'échec précédent (programme d'orchestration, Sonnet, lecture seule) :\n\n" + text
        return journal.write_note(root, f'diagnostic-{task_id}.md', body), None, triage.diagnosis_cause(text)
    run = Path(root) / '.workspace' / 'run' / 'delegate' / task_id
    blocks = [block for block in (_evidence(log), _evidence(run / 'verify.txt')) if block.startswith(('FAIL', 'ERROR'))]
    if not blocks:
        return None, error, triage.EXECUTOR
    body = f"Diagnostic du modèle impossible ({error}) ; tests rouges de l'échec précédent, relevés par le programme et donnés tels quels :"
    return journal.write_note(root, f'diagnostic-{task_id}.md', body + '\n\n```\n' + blocks[0] + '\n```'), error, triage.EXECUTOR


def new_files(root, clone, diff):
    """Contenu entier des fichiers neufs du diff, lu dans le clone et masqué comme le circuit, sous les plafonds."""
    blocks, used = [], 0
    for name in NEW_FILE.findall(diff):
        try:
            path = journal.inside(clone, name)
        except ValueError:
            continue
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        room = min(NEW_FILE_LIMIT, NEW_FILES_LIMIT - used)
        if room <= 0:
            blocks.append(f'### {name}\n\n(non donné : plafond de {NEW_FILES_LIMIT} caractères atteint, lire le fichier)')
            continue
        used += min(len(text), room)
        cut = '' if len(text) <= room else f'\n[… tronqué à {room} caractères sur {len(text)} : lire la suite dans le fichier]'
        blocks.append(f'### {name}\n\n```\n{text[:room]}\n```{cut}')
    return journal.mask(root, '\n\n'.join(blocks)) or ''


def primitives(root):
    """Primitives du circuit présentes dans le projet, une ligne chacune."""
    lines = []
    for name, role in PRIMITIVES:
        found = [f'{folder}/{name}' for folder in LIB_DIRS if (Path(root) / folder / name).is_file()]
        if found:
            lines.append(f'- `{found[0]}` : {role}')
    return '\n'.join(lines)


def review(root, item):
    """Relecture Opus : ('bon', None, None), ('à corriger', chemin des corrections, None) ou (None, None, raison)."""
    task_id = item['id']
    run = Path(root) / '.workspace' / 'run' / 'delegate' / task_id
    # T101 : la relecture lit les fichiers du clone de la tâche, pas ceux de la base dans le dépôt principal.
    clone = steps.task_clone(root, task_id)
    diff = _tail(run / 'diff.txt', 4 * TAIL)
    parts = [REVIEW_PROMPT, _section('Fiche', _tail(Path(root) / item['fiche'])), _section('Diff', diff)]
    parts += [_section('Fichiers neufs, contenu entier', new_files(root, clone, diff))]
    parts += [_section('Primitives du circuit à réutiliser', primitives(root))]
    parts += [_section(name, _tail(run / name)) for name in ('status.json', 'verify.txt', 'acceptance.txt')]
    text, error = ask(root, '\n\n'.join(parts), ('relecture', REVIEW_MODEL, task_id), clone if clone.is_dir() else root)
    return _verdict(root, task_id, text, error)


def recheck(root, item, fixes):
    """Seconde relecture après une ronde de correction (T113) : les corrections demandées (`fixes`, relatif à root) et
    le diff de la ronde seule (`diff.txt` de la dernière délégation) ; même retour que `review`."""
    task_id = item['id']
    run = Path(root) / '.workspace' / 'run' / 'delegate' / task_id
    clone = steps.task_clone(root, task_id)
    parts = [RECHECK_PROMPT, _section('Corrections demandées', _tail(Path(root) / fixes))]
    parts += [
        _section('Diff de la ronde de correction', _tail(run / 'diff.txt', 4 * TAIL)),
        _section('verify.txt', _tail(run / 'verify.txt')),
    ]
    text, error = ask(root, '\n\n'.join(parts), ('relecture', REVIEW_MODEL, task_id), clone if clone.is_dir() else root)
    return _verdict(root, task_id, text, error)


def _verdict(root, task_id, text, error):
    if text is None:
        return None, None, error
    first = text.splitlines()[0].strip()
    if first == VERDICT_GOOD:
        return 'bon', None, None
    if first != VERDICT_FIX:
        return None, None, 'verdict illisible : ' + first[:80]
    body = 'Corrections demandées par la relecture ciblée (Opus, lecture seule) :\n\n' + '\n'.join(text.splitlines()[1:]).strip()
    return 'à corriger', journal.write_note(root, f'relecture-{task_id}.md', body), None
