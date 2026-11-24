"""Verrou d'orchestration déclaratif et état d'ensemble (Python 3.14, stdlib seule).

`claim` déclare qu'une session orchestre ce dépôt dans `.workspace/orchestrator.json`,
`release` retire la déclaration, `beat` rafraîchit son battement de cœur, `status` résume
verrou, tâches actives, conteneurs et processus d'agents. Le verrou n'empêche aucune autre
commande : il ne fait que se voir. Il est vivant si son pid vit ou si son battement est récent.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import events
import safety
import sandbox
import task_phase

sys.dont_write_bytecode = True

LOCK_PATH = '.workspace/orchestrator.json'
TASKS_GLOB = '.workspace/orchestration/tasks/*/state.json'
CAMPAIGN_TASKS_GLOB = '.workspace/campaign/*/.workspace/orchestration/tasks/*/state.json'
ORCHESTRATOR_ID = 'orchestrator'
ACTIVE_STATUSES = ('prepared', 'running', 'merge_conflict', 'integration_failed')
SHELLS = ('sh', 'bash', 'zsh', 'dash', 'fish', 'ksh')
MAX_ANCESTORS = 16
AGENT_MARKERS = ('claude -p', 'campaign.py')
CODEX_EXEC = re.compile(r'\bcodex(@[\w.-]+)?\s+exec\s')
COMMAND_LIMIT = 160
SUBPROCESS_TIMEOUT = 10
REMOVE_TIMEOUT = 30
LAUNCHER_LIMIT = 64
BEAT_KEY = 'beat_at'
BEAT_FORMAT = '%Y-%m-%dT%H:%M:%SZ'
BEAT_PATTERN = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ')
BEAT_MAX_AGE = 600
BEAT_MIN_AGE = -60
DELEGATE_RUN_DIR = '.workspace/run/delegate'
DELEGATE_SCRIPT = 'delegate.py'
REPAIR_FLAG = '--repair'
SESSION_ID_VARIABLES = ('CLAUDE_CODE_SESSION_ID', 'CLAUDE_SESSION_ID')
SESSION_ID_FORMAT = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
EXIT_HELD = 3
EXIT_USAGE = 64
USAGE = (
    'usage : orchestrator.sh claim [--session NOM] [--model NOM] [--repos CHEMIN[,CHEMIN...]] '
    '[--by NOM] [--pid N] [--force]\n        orchestrator.sh release\n        orchestrator.sh beat\n'
    '        orchestrator.sh status [--repair]'
)


def _lock_path(root):
    path = root / LOCK_PATH
    safety.check_no_symlinks(path, root)
    return path


def _read_lock(path):
    """Contenu du verrou, ou `None` s'il est illisible ou n'est pas un objet JSON."""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_atomic(path, data):
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix='.orchestrator-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def _command_name(text):
    """Nom de commande nu : sans chemin ni tiret initial (`-zsh` de login)."""
    return os.path.basename(text.strip()).lstrip('-')


def _process_info(pid):
    """(pid parent, nom de commande) lus par `ps`, sinon par `/proc` ; `None` si illisible."""
    if shutil.which('ps'):
        try:
            done = subprocess.run(
                ['ps', '-o', 'ppid=,comm=', '-p', str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=SUBPROCESS_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        fields = done.stdout.split(None, 1)
        if done.returncode != 0 or len(fields) != 2 or not fields[0].isdigit():
            return None
        return int(fields[0]), _command_name(fields[1])
    try:
        stat = Path('/proc/%d/stat' % pid).read_text(encoding='utf-8')
    except (OSError, ValueError):
        return None
    # Le nom est entre parenthèses et peut en contenir : on coupe au dernier `)`.
    head, _, tail = stat.rpartition(')')
    fields = tail.split()
    if len(fields) < 2 or not fields[1].isdigit():
        return None
    return int(fields[1]), _command_name(head.partition('(')[2])


def _session_pid(start):
    """Premier ancêtre qui n'est pas un shell, à partir de `start` ; pid 1 n'est jamais retenu."""
    pid = start
    for _ in range(MAX_ANCESTORS):
        info = _process_info(pid)
        if info is None:
            break
        parent, name = info
        if name not in SHELLS:
            return pid
        if parent <= 1:
            break
        pid = parent
    return pid


def _valid_launcher(value):
    """Nom de lanceur : de 1 à LAUNCHER_LIMIT caractères, tous imprimables (pas de saut de ligne)."""
    return 0 < len(value) <= LAUNCHER_LIMIT and value.isprintable()


def _real_cwd():
    try:
        return os.path.realpath(os.getcwd())
    except OSError:
        return None


def claude_session_id(environ=None):
    """Identifiant de la session Claude : première variable présente et non vide, si c'est un UUID en minuscules."""
    environ = os.environ if environ is None else environ
    for name in SESSION_ID_VARIABLES:
        value = environ.get(name)
        if value:
            return value if SESSION_ID_FORMAT.fullmatch(value) else None
    return None


