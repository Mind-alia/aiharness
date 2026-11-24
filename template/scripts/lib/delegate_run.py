"""Helpers of delegate.py: bounded output, worktree snapshots and changes, token accounting from the agent's
output, Claude session records, and the settings that wire the executor hook.
"""

import difflib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from itertools import chain
from pathlib import Path
from secrets import mask_secrets

import consumption
import events
import executor_guard
from output import safe_text
from scope import changed_paths, fingerprint
from task import safe_path

LIMIT = 2 * 1024 * 1024
DEFAULT_MAX_TURNS = 40
REPORT_WINDOW = 64 * 1024
INTERRUPTED_MESSAGE = "La tentative précédente s'est interrompue ; reprends la tâche là où elle en était."
REPORT_SECTION = """Compte rendu final (tous les exécutants) :
Termine ta réponse par un bloc ```json contenant exactement les clés suivantes :
- "files_changed" : liste des chemins modifiés ;
- "tests_run" : liste des commandes de test lancées ;
- "exit_codes" : liste d'entiers, un code de sortie par commande de "tests_run" ;
- "notes" : texte court.
Exemple :
```json
{"files_changed": ["src/a.py"], "tests_run": ["python3 -m unittest tests.test_a"], "exit_codes": [0], "notes": "RAS"}
```
La prose seule est tolérée, mais elle est signalée (report_format: prose)."""


def bounded(text):
    return text if len(text) <= LIMIT else text[:LIMIT] + '\n[TRUNCATED]\n'


def snapshot(target_root):
    """Compare task-time contents, including staged and untracked files.

    Exclude only our own run artifacts. Existing dirty content is a baseline,
    so another task's unchanged work is not attributed to this delegate.
    """
    result = subprocess.run(
        ['git', '-C', str(target_root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise ValueError('delegate.sh nécessite un dépôt Git: ' + result.stderr.decode(errors='replace'))
    state = {}
    for directory, dirs, files in os.walk(target_root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', '.workspace')]
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink() and not path.resolve().is_relative_to(target_root):
                raise ValueError('Lien sortant du projet : ' + str(path.relative_to(target_root)))
    entries = set(result.stdout.split(b'\0')) | {os.fsencode(p) for p in changed_paths(target_root)}
    for raw in entries:
        if not raw:
            continue
        name = os.fsdecode(raw)
        if name.startswith('.workspace/'):
            continue
        path = target_root / name
        if path.is_symlink() and not path.resolve().is_relative_to(target_root):
            raise ValueError('Lien sortant du projet : ' + name)
        state[name] = fingerprint(path)
    return state


def changed(before, after):
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def claude_tokens(record):
    usage = record.get('usage')
    if not isinstance(usage, dict):
        return {}
    return {
        key: value if type(value) is int and value >= 0 else None
        for key, source in (
            ('input', 'input_tokens'),
            ('cached_input', 'cache_read_input_tokens'),
            ('output', 'output_tokens'),
            ('cache_creation', 'cache_creation_input_tokens'),
        )
        for value in (usage.get(source),)
    }


def collect_tokens(agent, text, metrics):  # noqa: C901
    records = []
    # Claude emits one JSON object, which may span multiple lines.
    if agent.startswith('claude-'):
        try:
            record = json.loads(text)
        except ValueError:
            return
        if isinstance(record, dict):
            records.append(record)
    for line in [] if agent.startswith('claude-') else text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    for record in records:
        usage = record.get('usage')
        if not isinstance(usage, dict):
            continue
        if agent.startswith('claude-'):
            usage = claude_tokens(record)
        elif agent == 'antigravity':
            usage = {'total': usage.get('total_tokens')}
        if not any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in usage.values()):
            continue
        if metrics['tokens'] is None:
            metrics['tokens'] = {}
        for key, value in usage.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                metrics['tokens'][key] = (metrics['tokens'].get(key) or 0) + value
            else:
                metrics['tokens'].setdefault(key, None)


def claude_record(output):
    """The JSON object printed by `claude -p --output-format json`, or {} when the output is something else."""
    try:
        record = json.loads(output)
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


def claude_session(record):
    session = record.get('session_id')
    return session if isinstance(session, str) and re.fullmatch(r'[A-Za-z0-9_-]+', session) else None


def claude_max_turns_reached(record):
    return record.get('subtype') == 'error_max_turns'


def workshop_section(worktree):
    """The executor's rule 9: its workshop is the worktree and its container, nothing else."""
    return (
        f"Atelier de l'exécutant (worktree {worktree}) :\n"
        "Ton atelier est ce worktree et son conteneur, rien d'autre.\n"
        "Toute commande passe par `bash scripts/run-in-sandbox.sh -- <commande>`.\n"
        "Ce qui n'est pas dans le worktree ne se cherche pas : demande-le à l'orchestrateur "
        "avec `bash scripts/ask.sh \"<question>\"` et continue ce qui ne dépend pas de la réponse.\n"
        "Lire et chercher dans le worktree se fait avec les outils Read, Grep et Glob, jamais avec `grep` ou `cat` en Bash.\n"
        "Un tube ou une redirection s'écrit dans le conteneur : "
        "`bash scripts/run-in-sandbox.sh -- sh -c '<commande> | <filtre>'` ; "
        "seuls `2>&1 | tail -n N` ou `| head -n N` sont admis après `run-in-sandbox.sh`.\n"
        "Une commande refusée ne se réessaie pas sous une autre forme."
    )


def guard_settings(root, worktree, task_id):
    """The --settings JSON that wires the `executor` PreToolUse hook (absolute paths)."""
    command = ' '.join(
        shlex.quote(part)
        for part in (sys.executable, os.path.join(str(root), 'scripts/lib/hooks_guard.py'), 'executor', str(root), str(worktree), task_id)
    )
    return json.dumps({'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': command}]}]}})


