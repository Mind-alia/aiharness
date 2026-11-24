"""Verdicts du banc d'attaque du conteneur : chaque fonction lit le code de sortie et les flux d'une tentative
et rend `(statut, raison)` avec le statut `bloquée`, `FAILLE` ou `non testable`."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True


def check_fail_is_blocked(returncode: int, stdout: str, stderr: str) -> tuple[str, str]:
    if returncode != 0:
        return 'bloquée', ''
    return 'FAILLE', 'Action interdite réussie'


def check_network(returncode: int, stdout: str, stderr: str) -> tuple[str, str]:
    if returncode == 125 or 'NO_NET_TOOL' in stderr or 'NO_NET_TOOL' in stdout:
        return 'non testable', 'aucun outil réseau disponible (node, python3, curl, wget)'
    if returncode == 0:
        return 'FAILLE', 'Connexion réseau sortante établie'
    return 'bloquée', ''


def check_cgroup_pids(returncode: int, stdout: str, stderr: str) -> tuple[str, str]:
    if returncode == 125 or 'NO_CGROUP' in stderr or 'NO_CGROUP' in stdout:
        return 'non testable', '/sys/fs/cgroup/pids.max introuvable'
    if returncode == 0:
        return 'FAILLE', 'Limite de processus absente (/sys/fs/cgroup/pids.max vaut max)'
    return 'bloquée', ''


def check_cgroup_memory(returncode: int, stdout: str, stderr: str) -> tuple[str, str]:
    if returncode == 125 or 'NO_CGROUP' in stderr or 'NO_CGROUP' in stdout:
        return 'non testable', '/sys/fs/cgroup/memory.max introuvable'
    if returncode == 0:
        return 'FAILLE', 'Limite de mémoire absente (/sys/fs/cgroup/memory.max vaut max)'
    return 'bloquée', ''


def check_link_from_container(returncode: int, stdout: str, stderr: str) -> tuple[str, str]:
    if returncode == 0:
        return 'FAILLE', 'Lien symbolique créé dans /work : l\'hôte pourrait le suivre hors du worktree'
    return 'bloquée', ''
