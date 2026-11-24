"""Règles de périmètre et de sécurité des chemins pour mission.py.

Python 3.14 standard library uniquement.
"""

import fnmatch
from pathlib import Path

from scope import matches

SENSITIVE_PATHS = [
    '.git',
    '.git/config',
    '.git/HEAD',
    '.workspace',
    '.workspace/orchestration',
    '.workspace/worktrees',
    'scripts/gate.sh',
    'scripts/check-scope.sh',
    'scripts/scope-baseline.sh',
    'scripts/verify-task.sh',
    'scripts/docs-check.sh',
    'scripts/check-secret-tracing.sh',
    'scripts/lib/task.py',
    'scripts/lib/scope.py',
    'scripts/lib/secrets.py',
    'scripts/lib/output.py',
    'scripts/lib/mission.py',
    '.env',
    '.env.local',
    '.env.production',
    'secrets/key',
    'secrets/token.txt',
    'cert.key',
]


def remove_prefix(text: str, prefix: str) -> str:
    if text.startswith(prefix):
        return text[len(prefix) :]
    return text


def is_relative_to_path(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def is_sensitive_pattern(pattern: str) -> bool:  # noqa: C901
    if not isinstance(pattern, str):
        return True
    stripped = pattern.strip()
    if not stripped or stripped in ('.', './', '/'):
        return True
    clean = remove_prefix(stripped, './').strip('/')
    if not clean or clean in ('.', '/', '*', '**'):
        return True

    parts = [p for p in clean.split('/') if p and p != '.']
    if not parts or any(p in ('.', '..') for p in parts):
        return True

    if clean in ('*', '**') or parts[0] in ('*', '**'):
        return True

    # Protéger .git, .workspace et scripts dès la racine
    if parts[0] in ('.git', '.workspace', 'scripts') or any(p in ('.git', '.workspace') for p in parts):
        return True

    # Secrets and env files at any depth (excluding inside node_modules)
    if 'node_modules' not in parts:
        if 'secrets' in parts:
            return True
        if any(p.startswith('.env') for p in parts):
            return True
        if any(p.endswith('.key') for p in parts):
            return True

    for sp in SENSITIVE_PATHS:
        try:
            if matches(sp, clean) or matches(clean, sp) or patterns_overlap(clean, sp):
                return True
        except Exception:
            pass
    return False


def validate_allowed_patterns(root: Path, allowed: list[str]) -> None:  # noqa: C901
    if not isinstance(allowed, list) or not allowed:
        raise ValueError("Le périmètre allowed doit être une liste non vide de motifs.")
    for pattern in allowed:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError("Motif allowed vide ou invalide.")
        stripped = pattern.strip()
        if stripped in ('.', './', '/'):
            raise ValueError(f"Motif racine ou vide interdit dans le périmètre allowed : {pattern}")
        if pattern.startswith('/') or pattern.startswith('\\'):
            raise ValueError(f"Chemin absolu interdit dans le périmètre allowed : {pattern}")
        parts = remove_prefix(stripped, './').split('/')
        if '..' in parts:
            raise ValueError(f"Traversée de chemin (..) interdite dans le périmètre allowed : {pattern}")
        if is_sensitive_pattern(pattern):
            raise ValueError(f"Périmètre sensible ou interdit dans allowed : {pattern}")
        curr = root
        for part in parts:
            if any(char in part for char in '*?[]'):
                break
            curr = curr / part
            if curr.is_symlink():
                try:
                    target = curr.resolve()
                    if not is_relative_to_path(target, root):
                        raise ValueError(f"Lien symbolique (symlink) sortant interdit dans allowed : {pattern}")
                except (OSError, ValueError) as exc:
                    raise ValueError(f"Lien symbolique (symlink) sortant interdit dans allowed : {pattern}") from exc


def check_path_security(path: Path, root: Path, label: str = "fichier de tâche") -> None:
    if not is_relative_to_path(path, root):
        raise ValueError(f"Le {label} pointe hors de la racine du projet (lien symbolique sortant ou chemin externe).")
    curr = path
    while True:
        if curr.is_symlink():
            raise ValueError(f"Lien symbolique (symlink) interdit pour le {label} : {curr}")
        if curr == root or curr == curr.parent:
            break
        curr = curr.parent


def check_infrastructure_symlinks(root: Path) -> None:
    for rel in (
        '.workspace',
        '.workspace/orchestration',
        '.workspace/orchestration/.lock',
        '.workspace/orchestration/tasks',
        '.workspace/worktrees',
    ):
        p = root / rel
        if p.is_symlink():
            raise ValueError(f"Lien symbolique (symlink) interdit dans l'infrastructure : {p}")
        if p.exists():
            for parent in (p, *p.parents):
                if parent == root:
                    break
                if parent.is_symlink():
                    raise ValueError(f"Lien symbolique (symlink) interdit dans l'infrastructure : {parent}")


def patterns_overlap(pat1: str, pat2: str) -> bool:  # noqa: C901
    p1 = remove_prefix(pat1.strip(), './').strip('/')
    p2 = remove_prefix(pat2.strip(), './').strip('/')
    if p1 == p2:
        return True

    try:
        if matches(p1, p2) or matches(p2, p1):
            return True
    except Exception:
        pass

    parts1 = [p for p in p1.split('/') if p]
    parts2 = [p for p in p2.split('/') if p]

    def segments_could_match(s1: str, s2: str) -> bool:
        if s1 == s2 or s1 == '*' or s2 == '*':
            return True
        if fnmatch.fnmatchcase(s1, s2) or fnmatch.fnmatchcase(s2, s1):
            return True
        if any(c in s1 for c in '*?[]') and any(c in s2 for c in '*?[]'):
            return True
        return False

    i = 0
    while i < len(parts1) and i < len(parts2):
        s1, s2 = parts1[i], parts2[i]
        if s1 == '**' or s2 == '**':
            return True
        if not segments_could_match(s1, s2):
            return False
        i += 1

    # If all segments of one pattern matched the prefix of the other pattern,
    # the shorter pattern represents an ancestor directory/parent scope containing the child,
    # so they overlap and must never be considered disjoint.
    return True
