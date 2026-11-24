"""Cascade Haiku -> Sonnet -> Opus : on monte d'un étage seulement si les tests cachés échouent.

Usage : cascade.py <racine> <liste> <fiche>. Python 3.14, bibliothèque standard.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import events
import workdir
from failure_cause import NO_CAUSE, cause_of

RESUME_RATIO = 2 / 3
MODELS = ('haiku', 'sonnet', 'opus')
STOP_OUTCOMES = ('out_of_scope', 'control_tampered', 'waiting_answer', 'quota')
DONE_OUTCOMES = ('done', 'done_with_warning')
RESUME_HEADER = 'Étage précédent (repris) :'
RESTART_HEADER = 'Étage précédent (raté, pour lecture seulement) :'
USAGE = 'usage: cascade.py <racine> <haiku,sonnet,opus> <fiche-de-tâche>'


def parse_models(text: str) -> list[str]:
    names = text.split(',') if isinstance(text, str) else []
    if not 1 <= len(names) <= len(MODELS) or len(set(names)) != len(names) or any(n not in MODELS for n in names):
        raise ValueError('liste de modèles invalide : ' + repr(text))
    return ['claude-' + name for name in names]


def pass_ratio(output: str) -> float | None:
    ran = re.search(r'^Ran (\d+) tests?\b', output or '', re.M)
    if ran is None or int(ran.group(1)) < 1:
        return None
    total = int(ran.group(1))
    tail = output[ran.end() :]
    failed = re.search(r'^FAILED \(([^)]*)\)', tail, re.M)
    if failed:
        fields = dict(re.findall(r'(\w+)=(\d+)', failed.group(1)))
        bad = int(fields.get('failures', 0)) + int(fields.get('errors', 0))
        return max(total - bad, 0) / total
    if re.search(r'^OK\b', tail, re.M):
        return 1.0
    return None


def check_worktree(root, task_id, worktree) -> None:
    """Refuse (ValueError) un clone qui n'est pas celui de la tâche : lien, autre chemin ou état altéré."""
    path = Path(worktree)
    if path.is_symlink():
        raise ValueError('le worktree est un lien symbolique : ' + str(path))
    if path.resolve() != workdir.task_worktree(root, task_id).resolve():
        raise ValueError('le worktree ne correspond pas à celui de la tâche ' + str(task_id))
    try:
        state = json.loads((Path(root) / '.workspace/orchestration/tasks' / task_id / 'state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError("état de la tâche illisible : %s" % error)
    if not isinstance(state, dict) or state.get('task_id') != task_id:
        raise ValueError("l'état de la tâche ne porte pas l'identifiant " + str(task_id))


def reset_to_base(worktree, base) -> None:
    if not isinstance(base, str) or not re.fullmatch(r'[0-9a-fA-F]{7,40}', base):
        raise ValueError('base invalide (hexadécimal de 7 à 40 caractères attendu) : ' + repr(base))
    for name, command in (
        ('reset', workdir.git_command('reset', '-q', '--hard', base, cwd=worktree)),
        ('clean', workdir.git_command('clean', '-fdq', cwd=worktree)),
    ):
        proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if proc.returncode:
            raise RuntimeError('git %s a échoué : %s' % (name, proc.stdout.strip()))


def run_stages(root, task_id: str, agents: list[str], runner: Callable[[str, str], dict], worktree, base) -> dict:  # noqa: PLR0913
    root = Path(root)
    stages = []
    context = ''
    for number, agent in enumerate(agents, 1):
        result = runner(agent, context)
        outcome = result.get('outcome')
        ratio = None
        if outcome in DONE_OUTCOMES or outcome in STOP_OUTCOMES:
            following = 'stop'
        else:
            ratio = pass_ratio(result.get('acceptance') or '')
            if number == len(agents):
                following = 'stop'
            elif ratio and ratio >= RESUME_RATIO:
                following = 'resume'
                context = '\n\n'.join([RESUME_HEADER, result.get('diff') or '', result.get('acceptance') or ''])
            else:
                following = 'restart'
                reset_to_base(worktree, base)
                context = '\n\n'.join([RESTART_HEADER, result.get('diff') or ''])
        stages.append(
            {
                'stage': number,
                'agent': agent,
                'outcome': outcome,
                'attempts': result.get('attempts'),
                'tokens': result.get('tokens'),
                'ratio': ratio,
                'next': following,
            }
        )
        detail = {
            'stage': number,
            'agent': agent,
            'outcome': outcome,
            'ratio': ratio,
            'next': following,
            'attempts': result.get('attempts'),
        }
        if outcome not in NO_CAUSE:
            detail['cause'] = cause_of(root, task_id, outcome)
        events.record(root, task_id, 'cascade_step', detail)
        if following == 'stop':
            break
    summary = {'outcome': stages[-1]['outcome'] if stages else None, 'stages': stages}
    run = root / '.workspace/run/delegate' / task_id
    run.mkdir(parents=True, exist_ok=True)
    (run / 'cascade.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8') if path.is_file() and not path.is_symlink() else ''
    except OSError:
        return ''


def make_runner(root: Path, task_path: Path, task_id: str, worktree=None):
    run = root / '.workspace/run/delegate' / task_id
    counter = [0]

    def runner(agent: str, context: str) -> dict:
        counter[0] += 1
        run.mkdir(parents=True, exist_ok=True)
        (run / 'acceptance.txt').unlink(missing_ok=True)
        env = dict(os.environ, WORKSPACE_AGENT_OVERRIDE=agent)
        env.pop('WORKSPACE_STAGE_CONTEXT', None)
        if context:
            file = run / ('cascade-context-%d.md' % counter[0])
            file.write_text(context, encoding='utf-8')
            env['WORKSPACE_STAGE_CONTEXT'] = str(file)
        code = subprocess.run([sys.executable, str(root / 'scripts/lib/delegate.py'), str(root), str(task_path)], env=env).returncode
        status_file = run / 'status.json'
        try:
            status = json.loads(status_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            status = {}
        status = status if isinstance(status, dict) else {}
        if status_file.is_file():
            shutil.copyfile(str(status_file), str(run / ('cascade-%d-%s.json' % (counter[0], agent))))
        metrics = status.get('metrics') if isinstance(status.get('metrics'), dict) else {}
        outcome = status.get('outcome')
        if code == 75 and outcome not in STOP_OUTCOMES:
            outcome = 'quota'
        if worktree is not None and outcome not in DONE_OUTCOMES and outcome not in STOP_OUTCOMES:
            try:
                check_worktree(root, task_id, worktree)
            except ValueError as error:
                print('cascade.py: ' + str(error), file=sys.stderr)
                outcome = 'control_tampered'
        return {
            'outcome': outcome or 'failed',
            'attempts': status.get('attempts', metrics.get('attempts')),
            'tokens': metrics.get('tokens'),
            'acceptance': read_text(run / 'acceptance.txt'),
            'diff': read_text(run / 'diff.txt'),
        }

    return runner


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(USAGE, file=sys.stderr)
        return 64
    root = Path(argv[0])
    try:
        agents = parse_models(argv[1])
    except ValueError as error:
        print('cascade.py: ' + str(error), file=sys.stderr)
        return 64
    task_path = Path(argv[2])
    try:
        sys.path.insert(0, str(root / 'scripts/lib'))
        from task import read_task

        task = read_task(task_path, root)
        task_id = task['id']
        state = json.loads((root / '.workspace/orchestration/tasks' / task_id / 'state.json').read_text(encoding='utf-8'))
        worktree = workdir.state_worktree(root, state)
        check_worktree(root, task_id, worktree)
        base = state.get('base_commit')
        if state.get('status') != 'prepared' or not worktree.is_dir() or not isinstance(base, str) or not base:
            raise ValueError("la tâche n'est pas préparée (état, worktree ou base absents)")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('cascade.py: ' + str(error), file=sys.stderr)
        return 64
    result = run_stages(root, task_id, agents, make_runner(root, task_path, task_id, worktree), worktree, base)
    if result['outcome'] in DONE_OUTCOMES:
        return 0
    return 75 if result['outcome'] == 'quota' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