def claim(root, args):
    if args.by is not None and not _valid_launcher(args.by):
        print('orchestrator: --by doit compter de 1 à %d caractères imprimables' % LAUNCHER_LIMIT, file=sys.stderr)
        return EXIT_USAGE
    path = _lock_path(root)
    if os.path.lexists(path) and not args.force:
        held = _read_lock(path)
        name = held.get('session') if held else None
        print('verrou déjà pris par la session %s ; --force pour le remplacer' % (name or 'inconnue'), file=sys.stderr)
        return EXIT_HELD
    pid = args.pid if args.pid is not None else _session_pid(os.getppid())
    data = {
        'session': args.session or '%s-%d' % (socket.gethostname(), pid),
        'model': args.model or 'inconnu',
        'started_at': datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'pid': pid,
        'repos': [item.strip() for item in args.repos.split(',') if item.strip()] if args.repos else [str(root)],
        'launched_by': args.by,
        'cwd': _real_cwd(),
        'claude_session_id': claude_session_id(),
    }
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700)
    _write_atomic(path, data)
    events.record(
        root,
        ORCHESTRATOR_ID,
        'orchestrator_claimed',
        {
            'session': data['session'],
            'model': data['model'],
            'pid': pid,
            'launched_by': data['launched_by'],
            'cwd': data['cwd'],
            'claude_session_id': data['claude_session_id'],
        },
    )
    print('verrou pris : %s (%s)' % (data['session'], data['model']))
    return 0


def release(root, _args):
    path = _lock_path(root)
    if not os.path.lexists(path):
        print('aucun verrou')
        return 0
    held = _read_lock(path)
    path.unlink()
    events.record(root, ORCHESTRATOR_ID, 'orchestrator_released', {'session': held.get('session') if held else None})
    print('verrou libéré')
    return 0


def _pid_alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _beat_age(held):
    """Âge en secondes du battement de cœur du verrou, ou `None` s'il est absent ou mal formé."""
    text = held.get(BEAT_KEY)
    if not isinstance(text, str) or BEAT_PATTERN.fullmatch(text) is None:
        return None
    try:
        beat = datetime.datetime.strptime(text, BEAT_FORMAT).replace(tzinfo=datetime.UTC)
    except ValueError:
        return None
    return (datetime.datetime.now(datetime.UTC) - beat).total_seconds()


def _beat_recent(held):
    age = _beat_age(held)
    return age is not None and BEAT_MIN_AGE <= age < BEAT_MAX_AGE


def _alive(held):
    """Vivant si le pid vit ou si le battement de cœur est récent (hors WSL, le pid ne dit pas tout)."""
    return _pid_alive(held.get('pid')) or _beat_recent(held)


