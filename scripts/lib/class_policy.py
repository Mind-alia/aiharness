"""Execution policy derived from the class of a task sheet (small, medium, large).

Python 3.14, standard library only. The policy applies only to a class written in
the sheet header; a computed class is recorded but changes nothing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import plan_check  # noqa: E402

BREAKER_MODE = {'small': 'none', 'medium': 'instrument', 'large': 'blocking'}
SMALL_MAX_ATTEMPTS = 2
LARGE_EXECUTOR = 'claude-opus'


def task_class(task, root) -> str:
    declared = task.get('class')
    if declared is not None:
        return declared
    return plan_check.classify(root, task)[0]


def policy(task, root) -> dict:
    declared = task.get('class')
    if declared is None:
        return dict(**{'class': task_class(task, root)}, declared=False, attempts=task['attempts'], breaker='blocking', refusal=None)
    if declared not in BREAKER_MODE:
        raise ValueError('class doit être small, medium ou large')
    attempts = task['attempts']
    refusal = None
    if declared == 'small':
        attempts = min(attempts, SMALL_MAX_ATTEMPTS)
    elif declared == 'large' and task['agent'] != LARGE_EXECUTOR:
        refusal = 'fiche grande : seul Opus (%s) peut l\'exécuter, pas %s ; découper la fiche en fiches moyennes ou petites.' % (
            LARGE_EXECUTOR,
            task['agent'],
        )
    return dict(**{'class': declared}, declared=True, attempts=attempts, breaker=BREAKER_MODE[declared], refusal=refusal)
