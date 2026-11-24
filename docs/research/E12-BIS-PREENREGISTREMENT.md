# E12 bis — Préenregistrement : le circuit maigre, sans Opus, sur la même tâche

Écrit par Fable le 24 octobre 2026 au soir, avant la course, à la demande du propriétaire après les résultats d'E12 (`E12-RESULTATS.md`) :
« Sonnet orchestrateur, Sonnet exécutant, on n'utilise pas Opus, on va voir ce qui va se passer ; l'orchestrateur peut prendre d'autres
agents de l'équipe. Pas de relecteur ni rien. » Figé par son commit ; tout changement après la course est un amendement daté.

## 1. Question

Le circuit du kit, mené par **Sonnet 5.5** avec une consigne courte et sans Opus, livre-t-il la même tâche T62 (`E12-demande-sauvegarde.md`)
aussi bien que les deux bras d'E12 (Opus seul : 28/28, 24 min, 3,23 $ ; circuit Opus : 28/28, 77 min, ≈ 7,9 $), et à quel coût ?

## 2. Hypothèses, écrites avant

- H1 : le bras C passe la majorité des 28 tests cachés ; l'écart avec 28/28 est petit (0 à 3 tests).
- H2 : le coût Claude du bras C est inférieur à celui d'Opus seul (3,23 $) et au tiers du circuit Opus.
- H3 : la durée est inférieure à celle du circuit Opus (77 min) mais supérieure à Opus seul (24 min), à cause du cadrage et de la suite.
- H4 : l'orchestrateur Sonnet prend au moins une décision de délégation vers un autre agent de l'équipe (Haiku ou Antigravity) ; sinon le
  « choix d'exécutant » est une option morte sur une tâche à une fiche.

## 3. Bras C, définition exacte

| Qui | Comment |
|---|---|
| Orchestrateur **Sonnet 5.5**, session Claude Code ouverte **depuis le dossier du kit** (pour que `report.sh --usine` compte ses tokens, T88), verrou `usine-e12-circuit-c` | consigne courte (`docs/journal/2026-10-24/CONSIGNE-usine-e12-circuit-c.md`, ~1 500 tokens) à la place de la lecture complète d'`ORCHESTRATEUR.md` ; une fiche `e12-archive-c` à partir de la demande ; **6 à 8 tests cachés** ciblés (`acceptance_min: 6`, raison : interface imposée) ; `plan-check --with-model` une fois ; `check` ; `prepare --selfcheck` ; délégation en conteneur ; `fast` pendant, `full` avant fusion ; fusion dans la branche d'étude `e12-bras-c` ; miroir ; commit ; rapport **sans `report-check`** ; `release` |
| Exécutant par défaut **Sonnet 5.5** (`Bric`), en conteneur | l'orchestrateur peut confier une partie à un autre membre de `team.json` (`Plume` Haiku pour la documentation, `Crash` Haiku pour les tests, `Atlas`/`Fiole` Antigravity pour l'exploration, `Loupe` Codex) s'il le juge utile et le dit ; **aucun Opus**, ni orchestrateur, ni exécutant, ni relecteur |
| Régime | orchestrateur seul : aucune question à Fable hors question bloquante et pertinente ; deux échecs successifs : arrêt, clone gardé |

Base : `main` du kit au départ (`f8babd7` ou plus récent, les 28 tests cachés rejoués rouges dessus). Une course. Antigravity, s'il est appelé,
ne reçoit que des fichiers : il refuse les commandes en mode sans interaction (§5 d'`ORCHESTRATEUR.md`).

## 4. Mesures (identiques à E12 §6)

Livré : 28 tests cachés de Fable sur la branche fusionnée, hôte `/usr/bin/python3`. Suite complète rejouée par Fable dans un clone.
Tokens : `report.sh --usine usine-e12-circuit-c` (orchestrateur) et `--task e12-archive-c` (exécutants), plus la transcription si le premier
rend zéro. Durée : claim → release. Questions, décisions hors demande, délégations vers d'autres agents. Qualité aveugle : relecture Codex du
diff C avec les mêmes trois critères (régression, sécurité, lisibilité), même format qu'E12, diff anonymisé, `LOG.md` retiré ; comparée aux
notes d'E12 avec la réserve connue (le relecteur s'est trompé sur la régression en E12). Forme : mêmes mesures `ast`.

## 5. Règle de décision, écrite avant

1. 28/28 : égalité sur « livré » avec A et B. Moins : le bras C a perdu sur « livré », et on compte ce qui manque.
2. Le coût est rapporté, pas jugé : si C ≤ 3,23 $ à livraison égale, le circuit a une raison d'être économique ; si C > B, le régime maigre
   n'a pas maigri.
3. Question répondue ou intervention humaine : la course est dite exclue (règle d'E12), sauf si la question porte sur la procédure du
   plan et non sur la tâche, ce qui est alors écrit en réserve (leçon d'E12).

## 6. Menaces à la validité

Une course, une tâche, la même qu'E12 (les modèles n'ont pas de mémoire entre sessions, mais Fable a écrit la demande et les tests cachés
en connaissant déjà deux solutions). La consigne courte est écrite par Fable après avoir vu ce qui a coûté en E12 : un biais en faveur de C.
Sonnet orchestrateur n'a jamais été mesuré dans ce rôle sur le kit. Pas de relecteur : la qualité hors tests ne repose que sur la
relecture aveugle après coup.

## Signature

Préenregistré par Fable le 24 octobre 2026, lancé sur l'accord du propriétaire donné dans sa session (« fais ça, fais ton plan… commence comme ça
avec Sonnet orchestrateur, Sonnet exécutant »). Réserve d'E12 (règle 4 sur la course B) toujours à trancher par le propriétaire.
