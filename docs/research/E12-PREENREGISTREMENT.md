# E12 — Préenregistrement : Opus seul contre le circuit, une tâche moyenne

Écrit par Fable le 24 octobre 2026, avant toute course, à la demande du propriétaire (« Opus 100 % seul sans sous-agent contre mon système sur
une tâche moyenne ; on calcule l'ensemble et on voit qui arrive à faire quoi »). Ce document est figé par son commit ; toute modification
après la première course est un amendement daté, jamais une réécriture.

## 1. Question

Sur une tâche de classe moyenne, bien spécifiée, un seul modèle Opus 5.5 en session Claude Code, sans sous-agent ni circuit, livre-t-il
aussi bien que le circuit complet du kit (orchestrateur Opus qui écrit fiche et tests cachés, exécutant Sonnet en conteneur, casseur,
fusion), et à quel coût total ?

## 2. Hypothèses, écrites avant

- H1 : les deux bras passent la majorité des tests cachés ; l'écart sur « livré » est petit (E11 palier 2 : Opus seul 2/2, Opus circuit 2/2).
- H2 : le circuit coûte plus en tokens totaux (orchestrateur + exécutant + casseur + contrôles) et en durée, d'un facteur 1,5 à 3.
- H3 : la qualité hors tests (lisibilité, sécurité, régressions trouvées par un relecteur aveugle) est meilleure pour le circuit, grâce aux
  tests cachés de l'orchestrateur et au casseur ; c'est l'hypothèse la moins sûre.
- H4 : Opus seul prend plus de décisions non demandées (interface, périmètre) ; le circuit en prend moins mais les prend sans le propriétaire aussi.

## 3. Bras, définitions exactes

| Bras | Qui | Comment |
|---|---|---|
| A — Opus seul | une session Claude Code Opus 5.5, outil `Agent` interdit (`--disallowedTools Agent`), aucun autre modèle | clone jetable de `main` (`git clone --depth 1`, hors du kit), reçoit la demande mot pour mot et la consigne « tu fais tout toi-même : lecture, code, tests, documentation ; tu livres sur la branche du clone ; tu lances la suite complète avant de dire fini » ; aucune question possible (Fable ne répond pas ; une question notée compte comme décision prise seul) |
| B — Circuit | orchestrateur Opus 5.5 avec verrou, Sonnet 5.5 exécutant en conteneur, casseur, tests cachés de l'orchestrateur, fusion | plan du jour à une fiche écrite à partir de la même demande ; l'orchestrateur écrit la fiche et ses propres tests cachés (il ne voit pas ceux de Fable), délègue, vérifie, fusionne dans une branche d'étude (pas `main` tant que l'étude n'est pas lue) ; même régime « seul » que les usines du 24/10 : pas d'accompagnement |

Même base : le commit de `main` à la fin de l'usine `usine-corrections` du 24/10. Même demande : `docs/research/E12-demande-sauvegarde.md`.
Ordre des bras tiré au sort (graine 20261024, pair = A d'abord). Une course par bras au minimum ; deux si le quota le permet, décision
du propriétaire avant la première course. Les deux bras tournent l'un après l'autre, jamais en même temps (quota et verrou).

## 4. Tâche

T62, sauvegarde de l'état vivant, classe moyenne : un module nouveau, un module existant touché, deux commandes, rotation, restauration
sûre, documentation. Interface imposée par la demande pour que les tests cachés s'appliquent aux deux bras.

## 5. Tests cachés de Fable

Écrits par Fable avant toute course, hors du kit (`~/Workspace-IA/etudes/E12/acceptance/test_e12_archive.py`),
invisibles aux deux bras. Vingt tests au moins, tous rouges sur la base, tous verts sur une solution de référence écrite par Fable dans
un clone jetable (règle T75 appliquée à l'étude elle-même) ; le commit de référence est noté ici par amendement avant la première course,
jamais montré aux bras. Ce qu'ils visent, au-delà de la preuve de la demande : rotation par nom et non par date ; nom en collision ;
lien symbolique vers l'extérieur ignoré et listé ; `path` du manifeste avec `..` ou absolu refusé sans écriture ; `sha256` faux refusé ;
lien symbolique à l'arrivée du `restore` non suivi ; `restore` sans `--force` refuse, avec `--force` n'écrase que le manifeste ; dossier
non inscriptible → `archive : échec`, verrou rendu, événement `archive_failed` ; `orchestrator_released` présent dans la sauvegarde ;
`archived` présent dans le journal du dépôt mais pas dans la sauvegarde ; mode `0700` du dossier ; entrée étrangère du dossier intacte ;
`claim` sur dossier vide → `archive : aucune` ; `list` trié ; codes de retour 0, 1, 64.

## 6. Mesures, par course

| Mesure | Bras A | Bras B |
|---|---|---|
| Livré | tests cachés de Fable sur l'hôte (`/usr/bin/python3` 3.9), n/N dans le clone final | même chose dans la branche fusionnée |
| Suite existante | `sh tests/run.sh full` vert (hors erreur connue) | idem |
| Tokens | transcription Claude Code du clone (`metrics_claude`, dossier du clone) : entrée hors cache, cache lu, cache créé, sortie | `report.sh --usine <nom>` (orchestrateur) + `report.sh --task <id>` (exécutant, casseur, contrôles, relectures) ; total toutes lignes |
| Durée | début de session → dernier message | claim → release |
| Interventions humaines | 0 attendu ; toute intervention disqualifie la course | idem |
| Questions / décisions seul | questions écrites par le modèle, décisions hors demande listées | questions `ask.sh`, décisions au rapport |
| Qualité aveugle | relecture Codex (`gpt-6.1-sol`) des deux diffs, anonymisés et dans un ordre tiré au sort : régression, sécurité, lisibilité, chacune notée 0, 1 ou 2 avec une phrase | idem |
| Forme | complexité max des fonctions nouvelles (radon), nombres nus dans le code nouveau, tests ajoutés, documentation touchée | idem |

## 7. Règle de décision, écrite avant

1. Un bras qui ne passe pas tous les tests cachés de Fable a perdu sur « livré » ; si les deux passent tout, égalité sur « livré ».
2. À égalité, la qualité aveugle (somme des six notes) départage ; un écart d'un point est « non tranché ».
3. Le coût est rapporté séparément : tokens totaux, tokens de sortie, durée ; il ne fait pas gagner, il dit le prix de ce qui a été livré.
4. Une course avec intervention humaine ou question répondue est exclue et rejouée si le quota le permet, sinon dite exclue.

## 8. Menaces à la validité, connues d'avance

- Une seule tâche, une ou deux courses par bras : on observe, on ne conclut pas ; E11 a montré des écarts d'une course sur deux.
- Le bras A tourne sur l'hôte sans conteneur ni crochet d'exécutant : il a plus de liberté et moins de sûreté ; c'est voulu (« 100 % seul »)
  et c'est dit.
- La demande impose l'interface : elle favorise les deux bras également, mais elle mesure la réalisation, pas la conception.
- Les tests cachés de Fable et ceux de l'orchestrateur (bras B) peuvent se recouper : le bras B est aussi mesuré par ses propres tests,
  qui ne comptent pas dans « livré ».
- Les tokens du bras B ne comptent pas la session Fable qui écrit le plan (travail identique pour les deux bras : la demande).

## 9. Livrables

`docs/research/E12-RESULTATS.md` écrit par Fable depuis les journaux et les mesures, sans lire les diffs avant la relecture aveugle ;
vérifié par `report-check.sh` ; entrée `docs/LOG.md` ; branches d'étude conservées, rien fusionné dans `main` avant lecture par le propriétaire.

## Signature

Préenregistré par Fable le 24 octobre 2026. En attente : accord du propriétaire sur la tâche (T62) et sur le nombre de courses.

## Amendement du 24 octobre 2026, 17 h UTC (avant toute course) : tests cachés et référence

- Tests cachés de Fable : `~/Workspace-IA/etudes/E12/acceptance/test_e12_archive.py`, **28 tests** en sept
  groupes (claim, release, rotation, échec, list, restore, miroir et documentation), boîte noire par les deux commandes imposées, projet
  temporaire construit depuis `template/scripts/` du dépôt candidat (`E12_REPO`).
- Base : `main` du kit à `0a90c4d` (après les corrections de l'après-midi). Sur la base : 28 rouges (14 échecs, 14 erreurs).
- Solution de référence écrite par Fable dans un clone jetable, commit `3568b29` (patch conservé hors du kit, jamais montré aux bras) :
  28 verts au premier passage. La référence n'a pas été jugée sur la suite complète du kit : elle sert à prouver que les tests sont
  satisfaisables, pas à concourir.
- Piège connu, laissé volontairement : `tests/test_events.py` fige le vocabulaire des événements ; ajouter `archived` et `archive_failed`
  le rend rouge, et « la suite complète reste verte » oblige chaque bras à le constater et à le mettre à jour proprement.
- La base des courses sera le `main` du kit au moment du départ, suite complète verte attestée ; si elle diffère de `0a90c4d`, les
  28 tests sont rejoués rouges dessus avant la première course.
