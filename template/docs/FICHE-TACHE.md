# Fiche de tâche

## Besoin
Décrire le comportement attendu et son utilité.

## Interface imposée
Fixer les fichiers, noms exportés, signatures et formes des données.

L'en-tête facultatif `type:` (`code`, `docs`, `test`, `security` ou `exploration`) range la tâche dans l'écran « l'Usine » ;
sans lui, `mode: review` vaut `exploration`, `agent: antigravity` vaut `docs`, et tout autre cas vaut `code`.

## Règles numérotées dans l'ordre d'application
1. Décrire la première règle et sa priorité.
2. Décrire les règles suivantes, sans ambiguïté.

## Cas limites attendus
Lister les entrées vides, bornes, valeurs extrêmes et erreurs attendues.

## Contrat strict
- Objets stricts : refuser les champs inconnus.
- Unions discriminées : imposer les champs propres à chaque variante.
- Cohérence des dates : préciser ordre, validité et fuseaux.
- Aucune valeur inventée : représenter explicitement l'inconnu.

## Critères de conformité
Lister les comportements observables et les commandes de vérification.
Les tests cachés restent hors du worktree, dans `.workspace/acceptance/<id>/`.
L'en-tête facultatif associe `acceptance: [tests/cas.test.ts]` à
`acceptance_verify: npx vitest run tests/cas.test.ts`.

## Atelier de l'exécutant
La fiche donne tout ce qu'il faut dans le worktree, en chemins relatifs : l'exécutant ne voit que son worktree et son conteneur.
Ce qui manque se demande par `bash scripts/ask.sh "<question>"`, jamais en cherchant sur l'hôte.
