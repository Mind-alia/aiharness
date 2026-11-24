"""Delegate one task sheet to its executor (Claude, Codex or Antigravity) and judge the result.

Runs the agent in its sandbox, then checks scope, secrets, the hidden tests and the breaker, and records the
status, cost and events of the delegation. Entry point of scripts/delegate.sh. main() only chains the steps;
the prompt is built in delegate_prompt.py, the checks after each attempt live in delegate_checks.py and the
other helpers in delegate_run.py.
"""

import datetime
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

root = Path(sys.argv[1])
task_path = Path(sys.argv[2]).resolve()
os.chdir(root)
sys.path.insert(0, str(root / 'scripts/lib'))
from secrets import find_secrets, known_values, mask_secrets

import antigravity_files
import correction
import delegate_checks
import delegate_interrupt
import delegate_prompt
import events
import failure_cause
import procs
import questions
import reading
import sandbox
import selfcheck
import task_phase
import workdir
from breaker import extra_names, has_acceptance, instrument_names
from class_policy import policy
from delegate_checks import END, EXIT, RETRY
from delegate_run import (
    INTERRUPTED_MESSAGE,
    LIMIT,
    bounded,
    build_diff,
    capture_initial,
    changed,
    claude_args,
    claude_max_turns_reached,
    claude_record,
    claude_response,
    claude_session,
    collect_tokens,
    extract_report,
    read_relay_events,
    snapshot,
)
from exchange import run_broker
from isolation import choose_isolation
from output import safe_text
from scope import fingerprint
from task import read_task, safe_path


def now():
    return datetime.datetime.now(datetime.UTC).isoformat()