def _lock_line(root):
    path = _lock_path(root)
    if not os.path.lexists(path):
        return 'verrou : aucun'
    held = _read_lock(path)
    if held is None:
        return 'verrou : illisible'
    state = 'vivant' if _alive(held) else 'terminé'
    line = 'verrou : %s (%s) depuis %s, pid %s (%s)' % (
        held.get('session'),
        held.get('model'),
        held.get('started_at'),
        held.get('pid'),
        state,
    )
    if not _pid_alive(held.get('pid')) and _beat_recent(held):
        line += ', battement il y a %d min' % max(0, int(_beat_age(held) // 60))
    launcher = held.get('launched_by')
    if isinstance(launcher, str) and launcher:
        line += ', lancé par %s' % launcher
    return line


def _task_line(root, state_file):
    """Ligne d'une tâche active ou illisible ; `None` pour une tâche terminée."""
    try:
        safety.check_no_symlinks(state_file, root)
        data = json.loads(state_file.read_text(encoding='utf-8'))
        status = data['status'] if isinstance(data, dict) else None
    except (OSError, ValueError, KeyError):
        status = None
    if not isinstance(status, str):
        return 'tâche illisible : %s' % state_file.parent.name
    if status in ACTIVE_STATUSES:
        return 'tâche active : %s (%s)' % (state_file.parent.name, status)
    return None


def _repo_task_lines(repo, suffix=''):
    """Tâches du dépôt, puis celles de ses bras de campagne ; `suffix` ajoute le chemin du dépôt."""
    lines = [_task_line(repo, state_file) for pattern in (TASKS_GLOB, CAMPAIGN_TASKS_GLOB) for state_file in sorted(repo.glob(pattern))]
    return [line + suffix for line in lines if line]


def _declared_repos(root, held):
    """Chemins du verrou autres que le dépôt courant, sans doublon ni dépôt absent."""
    if not held or not isinstance(held.get('repos'), list):
        return []
    seen = {os.path.realpath(str(root))}
    found = []
    for text in held['repos']:
        if not isinstance(text, str) or not text:
            continue
        real = os.path.realpath(text)
        if real in seen or not os.path.isdir(real):
            continue
        seen.add(real)
        found.append(text)
    return found


def _task_lines(root, held):
    lines = _repo_task_lines(root)
    for text in _declared_repos(root, held):
        lines += _repo_task_lines(Path(text), ' [%s]' % text)
    return lines or ['tâches actives : aucune']


def _container_lines():
    command = ['docker', 'ps', '--filter', 'name=workspace-sandbox-', '--format', '{{.Names}}']
    try:
        done = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=SUBPROCESS_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return ['conteneurs : docker indisponible']
    if done.returncode != 0:
        return ['conteneurs : docker indisponible']
    names = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    return ['conteneur : ' + name for name in names] or ['conteneurs : aucun']


def _list_processes():
    """Couples (pid, ligne de commande) : `ps` s'il existe, sinon `/proc`."""
    found = []
    if shutil.which('ps'):
        done = subprocess.run(
            ['ps', '-eo', 'pid=,args='],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
        for line in done.stdout.splitlines():
            pid, _, command = line.strip().partition(' ')
            if pid.isdigit() and command.strip():
                found.append((int(pid), command.strip()))
        return found
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / 'cmdline').read_bytes()
        except OSError:
            continue
        command = raw.replace(b'\0', b' ').decode('utf-8', errors='replace').strip()
        if command:
            found.append((int(entry.name), command))
    return found


def _is_agent_process(command):
    """Agents d'orchestration seuls : `claude -p`, `campaign.py`, relais Codex lancé en `exec`."""
    return any(marker in command for marker in AGENT_MARKERS) or CODEX_EXEC.search(command) is not None


def _process_lines():
    own = {os.getpid(), os.getppid()}
    try:
        processes = _list_processes()
    except (OSError, subprocess.SubprocessError):
        processes = []
    lines = [
        'processus : %d %s' % (pid, ' '.join(command.split())[:COMMAND_LIMIT])
        for pid, command in processes
        if pid not in own and _is_agent_process(command)
    ]
    return lines or ['processus : aucun']


def _orphan_lines(held):
    """Avertissement quand le verrou lisible désigne un pid mort ; liste vide sinon."""
    if held is None or _alive(held):
        return []
    return ['verrou orphelin : pid %s mort ; libérable par bash scripts/orchestrator.sh release' % held.get('pid')]


def beat(root, _args):
    """Écrit l'heure UTC courante dans `beat_at` du verrou, sans toucher aux autres clés."""
    path = _lock_path(root)
    held = _read_lock(path) if os.path.lexists(path) else None
    if held is None:
        print('orchestrator: verrou absent ou illisible ; rien à battre', file=sys.stderr)
        return 1
    stamp = datetime.datetime.now(datetime.UTC).strftime(BEAT_FORMAT)
    _write_atomic(path, dict(held, **{BEAT_KEY: stamp}))
    print('battement : %s' % stamp)
    return 0


def _release_orphan(root):
    """Libère un verrou lisible sans pid vivant ni battement récent ; renvoie la ligne d'état, ou `None`."""
    path = _lock_path(root)
    held = _read_lock(path) if os.path.lexists(path) else None
    if held is None or _alive(held):
        return None
    path.unlink()
    events.record(root, ORCHESTRATOR_ID, 'orchestrator_released', {'session': held.get('session'), 'by': 'repair'})
    return 'verrou orphelin libéré : %s' % held.get('session')


def status(root, args):
    path = _lock_path(root)
    held = _read_lock(path) if os.path.lexists(path) else None
    lines = [_lock_line(root)] + _orphan_lines(held)
    lines += _task_lines(root, held) + _container_lines() + _process_lines()
    for line in lines:
        print(line)
    if args is not None and args.repair:
        for line in repair(root):
            print(line)
        released = _release_orphan(root)
        if released is not None:
            print(released)
    return 0


def _process_snapshot():
    """Couples (pid, commande), ou `None` si la liste des processus est illisible."""
    try:
        return _list_processes()
    except (OSError, subprocess.SubprocessError):
        return None


def delegation_alive(task_id, processes=None):
    """Vrai si un processus `delegate.py` vise la tâche, ou si la liste des processus est illisible (prudence)."""
    if processes is None:
        processes = _process_snapshot()
    if processes is None:
        return True
    suffixes = ('/%s.md' % task_id, '/%s/task.md' % task_id)
    return any(DELEGATE_SCRIPT in command and any(word.endswith(suffixes) for word in command.split()) for _pid, command in processes)


def _container_removed(name):
    """`True` si `docker rm -f` réussit, `False` s'il échoue, `None` si docker est absent ou muet."""
    docker_bin = shutil.which('docker')
    if not docker_bin:
        return None
    try:
        done = subprocess.run(
            [docker_bin, 'rm', '-f', name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=REMOVE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode == 0


def _container_phrase(name):
    removed = _container_removed(name)
    if removed is None:
        return 'docker indisponible'
    if removed:
        return 'conteneur %s supprimé' % name
    return 'conteneur %s non supprimé (absent ou refusé par docker)' % name


def _interrupt(root, task_id, status_file, data):
    """Marque une délégation morte comme interrompue, puis retire son conteneur ; renvoie la ligne d'état."""
    ended_at = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    _write_atomic(status_file, dict(data, outcome='interrupted', ended_at=ended_at, interrupted_by='repair'))
    events.record(root, task_id, 'outcome', {'outcome': 'interrupted', 'attempts': data.get('attempts'), 'by': 'repair'})
    task_phase.set_phase(root, task_id, 'failed')
    name = sandbox.get_container_name(root, task_id)
    return 'réparé : délégation %s interrompue (processus mort), %s' % (task_id, _container_phrase(name))


def _repair_one(root, folder):
    """Ligne de réparation pour un dossier de délégation ; `None` s'il n'y a rien à faire."""
    status_file = folder / 'status.json'
    if not os.path.lexists(status_file):
        return None
    try:
        safety.check_no_symlinks(status_file, root)
    except ValueError:
        return 'réparation refusée : %s (lien)' % folder.name
    data = _read_lock(status_file)  # même lecture : un objet JSON, sinon None
    if data is None or data.get('outcome') != 'running' or delegation_alive(folder.name):
        return None
    return _interrupt(root, folder.name, status_file, data)


def repair(root):
    """Répare les délégations dont le processus est mort alors que leur statut reste `running`."""
    base = root / DELEGATE_RUN_DIR
    lines = []
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            line = _repair_one(root, folder)
            if line is not None:
                lines.append(line)
    return lines or ['réparation : rien à faire']


def _claim_parser():
    parser = argparse.ArgumentParser(prog='orchestrator.sh claim')
    parser.add_argument('--session')
    parser.add_argument('--model')
    parser.add_argument('--repos', help='chemins séparés par des virgules')
    parser.add_argument('--by', metavar='NOM', help='qui a lancé l\'orchestrateur')
    parser.add_argument('--pid', type=int)
    parser.add_argument('--force', action='store_true')
    return parser


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8', errors='replace')
    if len(argv) < 3 or argv[2] not in ('claim', 'release', 'beat', 'status'):
        print(USAGE, file=sys.stderr)
        return EXIT_USAGE
    root, command, rest = Path(argv[1]), argv[2], argv[3:]
    if command == 'claim':
        args = _claim_parser().parse_args(rest)
    elif command == 'status' and rest in ([], [REPAIR_FLAG]):
        args = argparse.Namespace(repair=bool(rest))
    elif rest:
        print(USAGE, file=sys.stderr)
        return EXIT_USAGE
    else:
        args = None
    try:
        return {'claim': claim, 'release': release, 'beat': beat, 'status': status}[command](root, args)
    except (OSError, ValueError) as error:
        print('orchestrator: ' + str(error).replace('\n', ' '), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
