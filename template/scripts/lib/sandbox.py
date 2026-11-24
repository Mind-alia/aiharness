"""Tranche S2 : Gestionnaire de conteneurs de tâche (Sandbox).

Commandes :
- deps [--rebuild] <id> : Prépare les dépendances dans un volume réutilisable.
- start <id> [--base] : Démarre le conteneur confiné avec les options de sécurité exactes. `--base` (T123) monte à la place
  du clone le dossier `.workspace/orchestration/tasks/<id>/base` (base de la tâche, même politique) ; refusé si un
  conteneur de la tâche tourne déjà.
- exec <id> [--] <cmd...> : Exécute une commande dans le conteneur, masque les secrets et journalise dans exec-NNN.log.
- stop <id> : Arrête et supprime le conteneur de façon idempotente sans toucher au worktree.
- status <id> : Affiche l'état d'exécution du conteneur.

Sécurité :
- Utilisateur non-root (--user 1000:1000).
- --cap-drop ALL, --security-opt no-new-privileges.
- Limites --cpus et --memory.
- Réseau coupé par défaut (--network none), sans exception dans le conteneur de tâche.
- Système de fichiers racine en lecture seule (--read-only) avec --tmpfs /tmp.
- Montage strict du clone de la tâche sur /work, en lecture seule (les écritures du worktree passent par les outils
  de l'hôte, contrôlés ; le worktree d'intégration, sans agent, reste en écriture), `.git` masqué par un montage vide
  en lecture seule (dossier vide pour le clone, fichier vide pour le worktree d'intégration).
- Dossiers de travail inscriptibles en mémoire (tmpfs, invisibles de l'hôte) : `.workspace`, et pour node `dist`,
  `coverage`, `test-results`, `playwright-report` (`WRITABLE_DIRS`).
- `scripts/` et `.claude/` du worktree toujours remontés en lecture seule
  (un dossier absent est remplacé par un dossier vide en lecture seule), `project.profile` s'il existe :
  `run-in-sandbox.sh`, `ask.sh` et la reprise de l'exécutant (`claude --resume`) lisent ces copies sur l'hôte.
- Jamais /var/run/docker.sock ni le dossier personnel de l'hôte.
- Nom et étiquettes uniques par tâche.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

LIB_DIR = Path(__file__).resolve().parent
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import workdir

try:
    from secrets import known_values, mask_secrets

    from safety import check_no_symlinks
    from task import read_task
except ImportError:
    pass


TASKS_DIR = '.workspace/orchestration/tasks'
DEFAULT_IMAGE_NODE = 'node:24-bookworm-slim'
DEFAULT_IMAGE_NODE_FULL = 'workspace-node-git:24-bookworm'
NODE_FULL_BUILD_COMMAND = 'docker build -t workspace-node-git:24-bookworm -f template/docker/node-git.Dockerfile template/docker'
DEFAULT_IMAGE_PYTHON = 'python:3.12-slim-bookworm'
DEFAULT_IMAGE_C = 'workspace-c-norm:bookworm'
DEFAULT_CPUS = '2'
DEFAULT_MEMORY = '4g'
DEFAULT_USER = '1000:1000'
# Toujours remontés en lecture seule : un dossier absent est remplacé par un dossier vide du dossier `sandbox/`.
READ_ONLY_DIRS = ('scripts', '.claude')
# Remonté seulement s'il existe : sa création par le conteneur est un chemin protégé, refusé au périmètre.
READ_ONLY_FILES = ('project.profile',)
# Seuls dossiers inscriptibles de `/work` (le reste est en lecture seule), montés en mémoire par profil.
DEFAULT_WRITABLE_DIRS = ('.workspace',)
WRITABLE_DIRS: dict[str, tuple[str, ...]] = {
    'python': DEFAULT_WRITABLE_DIRS,
    'docs': DEFAULT_WRITABLE_DIRS,
    'c': DEFAULT_WRITABLE_DIRS,
    'node': ('.workspace', 'dist', 'coverage', 'test-results', 'playwright-report'),
}


def get_docker_binary() -> str:
    docker_bin = shutil.which('docker')
    if not docker_bin:
        raise RuntimeError("Binaire docker introuvable dans le PATH. Veuillez installer Docker ou vérifier votre PATH.")
    return docker_bin


def check_docker_engine(docker_bin: str) -> None:
    res = subprocess.run([docker_bin, 'info'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError("Le moteur Docker est arrêté ou inaccessible. Veuillez démarrer Docker Desktop ou le démon Docker.")


def validate_and_load_task_state(root: Path, task_id: str) -> tuple[dict[str, Any], Path, Path]:
    if not isinstance(task_id, str) or not re.fullmatch(r'[a-z0-9-]+', task_id):
        raise ValueError(f"Identifiant de tâche invalide : '{task_id}'")

    check_no_symlinks(root, root)

    task_dir = root / '.workspace/orchestration/tasks' / task_id
    state_file = task_dir / 'state.json'

    if not task_dir.is_dir() or not state_file.is_file():
        raise ValueError(f"Tâche inconnue ou non enregistrée dans le registre S1 : {task_id}")

    check_no_symlinks(state_file, root)

    try:
        state = json.loads(state_file.read_text(encoding='utf-8'))
    except Exception as exc:
        raise ValueError(f"État de tâche corrompu pour {task_id}") from exc

    if state.get('task_id') != task_id:
        raise ValueError(f"Incohérence d'identifiant dans state.json : attendu {task_id}, trouvé {state.get('task_id')}")

    worktree_rel = state.get('worktree')
    if not worktree_rel or not isinstance(worktree_rel, str):
        raise ValueError(f"Champ 'worktree' manquant ou invalide pour {task_id}")

    worktree_path = workdir.state_worktree(root, state)
    # Seuls chemins admis : le clone de la tâche, ou le worktree d'intégration pendant la porte d'intégration.
    if worktree_path == workdir.task_worktree(root, task_id):
        if not worktree_path.is_dir():
            raise ValueError(f"Répertoire de worktree introuvable pour {task_id} : {worktree_rel}")
        workdir.check_no_symlinks_below(workdir.work_base(root), worktree_path)
        return state, task_dir, worktree_path

    if worktree_path != root / '.workspace/integration' / task_id:
        raise ValueError(f"Le worktree {worktree_rel} n'est ni le clone de la tâche ni son worktree d'intégration.")
    if not worktree_path.is_dir():
        raise ValueError(f"Répertoire de worktree introuvable pour {task_id} : {worktree_rel}")
    check_no_symlinks(worktree_path, root)
    return state, task_dir, worktree_path


def base_worktree(root: Path, task_id: str) -> Path:
    """Dossier de la base de la tâche (`start <id> --base`), clone jetable que le programme d'orchestration y pose ;
    ValueError s'il manque, est un lien ou passe par un lien entre `root` et lui."""
    if not isinstance(task_id, str) or not re.fullmatch(r'[a-z0-9-]+', task_id):
        raise ValueError(f"Identifiant de tâche invalide : '{task_id}'")
    path = root / TASKS_DIR / task_id / 'base'
    check_no_symlinks(path, root)
    if not path.is_dir():
        raise ValueError(f"Base de la tâche introuvable pour {task_id} : {path.relative_to(root).as_posix()}")
    return path


