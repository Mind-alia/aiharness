"""Tableau de bord chiffré du projet (Python 3.14, standard library uniquement)."""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

# Permet d'importer questions.py s'il est présent
LIB_DIR = Path(__file__).resolve().parent
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


import reading
from consumption import token_total
from metrics_claude import collect_claude_tokens, parse_record_date
from metrics_format import fmt_float, fmt_int, fmt_pct, format_markdown, format_text


def final_task_status(root, task_id):
    if not isinstance(task_id, str) or not re.fullmatch(r'[a-z0-9-]+', task_id):
        return None
    try:
        task_dir = root / '.workspace/orchestration/tasks' / task_id
        if task_dir.is_symlink() or not task_dir.is_dir():
            return None
        state_file = task_dir / 'state.json'
        if state_file.is_symlink() or not state_file.is_file():
            return None
        data = json.loads(state_file.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            status = data.get('status')
            return str(status) if status is not None else None
    except (OSError, ValueError):
        return None
    return None


def collect_delegation(root, since):  # noqa: C901, PLR0915
    delegate_dir = root / '.workspace/run/delegate'
    if delegate_dir.is_symlink() or not delegate_dir.is_dir():
        return {
            "tasks": 0,
            "first_try_success_rate": None,
            "mean_attempts": None,
            "mean_duration_seconds": None,
            "merged": 0,
            "abandoned": 0,
            "outcomes": {},
            "final_states": {},
            "by_agent": {},
        }

    tasks = []
    for path in sorted(delegate_dir.glob('*/status.json')):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            status = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(status, dict):
            continue

        started_at = status.get('started_at')
        if since is not None:
            st_date = parse_record_date(started_at)
            if st_date is None or st_date < since:
                continue

        metrics = status.get('metrics')
        if not isinstance(metrics, dict):
            metrics = {}

        agent = str(status.get('agent', 'null'))
        attempts = metrics.get('attempts', status.get('attempts'))
        if not isinstance(attempts, (int, float)) or isinstance(attempts, bool):
            attempts = None

        outcome = status.get('outcome')
        if outcome is not None:
            outcome = str(outcome)

        duration = metrics.get('duration_seconds')
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            duration = None
        if duration is None and started_at and status.get('ended_at'):
            try:
                s_dt = datetime.datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                e_dt = datetime.datetime.fromisoformat(status['ended_at'].replace('Z', '+00:00'))
                if s_dt.tzinfo is not None and e_dt.tzinfo is None:
                    e_dt = e_dt.replace(tzinfo=datetime.UTC)
                elif s_dt.tzinfo is None and e_dt.tzinfo is not None:
                    s_dt = s_dt.replace(tzinfo=datetime.UTC)
                duration = max(0.0, (e_dt - s_dt).total_seconds())
            except (ValueError, TypeError):
                duration = None

        tok = None if metrics.get('tokens_complete') is False else token_total(metrics.get('tokens'), agent)
        task_id = status.get('id') or path.parent.name
        final_state = final_task_status(root, task_id)

        first_try = bool(attempts == 1 and outcome in ('done', 'done_with_warning'))

        tasks.append(
            {
                'id': task_id,
                'agent': agent,
                'attempts': attempts,
                'outcome': outcome,
                'duration': duration,
                'tokens': tok,
                'final_state': final_state,
                'first_try': first_try,
            }
        )

    tasks_count = len(tasks)
    if tasks_count == 0:
        return {
            "tasks": 0,
            "first_try_success_rate": None,
            "mean_attempts": None,
            "mean_duration_seconds": None,
            "merged": 0,
            "abandoned": 0,
            "outcomes": {},
            "final_states": {},
            "by_agent": {},
        }

    first_tries = sum(1 for t in tasks if t['first_try'])
    first_try_rate = first_tries / tasks_count

    att_vals = [t['attempts'] for t in tasks if t['attempts'] is not None]
    mean_attempts = sum(att_vals) / len(att_vals) if att_vals else None

    dur_vals = [t['duration'] for t in tasks if t['duration'] is not None]
    mean_dur = sum(dur_vals) / len(dur_vals) if dur_vals else None

    merged = sum(1 for t in tasks if t['final_state'] == 'merged')
    abandoned = sum(1 for t in tasks if t['final_state'] == 'abandoned')

    outcomes = {}
    for t in tasks:
        if t['outcome'] is not None:
            outcomes[t['outcome']] = outcomes.get(t['outcome'], 0) + 1

    final_states = {}
    for t in tasks:
        if t['final_state'] is not None:
            final_states[t['final_state']] = final_states.get(t['final_state'], 0) + 1

    agents = sorted({t['agent'] for t in tasks})
    by_agent = {}
    for ag in agents:
        ag_tasks = [t for t in tasks if t['agent'] == ag]
        ag_count = len(ag_tasks)
        ag_first = sum(1 for t in ag_tasks if t['first_try'])
        ag_att = [t['attempts'] for t in ag_tasks if t['attempts'] is not None]
        ag_dur = [t['duration'] for t in ag_tasks if t['duration'] is not None]
        ag_tokens = [t['tokens'] for t in ag_tasks]

        by_agent[ag] = {
            "tasks": ag_count,
            "first_try_success_rate": ag_first / ag_count if ag_count else None,
            "mean_attempts": sum(ag_att) / len(ag_att) if ag_att else None,
            "mean_duration_seconds": sum(ag_dur) / len(ag_dur) if ag_dur else None,
            "tokens": sum(ag_tokens) if ag_tokens and all(value is not None for value in ag_tokens) else None,
        }

    return {
        "tasks": tasks_count,
        "first_try_success_rate": first_try_rate,
        "mean_attempts": mean_attempts,
        "mean_duration_seconds": mean_dur,
        "merged": merged,
        "abandoned": abandoned,
        "outcomes": dict(sorted(outcomes.items())),
        "final_states": dict(sorted(final_states.items())),
        "by_agent": by_agent,
    }


def count_pending_questions(root):  # noqa: C901
    try:
        import questions

        agent_q, closed_q = questions.read_agent_questions(root)
        return len(agent_q) + len(closed_q)
    except Exception:
        pass
    tasks_dir = root / '.workspace/orchestration/tasks'
    if tasks_dir.is_symlink() or not tasks_dir.is_dir():
        return 0
    count = 0
    for task_dir in sorted(tasks_dir.iterdir()):
        if task_dir.is_symlink() or not task_dir.is_dir():
            continue
        if not re.fullmatch(r'[a-z0-9-]+', task_dir.name):
            continue
        q_dir = task_dir / 'context/questions'
        a_dir = task_dir / 'context/answers'
        if q_dir.is_symlink() or not q_dir.is_dir():
            continue
        for q_file in sorted(q_dir.iterdir()):
            if q_file.is_symlink() or not q_file.is_file():
                continue
            if not re.fullmatch(r'\d+\.md', q_file.name):
                continue
            a_file = a_dir / q_file.name
            if not a_file.is_file() or a_file.is_symlink():
                count += 1
    return count


def collect_advisor(root, since):  # noqa: C901
    tasks_dir = root / '.workspace/orchestration/tasks'
    questions_count = 0
    answered = 0
    unknown = 0
    unavailable = 0
    research = 0
    seconds_list = []

    if not tasks_dir.is_symlink() and tasks_dir.is_dir():
        for task_dir in sorted(tasks_dir.iterdir()):
            if task_dir.is_symlink() or not task_dir.is_dir():
                continue
            if not re.fullmatch(r'[a-z0-9-]+', task_dir.name):
                continue
            advisor_dir = task_dir / 'context/advisor'
            if advisor_dir.is_symlink() or not advisor_dir.is_dir():
                continue
            for note_file in sorted(advisor_dir.glob('*.json')):
                if note_file.is_symlink() or not note_file.is_file():
                    continue
                try:
                    note = json.loads(note_file.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                if not isinstance(note, dict):
                    continue

                if since is not None:
                    at_val = note.get('at')
                    note_date = parse_record_date(at_val)
                    if note_date is None or note_date < since:
                        continue

                questions_count += 1
                st = note.get('status')
                if st == 'answered':
                    answered += 1
                elif st == 'unknown':
                    unknown += 1
                elif st == 'unavailable':
                    unavailable += 1
                elif st == 'research':
                    research += 1

                sec = note.get('seconds')
                if isinstance(sec, (int, float)) and not isinstance(sec, bool):
                    seconds_list.append(float(sec))

    ans_denom = answered + unknown
    answer_rate = (answered / ans_denom) if ans_denom > 0 else None
    mean_seconds = (sum(seconds_list) / len(seconds_list)) if seconds_list else None
    pending = count_pending_questions(root)
    import alerts
    import questions

    _, closed_q = questions.read_agent_questions(root)
    alert_count = len({(r['task_id'], r['question']) for r in alerts.read_alerts(root) if r.get('reason') != 'ntfy_failed'})

    return {
        "questions": questions_count,
        "answered": answered,
        "unknown": unknown,
        "unavailable": unavailable,
        "research": research,
        "answer_rate": answer_rate,
        "mean_seconds": mean_seconds,
        "pending_for_orchestrator": pending,
        "unanswered_in_closed_tasks": len(closed_q),
        "alerts": alert_count,
    }


def collect_codex(root, since):  # noqa: C901
    codex_dir = root / '.workspace/run/codex'
    res = {"turns": 0, "input": 0, "cached_input": 0, "output": 0, "reasoning_output": 0}
    if codex_dir.is_symlink() or not codex_dir.is_dir():
        return res

    for jsonl_file in sorted(codex_dir.glob('*.jsonl')):
        if jsonl_file.is_symlink() or not jsonl_file.is_file():
            continue
        try:
            with jsonl_file.open('r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    if record.get('type') != 'turn.completed':
                        continue

                    if since is not None:
                        ts = record.get('timestamp') or record.get('created_at') or record.get('at') or record.get('time')
                        rec_date = parse_record_date(ts)
                        if rec_date is None or rec_date < since:
                            continue

                    usage = record.get('usage')
                    if not isinstance(usage, dict):
                        usage = {}

                    res["turns"] += 1
                    res["input"] += int(usage.get('input_tokens') or 0)
                    res["cached_input"] += int(usage.get('cached_input_tokens') or 0)
                    res["output"] += int(usage.get('output_tokens') or 0)
                    res["reasoning_output"] += int(usage.get('reasoning_output_tokens') or 0)
        except OSError:
            continue

    return res


def collect_consumption(root, since):  # noqa: C901, PLR0915
    consumption_file = root / '.workspace/metrics/consumption.jsonl'
    res = {"events": 0, "by_role": {}, "by_provider": {}, "quota_limits": 0, "last_quota_limit": None}
    if consumption_file.is_symlink() or not consumption_file.is_file():
        return res

    def _update_stats(stats_dict, key, is_proxy, val_inp, val_cached, val_out, val_reasoning, dur_val, creation, total):  # noqa: PLR0913
        if key not in stats_dict:
            stats_dict[key] = {
                "calls": 0,
                "input": 0,
                "cached_input": 0,
                "output": 0,
                "reasoning": 0,
                "cache_creation": 0,
                "total_tokens": 0,
                "unknown_token_calls": 0,
                "proxy_calls": 0,
                "duration_seconds": 0.0,
            }
        entry = stats_dict[key]
        entry["calls"] += 1
        entry["input"] += val_inp
        entry["cached_input"] += val_cached
        entry["output"] += val_out
        entry["reasoning"] += val_reasoning
        entry["cache_creation"] = (
            entry["cache_creation"] + creation if entry["cache_creation"] is not None and creation is not None else None
        )
        entry["total_tokens"] = entry["total_tokens"] + total if entry["total_tokens"] is not None and total is not None else None
        entry["unknown_token_calls"] += int(total is None)
        entry["proxy_calls"] += is_proxy
        entry["duration_seconds"] = round(entry["duration_seconds"] + dur_val, 3)

    try:
        with consumption_file.open('r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if not isinstance(record, dict):
                    continue

                at_val = record.get('at')
                if since is not None:
                    rec_date = parse_record_date(at_val)
                    if rec_date is None or rec_date < since:
                        continue

                role = record.get('role')
                provider = record.get('provider')
                if not isinstance(role, str) or not role:
                    continue
                if not isinstance(provider, str) or not provider:
                    continue

                res["events"] += 1

                tokens = record.get('tokens')
                total = token_total(tokens, provider)
                creation = tokens.get('cache_creation') if isinstance(tokens, dict) else None
                if creation is None and provider != 'claude':
                    creation = 0
                if type(creation) is not int or creation < 0:
                    creation = None
                if not isinstance(tokens, dict):
                    all_tokens_null = True
                    inp = cached = out = reasoning = None
                else:
                    inp = tokens.get('input')
                    cached = tokens.get('cached_input')
                    out = tokens.get('output')
                    reasoning = tokens.get('reasoning')
                    all_tokens_null = (
                        inp is None and cached is None and out is None and reasoning is None and tokens.get('cache_creation') is None
                    )

                is_proxy = 1 if all_tokens_null else 0
                val_inp = int(inp) if (isinstance(inp, (int, float)) and not isinstance(inp, bool)) else 0
                val_cached = int(cached) if (isinstance(cached, (int, float)) and not isinstance(cached, bool)) else 0
                val_out = int(out) if (isinstance(out, (int, float)) and not isinstance(out, bool)) else 0
                val_reasoning = int(reasoning) if (isinstance(reasoning, (int, float)) and not isinstance(reasoning, bool)) else 0

                dur_val = 0.0
                proxy = record.get('proxy')
                dur = None
                if isinstance(proxy, dict):
                    dur = proxy.get('duration_seconds')
                if dur is None:
                    dur = record.get('duration_seconds')
                if isinstance(dur, (int, float)) and not isinstance(dur, bool):
                    dur_val = float(dur)

                quota = record.get('quota')
                if isinstance(quota, dict) and quota.get('limit_reached') is True:
                    res["quota_limits"] += 1
                    detail_val = quota.get("detail")
                    res["last_quota_limit"] = {
                        "at": str(at_val) if at_val is not None else None,
                        "provider": str(provider),
                        "detail": str(detail_val) if detail_val is not None else None,
                    }

                _update_stats(res["by_role"], role, is_proxy, val_inp, val_cached, val_out, val_reasoning, dur_val, creation, total)
                _update_stats(res["by_provider"], provider, is_proxy, val_inp, val_cached, val_out, val_reasoning, dur_val, creation, total)
    except OSError:
        pass

    res["by_role"] = dict(sorted(res["by_role"].items()))
    res["by_provider"] = dict(sorted(res["by_provider"].items()))
    return res


def collect_git(root, since):
    try:
        cmd_c = ['git', '-C', str(root), 'rev-list', '--count']
        cmd_m = ['git', '-C', str(root), 'rev-list', '--count', '--merges']
        if since is not None:
            since_arg = f'--since={since.isoformat()}'
            cmd_c.append(since_arg)
            cmd_m.append(since_arg)
        cmd_c.append('HEAD')
        cmd_m.append('HEAD')

        rc_c = subprocess.run(cmd_c, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        rc_m = subprocess.run(cmd_m, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if rc_c.returncode == 0 and rc_m.returncode == 0:
            return {"commits": int(rc_c.stdout.strip() or 0), "merges": int(rc_m.stdout.strip() or 0)}
    except Exception:
        pass
    return {"commits": 0, "merges": 0}


def collect_bench(root):
    bench_file = root / '.workspace/metrics/advisor-bench.json'
    if not bench_file.is_symlink() and bench_file.is_file():
        try:
            return {"advisor": json.loads(bench_file.read_text(encoding='utf-8'))}
        except Exception:
            pass
    return {"advisor": None}


def collect_all_metrics(root, since=None, claude_dir=None, claude_roots=None):
    root_resolved = root.resolve()
    cl_proj, cl_orch = collect_claude_tokens(root, claude_dir, since, orchestrator_roots=claude_roots)
    return {
        "project": root_resolved.name or str(root_resolved),
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "since": since.isoformat() if since is not None else None,
        "delegation": collect_delegation(root, since),
        "reading": reading.collect(root, since),
        "advisor": collect_advisor(root, since),
        "tokens": {"claude": cl_proj, "claude_orchestrator": cl_orch, "codex": collect_codex(root, since)},
        "consumption": collect_consumption(root, since),
        "git": collect_git(root, since),
        "bench": collect_bench(root),
    }


def snapshot(root, data):
    date_str = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d')
    docs_dir = root / 'docs'
    metrics_dir = docs_dir / 'metrics'

    for p in (docs_dir, metrics_dir):
        if p.is_symlink():
            raise ValueError(f"Lien symbolique interdit : {p}")
    metrics_dir.mkdir(parents=True, exist_ok=True)
    if metrics_dir.is_symlink():
        raise ValueError(f"Lien symbolique interdit : {metrics_dir}")

    candidate = metrics_dir / f"{date_str}.md"
    suffix = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = metrics_dir / f"{date_str}-{suffix}.md"
        suffix += 1

    md_content = format_markdown(data)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(candidate, flags, 0o644)
    with open(fd, 'w', encoding='utf-8') as f:
        f.write(md_content)

    ws_dir = root / '.workspace'
    hist_dir = ws_dir / 'metrics'
    if ws_dir.is_symlink() or hist_dir.is_symlink():
        raise ValueError("Lien symbolique interdit dans .workspace")
    hist_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    hist_dir.chmod(0o700)
    hist_file = hist_dir / 'history.jsonl'
    if hist_file.is_symlink():
        raise ValueError(f"Lien symbolique interdit : {hist_file}")

    h_flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
    h_fd = os.open(hist_file, h_flags, 0o600)
    with open(h_fd, 'a', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False) + '\n')
    hist_file.chmod(0o600)

    try:
        rel = candidate.relative_to(root)
        print(str(rel))
    except ValueError:
        print(str(candidate))


def history(root, markdown=False):  # noqa: C901
    hist_file = root / '.workspace/metrics/history.jsonl'
    if hist_file.is_symlink() or not hist_file.is_file():
        print("Aucun historique disponible.")
        return 0
    try:
        lines = hist_file.read_text(encoding='utf-8').splitlines()
    except OSError:
        print("Aucun historique disponible.")
        return 0
    records = []
    for line in lines:
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    if not records:
        print("Aucun historique disponible.")
        return 0

    headers = ["Date", "Tâches", "1er essai", "Tentatives", "Conseiller", "Claude out", "Codex out", "Quotas"]
    rows = []
    for r in records:
        dt = r.get("since") or r.get("generated_at", "")[:10] or "-"
        tasks = fmt_int(r.get("delegation", {}).get("tasks", 0))
        first = fmt_pct(r.get("delegation", {}).get("first_try_success_rate"))
        att = fmt_float(r.get("delegation", {}).get("mean_attempts"))
        adv = fmt_pct(r.get("advisor", {}).get("answer_rate"))
        cl_out = fmt_int(r.get("tokens", {}).get("claude", {}).get("output", 0))
        cx_out = fmt_int(r.get("tokens", {}).get("codex", {}).get("output", 0))
        quotas = fmt_int((r.get("consumption") or {}).get("quota_limits", 0))
        rows.append([dt, tasks, first, att, adv, cl_out, cx_out, quotas])

    if markdown:
        print("| " + " | ".join(headers) + " |")
        print("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            print("| " + " | ".join(row) + " |")
    else:
        widths = [max(len(h), max(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
        fmt_header = " | ".join(f"{h:<{widths[i]}}" for i, h in enumerate(headers))
        sep = "-+-".join("-" * widths[i] for i in range(len(headers)))
        print(fmt_header)
        print(sep)
        for row in rows:
            print(" | ".join(f"{col:<{widths[i]}}" for i, col in enumerate(row)))
    return 0


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Tableau de bord chiffré du projet")
    parser.add_argument("root", type=Path, help="Racine du projet")
    parser.add_argument("--since", type=datetime.date.fromisoformat, default=None, help="Filtrer à partir d'une date (AAAA-MM-JJ)")
    parser.add_argument("--json", action="store_true", help="Sortie JSON de l'interface commune")
    parser.add_argument("--markdown", action="store_true", help="Sortie Markdown")
    parser.add_argument(
        "--snapshot", action="store_true", help="Écrit le Markdown dans docs/metrics/AAAA-MM-JJ.md et archive dans history.jsonl"
    )
    parser.add_argument("--history", action="store_true", help="Affiche l'évolution des indicateurs principaux à partir de history.jsonl")
    parser.add_argument("--claude-projects-dir", type=Path, default=None, help="Dossier alternatif des projets Claude (~/.claude/projects)")
    parser.add_argument(
        "--claude-root",
        action="append",
        default=[],
        type=Path,
        help="Racine additionnelle de cwd pour comptabiliser les tokens Claude de l'orchestrateur (répétable ; s'ajoute aux racines de .workspace/metrics/claude-roots.txt)",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()

    if args.history:
        return history(root, markdown=args.markdown)

    data = collect_all_metrics(root, since=args.since, claude_dir=args.claude_projects_dir, claude_roots=args.claude_root)

    if args.snapshot:
        snapshot(root, data)
        return 0

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    if args.markdown:
        print(format_markdown(data), end="")
        return 0

    print(format_text(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
