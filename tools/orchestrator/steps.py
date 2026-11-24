"""Processus et étapes du circuit de l'orchestrateur programme (fiche T14, fiche 2, règles 4 à 13).

Rien ici n'écrit le journal : `run.py` décide, ce module lance, lit l'état réel du circuit et rend des codes.
"""

import filecmp
import json
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

import journal

LOCK = '.workspace/orchestrator.json'
EVENTS = '.workspace/events.jsonl'
EXCLUDED = ('__tests__', '__pycache__')
SUCCESS = ('done', 'done_with_warning')
PS_TIMEOUT = 10
# T115 : seules variables du lancement que reçoit la vérification sur l'hôte (`verify_env`).
VERIFY_NAMES = frozenset(
    (
        'PATH',
        'HOME',
        'USER',
        'LOGNAME',
        'SHELL',
        'TMPDIR',
        'TZ',
        'LANG',
        'LANGUAGE',
        'TERM',
        'DOCKER_HOST',
        'DOCKER_CONTEXT',
        'DOCKER_CONFIG',
        'GIT_AUTHOR_NAME',
        'GIT_AUTHOR_EMAIL',
        'GIT_COMMITTER_NAME',
        'GIT_COMMITTER_EMAIL',
        'GIT_CONFIG_GLOBAL',
        'GIT_CONFIG_NOSYSTEM',
    )
)
VERIFY_PREFIXES = ('LC_', 'UV_', 'XDG_')
SECRET_WORDS = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL')
BEAT_DEFAULT = 300.0
POLL_DEFAULT = 5.0


def seconds(name, default):
    """Nombre de secondes d'une variable d'environnement, `default` si absente, illisible ou ≤ 0."""
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


# --- processus (règle 6) ---------------------------------------------------------------------------