def claude_args(task, isolation, session_id=None, guard=None):
    args = [
        'claude',
        '-p',
        '--output-format',
        'json',
        '--model',
        task['agent'].split('-')[1],
        '--permission-mode',
        'plan' if task['mode'] == 'review' else 'acceptEdits',
        '--max-turns',
        str(task.get('max_turns') or DEFAULT_MAX_TURNS),
    ]
    if task['mode'] == 'write' and isolation == 'exchange':
        # Same reach as Codex: commands only through the task container.
        allowed = 'Bash(bash scripts/run-in-sandbox.sh:*)'
        args += ['--allowedTools', allowed + ',Bash(bash scripts/ask.sh:*)' if guard else allowed]
    if guard:
        # The agent process stays on the host: the hook is what makes the host blind to it.
        root, worktree = guard
        # No personal plugins, MCP servers or skills; only the tools the hook knows how to check.
        args += [
            '--disallowedTools',
            'WebFetch,WebSearch,Agent',
            '--settings',
            guard_settings(root, worktree, task['id']),
            '--setting-sources',
            'project,local',
            '--strict-mcp-config',
            '--mcp-config',
            json.dumps({'mcpServers': {}}),
            '--tools',
            ','.join(executor_guard.EXECUTOR_TOOLS),
        ]
    if session_id:
        args += ['--resume', session_id]
    return args


def _report_shape(candidate):
    if not isinstance(candidate, dict):
        return None
    try:
        files, tests, codes, notes = (candidate[key] for key in ('files_changed', 'tests_run', 'exit_codes', 'notes'))
    except KeyError:
        return None
    if (
        isinstance(files, list)
        and all(isinstance(item, str) for item in files)
        and isinstance(tests, list)
        and all(isinstance(item, str) for item in tests)
        and isinstance(codes, list)
        and all(type(item) is int for item in codes)
        and isinstance(notes, str)
    ):
        return {'files_changed': files, 'tests_run': tests, 'exit_codes': codes, 'notes': notes}
    return None


def extract_report(text):
    """Last JSON object of the final 64 KiB that carries the four report keys, else None."""
    window = text.encode('utf-8', errors='replace')[-REPORT_WINDOW:].decode('utf-8', errors='ignore')
    decoder = json.JSONDecoder()
    report = None
    for match in re.finditer(r'\{', window):
        try:
            candidate, _ = decoder.raw_decode(window, match.start())
        except (ValueError, RecursionError):
            continue
        report = _report_shape(candidate) or report
    return report


def claude_response(root, metrics, task, attempt, code, output, error):  # noqa: PLR0913
    try:
        record = json.loads(output)
    except ValueError:
        record = {}
    if not isinstance(record, dict):
        record = {}
    answer = record.get('result')
    answer = answer if isinstance(answer, str) else output
    # Only inspect diagnostics: generated prose must not signal a quota limit.
    diagnostic = error + ('\n' + answer if code or record.get('is_error') is True else '')
    expired = 'oauth session expired' in diagnostic.lower()
    limited = expired or any(term in diagnostic.lower() for term in ('usage limit', 'rate limit', 'quota'))
    quota_detail = ('authentification expirée' if expired else 'Claude usage limit detected') if limited else None
    if limited:
        events.record(root, task['id'], 'quota', {'provider': 'claude', 'detail': quota_detail})
    try:
        tokens = claude_tokens(record)
        complete = all(type(tokens.get(key)) is int for key in ('input', 'cached_input', 'cache_creation', 'output'))
        metrics['tokens_complete'] = metrics.get('tokens_complete', True) and complete
        duration = record.get('duration_ms')
        duration = duration / 1000 if type(duration) in (int, float) and duration >= 0 else None
        turns = record.get('num_turns')
        turns = turns if type(turns) is int and turns >= 0 else None
        consumption.record(
            root,
            provider='claude',
            model=task['agent'].split('-')[1],
            role=os.environ.get('WORKSPACE_ROLE') or ('relecture' if task['mode'] == 'review' else 'execution'),
            task_id=task['id'],
            attempt=attempt,
            **{key: tokens.get(key) for key in ('input', 'cached_input', 'cache_creation', 'output')},
            turns=turns,
            duration=duration,
            limit_reached=limited,
            quota_detail=quota_detail,
            exit=code,
            cost_usd=consumption.claude_cost(record),
        )
    except Exception:
        # Accounting is best effort and must never affect delegation.
        pass
    return answer


