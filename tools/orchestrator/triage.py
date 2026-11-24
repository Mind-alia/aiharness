"""Classement d'une vérification rouge sur l'hôte (T117) : exécutant, environnement ou fiche.

Nuit du 3/11 : deux fiches finies et vertes ont été déclarées en échec parce que leur première erreur venait de
l'environnement (variable d'image héritée, nom de l'interpréteur dans le conteneur), une troisième parce que sa fiche
contredisait un test existant hors de son périmètre. Chaque fois, une tentative de l'exécutant a été comptée. Le
programme rejoue donc la même vérification sur la base de la tâche, sans son travail, et compare les tests rouges :

- environnement : tout test rouge de la tâche l'est aussi sur la base (ou la base échoue sans test nommé) ; rien n'est
  compté, le plan s'arrête, la même vérification est relancée telle quelle une fois l'environnement corrigé ;
- fiche : les tests rouges neufs sont tous dans des fichiers de test existants que la fiche ne permet pas de modifier
  et que l'exécutant n'a pas touchés (hors suite de qualité, qui juge le code de la tâche) : la fiche contredit un test
  figé, ou l'exécutant a cassé un test qu'il ne pouvait pas corriger ; rien n'est compté, arrêt et question au propriétaire ;
- exécutant : tout le reste ; l'échec compte, comme avant.

Le diagnostic Sonnet d'un premier échec (règle 14) finit aussi par une catégorie, sa ligne `CAUSE:` : étude E15 (12/11),
le diagnostic mettait le circuit en cause, la reprise a refait le même échec (environ 0,70 $ perdus). Une cause autre
que l'exécutant arrête donc la fiche comme ci-dessus, sans reprise ni tentative comptée.
"""

import re

import journal
import notices

ENVIRONMENT, FICHE, EXECUTOR = 'environnement', 'fiche', 'exécutant'
CIRCUIT, DIAGNOSIS = 'circuit', 'diagnostic'
# Dernière ligne `CAUSE: <valeur>` du diagnostic (backticks ou gras tolérés autour) ; toute autre valeur vaut exécutant.
CAUSE_LINE = re.compile(r'^[`*\s]*CAUSE\b(.*)$', re.M)
CAUSE_VALUE = re.compile(r'^\s*:\s*([^\s`*.]+)[`*.\s]*$')
CAUSES = {'executant': EXECUTOR, 'exécutant': EXECUTOR, 'fiche': FICHE, 'environnement': ENVIRONMENT, 'circuit': CIRCUIT}
CAUSE_TEXT = {
    CIRCUIT: 'le circuit lui-même (scripts, clone de tâche, conteneur, sceau, délégation)',
    FICHE: 'la fiche (consigne, périmètre ou tests qui se contredisent)',
    ENVIRONMENT: "l'environnement de la vérification (outil, interpréteur, image, variable)",
}
# Blocs `FAIL: nom (identifiant)` de unittest, et lignes `FAILED chemin::nom` de pytest.
UNITTEST = re.compile(r'^(?:FAIL|ERROR): (\S+)(?: \(([^)\s]+)\))?', re.M)
PYTEST = re.compile(r'^(?:FAILED|ERROR) (\S+?\.py)::(\S+)', re.M)
# Tests qui jugent le code de la tâche, pas un comportement figé : une erreur y reste celle de l'exécutant.
RULE_FILES = ('tests/test_code_quality.py', 'tests/test_code_structure.py', 'tests/test_self_hosting.py')
LIST_LIMIT = 12


def red_tests(text):
    """Identifiants des tests rouges d'une sortie unittest ou pytest (`chemin::nom` pour pytest)."""
    found = {match.group(2) or match.group(1) for match in UNITTEST.finditer(text or '')}
    found |= {f'{match.group(1)}::{match.group(2)}' for match in PYTEST.finditer(text or '')}
    return found


