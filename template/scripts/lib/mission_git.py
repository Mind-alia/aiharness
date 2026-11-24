"""Aides Git pour mission.py.

Python 3.14 standard library uniquement.
"""

import os
import shutil
import subprocess
from pathlib import Path

import workdir


def run_git(root: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    cmd = workdir.git_command(*args, cwd=root)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and res.returncode != 0:
        raise subprocess.CalledProcessError(res.returncode, cmd, output=res.stdout, stderr=res.stderr)
    return res


def verify_git_root(root: Path) -> None:
    res = run_git(root, ['rev-parse', '--show-toplevel'], check=False)
    if res.returncode != 0:
        raise ValueError("Le projet n'est pas un dépôt Git valide.")
    toplevel = Path(res.stdout.strip()).resolve()
    if toplevel != root.resolve():
        raise ValueError(f"La racine indiquée ({root}) ne correspond pas à la racine exacte du dépôt Git ({toplevel}).")


def verify_git_head_and_clean(root: Path) -> str:
    res_head = run_git(root, ['rev-parse', '--verify', 'HEAD'], check=False)
    if res_head.returncode != 0:
        raise ValueError("Dépôt Git sans commit HEAD (dépôt vide ou branche initiale sans aucun commit).")
    head_commit = res_head.stdout.strip()

    res_status = run_git(root, ['status', '--porcelain=v1'], check=False)
    if res_status.returncode != 0:
        raise ValueError("Impossible de vérifier l'état de l'arbre Git.")
    if res_status.stdout.strip():
        raise ValueError("L'arbre de travail Git principal n'est pas propre (modifications non validées ou fichiers non suivis présents).")

    return head_commit


def resolve_base_commit(root: Path, base_ref: str | None, head_commit: str) -> str:
    if not base_ref:
        return head_commit

    if base_ref.startswith('-'):
        raise ValueError(f"Référence de base invalide (option interdite) : {base_ref}")

    res = run_git(root, ['rev-parse', '--verify', '--quiet', '--end-of-options', f'{base_ref}^{{commit}}'], check=False)
    if res.returncode != 0:
        raise ValueError(f"Référence de base invalide ou introuvable : {base_ref}")
    return res.stdout.strip()


def prevalidate_branch_and_worktree(root: Path, task_id: str) -> tuple[str, Path, Path]:
    branch_name = f"workspace-task/{task_id}"
    worktree_path = workdir.task_worktree(root, task_id)
    state_file = root / '.workspace/orchestration/tasks' / task_id / 'state.json'

    res = run_git(root, ['rev-parse', '--verify', '--quiet', '--end-of-options', f'refs/heads/{branch_name}'], check=False)
    if res.returncode == 0:
        raise ValueError(f"La branche {branch_name} existe déjà (collision).")

    if worktree_path.exists() or worktree_path.is_symlink():
        raise ValueError(f"Le chemin cible de worktree existe déjà : {worktree_path}")

    if state_file.exists() or state_file.is_symlink():
        raise ValueError(f"Un état existe déjà pour la tâche : {task_id}")

    return branch_name, worktree_path, state_file


def create_task_clone(root: Path, branch: str, clone: Path) -> None:
    """Clone de profondeur 1 de `branch` : sans remote, sans autre référence, sans objets partagés avec le projet."""
    base = workdir.work_base(root)
    workdir.ensure_private_dir(base, base)
    # file:// force le transport par paquets : jamais de liens durs ni d'objects/info/alternates.
    res = run_git(
        base,
        [
            'clone',
            '--quiet',
            '--depth',
            '1',
            '--single-branch',
            '--no-tags',
            '--branch',
            branch,
            '--end-of-options',
            Path(root).resolve().as_uri(),
            str(clone),
        ],
        check=False,
    )
    if res.returncode != 0:
        raise ValueError(f"Échec du clonage de {branch} dans {clone} : {res.stderr.strip()}")
    clone.chmod(0o700)
    run_git(clone, ['remote', 'remove', 'origin'])
    run_git(clone, ['config', 'core.hooksPath', '/dev/null'])
    run_git(clone, ['config', 'core.fsmonitor', 'false'])
    workdir.record_project(clone, root)
    # Le clone ne lit pas la configuration locale du projet : l'identité des commits de fusion s'y copie.
    for key in ('user.name', 'user.email'):
        value = run_git(root, ['config', '--get', key], check=False)
        if value.returncode == 0 and value.stdout.strip():
            run_git(clone, ['config', key, value.stdout.strip()])
    copy_local_excludes(root, clone)


def copy_local_excludes(root: Path, clone: Path) -> None:
    """Append the project's `.git/info/exclude` to the clone's: a clone does not inherit it.

    An adopted mission declares `.workspace/` ignored only there (its `.gitignore` is the client's), and the circuit's
    own files in the clone (`.workspace/exchange/`) would otherwise be untracked files outside the task's scope."""
    source = Path(run_git(root, ['rev-parse', '--path-format=absolute', '--git-path', 'info/exclude']).stdout.strip())
    if source.is_symlink() or not source.is_file():
        return
    text = source.read_text(encoding='utf-8', errors='replace')
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith('#')]
    if not lines:
        return
    target = clone / '.git/info/exclude'
    if target.is_symlink():
        raise ValueError(f"Lien symbolique interdit dans le clone : {target}")
    target.parent.mkdir(exist_ok=True)
    current = target.read_text(encoding='utf-8', errors='replace') if target.is_file() else ''
    if current and not current.endswith('\n'):
        current += '\n'
    target.write_text(current + '\n'.join(lines) + '\n', encoding='utf-8')


