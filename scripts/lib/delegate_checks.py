"""Checks run after each attempt of the executor (part of delegate.py).

judge() runs them in order: scope, the verify command, the hidden tests, the breaker instruments, then the scope
again. Every step first checks that the sealed controls are intact. A failure the executor can fix goes back
into the prompt for the next attempt, unless a question waits for the owner's answer. correction_step() is the
referee of a correction round.

The delegation `d` is the namespace built by delegate.py: root, task, run, contract, seals, isolation, work_dir,
status, prompt, followup, breaker_run, correction_round, base_commit, baseline, initial, started,
study_force_fail, and the callables execute, mask (masks the known secrets) and known (the known secret values).
"""

import correction
import events
import questions
import safety
import task_phase
import workdir
from breaker import has_acceptance, instrument_names, run_acceptance, run_instruments
from delegate_prompt import ACCEPTANCE_FAILURE, VERIFY_FAILURE
from delegate_run import bounded, build_diff, changed, snapshot
from scope import fingerprint
from task import run_acceptance as original_acceptance

# What the attempt loop does next.
RETRY, END, EXIT = 'retry', 'end', 'exit'
DONE = ('done', 'done_with_warning')
ACCEPTANCE_OUTPUT_LIMIT = 200000
ACCEPTANCE_FULL_FILE = 'acceptance-full.txt'
STDERR_SEPARATOR = '--- stderr ---'
OMITTED_LINE = '[… %d caractères omis …]'
SUMMARY_PREFIX = 'résumé : '
NO_SUMMARY = 'sans résumé (code %s)'
BREAKER_SUFFIX = ' ; casseur : échec'
STUDY_FORCED_MESSAGE = 'Échec forcé (étude de reprise) : relance ta vérification ciblée et confirme ton travail.'


def intact(d):
    """True while every sealed control is unchanged and, in exchange mode, the clone's git config is safe."""
    # Exchange: the clone's git config is checked first, before any other command touches the clone.
    if d.isolation == 'exchange':
        unsafe = workdir.unsafe_clone_config(d.work_dir)
        if unsafe:
            warning_text = 'configuration git du clone refusée : ' + ', '.join(unsafe)
            if warning_text not in d.status['warnings']:
                d.status['warnings'].append(warning_text)
            return False
    try:
        return all(not p.is_symlink() and fingerprint(p) == seal for p, seal in d.seals.items())
    except (OSError, ValueError):
        return False


def tampered(d):
    d.status['outcome'] = 'control_tampered'
    return END


def waiting_answer(d):
    """Turn a failure into waiting_answer when the executor asked a question nobody has answered yet."""
    if d.status['outcome'] in ('failed', 'verify_failed', 'acceptance_failed') and intact(d):
        worktree = d.work_dir if d.isolation == 'exchange' else None
        pending = questions.unanswered(d.root, d.task['id'], since=d.started, worktree=worktree)
        if pending:
            d.status.update(outcome='waiting_answer', waiting_questions=pending)
            return True
    return False


def retry_with(d, followup):
    """Hand the failure back to the executor on the next attempt, unless a question waits for an answer."""
    d.followup = followup
    d.prompt += '\n\n' + followup
    return END if waiting_answer(d) else RETRY


def scope_args(d):
    args = ['bash', str(d.root / 'scripts/check-scope.sh'), str(d.contract)]
    if d.isolation == 'exchange':
        args += ['--baseline', str(d.run / 'baseline.json')]
    return args


def scope_step(d):
    """Refuse an attempt that touched files outside the allowed scope."""
    exit_code, out, err = d.execute(scope_args(d), cwd=d.work_dir)
    (d.run / 'scope.txt').write_text(d.mask(bounded(out + err)), encoding='utf-8')
    if not intact(d):
        return tampered(d)
    if exit_code:
        d.status['outcome'] = 'out_of_scope'
        return END
    return None


def verify_step(d, attempt, relay_ok):
    """Run the sheet's verify command; its output goes back to the executor, who cannot run the profile checks."""
    task, status = d.task, d.status
    verify = d.root / 'scripts/verify-task.sh'
    task_phase.set_phase(d.root, task['id'], 'verifying')
    exit_code, out, err = d.execute(['bash', str(verify), str(d.contract)], cwd=d.work_dir)
    if d.study_force_fail and attempt == 1 and task['attempts'] >= 2:
        out = STUDY_FORCED_MESSAGE + '\n' + out
        exit_code = exit_code or 1
        status['study']['forced_first_failure'] = True
    (d.run / 'verify.txt').write_text(d.mask(bounded(out + err)), encoding='utf-8')
    if not intact(d):
        return tampered(d)
    status['verify'] = {'command': task.get('verify') or str(verify), 'exit': exit_code}
    events.record(d.root, task['id'], 'verify', {'exit': exit_code})
    if not exit_code:
        return None
    status['outcome'] = 'verify_failed'
    if relay_ok and attempt < task['attempts'] and not status.get('refused_files'):
        status['outcome'] = 'failed'
        return retry_with(d, VERIFY_FAILURE + out + err)
    return None


def checked_so_far(d):
    """The attempt passed so far, and this run is not the breaker's own run (its tests are not ours)."""
    return d.status['outcome'] in DONE and not d.breaker_run


def capturing(execute, command, captured):
    """Wrap execute: keep the result of the call whose last argument is the hidden tests command."""

    def wrapper(args, *rest, **kwargs):
        result = execute(args, *rest, **kwargs)
        if args and args[-1] == command:
            captured.append(result)
        return result

    return wrapper