def proc_info(pid):
    """(état, ligne de commande) du processus, None s'il est illisible."""
    if shutil.which('ps'):
        try:
            done = subprocess.run(
                ['ps', '-ww', '-o', 'stat=,command=', '-p', str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=PS_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        fields = done.stdout.strip().split(None, 1)
        if done.returncode != 0 or not fields:
            return None
        return fields[0], fields[1] if len(fields) > 1 else ''
    try:
        stat = Path('/proc/%d/stat' % pid).read_text(encoding='utf-8', errors='replace')
        raw = Path('/proc/%d/cmdline' % pid).read_bytes()
    except OSError:
        return None
    fields = stat.rpartition(')')[2].split()
    if not fields:
        return None
    return fields[0], raw.replace(b'\0', b' ').decode('utf-8', 'replace').strip()


def alive(pid):
    """Vivant : `os.kill(pid, 0)` réussit et le processus n'est pas un zombie ; illisible vaut mort."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass
    except OSError:
        return False
    info = proc_info(pid)
    return info is not None and not info[0].startswith('Z')


def command_of(pid):
    info = proc_info(pid)
    return info[1] if info else ''


def child_alive(pid):
    """Enfant du programme : vivant et chef de sa session (écarte un pid réutilisé)."""
    if not alive(pid):
        return False
    try:
        return os.getsid(pid) == pid
    except OSError:
        return False


def find_processes(*needles):
    """Pids (hors le nôtre) dont la ligne de commande contient tous les `needles` ; `ps` s'il existe, sinon `/proc`."""
    listing = []
    if shutil.which('ps'):
        try:
            done = subprocess.run(
                ['ps', '-ww', '-eo', 'pid=,command='],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=PS_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        for line in done.stdout.splitlines():
            pid, _, command = line.strip().partition(' ')
            if pid.isdigit():
                listing.append((int(pid), command))
    else:
        for entry in Path('/proc').iterdir():
            try:
                raw = (entry / 'cmdline').read_bytes()
            except OSError:
                continue
            if entry.name.isdigit():
                listing.append((int(entry.name), raw.replace(b'\0', b' ').decode('utf-8', 'replace')))
    return [pid for pid, command in listing if pid != os.getpid() and all(item in command for item in needles)]


def spawn(argv, log, env, cwd, started=None):
    """Lance argv détaché (nouvelle session, sorties ajoutées à `log`), attend sa fin, rend son code."""
    fd = os.open(str(log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        proc = subprocess.Popen(
            argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=fd, stderr=subprocess.STDOUT, start_new_session=True
        )
    finally:
        os.close(fd)
    if started is not None:
        started(proc.pid)
    return proc.wait()


def circuit_env(image=None, extra=None):
    """Environnement du circuit ; extra ajoute des variables (exécutant imposé, contexte d'étape)."""
    env = dict(os.environ)
    env.pop('WORKSPACE_AGENT_ROOT', None)
    for name in ('WORKSPACE_AGENT_OVERRIDE', 'WORKSPACE_STAGE_CONTEXT'):
        env.pop(name, None)
    if image:
        env['WORKSPACE_IMAGE_PYTHON'] = image
    env.update(extra or {})
    return env


def verify_env(source=None):
    """T115 : environnement de la vérification sur l'hôte, construit sur une liste blanche, jamais hérité du lancement.

    Nuit du 3/11 : `WORKSPACE_IMAGE_PYTHON`, exportée par le lanceur, a fait rougir `tests/test_sandbox.py` dans tous
    les clones et coûté une tentative à `gt2-run-states`. Seuls passent les chemins, la langue, l'identité Git et
    l'accès à Docker ; aucune variable du circuit (`WORKSPACE_*`, `ORCHESTRATOR_*`), aucun nom de secret."""
    source = os.environ if source is None else source
    return {
        name: value
        for name, value in source.items()
        if (name in VERIFY_NAMES or name.startswith(VERIFY_PREFIXES)) and not any(word in name.upper() for word in SECRET_WORDS)
    }


def git(cwd, *args):
    return subprocess.run(['git'] + list(args), cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def clone_git(cwd, *args):
    """git sur un clone de tâche (que l'exécutant a pu écrire) : ni crochet ni moniteur de fichiers."""
    return git(cwd, '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', *args)


# --- commandes du circuit (règles 10 à 14) ---------------------------------------------------------


def prepare_cmd(fiche, retry):
    return ['bash', 'scripts/task-workspace.sh', 'prepare', fiche] + (['--retry'] if retry else []) + ['--selfcheck']


def delegate_cmd(fiche):
    return ['bash', 'scripts/delegate.sh', fiche]


def propose_cmd(demande, task_id, executor):
    return ['bash', 'scripts/prepare-task.sh', '--from-demande', demande, task_id, '--executor', executor]


def plan_check_cmd(fiche):
    return ['bash', 'scripts/plan-check.sh', fiche, '--with-model']


def merge_cmd(task_id):
    return ['bash', 'scripts/task-workspace.sh', 'merge', task_id, '--no-verify-again', '--no-integration']


def close_cmd(task_id):
    return ['bash', 'scripts/task-workspace.sh', 'close', task_id, '--abandoned', '--force']


# --- état réel du circuit --------------------------------------------------------------------------


def read_json(root, rel):
    """Objet JSON de root/rel ; None s'il manque, est illisible ou passe par un lien symbolique."""
    try:
        path = journal.inside(root, rel)
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def task_state(root, task_id):
    return read_json(root, f'.workspace/orchestration/tasks/{task_id}/state.json')


def delegate_status(root, task_id):
    return read_json(root, f'.workspace/run/delegate/{task_id}/status.json')


def delegation_ok(state, status, since=None):
    """Réussie, et lancée après la préparation de l'état courant (le statut d'une tentative antérieure ne compte pas) ;
    `since` repousse ce seuil : une ronde de correction ne compte qu'à partir de sa demande."""
    if not state or not status or status.get('outcome') not in SUCCESS:
        return False
    return ran_after(state, status) and (since is None or ran_since(status, since))


def ran_after(state, status):
    """La délégation de `status` a commencé après la préparation courante de la tâche."""
    if not state or not status:
        return False
    return str(status.get('started_at') or '')[:19] >= str(state.get('created_at') or '')[:19]


def ran_since(status, start):
    """La délégation de `status` a commencé à `start` ou après (instant précis du journal)."""
    begun, since = _instant((status or {}).get('started_at')), _instant(start)
    return begun is not None and since is not None and begun >= since


def events(root, task_id, kind):
    """Événements `kind` de la tâche dans `.workspace/events.jsonl`, dans l'ordre ; lignes illisibles ignorées."""
    try:
        text = journal.inside(root, EVENTS).read_text(encoding='utf-8', errors='replace')
    except (OSError, ValueError):
        return []
    found = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get('task_id') == task_id and item.get('event') == kind:
            found.append(item)
    return found


def _instant(text):
    try:
        value = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def quota_since(root, task_id, since):
    """Vrai si un événement `quota` de la tâche est daté du début de la délégation ou après."""
    start = _instant(since)
    return any(start is not None and (_instant(item.get('at')) or start) >= start for item in events(root, task_id, 'quota'))


def merge_commit(root, task_id):
    """`detail.commit` du dernier événement `merged` de la tâche, sinon `git rev-parse HEAD`."""
    for item in reversed(events(root, task_id, 'merged')):
        detail = item.get('detail')
        if isinstance(detail, dict) and detail.get('commit'):
            return str(detail['commit'])
    done = git(root, 'rev-parse', 'HEAD')
    return done.stdout.decode().strip() if done.returncode == 0 else None


# --- verrou et battement (règle 4) -----------------------------------------------------------------


def read_lock(root):
    """Contenu du verrou ; None s'il n'existe pas, {} s'il est illisible (tenu par un inconnu)."""
    path = journal.inside(root, LOCK)
    if not os.path.lexists(str(path)):
        return None
    data = read_json(root, LOCK)
    return data if data is not None else {}


def orchestrator(root, *args):
    done = subprocess.run(
        ['bash', 'scripts/orchestrator.sh'] + list(args),
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return done.returncode, done.stdout.strip()


def claim(root, plan, pid, force):
    args = ['claim', '--session', plan['session'], '--model', plan['model'], '--by', plan['by'], '--repos', str(root), '--pid', str(pid)]
    return orchestrator(root, *(args + (['--force'] if force else [])))


class Beat:
    """Battement du verrou : un appel immédiat, puis un fil démon toutes les ORCHESTRATOR_BEAT_SECONDS."""

    def __init__(self, root):
        self.root = root
        self.stopped = threading.Event()
        self.thread = None

    def once(self):
        log = journal.logs(self.root) / 'beat.log'
        fd = os.open(str(log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            subprocess.run(
                ['bash', 'scripts/orchestrator.sh', 'beat'],
                cwd=str(self.root),
                stdin=subprocess.DEVNULL,
                stdout=fd,
                stderr=subprocess.STDOUT,
            )
        finally:
            os.close(fd)

    def start(self):
        self.once()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stopped.wait(seconds('ORCHESTRATOR_BEAT_SECONDS', BEAT_DEFAULT)):
            try:
                self.once()
            except (OSError, ValueError):
                pass

    def stop(self):
        """Arrête le fil et attend le battement en cours : il ne doit pas recréer un verrou libéré."""
        self.stopped.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(60)


# --- miroir des scripts et commit (règle 13) -------------------------------------------------------


def resync(base):
    """Recopie template/scripts/ vers scripts/ (comme `rsync -a --exclude __tests__ --exclude __pycache__`, sans suppression)."""
    source, target = Path(base) / 'template' / 'scripts', Path(base) / 'scripts'
    for folder, dirs, files in os.walk(str(source)):
        dirs[:] = sorted(name for name in dirs if name not in EXCLUDED)
        here = Path(folder)
        if any((here / name).is_symlink() for name in dirs + files):
            raise ValueError(f'lien symbolique dans le miroir : {here}')
        destination = target / here.relative_to(source)
        destination.mkdir(parents=True, exist_ok=True)
        for name in files:
            copy, goal = here / name, destination / name
            same = goal.is_file() and filecmp.cmp(str(copy), str(goal), shallow=False)
            if not same or (goal.stat().st_mode & 0o7777) != (copy.stat().st_mode & 0o7777):
                shutil.copyfile(str(copy), str(goal))
                shutil.copymode(str(copy), str(goal))


def commit_scripts(root, task_id):
    """Commit du miroir si `scripts/` a changé : sha du commit, None sinon."""
    status = git(root, 'status', '--porcelain', '--', 'scripts')
    if status.returncode != 0 or not status.stdout.strip():
        return None
    for args in (('add', '--', 'scripts'), ('commit', '-q', '-m', f'chore(circuit): resync scripts mirror after {task_id}')):
        done = git(root, *args)
        if done.returncode != 0:
            raise RuntimeError('git {} : {}'.format(args[0], done.stderr.decode('utf-8', 'replace').strip()))
    return git(root, 'rev-parse', 'HEAD').stdout.decode().strip()


def commit_paths(root, paths, message):
    """Commite exactement `paths` s'ils ont changé ; vrai si un commit est fait."""
    if git(root, 'add', '--', *paths).returncode != 0:
        raise RuntimeError('git add refusé : ' + ', '.join(paths))
    if git(root, 'diff', '--cached', '--quiet', '--', *paths).returncode == 0:
        return False
    done = git(root, 'commit', '-q', '-m', message, '--', *paths)
    if done.returncode != 0:
        raise RuntimeError('git commit : ' + done.stderr.decode('utf-8', 'replace').strip())
    return True


def commit_report(root, report, session):
    """Indexe le rapport et le commite s'il y a un changement indexé ; vrai si un commit est fait."""
    if git(root, 'add', '--', report).returncode != 0:
        raise RuntimeError('git add du rapport refusé')
    if git(root, 'diff', '--cached', '--quiet', '--', report).returncode == 0:
        return False
    done = git(root, 'commit', '-q', '-m', f'docs: orchestrator report {session}', '--', report)
    if done.returncode != 0:
        raise RuntimeError('git commit du rapport : ' + done.stderr.decode('utf-8', 'replace').strip())
    return True


# --- vérification sur l'hôte (règle 12) ------------------------------------------------------------


def _note(log, text):
    with open(str(log), 'ab') as stream:
        stream.write((text + '\n').encode('utf-8'))


def task_clone(root, task_id):
    """Dossier de travail de la tâche : chemin de l'état (absolu, ou relatif au projet pour une tâche ancienne)."""
    recorded = (task_state(root, task_id) or {}).get('worktree')
    if not isinstance(recorded, str) or not recorded:
        recorded = '.workspace/worktrees/' + task_id
    return Path(root) / recorded


def _overlay(worktree, repo):
    listing = clone_git(worktree, 'ls-files', '-z', '--cached', '--others', '--exclude-standard')
    if listing.returncode != 0:
        raise ValueError('git ls-files : ' + listing.stderr.decode('utf-8', 'replace').strip())
    for rel in filter(None, listing.stdout.decode('utf-8', 'replace').split('\0')):
        source, target = os.path.join(str(worktree), rel), os.path.normpath(os.path.join(repo, rel))
        if not target.startswith(repo + os.sep):
            raise ValueError(f'chemin hors de la copie : {rel}')
        if os.path.islink(source):
            raise ValueError(f'lien symbolique refusé : {rel}')
        if os.path.isfile(source):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.islink(target):
                os.unlink(target)
            shutil.copy2(source, target)
        elif not os.path.lexists(source) and os.path.lexists(target):
            os.unlink(target)


def _scratch(root):
    """(racine réelle, dossier jetable hors de la racine) d'une vérification."""
    root = os.path.realpath(str(root))
    base = os.path.realpath(os.environ.get('ORCHESTRATOR_TMP') or tempfile.gettempdir())
    if base == root or base.startswith(root + os.sep):
        raise ValueError(f'dossier temporaire sous la racine : {base}')
    return root, tempfile.mkdtemp(prefix='orchestrator-verify-', dir=base)


def _run_in(repo, tmp, command, log, env, started):  # noqa: PLR0913
    test_tmp = os.path.join(tmp, 'test-tmp')
    os.mkdir(test_tmp)
    return spawn(['sh', '-c', command], log, dict(env, WORKSPACE_TEST_TMP=test_tmp), repo, started)


def _task_copy(root, task_id, repo, log):
    """Copie jetable du travail de la tâche dans `repo` : sa branche reprise dans le projet, puis son clone par-dessus.

    Git ne tourne que dans le projet et dans la copie : le clone, que l'exécutant a pu écrire, est seulement lu. Rend 0, ou le
    code de l'échec (noté dans `log`)."""
    branch, clone = 'workspace-task/' + task_id, task_clone(root, task_id)
    if os.path.isdir(os.path.join(str(clone), '.git')):
        # Le clone de la tâche n'a pas d'historique commun avec le projet : le projet reprend d'abord son
        # travail validé (avance rapide), la copie jetable part de cette branche.
        done = git(root, 'fetch', '-q', '--no-tags', clone.resolve().as_uri(), f'refs/heads/{branch}:refs/heads/{branch}')
        if done.returncode != 0:
            _note(log, 'git fetch : ' + done.stderr.decode('utf-8', 'replace').strip())
            return done.returncode or 1
    done = git(root, 'clone', '-q', '--no-hardlinks', '--branch', branch, root, repo)
    if done.returncode != 0:
        _note(log, 'git clone : ' + done.stderr.decode('utf-8', 'replace').strip())
        return done.returncode or 1
    try:
        _overlay(clone, repo)
    except (OSError, ValueError) as error:
        _note(log, f'copie du worktree : {error}')
        return 1
    return 0


def verify(root, task_id, command, mirror, log, env, started):  # noqa: PLR0913
    """Lance `command` dans un clone jetable de la branche de la tâche ; rend son code (1 si la copie échoue)."""
    root, tmp = _scratch(root)
    try:
        repo = os.path.join(tmp, 'repo')
        code = _task_copy(root, task_id, repo, log)
        if code != 0:
            return code
        try:
            if mirror:
                resync(repo)
        except (OSError, ValueError) as error:
            _note(log, f'copie du worktree : {error}')
            return 1
        return _run_in(repo, tmp, command, log, env, started)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def export_patch(root, task_id, log):
    """T120 : diff complet du travail de la tâche depuis sa base (commits, modifications, fichiers neufs, binaires), pris dans
    une copie jetable ; écrit `.workspace/orchestrator-run/<id>-echec.patch` (0600) et rend son chemin relatif à root.
    ValueError si la base ou la copie manque : le clone est alors gardé."""
    sha = base_commit(root, task_id)
    if sha is None:
        raise ValueError('base de la tâche introuvable')
    root, tmp = _scratch(root)
    try:
        repo = os.path.join(tmp, 'repo')
        if _task_copy(root, task_id, repo, log) != 0:
            raise ValueError(f'copie du travail impossible (journal `{Path(log).name}`)')
        done = git(repo, 'add', '-A')
        if done.returncode == 0:
            done = git(repo, 'diff', '--cached', '--binary', '--no-renames', '--no-ext-diff', '--no-textconv', sha, '--')
        if done.returncode != 0:
            raise ValueError('git : ' + done.stderr.decode('utf-8', 'replace').strip())
        path = journal.logs(root) / f'{task_id}-echec.patch'
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(done.stdout)
        return path.relative_to(root).as_posix()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def base_commit(root, task_id):
    """Commit de base de la tâche : celui de son état, sinon la base commune de HEAD et de sa branche ; None sinon."""
    recorded = (task_state(root, task_id) or {}).get('base_commit')
    if isinstance(recorded, str) and recorded:
        return recorded
    done = git(root, 'merge-base', 'HEAD', 'workspace-task/' + task_id)
    sha = done.stdout.decode('utf-8', 'replace').strip()
    return sha if done.returncode == 0 and sha else None


def verify_base(root, sha, plan, log, started):
    """T117 : la vérification du plan dans un clone jetable du projet au commit `sha`, sans le travail de la tâche, dans
    le même environnement (`verify_env`) ; rend son code, None si la base ne peut pas être copiée."""
    root, tmp = _scratch(root)
    try:
        repo = os.path.join(tmp, 'repo')
        for cwd, args in (
            (root, ('clone', '-q', '--no-hardlinks', '--no-checkout', root, repo)),
            (repo, ('checkout', '-q', '--detach', sha)),
        ):
            done = git(cwd, *args)
            if done.returncode != 0:
                _note(log, 'git {} : {}'.format(args[0], done.stderr.decode('utf-8', 'replace').strip()))
                return None
        try:
            if plan['resync']:
                resync(repo)
        except (OSError, ValueError) as error:
            _note(log, f'miroir de la base : {error}')
            return None
        return _run_in(repo, tmp, plan['verify'], log, verify_env(), started)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