def git(*args):
    return subprocess.run(workdir.git_command(*args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)


active = None
current_run = None
values = []
active_sandbox_task_id = None
active_broker_stop = None
active_broker_thread = None


delegation_started = time.time()
metrics_started = time.monotonic()
attempt_started = None
metrics = dict(duration_seconds=0, attempt_durations_seconds=[], attempts=0, execution_requests=0, questions=0, tokens=None)
interaction_baseline = set()
# Study switches: force a fake verification failure on the first attempt, or turn session resuming off.
study_force_fail = os.environ.get('WORKSPACE_STUDY_FORCE_FAIL') == '1'
study_resume = os.environ.get('WORKSPACE_DELEGATE_RESUME') != '0'
resume_requested = delegate_interrupt.RESUME_OPTION in sys.argv[3:]
interrupted_signal = None


def interactions():
    if current_run is None:
        return set()
    task_id = current_run.name
    directory = root / '.workspace/orchestration/tasks' / task_id
    return set(directory.glob('exchange/request-*.json')) | set(directory.glob('context/questions/*.md'))


def finish_attempt():
    global attempt_started
    if attempt_started is not None:
        metrics['attempt_durations_seconds'].append(time.monotonic() - attempt_started)
        attempt_started = None


outcome_recorded = False


def record_outcome(status):
    """Journal the final outcome once per delegation, whichever exit path writes status.json."""
    global outcome_recorded
    if current_run is not None and not outcome_recorded:
        outcome_recorded = True
        detail = {'outcome': status.get('outcome'), 'attempts': status.get('attempts'), **delegate_interrupt.event_detail(status)}
        cause = failure_cause.cause_of(root, current_run.name, status.get('outcome'))
        if cause is not None:
            detail['cause'] = cause
        events.record(root, current_run.name, 'outcome', detail)
        if status.get('outcome') != 'waiting_answer':
            task_phase.set_phase(root, current_run.name, 'done' if status.get('outcome') in ('done', 'done_with_warning') else 'failed')


def update_metrics(status):
    metrics['duration_seconds'] = time.monotonic() - metrics_started
    entries = interactions() - interaction_baseline
    metrics['execution_requests'] = sum(p.name.startswith('request-') for p in entries)
    metrics['questions'] = sum(p.parent.name == 'questions' for p in entries)
    status['metrics'] = metrics


def execute(args, stdin=None, cwd=None, timeout=None):  # noqa: C901
    global active
    guardian = pgid = None
    try:
        active = subprocess.Popen(
            args,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            cwd=cwd,
        )
        pgid = active.pid
        guardian = procs.start_guardian(pgid)
        if stdin is not None:
            active.stdin.write(stdin.encode())
            active.stdin.close()
        buffers = [bytearray(), bytearray()]
        selector = selectors.DefaultSelector()
        for i, stream in enumerate((active.stdout, active.stderr)):
            selector.register(stream, selectors.EVENT_READ, i)
        truncated = [False, False]
        deadline = time.monotonic() + timeout if timeout else None
        timed_out, abandon = False, None
        while selector.get_map() or active.poll() is None:
            for key, _ in selector.select(0.5):
                block = os.read(key.fd, 65536)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                i = key.data
                available = LIMIT - len(buffers[i])
                buffers[i].extend(block[:available])
                truncated[i] |= len(block) > available
            if abandon is None:
                finished = active.poll() is not None
                timed_out = not finished and deadline is not None and time.monotonic() > deadline
                if finished or timed_out:
                    # Children left behind keep the pipes open: end them, then drain what remains.
                    procs.kill_group(pgid)
                    abandon = time.monotonic() + 2
            elif time.monotonic() > abandon:
                break
        code = active.wait()
        out, err = (b.decode(errors='replace') + ('\n[TRUNCATED]\n' if t else '') for b, t in zip(buffers, truncated, strict=False))
        if timed_out:
            code, err = 124, err + ('' if err.endswith('\n') or not err else '\n') + "delegate: délai de l'agent dépassé (%g s)\n" % timeout
        return code, out, err
    except OSError as error:
        return 127, '', str(error)
    finally:
        if pgid is not None:
            procs.kill_group(pgid)
        if guardian is not None:
            procs.release_guardian(guardian)
        active = None


def interrupt(signum, frame):
    global active_broker_stop, interrupted_signal
    interrupted_signal = signal.Signals(signum).name
    if active_broker_stop is not None:
        active_broker_stop.set()
    if active is not None:
        procs.kill_group(active.pid)
        active.wait()
    raise KeyboardInterrupt


for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(sig, interrupt)


def sanitize():
    global values
    values = sorted(set(values) | set(known_values(root)))
    for directory in (current_run, root / '.workspace/run/codex', root / '.workspace/run/gemini'):
        if directory is None or not directory.exists():
            continue
        safe_path(directory)
        for path in directory.rglob('*'):
            safe_path(path)
            if path.is_file():
                with path.open('rb') as stream:
                    raw = stream.read(LIMIT + 1)
                text = raw[:LIMIT].decode(errors='replace')
                if len(raw) > LIMIT:
                    text += '\n[TRUNCATED]\n'
                path.write_text(mask_secrets(text, values))
                path.chmod(0o600)
            elif path.is_dir():
                path.chmod(0o700)


def refresh_values():
    global values
    values = sorted(set(values) | set(known_values(root)))


def stop(d, outcome, message):
    """End the delegation early: record the outcome, keep status.json, mask the run folder, explain on stderr."""
    if outcome is not None:
        d.status['outcome'] = outcome
        d.status['ended_at'] = now()
    record_outcome(d.status)
    (d.run / 'status.json').write_text(json.dumps(d.status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    sanitize()
    print(mask_secrets(message, values), file=sys.stderr)
    return 1


def read_settings():
    """Read the sheet, its class policy and the delays; return the delegation, or exit code 64 when refused."""
    task = read_task(task_path, root)
    override = os.environ.get('WORKSPACE_AGENT_OVERRIDE')
    if override is not None:
        if override not in ('claude-haiku', 'claude-sonnet', 'claude-opus'):
            print('delegate.sh: WORKSPACE_AGENT_OVERRIDE invalide : ' + repr(override), file=sys.stderr)
            return 64
        task['agent'] = override
    rules = policy(task, root)
    if rules['refusal'] is not None:
        print(mask_secrets('delegate.sh: ' + rules['refusal'], values), file=sys.stderr)
        return 64
    task['attempts'] = rules['attempts']
    delay = float(os.environ.get('DELEGATE_RETRY_DELAY', '20'))
    if delay < 0:
        raise ValueError('DELEGATE_RETRY_DELAY doit être positif ou nul')
    agent_timeout = float(os.environ.get('DELEGATE_AGENT_TIMEOUT') or 0)
    if not agent_timeout >= 0:
        raise ValueError('DELEGATE_AGENT_TIMEOUT doit être positif ou nul')
    return types.SimpleNamespace(
        root=root,
        task=task,
        rules=rules,
        delay=delay,
        agent_timeout=agent_timeout,
        started=delegation_started,
        study_force_fail=study_force_fail,
        breaker_run=task_path == root / '.workspace/breaker' / task['id'] / 'task.md',
        execute=execute,
        mask=lambda text: mask_secrets(text, values),
        known=lambda: values,
    )


def open_run(d):
    """Keep the host baseline, then create the private run folder and start counting the interactions."""
    global current_run, interaction_baseline
    d.baseline = snapshot(root)
    d.initial = capture_initial(d.baseline, root, values)
    run = root / '.workspace/run/delegate' / d.task['id']
    safe_path(run)
    run.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ('codex', 'gemini'):
        safe_path(root / '.workspace/run' / name)
    current_run = d.run = run
    interaction_baseline = interactions()
    for path in run.rglob('*'):
        safe_path(path)
        path.chmod(0o700 if path.is_dir() else 0o600)
    for directory in (root / '.workspace', root / '.workspace/run', run.parent, run):
        directory.chmod(0o700)


def write_seals(d):
    (d.run / 'controls.json').write_text(json.dumps({str(p): v for p, v in d.seals.items()}))


def seal_controls(d):
    """Fingerprint the control scripts, the hidden tests and the sheet: any change during the run is tampering."""
    task = d.task
    controls = [root / 'scripts' / n for n in ('check-scope.sh', 'scope-baseline.sh', 'verify-task.sh')]
    controls += list((root / 'scripts/lib').glob('*.py')) + [task_path]
    extra_manifest = safe_path(root / '.workspace/acceptance' / task['id'] / 'extra.txt')
    if extra_manifest.exists():
        controls.append(extra_manifest)
        controls += [safe_path(extra_manifest.parent / 'breaker' / name) for name in extra_names(task, root)]
    instrument_manifest = extra_manifest.parent / 'instrument.txt'
    if instrument_manifest.exists():
        controls.append(safe_path(instrument_manifest))
        controls += [safe_path(instrument_manifest.parent / 'breaker' / name) for name in instrument_names(task, root)]
    if any(not p.is_file() or p.is_symlink() for p in controls):
        raise ValueError('Contrôle obligatoire absent')
    d.seals = {p: fingerprint(p) for p in controls}
    for name in ('gate.sh', 'docs-check.sh', 'check-secret-tracing.sh'):
        path = root / 'scripts' / name
        d.seals[path] = fingerprint(path)
    d.contract = d.run / 'task.md'
    d.contract.write_text(task_path.read_text())
    d.seals[d.contract] = fingerprint(d.contract)
    write_seals(d)


def read_profile():
    profile_path = root / 'project.profile'
    if profile_path.is_file() and not profile_path.is_symlink():
        try:
            return profile_path.read_text(encoding='utf-8').strip()
        except OSError:
            return 'docs'
    return 'docs'


def choose(d):
    """Pick the isolation level; return 1 when a sheet that demands full isolation cannot have it."""
    try:
        d.isolation, d.isolation_reason = choose_isolation(d.task, read_profile())
    except ValueError as exc:
        if d.task.get('isolation') != 'full':
            raise
        d.isolation, d.isolation_reason = 'full', str(exc)
        d.status = dict(
            id=d.task['id'],
            agent=d.task['agent'],
            agent_name=d.task['agent_name'],
            attempts=0,
            outcome='isolation_unavailable',
            files_changed=[],
            verify=None,
            started_at=now(),
            ended_at=now(),
            warnings=[],
            isolation=d.isolation,
            isolation_reason=d.isolation_reason,
            metrics=metrics,
        )
        return stop(d, None, f"delegate.sh: {d.isolation_reason}")
    # Read before status.json is rewritten below: only a delegation after a finished one is a correction round.
    d.correction_round = d.isolation == 'exchange' and not d.breaker_run and correction.is_correction_round(root, d.task)
    return None


def start_status(d):
    task, rules = d.task, d.rules
    d.status = dict(
        id=task['id'],
        agent=task['agent'],
        agent_name=task['agent_name'],
        attempts=0,
        outcome='running',
        acceptance_attempts=0,
        files_changed=[],
        verify=None,
        started_at=now(),
        ended_at=None,
        warnings=[],
        isolation=d.isolation,
        isolation_reason=d.isolation_reason,
        metrics=metrics,
        **{'class': rules['class']},
        class_declared=rules['declared'],
        breaker=rules['breaker'],
    )
    if d.resumed_dirty:
        d.status['resumed_dirty'] = True
    if study_force_fail or not study_resume:
        d.status['study'] = dict(resume=study_resume, forced_first_failure=False)
    (d.run / 'status.json').write_text(json.dumps(d.status))
    detail = {
        'agent': task['agent'],
        'agent_name': task['agent_name'],
        'isolation': d.isolation,
        'container': sandbox.get_container_name(root, task['id']) if d.isolation == 'exchange' else None,
        'verify': task.get('verify') or None,
    }
    events.record(root, task['id'], 'delegate_started', detail)
    task_phase.set_phase(root, task['id'], 'delegated')


def prepared_state(task_id):
    """Return the task state and its worktree when task-workspace.sh prepared this task, else None."""
    state_file = root / f'.workspace/orchestration/tasks/{task_id}/state.json'
    if not state_file.is_file() or state_file.is_symlink():
        return None
    try:
        task_state = json.loads(state_file.read_text(encoding='utf-8'))
        worktree_dir = workdir.state_worktree(root, task_state)
        if (
            worktree_dir == workdir.task_worktree(root, task_id)
            and worktree_dir.is_dir()
            and not worktree_dir.is_symlink()
            and task_state.get('task_id') == task_id
            and task_state.get('status') in ('prepared', 'running', 'active', 'done')
        ):
            return task_state, worktree_dir
    except Exception:
        return None
    return None


def prepare_workshop(d):
    """Point the executor at its workshop: the prepared worktree in exchange mode, else the project root."""
    task = d.task
    d.base_commit = None
    if d.isolation != 'exchange':
        d.work_dir = d.agent_cwd = root
        return None
    prepared = prepared_state(task['id'])
    if prepared is None:
        return stop(
            d,
            'task_not_prepared',
            f"delegate.sh: La tâche '{task['id']}' doit être préparée par task-workspace.sh avant délégation en niveau exchange.",
        )
    task_state, worktree_dir = prepared
    d.work_dir = d.agent_cwd = worktree_dir
    if d.correction_round:
        d.base_commit = task_state.get('base_commit')
        if not isinstance(d.base_commit, str) or not d.base_commit:
            raise ValueError('Commit de base absent de l’état de la tâche')
        correction.snapshot(d.work_dir, d.base_commit, correction.snapshot_path(root, task['id']))
    os.environ['WORKSPACE_ISOLATION'] = 'exchange'
    os.environ['TASK_ID'] = task['id']
    os.environ['WORKSPACE_TASK_ID'] = task['id']
    os.environ['WORKSPACE_AGENT_ROOT'] = str(worktree_dir)
    d.baseline = snapshot(d.work_dir)
    d.initial = capture_initial(d.baseline, d.work_dir, values)
    return None


def write_prompt(d):
    d.files_mode = antigravity_files.active(d.task)
    d.prompt, pitfalls_text, d.resume_rows = delegate_prompt.build(root, d.task, d.isolation, d.work_dir, d.status['warnings'])
    if d.resumed_dirty:
        d.prompt = delegate_interrupt.with_resume(d.prompt, d.resume_lines)
    d.status['reading'] = reading.measure(d.prompt, d.task['body'], pitfalls_text)
    events.record(root, d.task['id'], 'reading_measured', dict(d.status['reading'], role='execution', agent=d.task['agent']))


def capture_scope_baseline(d):
    """Clear the previous run's outputs, record the scope baseline and seal it with the controls."""
    global values
    run = d.run
    (run / 'events').write_text('', encoding='utf-8')
    (run / 'answer.md').write_text('', encoding='utf-8')
    for name in ('scope.txt', 'verify.txt', 'diff.txt'):
        (run / name).unlink(missing_ok=True)
    baseline_script = root / 'scripts/scope-baseline.sh'
    if baseline_script.is_file():
        baseline_args = ['bash', str(baseline_script), str(d.contract)]
        if d.isolation == 'exchange':
            baseline_args += ['--baseline', str(run / 'baseline.json')]
        code, out, err = execute(baseline_args, cwd=d.work_dir)
        (run / 'baseline.txt').write_text(mask_secrets(bounded(out + err), values), encoding='utf-8')
        if code:
            raise ValueError('Impossible de capturer le périmètre initial : ' + out + err)
    baseline_path = safe_path(run / 'baseline.json')
    if not baseline_path.is_file():
        raise ValueError('Baseline obligatoire absente')
    d.seals[baseline_path] = fingerprint(baseline_path)
    write_seals(d)
    values = known_values(root)


def start_sandbox(d):
    """Start the task's container and its exchange broker in exchange mode; return 1 when the container fails."""
    global active_sandbox_task_id, active_broker_stop, active_broker_thread
    d.sandbox_started = False
    if d.isolation != 'exchange':
        return None
    code, out, err = execute(['bash', str(root / 'scripts/sandbox.sh'), 'start', d.task['id']])
    if code != 0:
        return stop(d, 'failed', f"delegate.sh: Erreur au démarrage du sandbox : {out} {err}")
    d.sandbox_started = True
    active_sandbox_task_id = d.task['id']
    active_broker_stop = threading.Event()
    active_broker_thread = threading.Thread(target=run_broker, args=(root, d.task['id'], active_broker_stop), daemon=True)
    active_broker_thread.start()
    return None


def stop_sandbox(d):
    global active_sandbox_task_id, active_broker_stop, active_broker_thread
    if active_broker_stop is not None:
        active_broker_stop.set()
        if active_broker_thread is not None:
            active_broker_thread.join(timeout=2.0)
        active_broker_stop = None
        active_broker_thread = None
    if d.sandbox_started:
        subprocess.run(['bash', str(root / 'scripts/sandbox.sh'), 'stop', d.task['id']], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        active_sandbox_task_id = None


def acceptance_vacuous(d):
    """True when the hidden tests already pass on the untouched base: they would prove nothing."""
    return (
        d.isolation == 'exchange'
        and has_acceptance(d.task, root)
        and os.environ.get('WORKSPACE_NO_SELFCHECK') != '1'
        and not d.breaker_run
        and selfcheck.at_base(root, d.task['id'], d.work_dir)
        and selfcheck.run(root, d.task, d.work_dir, execute) == 0
    )


def reject_secret_prompt(d):
    """Refuse to send a prompt that carries a secret, and name where each secret came from."""
    if not find_secrets(d.prompt, values):
        return False
    d.status['secret_origins'] = delegate_prompt.secret_origins(root, task_path, d.prompt, values)
    (d.run / 'prompt.md').unlink(missing_ok=True)
    stop(d, 'secret_in_prompt', 'Secret dans la consigne : ' + ', '.join(d.status['secret_origins']))
    return True


def call_claude(d, attempt, resume, message):
    guard = (root, d.work_dir) if d.isolation == 'exchange' else None
    code, output, error = execute(claude_args(d.task, d.isolation, resume, guard=guard), message, cwd=d.agent_cwd, timeout=d.agent_timeout)
    record = claude_record(output)
    d.session_id = claude_session(record)
    reply = types.SimpleNamespace(code=code, output=output, error=error, max_turns_reached=claude_max_turns_reached(record))
    collect_tokens(d.task['agent'], output, metrics)
    answer = claude_response(root, metrics, d.task, attempt, code, output, error)
    safe_path(d.run / 'answer.md')
    (d.run / 'answer.md').write_text(mask_secrets(answer, values), encoding='utf-8')
    return reply


def call_relay(d, attempt):
    task = d.task
    os.environ['WORKSPACE_TASK_ID'] = task['id']
    os.environ['WORKSPACE_ATTEMPT'] = str(attempt)
    os.environ.setdefault('WORKSPACE_ROLE', 'execution')
    relay = 'codex.sh' if task['agent'] == 'codex' else 'gemini.sh'
    if d.files_mode:
        antigravity_files.reset_outbox(d.work_dir)
    code, output, error = execute(
        ['bash', str(root / 'scripts' / relay), task['mode'], str(d.run / 'prompt.md'), str(d.run / 'answer.md')],
        cwd=d.agent_cwd,
        timeout=d.agent_timeout,
    )
    if d.files_mode:
        d.status['applied_files'], d.status['refused_files'] = antigravity_files.apply_outbox(d.work_dir, task['allowed'])
    return types.SimpleNamespace(code=code, output=output, error=error, max_turns_reached=False)


def call_agent(d, attempt):
    """Run one attempt of the executor; a Claude retry resumes its session with only what changed since."""
    global attempt_started
    task = d.task
    finish_attempt()
    attempt_started = time.monotonic()
    metrics['attempts'] = attempt
    d.status['attempts'] = attempt
    resume = d.session_id if task['agent'].startswith('claude-') and attempt > 1 and study_resume else None
    message = (d.followup or INTERRUPTED_MESSAGE) if resume else d.prompt
    d.followup = d.session_id = None
    events.record(root, task['id'], 'attempt_started', {'attempt': attempt, 'phase': 'attempt', 'resumed': bool(resume)})
    task_phase.set_phase(root, task['id'], 'attempt %d' % attempt)
    (d.run / 'prompt.md').write_text(d.prompt, encoding='utf-8')
    # Never accept a response left over from an earlier attempt or run.
    (d.run / 'answer.md').write_text('', encoding='utf-8')
    d.previous = snapshot(d.work_dir)
    if task['agent'].startswith('claude-'):
        return call_claude(d, attempt, resume, message)
    return call_relay(d, attempt)


def remask(d):
    for name in ('answer.md', 'events'):
        path = d.run / name
        path.write_text(mask_secrets(bounded(path.open(encoding='utf-8', errors='replace').read(LIMIT + 1)), values), encoding='utf-8')


def transient_failure(d, attempt, has_answer):
    """A network or availability failure: keep the work the agent left, retry after a pause, or fail."""
    if changed(d.previous, snapshot(d.work_dir)) and has_answer:
        d.warning = True
        return True, None
    if attempt >= d.task['attempts']:
        return False, None
    d.status['outcome'] = 'failed'
    if delegate_checks.waiting_answer(d):
        return False, END
    finish_attempt()
    time.sleep(d.delay)
    return False, RETRY


def relay_outcome(d, attempt, reply, event_text, final_error):
    """Set the attempt's outcome from what the relay said; return (END or RETRY when that decides, relay ok)."""
    denied = re.findall(r'^DENIED: (.+)$', reply.error, re.M) if d.task['agent'] == 'antigravity' else []
    if denied:
        # A refused permission does not change on retry, and verifying an empty answer hides the cause.
        d.status.update(outcome='permission_denied', denied_actions=[name.strip() for name in denied])
        return END, False
    transient = not reply.max_turns_reached and bool(
        re.search(r'503|UNAVAILABLE|stream (?:was )?interrupted|network', event_text if reply.code else final_error, re.I)
    )
    has_answer = bool((d.run / 'answer.md').open(encoding='utf-8').read(LIMIT).strip())
    # Hitting the turn ceiling is a normal stop: verification decides, and a failure resumes the session.
    relay_ok = reply.max_turns_reached or (not reply.code and has_answer)
    if transient:
        relay_ok, step = transient_failure(d, attempt, has_answer)
        if step is not None:
            return step, relay_ok
    d.status['outcome'] = ('done_with_warning' if d.warning else 'done') if relay_ok else 'failed'
    return None, relay_ok


def attempt_once(d, attempt):
    """Run one attempt and judge it; return RETRY, END, or EXIT when a secret stops the delegation."""
    if not delegate_checks.intact(d):
        return delegate_checks.tampered(d)
    refresh_values()
    if reject_secret_prompt(d):
        return EXIT
    if attempt == 1:
        questions.mark_answers_read(root, d.task['id'], [number for number, _, _ in d.resume_rows])
    reply = call_agent(d, attempt)
    events.record(
        root, d.task['id'], 'attempt_finished', {'attempt': attempt, 'code': reply.code, 'max_turns_reached': reply.max_turns_reached}
    )
    for path in d.run.rglob('*'):
        safe_path(path)
    if not delegate_checks.intact(d):
        return delegate_checks.tampered(d)
    event_text, final_error = read_relay_events(root, d.task['agent'], reply.output, reply.error, reply.code, metrics)
    with (d.run / 'events').open('a', encoding='utf-8') as stream:
        stream.write(mask_secrets(bounded('\n--- attempt %d ---\n%s\n' % (attempt, event_text)), values))
    refresh_values()
    remask(d)
    step, relay_ok = relay_outcome(d, attempt, reply, event_text, final_error)
    return step or delegate_checks.judge(d, attempt, relay_ok)


def run_attempts(d):
    """Run the executor until an attempt ends the delegation; return EXIT when a secret stopped it."""
    d.warning = False
    d.session_id = d.followup = None
    if d.task['max_turns'] is not None and not d.task['agent'].startswith('claude-'):
        d.status['warnings'].append('max_turns ignoré pour ' + d.task['agent'])
    for attempt in range(1, d.task['attempts'] + 1):
        step = attempt_once(d, attempt)
        if step != RETRY:
            return step
    return END


def finish(d, round_diff):
    """Keep the diff, the report and the metrics, write the final status.json and print it."""
    status = d.status
    status['files_changed'] = changed(d.baseline, snapshot(d.work_dir))
    refresh_values()
    diff = build_diff(status['files_changed'], d.work_dir, d.initial, d.baseline, values)
    if round_diff is not None and 'correction_reverted' in status:
        diff = bytearray('ronde de correction rétablie : diff de la ronde ci-dessous, non fusionné\n'.encode()) + round_diff
    refresh_values()
    remask(d)
    report = extract_report((d.run / 'answer.md').open(encoding='utf-8', errors='replace').read(LIMIT))
    status.update(report=report, report_format='json' if report else 'prose')
    (d.run / 'diff.txt').write_text(safe_text(bytes(diff), values, len(diff) >= LIMIT), encoding='utf-8')
    finish_attempt()
    update_metrics(status)
    status['ended_at'] = now()
    record_outcome(status)
    (d.run / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    sanitize()
    print(mask_secrets(json.dumps(status, ensure_ascii=False), values))
    return 0 if status['outcome'] in ('done', 'done_with_warning') else (75 if status['outcome'] == 'waiting_answer' else 1)


def main():
    """Delegate the sheet; exit code 0 done, 75 waiting for an answer, 1 failed, 64 refused before the run."""
    global values
    os.environ.setdefault('ASK_TIMEOUT', '1800')
    values = known_values(root)
    d = read_settings()
    if isinstance(d, int):
        return d
    refused = delegate_interrupt.gate(d, task_path, resume_requested)
    if refused is not None:
        return refused
    open_run(d)
    seal_controls(d)
    refused = choose(d)
    if refused is None:
        start_status(d)
        refused = prepare_workshop(d)
    if refused is not None:
        return refused
    write_prompt(d)
    capture_scope_baseline(d)
    refused = start_sandbox(d)
    if refused is not None:
        return refused
    try:
        if acceptance_vacuous(d):
            return stop(d, 'acceptance_vacuous', selfcheck.MESSAGE)
        if run_attempts(d) == EXIT:
            return 1
        if not delegate_checks.intact(d):
            delegate_checks.tampered(d)
        return finish(d, delegate_checks.correction_step(d))
    finally:
        stop_sandbox(d)


try:
    sys.exit(main())
except KeyboardInterrupt:
    sanitize()
    if current_run:
        path = current_run / 'status.json'
        status = json.loads(path.read_text())
        status.update(outcome='interrupted', ended_at=now(), **delegate_interrupt.interruption(root, current_run.name, interrupted_signal))
        record_outcome(status)
        path.write_text(mask_secrets(json.dumps(status), values))
    sys.exit(130)
except (OSError, ValueError) as error:
    try:
        if current_run:
            path = safe_path(current_run / 'status.json')
            status = json.loads(path.read_text()) if path.exists() else {}
            status.update(outcome='failed', ended_at=now())
            record_outcome(status)
            path.write_text(mask_secrets(json.dumps(status), values))
        sanitize()
    except (OSError, ValueError):
        pass
    print(mask_secrets('delegate.sh: ' + str(error), values), file=sys.stderr)
    sys.exit(64)

finally:
    try:
        finish_attempt()
        if current_run is not None:
            metrics_path = safe_path(current_run / 'status.json')
            if metrics_path.is_file():
                final_status = json.loads(metrics_path.read_text())
                update_metrics(final_status)
                metrics_path.write_text(mask_secrets(json.dumps(final_status, ensure_ascii=False, indent=2) + '\n', values))
    except (OSError, ValueError):
        pass
    try:
        if active_broker_stop is not None:
            active_broker_stop.set()
            if active_broker_thread is not None:
                active_broker_thread.join(timeout=1.0)
    except Exception:
        pass
    try:
        if active_sandbox_task_id:
            subprocess.run(
                ['bash', str(root / 'scripts/sandbox.sh'), 'stop', active_sandbox_task_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
    except Exception:
        pass
    try:
        sanitize()
    except (OSError, ValueError):
        pass
