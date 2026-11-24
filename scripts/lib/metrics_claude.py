"""Collecte des jetons Claude (Python 3.14, standard library uniquement)."""

import datetime
import json
import os
import sys
from pathlib import Path

CLAUDE_ROOTS_FILE = Path('.workspace') / 'metrics' / 'claude-roots.txt'
CLAUDE_ROOTS_MAX_BYTES = 64 * 1024
TRANSCRIPT_MAX_BYTES = 200 * 1024 * 1024


def _warn(message):
    print(f"metrics: {message}", file=sys.stderr)


def read_claude_roots_file(root):
    """Racines d'orchestrateur déclarées par le projet ; un fichier suspect est ignoré avec un avertissement."""
    path = root / CLAUDE_ROOTS_FILE
    for link in (root / '.workspace', root / '.workspace' / 'metrics', path):
        if link.is_symlink():
            _warn(f"{link} est un lien symbolique, racines Claude ignorées")
            return []
    if not path.is_file():
        return []
    try:
        if path.stat().st_size > CLAUDE_ROOTS_MAX_BYTES:
            _warn(f"{path} dépasse 64 Kio, racines Claude ignorées")
            return []
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        _warn(f"{path} illisible, racines Claude ignorées")
        return []
    roots = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        candidate = Path(line).expanduser()
        roots.append(candidate if candidate.is_absolute() else root / candidate)
    return roots


def parse_record_date(ts):
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        if ts > 1e11:
            ts = ts / 1000.0
        try:
            return datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).date()
        except (OSError, ValueError):
            return None
    if isinstance(ts, str):
        if len(ts) >= 10:
            try:
                return datetime.date.fromisoformat(ts[:10])
            except ValueError:
                return None
    return None


def _empty_claude_res():
    return {"messages": 0, "input": 0, "cache_creation": 0, "cache_read": 0, "output": 0, "by_model": {}}


