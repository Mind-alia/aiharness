# E11 — Palier 2 : `reconstruct-events`, huit bras, seize courses
<!-- report-check:debut -->
> **Vérification externe** (2026-10-21 UTC, codex) : 15/20 affirmations vraies, 0 fausses, 5 non vérifiables ; témoins 2/3 ; coût : extraction 53952 tokens, vérification 307855 tokens.
> Témoin manqué : « Codex gpt-6-astra a vérifié le rapport de cette nuit. »
<!-- report-check:fin -->

Écrit par Fable le 22 octobre 2026 à 1 h 30, depuis `.workspace/campaign-e11/palier2/courses.jsonl` (export `courses.csv`,
`model_ids.json`, `audit_commands.json`, `usage.json`), sans lire un seul diff d'agent. Préenregistrement : section « Palier 2 »
de `docs/research/E11-PREENREGISTREMENT.md`. Palier exploratoire : deux répétitions par bras, un écart d'une course est « non tranché ».

## Dispositif, tel qu'il a été joué
- Fiche `reconstruct-events`, base `b2b171e`, 29 tests cachés (15 d'origine + 14 écrits par Fable depuis la fiche), vérifiés rouges sur
  la base et verts sur `main` avant les courses. Ordre tiré (graine 20261022), une course à la fois, du 21 octobre 23 h 19 au 22 octobre
  1 h 14 (1 h 55).
- Modèles réellement appelés (transcriptions Claude Code des worktrees ; `CODEX_MODEL` pour Codex) : `claude-haiku-5-5`,
  `claude-sonnet-5-5`, `claude-opus-5-5`, `gpt-6-luna` ; casseur `gpt-6.1-sol`.
- Verdict unique : les 29 tests sur l'hôte (`/usr/bin/python3` 3.9, `WORKSPACE_TEST_TMP` hors dépôt) dans le worktree final.
  « Livré » = 29/29. Le verdict du circuit (porte, tests cachés en conteneur, casseur, ronde) est lu en plus dans `status.json`.
- Travail conservé sur `workspace-task/e11p2-*` (seize branches), rien fusionné dans `main` ; aucun conteneur ni processus laissé,
  un seul worktree à la fin.

## Tableau des courses

| ordre | course | ronde 1 : issue / tentatives / acceptation | casseur retenues | ronde 2 : issue / tentatives | hôte | livré | sortie tokens (r1 + r2) | cache lu r1 | délégation s | course s |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | sonnet-circuit-1 | done / 1 / 1 | 3 | acceptance_failed / 2 | 29/29 | oui | 49 148 + 8 459 | 1 550 556 | 384 | 560 |
| 1 | luna-seul-1 | done / 1 / 0 | 5 (instrument) | – | 29/29 | oui | 12 836 | 494 208 | 123 | 208 |
| 2 | haiku-seul-1 | done / 1 / 0 | 4 (instrument) | – | 5/29 | non | 65 257 | 1 410 099 | 306 | 399 |
| 3 | sonnet-circuit-2 | done / 2 / 2 | 4 | acceptance_failed / 2 | 29/29 | oui | 40 447 + 15 243 | 1 246 811 | 330 | 577 |
| 4 | opus-seul-2 | done / 1 / 0 | 3 (instrument) | – | 29/29 | oui | 43 625 | 1 280 913 | 437 | 511 |
| 5 | luna-seul-2 | done / 1 / 0 | 3 (instrument) | – | 29/29 | oui | 8 230 | 340 608 | 77 | 129 |
| 6 | sonnet-seul-2 | done / 1 / 0 | 3 (instrument) | – | 6/29 | non | 34 497 | 809 288 | 246 | 310 |
| 7 | luna-circuit-2 | done / 2 / 2 | 2 | acceptance_failed / 2 | 29/29 | oui | 26 395 + 4 588 | 1 202 432 | 222 | 352 |
| 8 | luna-circuit-1 | acceptance_failed / 2 / 2 | non lancé | – | 28/29 | non | 9 442 | 384 640 | 101 | 106 |
| 9 | haiku-circuit-1 | done / 2 / 2 | 4 | acceptance_failed / 2 | 29/29 | oui | 61 502 + 23 326 | 2 269 090 | 316 | 537 |
| 10 | opus-circuit-2 | done / 1 / 1 | 5 | acceptance_failed / 2 | 29/29 | oui | 31 684 + 12 137 | 920 541 | 305 | 515 |
| 11 | opus-seul-1 | done / 1 / 0 | 3 (instrument) | – | 29/29 | oui | 44 693 | 1 392 081 | 412 | 490 |
| 12 | opus-circuit-1 | done / 2 / 2 | 3 | acceptance_failed / 2 | 29/29 | oui | 47 002 + 51 710 | 1 134 275 | 443 | 1 122 |
| 13 | sonnet-seul-1 | done / 1 / 0 | 2 (instrument) | – | 23/29 | non | 36 114 | 969 148 | 257 | 318 |
| 14 | haiku-seul-2 | done / 1 / 0 | 3 (instrument) | – | 6/29 | non | 57 759 | 1 850 126 | 268 | 335 |
| 15 | haiku-circuit-2 | done / 1 / 1 | 3 | acceptance_failed / 2 | 6/29 | non | 49 480 + 16 313 | 1 897 866 | 243 | 433 |

« done / 1 / 1 » : la ronde 1 a passé la porte et les 29 tests cachés en conteneur. Les bras seuls n'ont ni tests cachés ni ronde 2.
Codex sur les seize courses (`consumption.jsonl`) : Luna exécutant 8 appels, entrée 3 161 118 (dont cache 2 886 784), sortie 61 491 ;
Sol casseur 15 passages, entrée 2 221 870 (dont cache 1 848 832), sortie 36 847.

## Par bras (deux courses chacun)

| bras | livrées | sortie moy. (r1 + r2) | cache lu moy. | délégation moy. |
|---|---|---|---|---|
| Haiku seul | 0/2 | 61 508 | 1,63 M | 287 s |
| Haiku circuit | 1/2 | 75 310 | 3,22 M | 406 s |
| Sonnet seul | 0/2 | 35 306 | 0,89 M | 251 s |
| Sonnet circuit | 2/2 | 56 648 | 1,98 M | 490 s |
| Opus seul | 2/2 | 44 159 | 1,34 M | 424 s |
| Opus circuit | 2/2 | 71 266 | 1,99 M | 753 s |
| Luna seul | 2/2 | 10 533 | 0,42 M | 100 s |
| Luna circuit | 1/2 | 20 212 | 1,03 M | 199 s |

Totaux Claude des exécutants : sortie 688 396 tokens. Quota du compte (`usage.json`) : fenêtre 5 h 34 % → 83 %, semaine tous modèles
5 % → 10 %, semaine Fable 9 % → 17 % (la part Fable est la session de pilotage et ses audits, pas les courses).

## Ce que le palier dit
1. **La fiche discrimine.** Haiku seul 0/2 (5 et 6 sur 29), Sonnet seul 0/2 (6 et 23 sur 29), Opus seul 2/2, Luna seul 2/2.
2. **Le circuit relève Sonnet, et Haiku une fois sur deux.** Sonnet : 0/2 seul, 2/2 dans le circuit (différence observée, à confirmer).
   Haiku : 0/2 seul, 1/2 dans le circuit (non tranché). Dans les deux cas, ce qui fait passer de 6 à 29 tests est le retour des tests
   cachés et la seconde tentative : les quatre courses circuit Haiku et Sonnet ont toutes passé les 29 tests en conteneur en ronde 1.
3. **Le circuit n'apporte rien à Opus sur cette fiche** : 2/2 seul, 2/2 dans le circuit, pour 60 % de tokens en plus et un temps
   presque double.
4. **Le casseur trouve quelque chose à chaque fois** : 2 à 5 tests retenus sur les quinze courses où il est passé, Opus et Luna compris,
   alors que ces diffs passent 29/29. H4 est observée sur les huit bras seuls. Mais les mêmes trois thèmes reviennent chez les quatre
   modèles (`markdown_table_without_outer_pipes`, `invalid_merge_identifier`, `branch_must_point_at_first_commit`, noms lus dans
   `report.json`) : quand quatre modèles font la même « erreur », l'ambiguïté est probablement dans la fiche, et le casseur lit à la
   lettre. À trancher en relisant la fiche, pas les diffs.
5. **La ronde de correction n'a jamais résolu les tests du casseur** (sept rondes, sept `acceptance_failed`), et une fois elle a cassé une
   solution qui passait (Haiku circuit 2 : 29/29 en ronde 1, 6/29 après la ronde ; un seul fichier retouché, `reconstruct_events.py`).
   Au sens strict du circuit, aucune course circuit n'était fusionnable ; au sens des 29 tests, sept sur huit l'étaient avant la ronde.
6. **Luna** : rapide (1 à 2 min) et bon marché (8 à 26 k de sortie), 3/4 ; l'échec est une majuscule dans la phrase imposée de la
   documentation, non corrigée après le retour d'erreur.
7. **Coût estimé par course livrée** (tarifs API Anthropic du 6 octobre 2026 ; OpenAI d'après des sources tierces, Luna 0,10 / 0,50 $,
   Sol 2 / 10 $ par million ; cache Sol non tarifé ; `docs/TARIFS-API.json` absent, donc ordre de grandeur, pas facture) :
   Opus seul ≈ 1,15 $ ; Opus circuit ≈ 1,8 $ + casseur ; Sonnet circuit ≈ 0,95 $ + casseur ; Haiku circuit ≈ 0,07 $ + casseur ;
   Luna seul ≈ 0,02 $ ; un passage du casseur Sol ≈ 0,10 $. H3 (Haiku circuit sous le cinquième d'Opus seul) est **fausse en tokens**
   (75 k contre 44 k de sortie) et vraie en dollars, d'un facteur dix environ.

## Hypothèses, lecture provisoire (deux répétitions)
- H1 (Haiku circuit ≥ Opus seul) : 1/2 contre 2/2, non tranché, tendance « inférieur ».
- H2 (circuit > seul) : Sonnet 2/2 contre 0/2, différence observée ; Haiku 1/2 contre 0/2, non tranché ; Luna 1/2 contre 2/2, non tranché
  (le circuit a coûté une course à Luna par une lettre) ; Opus 2/2 contre 2/2, sans effet.
- H3 : fausse en tokens, vraie en prix (point 7). H4 : observée (point 4), sous réserve de la validité des trouvailles. H5 : non jouée.

## Écarts et menaces, déclarés
- **Historique git visible.** Le préenregistrement prévoyait un clone sans historique ; le pilote a utilisé des worktrees du kit, qui
  partagent `.git` avec `main` où la solution est fusionnée. Audit des seize courses (`audit_commands.py`, résultats d'outils relus) :
  sept courses Claude ont exécuté `git log` ou `git branch` sur l'hôte et vu les sujets « merge: task reconstruct-events » et des
  branches `archive/workspace-task/reconstruct-events-tentative…` ; aucune n'a lu le code (toutes les tentatives de `git ls-tree` et de
  `git show` ont été refusées par le mode sans interaction, et git ne fonctionne pas dans le conteneur). Luna n'a lancé que `git status`
  et `git diff --check`. Les agents ont donc su que la tâche existait, sans voir sa solution. À corriger avant le palier 3 : clone
  orphelin ou peu profond.
- Codex charge de lui-même des compétences « superpowers » depuis le cache `~/.codex` de la machine : influence extérieure au protocole.
- Le cache d'entrée Codex est partagé entre courses de la même fiche (2,9 M lus en cache sur 3,2 M pour Luna) : un effet de prix et de
  vitesse, pas de contenu.
- Les transcriptions Haiku et Sonnet contiennent une à deux lignes `claude-opus-5-5` parmi 47 à 182 lignes du modèle attendu ; non
  expliqué, non mesuré en tokens.
- Le pilote a enregistré l'identifiant de modèle Claude « non mesuré » (nom de dossier mal formé) ; les identifiants ont été relus après
  coup depuis les transcriptions (`model_ids.json`). Le champ `diff_stat` des lignes ignore les fichiers nouveaux ; les commits sur
  `workspace-task/e11p2-*` sont complets.
- Les 29 tests sont de la même famille que trois bras sur quatre (écrits par des orchestrateurs Claude et par Fable).

## Suite proposée
Avant le palier 3 : relire la fiche sur les trois ambiguïtés que le casseur relève (tableaux sans barres extérieures, identifiant de
fusion invalide, branche sur le premier commit) et décider si c'est la fiche ou le casseur qui a tort ; garder le dernier état qui passe
quand une ronde de correction régresse ; clone sans historique ; bras dans `campaign.py` (carte `campaign-arms-e11`). Puis trois
répétitions sur cette fiche pour trancher H1 et H2 pour Haiku, et `mesure-humain` et `mesure-lecture` pour le palier 3.
