"""Reprise d'un passage du programme d'orchestration (règles 3 et 8) : journal à reprendre, plan déjà terminé, passage
tenu par un autre processus, plan modifié pendant un passage. Sorti de `run.py`, qui reste sous les 800 lignes.
"""

import os
import sys

import journal
import steps

# Mêmes codes que run.py : verrou ou passage tenu, plan à reprendre avec son texte d'origine.
EXIT_HELD, EXIT_USAGE = 3, 64


def previous(root, plan):
    """Règle 3 : (journal à reprendre ou None, code de sortie ou None)."""
    old = journal.load(root)
    if old is None:
        return None, None
    same = old.get('plan') == plan['path']
    if old.get('status') == 'finished':
        if same and old.get('plan_sha256') == plan['sha256']:
            print('plan déjà terminé ({}) : rien à faire'.format(old.get('finished_at')))
            return None, 0
        journal.archive(root, old)
        return None, None
    if not same:
        print('un autre plan est en cours dans ce projet : {}'.format(old.get('plan')), file=sys.stderr)
        return None, EXIT_HELD
    if old.get('plan_sha256') != plan['sha256']:
        print('plan modifié pendant un passage : relancer avec le plan d\'origine', file=sys.stderr)
        return None, EXIT_USAGE
    pid = old.get('pid')
    if pid != os.getpid() and steps.alive(pid) and 'run.py' in steps.command_of(pid):
        print(f'déjà en cours (pid {pid})', file=sys.stderr)
        return None, EXIT_HELD
    return old, None


def resumed(old, waiting):
    """Journal d'un passage interrompu ou arrêté, remis en route ; les étapes de fin seront rejouées (et la fin d'un
    arrêt sur `quota` ou sur une raison de `waiting`, qui n'avait écrit que le rapport)."""
    where = old.get('current') or {}
    old['decisions'].append(
        'reprise après interruption (pid {} absent) à l\'étape {} de {}'.format(
            old.get('pid'), where.get('step') or 'départ', where.get('task') or 'plan'
        )
    )
    if old.get('stop_reason') in ('quota',) + tuple(waiting):
        old['final'] = {}
    old.update(pid=os.getpid(), status='running', stop_reason=None, resumes=old.get('resumes', 0) + 1)
    return old