def test_file(root, test_id):
    """Fichier de test d'un identifiant (`tests.test_x.Classe.test_y` ou `tests/test_x.py::test_y`), relatif à root ;
    None s'il ne correspond à aucun fichier du projet (module introuvable, test chargé par unittest.loader…)."""
    if '::' in test_id:
        candidates = [test_id.split('::', 1)[0]]
    else:
        parts = test_id.split('.')
        candidates = ['/'.join(parts[:end]) + '.py' for end in range(len(parts), 0, -1)]
    for name in candidates:
        try:
            if journal.inside(root, name).is_file():
                return name
        except ValueError:
            continue
    return None


def allowed_patterns(root, fiche):
    """Motifs `allowed` de la fiche, lus par le module `scope` du circuit ; None si la fiche ou le module manque."""
    scope = journal.circuit_module(root, 'scope')
    if scope is None:
        return None
    try:
        return list(scope.minimal_task(str(journal.inside(root, fiche))).get('allowed') or [])
    except (OSError, ValueError, SyntaxError):
        return None


def _in_scope(root, name, patterns):
    scope = journal.circuit_module(root, 'scope')
    return scope is not None and any(scope.matches(name, pattern) for pattern in patterns)


def classify(root, task, base):
    """Catégorie d'une vérification rouge et ses preuves.

    task = (sortie de la vérification de la tâche, fiche, fichiers changés) ; base = (code, tests rouges) de la même
    vérification sur la base, ou None si elle n'a pas pu tourner. Rend (catégorie, détail) ; détail : `red`, `base_red`,
    `new` (listes triées) et `frozen` (fichiers de test hors périmètre)."""
    output, fiche, changed = task
    red = red_tests(output)
    detail = {'red': sorted(red), 'base_red': [], 'new': sorted(red), 'frozen': []}
    if base is None:
        return EXECUTOR, detail
    base_code, base_red = base[0], set(base[1])
    new = red - base_red
    detail.update(base_red=sorted(base_red), new=sorted(new))
    if base_code != 0 and not new:
        return ENVIRONMENT, detail
    patterns = allowed_patterns(root, fiche)
    if not new or patterns is None:
        return EXECUTOR, detail
    found = [test_file(root, test_id) for test_id in new]
    files = {name for name in found if name is not None}
    touched = set(changed or [])
    if len(files) < len(set(found)) or files & set(RULE_FILES) or files & touched or any(_in_scope(root, n, patterns) for n in files):
        return EXECUTOR, detail
    detail['frozen'] = sorted(files)
    return FICHE, detail


def summary(detail):
    """Une ligne lisible du détail, bornée, pour le journal des décisions et l'avis au propriétaire."""

    def listing(names):
        shown = ', '.join(f'`{name}`' for name in names[:LIST_LIMIT])
        return (shown + (f' (+{len(names) - LIST_LIMIT})' if len(names) > LIST_LIMIT else '')) or 'aucun'

    parts = [f"rouges sur la tâche : {listing(detail['red'])}", f"rouges sur la base : {listing(detail['base_red'])}"]
    if detail.get('frozen'):
        parts.append(f"fichiers de test hors périmètre : {listing(detail['frozen'])}")
    return ' ; '.join(parts)


