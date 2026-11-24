"""Tranche S2 : trois niveaux d'isolation choisis par l'orchestrateur.

Règle de choix selon la spécification :
1. Valeur explicite de l'en-tête de tâche si présente.
2. Sinon 'none' pour une tâche en mode 'review' ou un projet de profil 'docs'.
3. Sinon 'exchange' pour les profils 'node', 'python' et 'docker' (dès que la tâche doit lancer des tests ou des outils).
4. 'full' n'est jamais choisi automatiquement tant que S3 n'existe pas ; le demander explicitement échoue avec un message clair.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

# Import task parser when a path is passed
try:
    from task import read_task
except ImportError:
    try:
        from .task import read_task  # type: ignore
    except ImportError:
        LIB_DIR = Path(__file__).resolve().parent
        if str(LIB_DIR) not in sys.path:
            sys.path.insert(0, str(LIB_DIR))
        from task import read_task  # type: ignore


def choose_isolation(task: dict[str, Any] | str | Path, profile: str) -> tuple[str, str]:
    """Détermine le niveau d'isolation ('none' | 'exchange') et la raison explicite.

    Args:
        task: Dictionnaire de tâche ou chemin vers un fichier task.md.
        profile: Profil du projet ('node', 'python', 'docker', 'docs').

    Returns:
        (niveau, raison) où niveau vaut 'none' ou 'exchange'.

    Raises:
        ValueError: Si 'full' est demandé (réservé S3), si l'isolation est invalide,
                    ou si le profil n'est pas supporté.
    """
    if isinstance(task, (str, Path)):
        task = read_task(Path(task))

    explicit = task.get('isolation')
    if explicit is not None:
        if explicit == 'full':
            raise ValueError("L'isolation 'full' n'est pas encore supportée dans la tranche S2 (prévue pour S3).")
        if explicit not in ('none', 'exchange'):
            raise ValueError(f"Niveau d'isolation inconnu dans la tâche : '{explicit}'")
        return explicit, f"Niveau d'isolation explicite dans l'en-tête de tâche : {explicit}"

    if task.get('mode') == 'review':
        return 'none', "Tâche en mode review (sans conteneur)"

    if profile == 'docs':
        return 'none', "Projet de profil docs (sans conteneur)"

    if profile in ('node', 'python', 'docker'):
        return 'exchange', f"Profil {profile} nécessitant l'exécution d'outils et de tests en conteneur"

    raise ValueError(f"Profil de projet inconnu ou non supporté : '{profile}'")