def full_output(out, err):
    """The command's stdout then stderr, split by the stderr separator line."""
    if out and not out.endswith('\n'):
        out += '\n'
    return out + STDERR_SEPARATOR + '\n' + err


def summarize(text, code):
    """Last `Ran ` line followed by the last `OK`/`FAILED` line; no invented figure when there is none."""
    lines = text.splitlines()
    ran = [line for line in lines if line.startswith('Ran ')]
    if not ran:
        return NO_SUMMARY % code
    verdict = [line for line in lines if line.startswith(('OK', 'FAILED'))]
    return ' / '.join([ran[-1]] + verdict[-1:])


def record_proof(d, captured, exit_code, feedback):
    """Keep the masked full output and the one-line summary; return the summary."""
    ran_code, out, err = captured[-1] if captured else (None, '', '')
    text = d.mask(full_output(out, err))
    summary = summarize(text, exit_code if ran_code is None else ran_code)
    if ran_code == 0 and exit_code:
        summary += BREAKER_SUFFIX
    if len(text) > ACCEPTANCE_OUTPUT_LIMIT:
        omitted = len(text) - ACCEPTANCE_OUTPUT_LIMIT
        text = OMITTED_LINE % omitted + '\n' + text[-ACCEPTANCE_OUTPUT_LIMIT:]
    safety.write_file_secure(d.run / ACCEPTANCE_FULL_FILE, text)
    filtered = feedback + '\n' if feedback and not feedback.endswith('\n') else feedback
    (d.run / 'acceptance.txt').write_text(filtered + SUMMARY_PREFIX + summary + '\n', encoding='utf-8')
    d.status['acceptance_summary'] = summary
    return summary


def acceptance_step(d, attempt):
    """Run the hidden tests; their failure output goes back to the executor while attempts remain."""
    task, status = d.task, d.status
    status['acceptance_attempts'] += 1
    task_phase.set_phase(d.root, task['id'], 'acceptance')
    captured = []
    execute = capturing(d.execute, task.get('acceptance_verify'), captured)
    exit_code, feedback = run_acceptance(task, d.root, d.work_dir, execute, d.isolation == 'exchange')
    feedback = d.mask(bounded(feedback))
    summary = record_proof(d, captured, exit_code, feedback)
    events.record(d.root, task['id'], 'acceptance', {'attempt': attempt, 'code': exit_code, 'summary': summary})
    if not exit_code:
        return None
    status['outcome'] = 'acceptance_failed'
    if intact(d) and attempt < task['attempts']:
        return retry_with(d, ACCEPTANCE_FAILURE + feedback)
    return None


def instrument_step(d):
    """Run the breaker's instruments: they are measured and recorded, they never fail the attempt."""
    instruments = run_instruments(d.task, d.root, d.work_dir, d.execute, d.isolation == 'exchange')
    d.status['breaker_instruments'] = instruments
    detail = {'passed': sum(1 for i in instruments if i['passed']), 'failed': [i['test'] for i in instruments if not i['passed']]}
    events.record(d.root, d.task['id'], 'breaker_instrument', detail)


def final_scope(d):
    """Check the scope once more after the hidden tests and the breaker ran in the workshop."""
    if not intact(d):
        tampered(d)
        return
    exit_code, out, err = d.execute(scope_args(d), cwd=d.work_dir)
    (d.run / 'scope.txt').write_text(d.mask(out + err))
    if exit_code:
        d.status['outcome'] = 'out_of_scope'
    if not intact(d):
        tampered(d)


def judge(d, attempt, relay_ok):
    """Check the attempt the executor just finished; return RETRY or END."""
    if (d.root / 'scripts/check-scope.sh').is_file():
        step = scope_step(d)
        if step:
            return step
    if (d.root / 'scripts/verify-task.sh').is_file():
        step = verify_step(d, attempt, relay_ok)
        if step:
            return step
    if d.status.get('refused_files'):
        d.status['outcome'] = 'out_of_scope'
        return END
    if checked_so_far(d) and has_acceptance(d.task, d.root):
        step = acceptance_step(d, attempt)
        if step:
            return step
    if checked_so_far(d) and instrument_names(d.task, d.root):
        instrument_step(d)
    final_scope(d)
    waiting_answer(d)
    return END


def correction_step(d):
    """Referee a correction round: keep its diff as proof, and revert it when the original hidden tests break.

    Return the round's diff, or None when this delegation is not a correction round.
    """
    if not d.correction_round or d.status['outcome'] in ('waiting_answer', 'control_tampered'):
        return None
    round_outcome = d.status['outcome']
    kept = {}

    def run_agent():
        # The attempt loop was the agent; keep what it left as proof before any rollback.
        kept['diff'] = build_diff(changed(d.baseline, snapshot(d.work_dir)), d.work_dir, d.initial, d.baseline, d.known())
        return round_outcome

    def run_original():
        # Only the original hidden tests, in the same container; the breaker tests are left out.
        # A round that only fails the breaker tests keeps its state: only a broken original reverts it.
        return original_acceptance(d.task, d.root, d.work_dir, d.execute, True) if d.task['acceptance'] else (0, '')

    result = correction.run_round(d.root, d.task, d.work_dir, d.base_commit, run_agent, run_original)
    if result['reverted']:
        d.status['outcome'] = result['outcome']
        d.status['correction_reverted'] = {'unresolved': result['unresolved'], 'round_outcome': round_outcome}
        exit_code, out, err = d.execute(scope_args(d), cwd=d.work_dir)
        (d.run / 'scope.txt').write_text(d.mask(out + err))
        if exit_code:
            d.status['outcome'] = 'out_of_scope'
        if not intact(d):
            tampered(d)
    return kept.get('diff')