def notice(root, task_id, category, detail, fiche):
    """Avis `ENVIRONNEMENT-<id>.md` ou `FICHE-<id>.md` : ce qui est rouge, pourquoi rien n'est compté, que faire."""
    if category == ENVIRONMENT:
        name, title = f'ENVIRONNEMENT-{task_id}.md', f"Vérification rouge sur la base aussi : {task_id}"
        action = [
            "Les tests rouges de la tâche le sont aussi sur sa base, sans son travail : l'environnement de la vérification",
            "(outil, interpréteur, image, variable) est en cause, pas l'exécutant. Aucune tentative n'est comptée.",
            "Corriger l'environnement, puis relancer la même commande : la vérification est rejouée telle quelle, sans",
            'nouvelle délégation.',
        ]
    else:
        name, title = f'FICHE-{task_id}.md', f'Test existant hors périmètre rouge : {task_id}'
        action = [
            'Les tests rouges neufs sont tous dans des fichiers de test existants que la fiche ne permet pas de modifier.',
            "Soit la fiche contredit un test figé (trou de la fiche : test à mettre dans `allowed`, règle du 23/10), soit",
            "l'exécutant a cassé un test qu'il ne pouvait pas corriger. Aucune tentative n'est comptée : à trancher.",
            f'Corriger la fiche `{fiche}` exige `close --abandoned` puis `prepare --retry` (la fiche est scellée) ;',
            'si l\'exécutant est en cause, relancer la même commande après avoir répondu dans `docs/QUESTIONS.md`.',
        ]
    lines = [f'# {title}', '', f'- {summary(detail)}', f'- Journaux : `.workspace/orchestrator-run/{task_id}-verify.log` (tâche),']
    lines += [f'  `.workspace/orchestrator-run/{task_id}-verify_base.log` (base)']
    if detail.get('container'):
        lines += [f'  `.workspace/orchestrator-run/{task_id}-verify_base_container.log` (base, dans le conteneur)']
    lines += ['']
    return journal.write_note(root, name, '\n'.join(lines + action))


def diagnosis_cause(text):
    """Cause nommée par la dernière ligne `CAUSE:` d'un diagnostic ; ligne absente, mal formée ou valeur inconnue :
    `exécutant`, le comportement d'avant (la tâche est reprise une fois)."""
    found = CAUSE_LINE.findall(text or '')
    value = CAUSE_VALUE.match(found[-1]) if found else None
    return CAUSES.get(value.group(1).lower(), EXECUTOR) if value else EXECUTOR


def diagnosis_notice(root, task_id, cause, diagnosis):
    """Avis `CIRCUIT-<id>.md` (cause circuit) ou `DIAGNOSTIC-<id>.md` (fiche, environnement) : ce que conclut le
    diagnostic, où le lire, pourquoi rien n'est compté, que faire."""
    name = '{}-{}.md'.format('CIRCUIT' if cause == CIRCUIT else 'DIAGNOSTIC', task_id)
    lines = [
        f"# Diagnostic : l'exécutant n'est pas en cause : {task_id}",
        '',
        f'- Cause conclue par le diagnostic (ligne `CAUSE: {cause}`) : {CAUSE_TEXT[cause]}.',
        f'- Diagnostic complet (Sonnet, lecture seule) : `{diagnosis}`',
        '',
        "Une reprise referait le même échec (étude E15, 12/11 : environ 0,70 $ perdus) : le programme n'a pas repris la",
        "tâche et n'a compté aucune tentative ; son clone et son travail sont gardés.",
        "Lire le diagnostic, corriger la cause, puis relancer la même commande : l'étape en échec est rejouée sur le même",
        'clone, avec le diagnostic en contexte.',
        f'Si la fiche doit changer, ou pour repartir de zéro : `bash scripts/task-workspace.sh close {task_id} --abandoned`,',
        'corriger la fiche si besoin, puis relancer la même commande (`prepare --retry`).',
    ]
    return journal.write_note(root, name, '\n'.join(lines))


def stop_on_diagnosis(runner, item, entry, cause):
    """Crochet de `Runner.failure` quand le diagnostic nomme une autre cause que l'exécutant : l'échec n'est pas compté,
    aucune reprise ; avis, ligne dans `docs/QUESTIONS.md`, et le plan attend le propriétaire comme pour l'environnement ou la fiche."""
    entry['failures'] -= 1
    notice = diagnosis_notice(runner.root, item['id'], cause, entry['stage_context'])
    notices.blocked(runner.root, item['id'], DIAGNOSIS, notice)
    return runner.wait_for_owner(item['id'], entry, DIAGNOSIS, notice)
