"""Rejeu de la vérification d'une délégation sur la base de la tâche, dans un conteneur (T123).

Le 3/11, `gt2-delegate-interrupt` a perdu sa première tentative sur un `verify_failed` rouge sur la base aussi (`python3`
contre `python3.14` dans le conteneur) ; le 8/11, une fusion a été refusée parce que l'image n'avait pas `git`. La vérification
de la délégation tourne dans le conteneur : le programme la rejoue donc dans un conteneur monté sur un clone jetable de la
base (`sandbox.sh start --base`), sans le travail de l'exécutant, et classe avec `triage.classify`. Seul le classement
`environnement` est retenu : rien n'est compté, le travail est exporté, le clone fermé, et la fiche repart de zéro à la relance.
Le clone de la base ne survit jamais au rejeu.
"""

import shutil

import journal
import notices
import quality
import steps
import triage

BASE_DIR = '.workspace/orchestration/tasks/{task_id}/base'
VERIFY_TXT = '.workspace/run/delegate/{task_id}/verify.txt'
STEP = 'verify_base_container'
SANDBOX = ('bash', 'scripts/sandbox.sh')
EMPTY = {'red': [], 'base_red': [], 'new': [], 'frozen': []}


def base_dir(root, task_id):
    """Dossier absolu (sous root) du clone de la base ; ValueError s'il passe par un lien symbolique."""
    return journal.inside(root, BASE_DIR.format(task_id=task_id))


def _sandbox(root, log, env, started, *args):
    return steps.spawn([*SANDBOX, *args], log, env, root, started)


def _checkout(root, target, sha, log):
    """Clone jetable de root à `sha` dans `target` ; faux (noté dans `log`) si git échoue."""
    for cwd, args in (
        (root, ('clone', '-q', '--no-hardlinks', '--no-checkout', str(root), str(target))),
        (target, ('checkout', '-q', '--detach', sha)),
    ):
        done = steps.git(cwd, *args)
        if done.returncode != 0:
            steps._note(log, 'git {} : {}'.format(args[0], done.stderr.decode('utf-8', 'replace').strip()))
            return False
    return True


def _in_container(root, task_id, command, log, run):
    """(code, sortie) de `command` dans le conteneur de la base ; None si le conteneur ne démarre pas. `run` lance sandbox.sh."""
    if run('start', task_id, '--base') != 0:
        return None
    try:
        offset = quality.size(log)
        code = run('exec', task_id, '--', 'sh', '-c', command)
        return code, quality.read_from(log, offset)
    finally:
        run('stop', task_id)


def replay(root, task_id, verify_command, log, started, env=None):  # noqa: PLR0913
    """(code, sortie) de la vérification de la fiche dans un conteneur monté sur la base de la tâche ; None si la base est
    inconnue, ne se copie pas ou si le conteneur ne démarre pas. Le dossier de la base est toujours supprimé."""
    sha = steps.base_commit(root, task_id)
    if sha is None:
        return None
    env = steps.circuit_env() if env is None else env
    try:
        target = base_dir(root, task_id)
    except ValueError as error:
        steps._note(log, f'base : {error}')
        return None
    shutil.rmtree(target, ignore_errors=True)
    try:
        if not _checkout(root, target, sha, log):
            return None
        return _in_container(root, task_id, verify_command, log, lambda *args: _sandbox(root, log, env, started, *args))
    except OSError as error:
        steps._note(log, f'sandbox.sh : {error}')
        return None
    finally:
        shutil.rmtree(target, ignore_errors=True)


def _verify_text(root, task_id):
    """Sortie de la vérification de la délégation (`verify.txt`) ; vide si absente ou illisible."""
    try:
        return journal.inside(root, VERIFY_TXT.format(task_id=task_id)).read_text(encoding='utf-8', errors='replace')
    except (OSError, ValueError):
        return ''


def category(root, task_id, fiche, verify_command, log, started, env=None):  # noqa: PLR0913
    """(catégorie, détail) d'un `verify_failed` de délégation : `triage.ENVIRONMENT` si les tests rouges de la tâche le sont
    aussi sur la base dans le conteneur, `triage.EXECUTOR` sinon (la catégorie « fiche » reste celle de la vérification hôte).
    Sans `verify.txt`, aucun conteneur n'est lancé."""
    output = _verify_text(root, task_id)
    if not output.strip():
        return triage.EXECUTOR, dict(EMPTY)
    result = replay(root, task_id, verify_command, log, started, env)
    base = None if result is None else (result[0], triage.red_tests(result[1]))
    changed = (steps.delegate_status(root, task_id) or {}).get('files_changed')
    found, detail = triage.classify(root, (output, fiche, changed), base)
    detail['container'] = True
    # Base en échec sans aucun test nommé : l'outil ou l'image a cassé avant les tests, la tâche n'y est pour rien.
    if base is not None and base[0] != 0 and not base[1]:
        found = triage.ENVIRONMENT
    return (found if found == triage.ENVIRONMENT else triage.EXECUTOR), detail


def verify_command(root, fiche):
    """Champ `verify:` de l'en-tête de la fiche ; None s'il manque. Lu ligne à ligne : l'en-tête n'est pas validé ici."""
    scope = journal.circuit_module(root, 'scope')
    try:
        lines = journal.inside(root, fiche).read_text(encoding='utf-8').splitlines()
    except (OSError, ValueError):
        return None
    if scope is None or not lines or lines[0].strip() != '---':
        return None
    for line in lines[1:]:
        if line.strip() == '---':
            break
        key, sep, value = line.partition(':')
        if sep and key == 'verify':
            try:
                return scope.scalar(value) or None
            except (SyntaxError, ValueError):
                return None
    return None


def guard(runner, item, entry):
    """Crochet de `Runner.delegation_result` pour un `verify_failed` : None pour continuer comme avant (`failure`), sinon
    l'issue de la fiche quand la vérification est rouge sur la base aussi (environnement) : rien n'est compté, le travail
    est exporté, le clone fermé, la fiche remise en file, et le plan attend le propriétaire (avis `ENVIRONNEMENT-<id>.md`)."""
    task_id, fiche, root = item['id'], item['fiche'], runner.root
    command = verify_command(root, fiche)
    if command is None:
        return None
    env = steps.circuit_env(runner.plan['image'])
    found, detail = runner.track(task_id, STEP, lambda log, started: category(root, task_id, fiche, command, log, started, env))
    if found != triage.ENVIRONMENT:
        return None
    entry['verify_category'] = triage.ENVIRONMENT
    runner.decide(f'fiche {task_id} : délégation verify_failed, catégorie {found} ({triage.summary(detail)})')
    entry['reason'] = 'délégation : verify_failed, rouge sur la base aussi (environnement)'
    if _export(runner, task_id, entry):
        outcome = runner.abandon(item, entry)
        if outcome != 'retry':
            return outcome
    notice = triage.notice(root, task_id, found, detail, fiche)
    notices.blocked(root, task_id, found, notice)
    return runner.wait_for_owner(task_id, entry, found, notice)


def _export(runner, task_id, entry):
    """Exporte le diff complet du travail avant la fermeture du clone ; faux (clone gardé) si l'export est impossible."""
    runner.at(task_id, 'export')
    try:
        entry['patch'] = steps.export_patch(runner.root, task_id, journal.logs(runner.root) / f'{task_id}-export.log')
    except (OSError, ValueError) as error:
        runner.decide(f'fiche {task_id} : diff non exporté ({error}), clone gardé')
        return False
    return True