def remove_task_clone(clone: Path) -> None:
    if clone.is_symlink():
        clone.unlink()
    elif clone.exists():
        shutil.rmtree(str(clone))


def verify_task_clone(root: Path, task_id: str, clone: Path, branch: str, walk: bool = False) -> None:
    """Le dossier est bien le clone de la tâche : `.git` réel, HEAD sur la branche ; `walk` : aucun lien symbolique dedans."""
    expected = workdir.task_worktree(root, task_id)
    if clone != expected:
        raise ValueError(
            f"Le dossier de travail enregistré n'est pas celui de la tâche : {clone} (attendu {expected} ; "
            f"{workdir.WORK_DIR_VARIABLE} a-t-il changé depuis prepare ?)."
        )
    workdir.check_no_symlinks_below(workdir.work_base(root), clone)
    if not clone.is_dir():
        raise ValueError("Worktree de tâche introuvable.")
    git_dir = clone / '.git'
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise ValueError(f"{git_dir} n'est pas un dossier Git réel : ce n'est pas le clone de la tâche.")
    head = run_git(clone, ['symbolic-ref', '--quiet', 'HEAD'], check=False).stdout.strip()
    if head != 'refs/heads/' + branch:
        raise ValueError("Branche du worktree incohérente.")
    if walk:
        for directory, dirs, files in os.walk(str(clone), followlinks=False):
            for name in dirs + files:
                if (Path(directory) / name).is_symlink():
                    raise ValueError("Lien symbolique interdit dans le worktree.")


def fetch_target_head(root: Path, clone: Path) -> str:
    """Le clone récupère la tête de la branche courante du projet ; rend son identifiant."""
    target_ref = run_git(root, ['symbolic-ref', '--quiet', 'HEAD']).stdout.strip()
    target_head = run_git(root, ['rev-parse', '--verify', 'HEAD']).stdout.strip()
    fetched = run_git(clone, ['fetch', '--quiet', '--no-tags', Path(root).resolve().as_uri(), target_ref], check=False)
    if fetched.returncode != 0:
        raise ValueError(f"Le clone n'a pas pu récupérer {target_ref} du projet : {fetched.stderr.strip()}")
    if run_git(clone, ['rev-parse', '--verify', 'FETCH_HEAD']).stdout.strip() != target_head:
        raise ValueError("la cible a changé pendant la récupération, relancer merge")
    return target_head


def adopt_clone_branch(root: Path, clone: Path, branch: str) -> None:
    """Le projet récupère la branche du clone, en avance rapide seulement (le travail validé n'est jamais perdu)."""
    ref = 'refs/heads/' + branch
    fetched = run_git(
        root, ['-c', 'transfer.fsckObjects=true', 'fetch', '--quiet', '--no-tags', clone.resolve().as_uri(), f'{ref}:{ref}'], check=False
    )
    if fetched.returncode != 0:
        raise ValueError(f"La branche {branch} du clone n'est pas en avance rapide sur celle du projet : {fetched.stderr.strip()}")


def remove_legacy_worktree(root: Path, worktree: Path, ref: str, force: bool) -> None:
    """Tâche ancienne : git worktree enregistré dans le projet (chemin relatif dans l'état)."""
    from mission_rules import check_path_security

    listing = run_git(root, ['worktree', 'list', '--porcelain', '-z']).stdout
    expected = 'worktree ' + str(worktree)
    records = listing.split('\0\0')
    if not any(expected in record.split('\0') and 'branch ' + ref in record.split('\0') for record in records):
        raise ValueError("Worktree non enregistré pour la branche de tâche.")
    for directory, dirs, files in os.walk(str(worktree), followlinks=False):
        for name in dirs + files:
            if (Path(directory) / name).is_symlink():
                raise ValueError("Lien symbolique interdit dans le worktree.")
    check_path_security(worktree / '.git', root, 'fichier Git du worktree')
    # The root may itself be a linked worktree (campaign arm): its .git is a file and
    # task worktrees live under the common Git directory of the main repository.
    common_args = ['rev-parse', '--path-format=absolute', '--git-common-dir']
    common_dir = Path(run_git(root, common_args).stdout.strip())
    task_common_dir = Path(run_git(worktree, common_args).stdout.strip())
    if common_dir.resolve() != task_common_dir.resolve():
        raise ValueError("Le worktree de tâche dépend d'un autre dépôt Git que la racine du projet.")
    git_dir = Path(run_git(worktree, ['rev-parse', '--absolute-git-dir']).stdout.strip())
    check_path_security(git_dir, common_dir / 'worktrees', 'métadonnées du worktree')
    # Ignored files (build outputs, caches) are not work to protect.
    dirty = run_git(worktree, ['status', '--porcelain=v1', '--untracked-files=all']).stdout
    if dirty.strip() and not force:
        raise ValueError("Modifications non validées dans le worktree ; --force requis.")
    run_git(root, ['worktree', 'remove'] + (['--force'] if force else []) + ['--', str(worktree)])