def git_mode(st_mode):
    if stat.S_ISLNK(st_mode):
        return '120000'
    return '100755' if st_mode & 0o111 else '100644'


def capture_initial(baseline, base_dir, values):
    # Keep only masked, bounded task-time contents in memory. Never write raw
    # initial files to Git objects, the index, or delegation artifacts.
    initial = {}
    remaining = 16 * LIMIT
    for name in sorted(baseline):
        path = base_dir / name
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:
                raw = stream.read(min(LIMIT, remaining) + 1)
            cap = min(LIMIT, remaining)
            initial[name] = safe_text(raw[:cap], values, len(raw) > cap)
            remaining -= min(len(raw), cap)
    return initial


def build_diff(names, work_dir, initial, baseline, values):  # noqa: C901
    diff = bytearray()
    for name in names:
        path = work_dir / name
        before = mask_secrets(initial.get(name, ''), values)
        after = ''
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:
                raw = stream.read(LIMIT + 1)
            after = safe_text(raw[:LIMIT], values, len(raw) > LIMIT)
        if '[TRUNCATED]' in before or '[TRUNCATED]' in after:
            diff.extend(b'\n[TRUNCATED]\n')
        lines = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile='a/' + name if name in initial else '/dev/null',
            tofile='b/' + name if path.exists() else '/dev/null',
        )
        headers = ['diff --git a/' + name + ' b/' + name + '\n']
        old_mode = baseline.get(name)
        new_mode = fingerprint(path)
        if old_mode is not None and new_mode is None:
            headers.append('deleted file mode %s\n' % git_mode(old_mode[0]))
        elif old_mode is None and new_mode is not None:
            headers.append('new file mode %s\n' % git_mode(new_mode[0]))
        elif old_mode and new_mode and git_mode(old_mode[0]) != git_mode(new_mode[0]):
            headers.extend(['old mode %s\n' % git_mode(old_mode[0]), 'new mode %s\n' % git_mode(new_mode[0])])
        for line in chain(headers, lines):
            if not line.endswith('\n'):
                line += '\n\\ No newline at end of file\n'
            block = line.encode()
            available = LIMIT - len(diff)
            diff.extend(block[: max(0, available)])
            if len(block) > available:
                break
        if len(diff) >= LIMIT:
            break
    return diff


def read_relay_events(root, agent, output, error, code, metrics):  # noqa: C901, PLR0913
    # Relays report event paths rather than streaming their events to stdout.
    event_text = output + '\n' + error
    final_error = ''
    codex_events_found = False
    for match in re.finditer(r'events in ([^\r\n]+)', event_text):
        event_path = Path(match.group(1).strip())
        if not event_path.is_absolute():
            event_path = root / event_path
        if event_path.is_file():
            safe_path(event_path)
            relay_events = event_path.open(encoding='utf-8', errors='replace').read(LIMIT + 1)
            if agent == 'codex':
                codex_events_found = True
                with event_path.open(encoding='utf-8', errors='replace') as token_stream:
                    observed = consumption.codex_events(token_stream)
                metrics['tokens_complete'] = metrics.get('tokens_complete', True) and observed['tokens_complete'] and code == 0
                for key in ('input', 'cached_input', 'output', 'reasoning'):
                    if key in observed:
                        if metrics['tokens'] is None:
                            metrics['tokens'] = {}
                        metrics['tokens'][key] = (metrics['tokens'].get(key) or 0) + observed[key]
            else:
                with event_path.open(encoding='utf-8', errors='replace') as token_stream:
                    for event_line in token_stream:
                        collect_tokens(agent, event_line, metrics)
            event_text = bounded(event_text + '\n' + relay_events)
            # Only a terminal error record can override a successful exit.
            # Agent messages and SUCCESS envelope responses are ordinary prose.
            try:
                envelope = json.loads(relay_events.strip().splitlines()[-1])
            except (ValueError, IndexError):
                envelope = None
            if isinstance(envelope, dict) and (envelope.get('status') == 'ERROR' or envelope.get('type') in ('error', 'turn.failed')):
                final_error = json.dumps(envelope)
    if agent == 'codex' and not codex_events_found:
        metrics['tokens_complete'] = False
    return event_text, final_error
