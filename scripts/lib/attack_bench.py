"""Banc d'attaque du conteneur de tâche (Isolation exchange).

Tente, depuis le conteneur, chaque action interdite.
Chaque tentative doit ÉCHOUER (bloquée).
Une tentative qui réussit est une FAILLE.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import select
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

LIB_DIR = Path(__file__).resolve().parent
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import attack_host  # noqa: E402
import bench_rule  # noqa: E402
import events  # noqa: E402
import orchestrator  # noqa: E402
import procs  # noqa: E402
import sandbox  # noqa: E402
import workdir  # noqa: E402
from attack_checks import (  # noqa: E402,F401
    check_cgroup_memory,
    check_cgroup_pids,
    check_fail_is_blocked,
    check_link_from_container,
    check_network,
)

try:
    from secrets import known_values, mask_secrets

    from safety import check_no_symlinks, write_file_secure
except ImportError:

    def known_values(project_root: Path) -> list[str]:
        return []

    def mask_secrets(text: str, known: list[str]) -> str:
        return text


@dataclass
class Attack:
    id: str
    category: str
    description: str
    command: str
    check: Callable[[int, str, str], tuple[str, str]]


@dataclass
class AttackResult:
    attack: Attack
    status: str
    reason: str
    returncode: int
    output: str


ATTACKS: list[Attack] = [
    # 1. Écriture hors du worktree
    Attack(
        id='write-etc',
        category='Écriture hors du worktree',
        description='Écriture dans /etc/attaque',
        command='touch /etc/attaque',
        check=check_fail_is_blocked,
    ),
    Attack(
        id='write-root',
        category='Écriture hors du worktree',
        description='Écriture dans /attaque',
        command='touch /attaque',
        check=check_fail_is_blocked,
    ),
    Attack(
        id='write-bin',
        category='Écriture hors du worktree',
        description='Écriture dans /usr/local/bin/attaque',
        command='touch /usr/local/bin/attaque',
        check=check_fail_is_blocked,
    ),
    Attack(
        id='write-parent-work',
        category='Écriture hors du worktree',
        description='Écriture dans /work/../attaque',
        command='touch /work/../attaque',
        check=check_fail_is_blocked,
    ),
    Attack(
        id='write-worktree-scripts',
        category='Écriture hors du worktree',
        description="Écriture dans /work/scripts (l'hôte en exécute la copie)",
        command='echo x >> /work/scripts/run-in-sandbox.sh',
        check=check_fail_is_blocked,
    ),
    Attack(
        id='link-from-container',
        category='Écriture hors du worktree',
        description="Lien symbolique vers l'hôte dans /work (course du lien : l'hôte lit le worktree après son contrôle)",
        command=(
            'ok=1; '
            'ln -s /etc/passwd /work/.lien-attaque 2>/dev/null && ok=0; '
            'rm -f /work/.lien-attaque; '
            'python3 -c \'import os; os.symlink("/etc/passwd", "/work/.lien-attaque")\' 2>/dev/null && ok=0; '
            'rm -f /work/.lien-attaque; '
            'exit $ok'
        ),
        check=check_link_from_container,
    ),
    # 2. Lecture du dépôt Git masqué
    Attack(
        id='read-git-objects',
        category='Lecture Git masqué',
        description='Lecture de /work/.git/objects',
        command='ls /work/.git/objects',
        check=check_fail_is_blocked,
    ),
    Attack(
        id='read-git-config',
        category='Lecture Git masqué',
        description='Lecture de /work/.git/config',
        command='cat /work/.git/config',
        check=check_fail_is_blocked,
    ),
    # 3. Réseau
    Attack(
        id='net-outbound',
        category='Réseau',
        description='Résolution DNS et connexion sortante',
        command=(
            'if command -v node >/dev/null 2>&1; then '
            'node -e \'Promise.any([fetch("http://1.1.1.1"), fetch("https://example.com")]).then(() => process.exit(0)).catch(() => process.exit(1))\'; '
            'elif command -v python3 >/dev/null 2>&1; then '
            'python3 -c \'import sys, urllib.request; '
            'urls = ["http://1.1.1.1", "https://example.com"]; '
            'any_ok = False; '
            'for u in urls: '
            '  try: '
            '    urllib.request.urlopen(u, timeout=5); any_ok = True; break; '
            '  except Exception: pass; '
            'sys.exit(0 if any_ok else 1)\'; '
            'elif command -v curl >/dev/null 2>&1; then '
            'curl -fsSL --connect-timeout 5 http://1.1.1.1 || curl -fsSL --connect-timeout 5 https://example.com; '
            'elif command -v wget >/dev/null 2>&1; then '
            'wget -q -T 5 -O - http://1.1.1.1 || wget -q -T 5 -O - https://example.com; '
            'else '
            'echo "NO_NET_TOOL: aucun outil réseau disponible (node, python3, curl, wget)" >&2; exit 125; '
            'fi'
        ),
        check=check_network,
    ),
    # 4. Docker de l'hôte
    Attack(
        id='docker-sock',
        category='Docker de l\'hôte',
        description='Présence ou usage de /var/run/docker.sock',
        command='test -S /var/run/docker.sock || test -e /var/run/docker.sock',
        check=check_fail_is_blocked,
    ),
    # 5. Privilèges
    Attack(
        id='priv-root-user',
        category='Privilèges',
        description='Exécution en tant que root (id -u == 0)',
        command='test "$(id -u)" -eq 0',
        check=check_fail_is_blocked,
    ),
    Attack(
        id='priv-sudo',
        category='Privilèges',
        description='Élévation de privilèges via sudo (sudo -n true)',
        command='sudo -n true',
        check=check_fail_is_blocked,
    ),
    Attack(
        id='priv-su',
        category='Privilèges',
        description='Changement d\'utilisateur via su (su -c true)',
        command='su -c true',
        check=check_fail_is_blocked,
    ),
    Attack(
        id='priv-mount',
        category='Privilèges',
        description='Montage de système de fichiers (mount -t tmpfs none /mnt)',
        command='mount -t tmpfs none /mnt',
        check=check_fail_is_blocked,
    ),
    Attack(
        id='priv-chown',
        category='Privilèges',
        description='Donner un fichier à root (CAP_CHOWN) ; /work est exclu car Docker Desktop y simule chown sans effet sur le Mac',
        command='touch /tmp/attaque_chown && chown root /tmp/attaque_chown',
        check=check_fail_is_blocked,
    ),
    # 6. Secrets de l'hôte
    Attack(
        id='secrets-host-dirs',
        category='Secrets de l\'hôte',
        description='Présence de dossiers sensibles (~/.ssh, ~/.codex, ~/.claude, ~/.config/workspace-ia)',
        command='test -e "$HOME/.ssh" || test -e "$HOME/.codex" || test -e "$HOME/.claude" || test -e "$HOME/.config/workspace-ia"',
        check=check_fail_is_blocked,
    ),
    Attack(
        id='secrets-env',
        category='Secrets de l\'hôte',
        description='Variables d\'environnement sensibles (TOKEN, KEY, etc.)',
        command='env | cut -d= -f1 | grep -E -i \'TOKEN|KEY|SECRET|PASSWORD|OPENAI|ANTHROPIC|GITHUB\'',
        check=check_fail_is_blocked,
    ),
    # 7. Ressources
    Attack(
        id='resource-cgroup-pids',
        category='Ressources',
        description='Limite de processus cgroup (/sys/fs/cgroup/pids.max != max)',
        command=(
            'if [ ! -f /sys/fs/cgroup/pids.max ]; then '
            'echo "NO_CGROUP: /sys/fs/cgroup/pids.max introuvable" >&2; exit 125; '
            'fi; '
            'val=$(cat /sys/fs/cgroup/pids.max 2>/dev/null); '
            'if [ "$val" = "max" ]; then exit 0; else exit 1; fi'
        ),
        check=check_cgroup_pids,
    ),
    Attack(
        id='resource-cgroup-memory',
        category='Ressources',
        description='Limite de mémoire cgroup (/sys/fs/cgroup/memory.max != max)',
        command=(
            'if [ ! -f /sys/fs/cgroup/memory.max ]; then '
            'echo "NO_CGROUP: /sys/fs/cgroup/memory.max introuvable" >&2; exit 125; '
            'fi; '
            'val=$(cat /sys/fs/cgroup/memory.max 2>/dev/null); '
            'if [ "$val" = "max" ]; then exit 0; else exit 1; fi'
        ),
        check=check_cgroup_memory,
    ),
    Attack(
        id='resource-tmp-overflow',
        category='Ressources',
        description='Écriture de 2 Go dans /tmp (tmpfs borné)',
        command='dd if=/dev/zero of=/tmp/attaque_2g bs=1M count=2048 2>/dev/null; ret=$?; rm -f /tmp/attaque_2g; exit $ret',
        check=check_fail_is_blocked,
    ),
]


INTERRUPTION = Attack(
    id='interruption',
    category='Interruption',
    description='Délégation tuée par SIGKILL : conteneur, issue et agent nettoyés',
    command='',
    check=check_fail_is_blocked,
)
USAGE = 'Usage : attack-bench.sh <task-id> [--markdown]'
EVIDENCE_DIR = '.workspace/run/delegate'
READY_TIMEOUT = 20
GROUP_TIMEOUT = 5
POLL_INTERVAL = 0.05

# Délégation factice : même ligne de commande que delegate.py (argv : delegate.py, racine, fiche).
# Elle écrit un statut running, lance un agent gardé et dort jusqu'au SIGKILL.
DELEGATE_STUB = """
import datetime, json, os, subprocess, sys, time
sys.path.insert(0, __LIB__)
import events
import procs
root, task_file = sys.argv[2], sys.argv[3]
task_id = os.path.basename(task_file)[:-len('.md')]
evidence = os.path.join(root, '.workspace', 'run', 'delegate', task_id)
os.makedirs(evidence, mode=0o700)
status = {'id': task_id, 'agent': 'factice', 'outcome': 'running', 'attempts': 1,
          'started_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
fd = os.open(os.path.join(evidence, 'status.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w', encoding='utf-8') as stream:
    json.dump(status, stream)
events.record(root, task_id, 'attempt_started', {'attempt': 1, 'phase': 'attempt', 'resumed': False})
agent = subprocess.Popen(['sleep', '300'], start_new_session=True)
guardian = procs.start_guardian(agent.pid)
print('ready %d' % agent.pid, flush=True)
time.sleep(300)
"""


def _interruption(status: str, reason: str = '') -> AttackResult:
    return AttackResult(attack=INTERRUPTION, status=status, reason=reason, returncode=0, output='')


def _stop_container(root: Path, task_id: str, sandbox_sh: Path) -> None:
    subprocess.run(['sh', str(sandbox_sh), 'stop', task_id], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _launch_stub(root: Path, task_id: str) -> subprocess.Popen:
    code = DELEGATE_STUB.replace('__LIB__', repr(str(LIB_DIR)))
    task_file = root / '.workspace/tasks' / (task_id + '.md')
    argv = [sys.executable, '-c', code, 'delegate.py', str(root), str(task_file)]
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)


def _read_ready(delegate: subprocess.Popen) -> int | None:
    """Pid de l'agent annoncé par `ready <pid>` dans le délai ; `None` sinon."""
    ready, _, _ = select.select([delegate.stdout], [], [], READY_TIMEOUT)
    parts = delegate.stdout.readline().split() if ready else []
    if len(parts) == 2 and parts[0] == 'ready' and parts[1].isdigit():
        return int(parts[1])
    return None


def _container_listed(name: str) -> bool:
    docker_bin = shutil.which('docker') or 'docker'
    done = subprocess.run(
        [docker_bin, 'ps', '-a', '--filter', 'name=' + name, '--format', '{{.Names}}'],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return name in done.stdout.splitlines()


def _status_outcome(root: Path, task_id: str) -> str | None:
    try:
        data = json.loads((root / EVIDENCE_DIR / task_id / 'status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data.get('outcome') if isinstance(data, dict) else None


def _interrupted_event(root: Path, task_id: str) -> bool:
    return any(
        item['event'] == 'outcome' and (item.get('detail') or {}).get('outcome') == 'interrupted'
        for item in events.read(root, task_id=task_id)
    )


def _group_alive_after_wait(pgid: int) -> bool:
    deadline = time.monotonic() + GROUP_TIMEOUT
    while procs.group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
    return procs.group_alive(pgid)


def _missed_checks(root: Path, task_id: str, name: str, agent_pid: int) -> list[str]:
    checks = (
        ('conteneur encore présent', _container_listed(name)),
        ('issue non écrite', _status_outcome(root, task_id) != 'interrupted'),
        ('événement absent', not _interrupted_event(root, task_id)),
        ('agent encore en vie', _group_alive_after_wait(agent_pid)),
    )
    return [label for label, failed in checks if failed]


def _cleanup(root: Path, task_id: str, sandbox_sh: Path, delegate: subprocess.Popen, agent_pid: int | None) -> None:
    if delegate.poll() is None:
        delegate.kill()
        delegate.wait()
    delegate.stdout.close()
    if agent_pid is not None and procs.group_alive(agent_pid):
        procs.kill_group(agent_pid)
    _stop_container(root, task_id, sandbox_sh)


def run_interruption_scenario(root: Path, task_id: str, sandbox_sh: Path) -> AttackResult:
    """Tue une délégation factice par SIGKILL, répare, puis constate qu'il ne reste rien."""
    name = sandbox.get_container_name(root, task_id)
    start = subprocess.run(
        ['sh', str(sandbox_sh), 'start', task_id], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if start.returncode != 0:
        return _interruption('non testable', start.stderr.strip())
    if os.path.lexists(root / EVIDENCE_DIR / task_id):
        _stop_container(root, task_id, sandbox_sh)
        return _interruption('non testable', 'dossier de preuves existant')
    delegate = _launch_stub(root, task_id)
    agent_pid = None
    try:
        agent_pid = _read_ready(delegate)
        if agent_pid is None:
            return _interruption('non testable', 'délégation factice sans ligne ready')
        delegate.kill()
        delegate.wait()
        orchestrator.repair(root)
        missed = _missed_checks(root, task_id, name, agent_pid)
    finally:
        _cleanup(root, task_id, sandbox_sh, delegate, agent_pid)
    if missed:
        return _interruption('FAILLE', 'constats manqués : ' + ', '.join(missed))
    return _interruption('bloquée')


def check_docker_available() -> bool:
    docker_bin = shutil.which('docker')
    if not docker_bin:
        return False
    try:
        res = subprocess.run([docker_bin, 'info'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return res.returncode == 0
    except Exception:
        return False


def validate_task_exists(root: Path, task_id: str) -> tuple[Path, Path, dict[str, Any]]:
    if not isinstance(task_id, str) or not re.fullmatch(r'[a-z0-9-]+', task_id):
        print(f"Identifiant de tâche invalide : '{task_id}'", file=sys.stderr)
        sys.exit(64)

    check_no_symlinks(root, root)

    task_dir = root / '.workspace/orchestration/tasks' / task_id
    state_file = task_dir / 'state.json'

    if task_dir.is_symlink() or not task_dir.is_dir() or state_file.is_symlink() or not state_file.is_file():
        print(f"Tâche inconnue ou non enregistrée dans le registre : {task_id}", file=sys.stderr)
        sys.exit(64)

    check_no_symlinks(state_file, root)

    try:
        state = json.loads(state_file.read_text(encoding='utf-8'))
    except Exception as exc:
        print(f"État de tâche corrompu pour {task_id} : {exc}", file=sys.stderr)
        sys.exit(64)

    try:
        worktree_path = workdir.state_worktree(root, state)
    except ValueError:
        print(f"Champ 'worktree' manquant ou invalide pour {task_id}", file=sys.stderr)
        sys.exit(64)

    if worktree_path.is_symlink() or not worktree_path.is_dir():
        print(f"Répertoire de worktree introuvable pour {task_id} : {worktree_path}", file=sys.stderr)
        sys.exit(64)

    try:
        workdir.check_no_symlinks_below(workdir.work_base(root), worktree_path)
    except ValueError:
        # Worktree d'intégration (relatif) ou tâche ancienne : sous la racine du projet.
        check_no_symlinks(worktree_path, root)
    return task_dir, worktree_path, state


def find_sandbox_script(root: Path) -> Path:
    candidates = [
        LIB_DIR.parent / 'sandbox.sh',
        root / 'scripts/sandbox.sh',
        root / 'template/scripts/sandbox.sh',
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError("Script sandbox.sh introuvable.")


def run_attack(sandbox_sh: Path, root: Path, task_id: str, attack: Attack, secrets_inventory: list[str]) -> AttackResult:
    exec_cmd = ['sh', str(sandbox_sh), 'exec', task_id, '--', 'sh', '-c', attack.command]
    try:
        proc = subprocess.run(exec_cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        returncode = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = exc.stdout or ''
        stderr = (exc.stderr or '') + '\nDélai maximal (30 s) dépassé'
    except Exception as exc:
        returncode = 126
        stdout = ''
        stderr = str(exc)

    raw_output = stdout
    if stderr:
        if raw_output and not raw_output.endswith('\n'):
            raw_output += '\n'
        raw_output += stderr
    raw_output = raw_output.strip()

    masked = mask_secrets(raw_output, secrets_inventory)
    truncated = masked[:300] + ('...' if len(masked) > 300 else '')

    status, reason = attack.check(returncode, stdout, stderr)
    return AttackResult(attack=attack, status=status, reason=reason, returncode=returncode, output=truncated)


def print_text_table(results: list[AttackResult]) -> None:
    headers = ['Attaque', 'Catégorie', 'Résultat']
    rows = []
    for r in results:
        res_display = r.status
        if r.reason:
            res_display += f" ({r.reason})"
        rows.append([r.attack.id, r.attack.category, res_display])

    col_w = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_w[i] = max(col_w[i], len(val))

    header_line = ' | '.join(h.ljust(col_w[i]) for i, h in enumerate(headers))
    sep_line = '-+-'.join('-' * col_w[i] for i in range(len(headers)))
    print(header_line)
    print(sep_line)
    for row in rows:
        print(' | '.join(val.ljust(col_w[i]) for i, val in enumerate(row)))

    blocked = sum(1 for r in results if r.status == 'bloquée')
    total = len(results)
    print(f"\nAttaques bloquées : {blocked}/{total}")


def save_markdown_report(root: Path, task_id: str, image: str, results: list[AttackResult], date_str: str) -> Path:
    docs_dir = root / 'docs' / 'security'
    check_no_symlinks(docs_dir, root)
    docs_dir.mkdir(parents=True, exist_ok=True)
    check_no_symlinks(docs_dir, root)

    base_name = f"attack-bench-{date_str}"
    target_path = docs_dir / f"{base_name}.md"
    counter = 1
    while target_path.exists():
        target_path = docs_dir / f"{base_name}-{counter}.md"
        counter += 1

    check_no_symlinks(target_path, root)

    blocked = sum(1 for r in results if r.status == 'bloquée')
    total = len(results)
    failles = [r.attack.id for r in results if r.status == 'FAILLE']
    status_str = "SUCCÈS (aucune faille)" if not failles else f"ÉCHEC ({len(failles)} faille(s) détectée(s))"

    lines = [
        f"# Rapport du banc d'attaque — Tâche `{task_id}`",
        "",
        f"- **Date** : {date_str}",
        f"- **Image** : `{image}`",
        f"- **Statut global** : {status_str}",
        f"- **Attaques bloquées** : {blocked}/{total}",
        "",
        "| Attaque | Catégorie | Description | Résultat | Sortie (tronquée) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in results:
        res_display = r.status
        if r.reason:
            res_display += f" ({r.reason})"
        output_display = r.output.replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {r.attack.id} | {r.attack.category} | {r.attack.description} | {res_display} | {output_display} |")

    lines.append("")
    content = '\n'.join(lines)

    # Création exclusive : n'écrase jamais
    with open(target_path, 'x', encoding='utf-8') as f:
        f.write(content)

    return target_path


def save_metrics_json(root: Path, image: str, results: list[AttackResult], now_iso: str) -> Path:
    metrics_dir = root / '.workspace' / 'metrics'
    check_no_symlinks(metrics_dir, root)
    metrics_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    metrics_dir.chmod(0o700)
    check_no_symlinks(metrics_dir, root)

    metrics_file = metrics_dir / 'attack-bench.json'
    check_no_symlinks(metrics_file, root)

    blocked = sum(1 for r in results if r.status == 'bloquée')
    total = len(results)
    failles = [r.attack.id for r in results if r.status == 'FAILLE']
    non_testables = [r.attack.id for r in results if r.status == 'non testable']

    payload = {"date": now_iso, "image": image, "blocked": blocked, "total": total, "failles": failles, "non_testables": non_testables}

    metrics_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    metrics_file.chmod(0o600)
    return metrics_file


def current_commit(root: Path) -> str | None:
    try:
        res = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError:
        return None
    commit = res.stdout.strip()
    return commit if res.returncode == 0 and commit else None


def record_last_bench(root: Path, blocked: int, total: int, failles: list[str], now_iso: str) -> Path:
    security_dir = root / '.workspace' / 'security'
    check_no_symlinks(security_dir, root)
    security_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    security_dir.chmod(0o700)
    check_no_symlinks(security_dir, root)

    record_file = root / bench_rule.RECORD
    check_no_symlinks(record_file, root)

    payload = {
        "commit": current_commit(root),
        "date": now_iso,
        "score": f"{blocked}/{total}",
        "blocked": blocked,
        "total": total,
        "failles": failles,
        "green": not failles,
    }

    write_file_secure(record_file, json.dumps(payload, indent=2, ensure_ascii=False))
    return record_file


def host_results(root: Path, task_id: str, worktree_path: Path, state: dict[str, Any]) -> list[AttackResult]:
    """Scénarios côté hôte, rangés comme les autres résultats du banc."""
    return [
        AttackResult(
            attack=Attack(id=r['id'], category=r['category'], description=r['description'], command='', check=check_fail_is_blocked),
            status=r['status'],
            reason=r['reason'],
            returncode=0,
            output='',
        )
        for r in attack_host.run_host_scenarios(root, task_id, worktree_path, state)
    ]


def parse_args(argv: list[str]) -> tuple[Path, str, bool, bool]:
    if not argv:
        print(USAGE, file=sys.stderr)
        sys.exit(64)

    root = Path(argv[0]).resolve()
    rest = argv[1:]
    markdown = '--markdown' in rest
    host = '--host' in rest  # accepté, sans effet : les scénarios hôte sont toujours joués
    non_flags = [arg for arg in rest if not arg.startswith('-')]

    if len(non_flags) != 1:
        print(USAGE, file=sys.stderr)
        sys.exit(64)

    task_id = non_flags[0]
    return root, task_id, markdown, host


def run_attack_bench(root: Path, task_id: str, markdown: bool, host: bool = True) -> int:
    task_dir, worktree_path, state = validate_task_exists(root, task_id)

    if not check_docker_available():
        print("Erreur : Docker ou le démon Docker est indisponible.", file=sys.stderr)
        return 4

    sandbox_sh = find_sandbox_script(root)

    # Démarrage du conteneur
    res_start = subprocess.run(
        ['sh', str(sandbox_sh), 'start', task_id], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if res_start.returncode != 0:
        print(f"Erreur au démarrage du conteneur : {res_start.stderr.strip()}", file=sys.stderr)
        return 4

    # Lecture de l'image
    image = 'inconnue'
    status_file = task_dir / 'sandbox/status.json'
    if status_file.is_file():
        try:
            status_data = json.loads(status_file.read_text(encoding='utf-8'))
            image = status_data.get('image', image)
        except Exception:
            pass

    secrets_inventory = known_values(root)
    results: list[AttackResult] = []

    try:
        for attack in ATTACKS:
            res = run_attack(sandbox_sh, root, task_id, attack, secrets_inventory)
            results.append(res)
    finally:
        # Arrêt idempotent garanti
        subprocess.run(['sh', str(sandbox_sh), 'stop', task_id], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    results.append(run_interruption_scenario(root, task_id, sandbox_sh))
    results.extend(host_results(root, task_id, worktree_path, state))

    # Affichage du tableau texte
    print_text_table(results)

    # Date ISO
    now = datetime.datetime.now(datetime.UTC)
    now_iso = now.isoformat()
    date_str = now.strftime('%Y-%m-%d')

    # Enregistrement des métriques JSON
    save_metrics_json(root, image, results, now_iso)

    # Enregistrement du dernier passage, lu par la porte (bench_rule)
    record_last_bench(
        root,
        sum(1 for r in results if r.status == 'bloquée'),
        len(results),
        [r.attack.id for r in results if r.status == 'FAILLE'],
        now_iso,
    )

    # Rapport Markdown si demandé
    if markdown:
        save_markdown_report(root, task_id, image, results, date_str)

    # Code de sortie : 0 si aucune faille, 1 s'il y a au moins une faille
    failles = [r for r in results if r.status == 'FAILLE']
    return 1 if failles else 0


def main() -> int:
    os.umask(0o077)
    root, task_id, markdown, host = parse_args(sys.argv[1:])
    return run_attack_bench(root, task_id, markdown, host)


if __name__ == '__main__':
    sys.exit(main())
