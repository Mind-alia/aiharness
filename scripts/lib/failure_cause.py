"""Cause of a failure, from a closed enumeration, set only when the program knows it for sure.

Python 3.14, standard library only. `unknown` stands for any failure whose cause is not established.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import events

CAUSES = ('quota', 'infra', 'test_invalid', 'scope', 'implementation', 'unknown')
NO_CAUSE = ('done', 'done_with_warning', 'waiting_answer', 'running')
UNKNOWN = 'unknown'
SCOPE_OUTCOMES = ('out_of_scope', 'control_tampered')
IMPLEMENTATION_OUTCOMES = ('verify_failed', 'no_implementation')
INFRA_OUTCOMES = ('isolation_unavailable', 'permission_denied', 'interrupted')
ACCEPTANCE_PATH = '.workspace/run/delegate/%s/acceptance.txt'
HIDDEN_TEST_SYNTAX_ERROR = re.compile(
    r'File "[^"\n]*test_acceptance[^"\n]*\.py", line \d+\n(?:[^\n]*\n){0,3}?\s*(?:SyntaxError|IndentationError|TabError)\b'
)


def _journal(root, task_id):
    try:
        return events.read(root, task_id=task_id)
    except Exception:
        return None


def _quota_after_last_start(journal) -> bool:
    started = [i for i, entry in enumerate(journal) if entry.get('event') == 'delegate_started']
    first = started[-1] + 1 if started else 0
    return any(entry.get('event') == 'quota' for entry in journal[first:])


def _acceptance_cause(root, task_id) -> str:
    path = Path(root) / (ACCEPTANCE_PATH % task_id)
    try:
        if path.is_symlink() or not path.is_file():
            return 'implementation'
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return 'implementation'
    return 'test_invalid' if HIDDEN_TEST_SYNTAX_ERROR.search(text) else 'implementation'


def cause_of(root, task_id, outcome) -> str | None:
    if not isinstance(outcome, str) or outcome in NO_CAUSE:
        return None
    if outcome == 'quota':
        return 'quota'
    journal = _journal(root, task_id)
    if journal is not None and _quota_after_last_start(journal):
        return 'quota'
    if outcome in SCOPE_OUTCOMES:
        return 'scope'
    if outcome == 'acceptance_vacuous':
        return 'test_invalid'
    if outcome == 'acceptance_failed':
        return _acceptance_cause(root, task_id)
    if outcome in IMPLEMENTATION_OUTCOMES:
        return 'implementation'
    if outcome in INFRA_OUTCOMES:
        return 'infra'
    return UNKNOWN


def closing_cause(root, task_id) -> str:
    journal = _journal(root, task_id) or []
    outcomes = [entry for entry in journal if entry.get('event') == 'outcome']
    if not outcomes:
        return UNKNOWN
    detail = outcomes[-1].get('detail')
    detail = detail if isinstance(detail, dict) else {}
    if detail.get('cause') in CAUSES:
        return detail['cause']
    return cause_of(root, task_id, detail.get('outcome')) or UNKNOWN