def node_full_image_present(docker_bin: str | None) -> bool:
    docker = docker_bin or shutil.which('docker')
    if not docker:
        return False
    try:
        res = subprocess.run(
            [docker, 'image', 'inspect', '--format', '{{.Id}}', DEFAULT_IMAGE_NODE_FULL],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return False
    return res.returncode == 0 and bool(res.stdout.strip())


def node_build_command(root: Path) -> str:
    if (root / 'docker/node-git.Dockerfile').exists():
        return f"docker build -t {DEFAULT_IMAGE_NODE_FULL} -f docker/node-git.Dockerfile docker"
    return f"docker build -t {DEFAULT_IMAGE_NODE_FULL} -f template/docker/node-git.Dockerfile template/docker"


def full_image_refusal(root: Path, profile: str, exchange: bool) -> str | None:
    if not exchange or profile != 'node':
        return None
    if os.environ.get('WORKSPACE_IMAGE') or os.environ.get('WORKSPACE_IMAGE_NODE'):
        return None
    if node_full_image_present(None):
        return None
    return (
        f"Image complète {DEFAULT_IMAGE_NODE_FULL} absente : construire avec « {node_build_command(root)} », "
        "ou relancer avec --allow-slim-image."
    )


def select_node_image(docker_bin: str | None) -> str:
    if node_full_image_present(docker_bin):
        return DEFAULT_IMAGE_NODE_FULL
    print(
        f"Image {DEFAULT_IMAGE_NODE_FULL} absente : repli sur {DEFAULT_IMAGE_NODE} "
        f"(sans python3 ni git ; pour la porte complète : {NODE_FULL_BUILD_COMMAND})",
        file=sys.stderr,
    )
    return DEFAULT_IMAGE_NODE


def select_image(profile: str, docker_bin: str | None) -> str:
    override = os.environ.get('WORKSPACE_IMAGE')
    if override:
        return override
    if profile == 'node':
        node_override = os.environ.get('WORKSPACE_IMAGE_NODE')
        if node_override:
            return node_override
        return select_node_image(docker_bin)
    if profile == 'c':
        c_override = os.environ.get('WORKSPACE_IMAGE_C')
        if c_override:
            return c_override
        return DEFAULT_IMAGE_C
    return os.environ.get('WORKSPACE_IMAGE_PYTHON', DEFAULT_IMAGE_PYTHON)


def resolve_image_and_profile(
    root: Path, task_dir: Path, worktree_path: Path, state: dict[str, Any], docker_bin: str | None = None
) -> tuple[str, str]:
    # Lecture des métadonnées de tâche d'origine si disponibles
    task_data = {}
    source_task = state.get('source_task')
    if source_task:
        source_path = root / source_task
        if source_path.is_file() and not source_path.is_symlink():
            try:
                task_data = read_task(source_path)
            except Exception:
                task_data = {}

    # Profil
    profile = task_data.get('profile')
    if not profile:
        profile_file = root / 'project.profile'
        if profile_file.is_file() and not profile_file.is_symlink():
            profile = profile_file.read_text(encoding='utf-8').strip()

    if not profile:
        if (worktree_path / 'package.json').exists() or (root / 'package.json').exists():
            profile = 'node'
        else:
            profile = 'python'

    return select_image(profile, docker_bin), profile


def node_heap_mb(memory: str) -> int:
    """Three quarters of the container memory limit, in MB (Docker size like 4g or 2048m)."""
    match = re.fullmatch(r'([0-9]+)([kmg]?)', memory.strip().lower())
    if not match:
        raise ValueError('WORKSPACE_SANDBOX_MEMORY doit ressembler à 4g ou 2048m.')
    value, unit = int(match.group(1)), match.group(2)
    megabytes = {'g': value * 1024, 'm': value, 'k': value // 1024, '': value // (1024 * 1024)}[unit]
    return max(256, megabytes * 3 // 4)


def get_project_fingerprint(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode('utf-8')).hexdigest()[:10]


def get_container_name(root: Path, task_id: str) -> str:
    return f"workspace-sandbox-{get_project_fingerprint(root)}-{task_id}"


def read_only_mounts(worktree_path: Path, sandbox_dir: Path) -> list[str]:
    """Arguments `-v` qui remontent en lecture seule les chemins du circuit du worktree.

    `scripts/` et `.claude/` le sont toujours : s'il manque, un dossier vide de `sandbox_dir` prend sa place,
    et le conteneur ne peut pas y créer un fichier que l'hôte chargerait (`claude --resume`)."""
    mounts: list[str] = []
    for name in READ_ONLY_DIRS + READ_ONLY_FILES:
        path = worktree_path.resolve() / name
        if path.is_symlink():
            raise ValueError(f"Lien symbolique interdit dans le worktree : {name}")
        if name in READ_ONLY_DIRS and not path.exists():
            path = sandbox_dir / ('empty_' + name.lstrip('.'))
            check_no_symlinks(path, sandbox_dir)
            path.mkdir(mode=0o500, exist_ok=True)
            path.chmod(0o500)
            path = path.resolve()
        if path.exists():
            mounts.extend(['-v', f'{path}:/work/{name}:ro'])
    return mounts


def work_mount(worktree_path: Path) -> list[str]:
    """Argument `-v` de `/work` : en lecture seule pour le clone d'une tâche (`.git` est un dossier), car l'hôte lit
    ce worktree après le contrôle du crochet et un lien posé par le conteneur entre les deux serait suivi ;
    en écriture pour le worktree d'intégration (`.git` est un fichier), où aucun agent ne tourne."""
    resolved = worktree_path.resolve()
    mode = 'rw' if (resolved / '.git').is_file() else 'ro'
    return ['-v', f'{resolved}:/work:{mode}']


def writable_overlays(worktree_path: Path, profile: str) -> list[str]:
    """Arguments `--tmpfs` des dossiers de travail du conteneur : en mémoire, invisibles de l'hôte.

    Le dossier vide est créé sur l'hôte s'il manque (c'est le point de montage dans `/work`, en lecture seule)."""
    resolved = worktree_path.resolve()
    if (resolved / '.git').is_file():
        return []
    overlays: list[str] = []
    for relative in WRITABLE_DIRS.get(profile, DEFAULT_WRITABLE_DIRS):
        path = resolved / relative
        if path.is_symlink():
            raise ValueError(f"Lien symbolique interdit dans le worktree : {relative}")
        if path.exists() and not path.is_dir():
            raise ValueError(f"Dossier de travail attendu dans le worktree, fichier trouvé : {relative}")
        path.mkdir(exist_ok=True)
        overlays.extend(['--tmpfs', f'/work/{relative}:rw,exec,size=256m,mode=1777'])
    return overlays


def cmd_start(root: Path, task_id: str, base: bool = False) -> int:  # noqa: C901, PLR0915
    docker_bin = get_docker_binary()
    check_docker_engine(docker_bin)

    state, task_dir, worktree_path = validate_and_load_task_state(root, task_id)
    if base:
        # T123 : le conteneur monte la base de la tâche (sans le travail de l'exécutant) à la place de son clone.
        worktree_path = base_worktree(root, task_id)
    image, profile = resolve_image_and_profile(root, task_dir, worktree_path, state, docker_bin)
    network_mode = 'none'
    container_name = get_container_name(root, task_id)

    sandbox_dir = task_dir / 'sandbox'
    sandbox_dir.mkdir(mode=0o700, exist_ok=True)
    sandbox_dir.chmod(0o700)
    check_no_symlinks(sandbox_dir, root)

    # Masquage de .git en lecture seule : un dossier vide pour le clone d'une tâche (`.git` est un dossier),
    # un fichier vide pour le worktree d'intégration (`.git` est un fichier).
    if (worktree_path / '.git').is_dir():
        empty_git = sandbox_dir / 'empty_git_dir'
        empty_git.mkdir(mode=0o500, exist_ok=True)
        empty_git.chmod(0o500)
    else:
        empty_git = sandbox_dir / 'empty_git'
        if not empty_git.exists():
            empty_git.write_bytes(b'')
            empty_git.chmod(0o400)
    check_no_symlinks(empty_git, root)

    # Vérification d'état préalable du conteneur
    res_inspect = subprocess.run(
        [docker_bin, 'inspect', '--format', '{{.State.Running}}', container_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if res_inspect.returncode == 0:
        if res_inspect.stdout.strip().lower() == 'true':
            if base:
                raise ValueError(f"Un conteneur de la tâche {task_id} tourne déjà : la base ne peut pas le remplacer.")
            print(f"Conteneur {container_name} déjà démarré.")
            return 0
        # Conteneur arrêté : suppression propre avant relance
        subprocess.run([docker_bin, 'rm', '-f', container_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Avant toute commande docker : un lien à la place d'un dossier de travail refuse le démarrage.
    overlays = writable_overlays(worktree_path, profile)

    from deps import NODE_CACHE_DIRS, prepare_dependencies, prepare_mount_points, volume_mounts

    plan = prepare_dependencies(root, task_dir, worktree_path, image, profile, docker_bin)
    prepare_mount_points(worktree_path, plan)

    cpus = os.environ.get('WORKSPACE_SANDBOX_CPUS', DEFAULT_CPUS)
    memory = os.environ.get('WORKSPACE_SANDBOX_MEMORY', DEFAULT_MEMORY)
    user = os.environ.get('WORKSPACE_SANDBOX_USER', DEFAULT_USER)
    cache_mounts = []
    if plan and plan['profile'] == 'node':
        if not re.fullmatch(r'[0-9]+:[0-9]+', user):
            raise ValueError('Les caches npm exigent WORKSPACE_SANDBOX_USER au format UID:GID numérique.')
        uid, gid = user.split(':')
        for path in plan['mounts']:
            for cache in NODE_CACHE_DIRS:
                cache_mounts.extend(['--tmpfs', f'/work/{path}/{cache}:rw,size=256m,uid={uid},gid={gid},mode=0700'])

    # Vérification formelle d'exclusion des chemins sensibles
    forbidden_mounts = ['/var/run/docker.sock', str(Path.home().resolve())]
    for fm in forbidden_mounts:
        if fm == str(worktree_path.resolve()):
            raise ValueError(f"Montage interdit d'un chemin sensible de l'hôte : {fm}")

    # Bounded by the attack bench: no fork bomb, no filling the VM through /tmp.
    pids_limit = os.environ.get('WORKSPACE_SANDBOX_PIDS', '512')
    tmp_size = os.environ.get('WORKSPACE_SANDBOX_TMP_SIZE', '1g')
    if not re.fullmatch(r'[1-9][0-9]{0,5}', pids_limit):
        raise ValueError('WORKSPACE_SANDBOX_PIDS doit être un entier positif.')
    if not re.fullmatch(r'[1-9][0-9]{0,5}[kmg]', tmp_size):
        raise ValueError('WORKSPACE_SANDBOX_TMP_SIZE doit ressembler à 512m ou 1g.')

    cmd = [
        docker_bin,
        'run',
        '-d',
        # A real init as PID 1 reaps orphaned processes (sleep infinity does not).
        '--init',
        '--name',
        container_name,
        '--label',
        f'workspace.task_id={task_id}',
        '--label',
        'workspace.managed_by=workspace-ia',
        '--label',
        'workspace.role=sandbox',
        '--label',
        f'workspace.project={get_project_fingerprint(root)}',
        '--user',
        user,
        '-e',
        'HOME=/tmp',
        '-e',
        'XDG_CACHE_HOME=/tmp/.cache',
        '-e',
        'npm_config_cache=/tmp/.npm',
        # Acceptable: the container holds only the worktree (.git masked), with no network by default.
        # One variable (no KEY/TOKEN in its name): the attack bench scans variable names.
        '-e',
        "GIT_CONFIG_PARAMETERS='safe.directory=*'",
        # Node sizes its heap from the machine, not the container: ESLint ran out at about 1 GB.
        '-e',
        f'NODE_OPTIONS=--max-old-space-size={node_heap_mb(memory)}',
        '--cap-drop',
        'ALL',
        '--security-opt',
        'no-new-privileges',
        '--cpus',
        cpus,
        '--memory',
        memory,
        '--network',
        network_mode,
        '--read-only',
        '--pids-limit',
        pids_limit,
        # exec: the tests write fake executables in /tmp (Docker mounts tmpfs noexec by default).
        '--tmpfs',
        f'/tmp:rw,exec,size={tmp_size},mode=1777',
        *work_mount(worktree_path),
        '-v',
        f'{empty_git.resolve()}:/work/.git:ro',
        *read_only_mounts(worktree_path, sandbox_dir),
        *overlays,
        '-w',
        '/work',
        *volume_mounts(plan),
        *cache_mounts,
        image,
        'sleep',
        'infinity',
    ]

    res_run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res_run.returncode != 0:
        print(f"Erreur au démarrage du conteneur : {res_run.stderr.strip()}", file=sys.stderr)
        return res_run.returncode

    status_data: dict[str, Any] = {
        "task_id": task_id,
        "container": container_name,
        "image": image,
        "network": network_mode,
        "status": "running",
    }
    if base:
        status_data["base"] = True
    status_file = sandbox_dir / 'status.json'
    status_file.write_text(json.dumps(status_data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    status_file.chmod(0o600)

    print(f"Conteneur {container_name} démarré avec succès.")
    return 0


def cmd_exec(root: Path, task_id: str, command: list[str]) -> int:  # noqa: C901
    if not command:
        print("Usage : sandbox.sh exec <id> [--] <commande...>", file=sys.stderr)
        return 64

    if command[0] == '--':
        command = command[1:]
    if not command:
        print("Usage : sandbox.sh exec <id> [--] <commande...>", file=sys.stderr)
        return 64

    docker_bin = get_docker_binary()
    check_docker_engine(docker_bin)

    _, task_dir, _ = validate_and_load_task_state(root, task_id)
    container_name = get_container_name(root, task_id)

    sandbox_dir = task_dir / 'sandbox'
    sandbox_dir.mkdir(mode=0o700, exist_ok=True)
    sandbox_dir.chmod(0o700)
    check_no_symlinks(sandbox_dir, root)

    # Vérification que le conteneur tourne
    res_inspect = subprocess.run(
        [docker_bin, 'inspect', '--format', '{{.State.Running}}', container_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if res_inspect.returncode != 0 or res_inspect.stdout.strip().lower() != 'true':
        print(f"Erreur : le conteneur {container_name} n'est pas en cours d'exécution.", file=sys.stderr)
        return 1

    # Numérotation NNN du fichier exec-NNN.log
    existing_nums = []
    for entry in sandbox_dir.iterdir():
        if entry.is_symlink():
            raise ValueError(f"Lien symbolique interdit dans sandbox : {entry}")
        m = re.fullmatch(r'exec-(\d+)\.log', entry.name)
        if m:
            existing_nums.append(int(m.group(1)))

    next_num = max(existing_nums, default=0) + 1
    log_file = sandbox_dir / f"exec-{next_num:03d}.log"

    exec_cmd = [docker_bin, 'exec', container_name, *command]
    proc = subprocess.run(exec_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # Masquage des secrets
    secrets_inventory = known_values(root)
    masked_stdout = mask_secrets(proc.stdout, secrets_inventory)
    masked_stderr = mask_secrets(proc.stderr, secrets_inventory)

    # Journalisation dans exec-NNN.log
    log_content = masked_stdout
    if masked_stderr:
        if log_content and not log_content.endswith('\n'):
            log_content += '\n'
        log_content += masked_stderr

    log_file.write_text(log_content, encoding='utf-8')
    log_file.chmod(0o600)

    # Affichage sur les flux standards
    if masked_stdout:
        print(masked_stdout, end='' if masked_stdout.endswith('\n') else '\n')
    if masked_stderr:
        print(masked_stderr, file=sys.stderr, end='' if masked_stderr.endswith('\n') else '\n')

    return proc.returncode


def cmd_stop(root: Path, task_id: str) -> int:
    docker_bin = get_docker_binary()
    # Note : pour stop, on n'exige pas un moteur actif pour être idempotent si possible, mais on tente la suppression
    validate_and_load_task_state(root, task_id)
    container_name = get_container_name(root, task_id)

    # Idempotent : supprime sans erreur si le conteneur n'existe pas ou est déjà supprimé
    subprocess.run([docker_bin, 'rm', '-f', container_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Le worktree n'est JAMAIS touché
    print(f"Conteneur {container_name} arrêté et supprimé (worktree préservé).")
    return 0


def cmd_status(root: Path, task_id: str) -> int:
    docker_bin = get_docker_binary()
    check_docker_engine(docker_bin)

    validate_and_load_task_state(root, task_id)
    container_name = get_container_name(root, task_id)

    res = subprocess.run(
        [docker_bin, 'inspect', '--format', '{{.State.Status}}', container_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )

    if res.returncode == 0:
        container_status = res.stdout.strip()
    else:
        container_status = 'absent'

    output = {"task_id": task_id, "container": container_name, "status": container_status}
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def split_base(action: str, rest: list[str]) -> tuple[list[str], bool]:
    """Arguments sans `--base`, et vrai si `start` le porte (seule commande qui l'accepte)."""
    if action == 'start' and '--base' in rest:
        return [arg for arg in rest if arg != '--base'], True
    return rest, False


def main() -> int:
    os.umask(0o077)
    if len(sys.argv) < 4:
        print("Usage : sandbox.sh deps|start|exec|stop|status <id> [args...]", file=sys.stderr)
        return 64

    root = Path(sys.argv[1]).resolve()
    action = sys.argv[2]
    rest = sys.argv[3:]
    rebuild = '--rebuild' in rest
    if action == 'deps':
        rest = [arg for arg in rest if arg != '--rebuild']
        if len(rest) != 1:
            print('Usage : sandbox.sh deps [--rebuild] <id>', file=sys.stderr)
            return 64
    rest, base = split_base(action, rest)
    task_id = rest[0]

    try:
        if action == 'deps':
            docker = get_docker_binary()
            check_docker_engine(docker)
            state, task_dir, worktree = validate_and_load_task_state(root, task_id)
            image, profile = resolve_image_and_profile(root, task_dir, worktree, state, docker)
            from deps import prepare_dependencies

            prepare_dependencies(root, task_dir, worktree, image, profile, docker, rebuild)
            return 0
        elif action == 'start':
            return cmd_start(root, task_id, base)
        elif action == 'exec':
            return cmd_exec(root, task_id, sys.argv[4:])
        elif action == 'stop':
            return cmd_stop(root, task_id)
        elif action == 'status':
            return cmd_status(root, task_id)
        else:
            print(f"Action de sandbox inconnue : {action}", file=sys.stderr)
            return 64
    except Exception as exc:
        print(f"Erreur sandbox : {exc}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
