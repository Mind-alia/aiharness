"""Phase lisible d'une tâche, écrite dans son état (événement `state`, vue `state.json`) (Python 3.14, stdlib seule).

La phase tient en un mot pour le hub des usines. Elle n'est jamais une condition
du circuit : une écriture qui échoue écrit `task_phase: ...` sur stderr et rend la main.
Sans `state.json` (délégation sur l'hôte sans `prepare`), rien n'est créé.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import safety
import state_view

sys.dont_write_bytecode = True

PHASES = ('prepared', 'delegated', 'verifying', 'acceptance', 'merging', 'done', 'failed', 'abandoned')
ATTEMPT_PHASE = re.compile(r'attempt [1-9][0-9]*')


def _warn(error):
    print('task_phase: ' + str(error).replace('\n', ' '), file=sys.stderr)


def set_phase(root, task_id, phase):
    """Écrit `phase` dans l'état de la tâche s'il existe. `ValueError` si `phase` est inconnue."""
    if not (phase in PHASES or (isinstance(phase, str) and ATTEMPT_PHASE.fullmatch(phase))):
        raise ValueError('phase inconnue : %r' % (phase,))
    root = Path(root)
    state_file = root / '.workspace/orchestration/tasks' / task_id / 'state.json'
    try:
        if not state_file.exists() and not state_file.is_symlink():
            return
        safety.check_no_symlinks(state_file, root)
        state = json.loads(state_file.read_text(encoding='utf-8'))
        if not isinstance(state, dict):
            raise ValueError('état illisible : objet JSON attendu')
        state['phase'] = phase
        state_view.write(root, task_id, state, 'phase')
    except (OSError, ValueError) as error:
        _warn(error)