def _assistant_records(claude_dir):
    """Enregistrements `assistant` des transcriptions ; liens symboliques et fichiers trop gros ignorés."""
    for jsonl_file in sorted(claude_dir.rglob('*.jsonl')):
        if jsonl_file.is_symlink() or not jsonl_file.is_file():
            continue
        try:
            if jsonl_file.stat().st_size > TRANSCRIPT_MAX_BYTES:
                continue
            with jsonl_file.open('r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(record, dict) and record.get('type') == 'assistant':
                        yield record
        except OSError:
            continue


def _counter(usage, key):
    try:
        return int(usage.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def assistant_messages(claude_dir):  # noqa: C901
    """Messages `assistant` dédupliqués par `message.id` : cwd, horodatage brut, modèle et compteurs (maximum)."""
    claude_dir = Path(claude_dir).expanduser()
    found = {}
    if not claude_dir.is_dir():
        return found
    for record in _assistant_records(claude_dir):
        msg = record.get('message')
        if not isinstance(msg, dict):
            continue
        msg_id = msg.get('id')
        if not msg_id or not isinstance(msg_id, str):
            continue
        usage = msg.get('usage')
        if not isinstance(usage, dict):
            usage = {}
        cwd = record.get('cwd')
        cwd = cwd if cwd and isinstance(cwd, str) else None
        session_id = record.get('sessionId')
        session_id = session_id if session_id and isinstance(session_id, str) else None
        model = str(msg.get('model') or 'unknown')
        ts = record.get('timestamp') or record.get('created_at') or msg.get('timestamp') or None
        entrypoint = record.get('entrypoint')
        entrypoint = entrypoint if entrypoint and isinstance(entrypoint, str) else None
        sidechain = record.get('isSidechain') is True
        counts = {
            'input': _counter(usage, 'input_tokens'),
            'cache_read': _counter(usage, 'cache_read_input_tokens'),
            'cache_creation': _counter(usage, 'cache_creation_input_tokens'),
            'output': _counter(usage, 'output_tokens'),
        }
        prev = found.get(msg_id)
        if prev is None:
            found[msg_id] = dict(
                counts, cwd=cwd, timestamp=ts, model=model, entrypoint=entrypoint, sidechain=sidechain, session_id=session_id
            )
            continue
        for key, value in counts.items():
            prev[key] = max(prev[key], value)
        if prev['cwd'] is None:
            prev['cwd'] = cwd
        if prev['session_id'] is None:
            prev['session_id'] = session_id
        if prev['timestamp'] is None:
            prev['timestamp'] = ts
        if prev['model'] == 'unknown':
            prev['model'] = model
        if prev['entrypoint'] is None:
            prev['entrypoint'] = entrypoint
        prev['sidechain'] = prev['sidechain'] or sidechain
    return found


def collect_claude_tokens(root, claude_dir_arg, since, orchestrator_roots=None):  # noqa: C901, PLR0915
    res_project = _empty_claude_res()
    res_orch = _empty_claude_res()

    claude_dir = Path(claude_dir_arg).expanduser() if claude_dir_arg else Path('~/.claude/projects').expanduser()
    if not claude_dir.is_dir():
        return res_project, res_orch

    resolved_root = root.resolve()

    resolved_orch_roots = []
    for r in list(orchestrator_roots or []) + read_claude_roots_file(root):
        try:
            res_r = Path(r).resolve()
        except Exception:
            continue
        if res_r not in resolved_orch_roots:
            resolved_orch_roots.append(res_r)

    env_roots = os.environ.get("WORKSPACE_ORCHESTRATOR_ROOTS", "")
    if env_roots:
        for p in env_roots.split(":"):
            p = p.strip()
            if p:
                try:
                    res_p = Path(p).resolve()
                    if res_p not in resolved_orch_roots:
                        resolved_orch_roots.append(res_p)
                except Exception:
                    pass

    deduped_messages = {}

    for record in _assistant_records(claude_dir):
        cwd_val = record.get('cwd')
        if not cwd_val or not isinstance(cwd_val, str):
            continue
        try:
            resolved_cwd = Path(cwd_val).resolve()
        except (ValueError, OSError):
            continue

        # Priorité d'abord au projet, puis aux racines orchestrateur
        target_bucket = None
        try:
            resolved_cwd.relative_to(resolved_root)
            target_bucket = 'project'
        except (ValueError, OSError):
            pass

        if target_bucket is None:
            for orch_r in resolved_orch_roots:
                try:
                    resolved_cwd.relative_to(orch_r)
                    target_bucket = 'orchestrator'
                    break
                except (ValueError, OSError):
                    continue

        if target_bucket is None:
            continue

        msg = record.get('message')
        if not isinstance(msg, dict):
            continue

        msg_id = msg.get('id')
        if not msg_id or not isinstance(msg_id, str):
            continue

        model = str(msg.get('model') or 'unknown')
        ts = record.get('timestamp') or record.get('created_at') or msg.get('timestamp')

        usage = msg.get('usage')
        if not isinstance(usage, dict):
            usage = {}

        inp = int(usage.get('input_tokens') or 0)
        cache_c = int(usage.get('cache_creation_input_tokens') or 0)
        cache_r = int(usage.get('cache_read_input_tokens') or 0)
        out = int(usage.get('output_tokens') or 0)

        # NE LIRE ET NE GARDER AUCUN contenu de message : uniquement les compteurs, le modèle, l'horodatage
        if msg_id in deduped_messages:
            prev = deduped_messages[msg_id]
            prev['input'] = max(prev['input'], inp)
            prev['cache_creation'] = max(prev['cache_creation'], cache_c)
            prev['cache_read'] = max(prev['cache_read'], cache_r)
            prev['output'] = max(prev['output'], out)
            if model != 'unknown' and prev['model'] == 'unknown':
                prev['model'] = model
            if ts and not prev.get('timestamp'):
                prev['timestamp'] = ts
            if target_bucket == 'project':
                prev['bucket'] = 'project'
        else:
            deduped_messages[msg_id] = {
                'bucket': target_bucket,
                'model': model,
                'timestamp': ts,
                'input': inp,
                'cache_creation': cache_c,
                'cache_read': cache_r,
                'output': out,
            }

    for msg_info in deduped_messages.values():
        if since is not None:
            msg_date = parse_record_date(msg_info.get('timestamp'))
            if msg_date is None or msg_date < since:
                continue

        target_res = res_project if msg_info['bucket'] == 'project' else res_orch

        target_res["messages"] += 1
        target_res["input"] += msg_info['input']
        target_res["cache_creation"] += msg_info['cache_creation']
        target_res["cache_read"] += msg_info['cache_read']
        target_res["output"] += msg_info['output']

        m = msg_info['model']
        if m not in target_res["by_model"]:
            target_res["by_model"][m] = {"messages": 0, "input": 0, "cache_creation": 0, "cache_read": 0, "output": 0}
        bm = target_res["by_model"][m]
        bm["messages"] += 1
        bm["input"] += msg_info['input']
        bm["cache_creation"] += msg_info['cache_creation']
        bm["cache_read"] += msg_info['cache_read']
        bm["output"] += msg_info['output']

    res_project["by_model"] = dict(sorted(res_project["by_model"].items()))
    res_orch["by_model"] = dict(sorted(res_orch["by_model"].items()))
    return res_project, res_orch


def collect_claude(root, claude_dir_arg, since, orchestrator_roots=None):
    res_project, _ = collect_claude_tokens(root, claude_dir_arg, since, orchestrator_roots=orchestrator_roots)
    return res_project
