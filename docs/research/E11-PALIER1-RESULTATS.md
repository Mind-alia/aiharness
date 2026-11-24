# E11 — Palier 1 (répétition générale) : résultats
<!-- report-check:debut -->
> **Vérification externe** (2026-10-21 UTC, codex) : 18/20 affirmations vraies, 0 fausses, 2 non vérifiables ; témoins 3/3 ; coût : extraction 49928 tokens, vérification 319922 tokens.
<!-- report-check:fin -->

Écrit par Fable le 21 octobre 2026 à 23 h, depuis `.workspace/campaign-e11/palier1/courses.jsonl` (export `courses.csv`), sans lire un
seul diff d'agent. Préenregistrement : `docs/research/E11-PREENREGISTREMENT.md`, section « Amendements du 21 octobre », point 4.
Le palier 1 est exploratoire : il ne compte pas dans les 42 courses d'E11 et ne tranche aucune hypothèse. Il répond à deux questions :
le pilote et le circuit tiennent-ils, et quel est un premier chiffre par bras sur la plus petite fiche.

## Dispositif, tel qu'il a été joué
- Fiche `instruments-defauts` (T47, T48), base `9e52800`, tests cachés d'origine inchangés
  (`.workspace/acceptance/instruments-defauts/tests/test_acceptance_instruments_defauts.py`, 4 tests, dont 2 rouges sur la base :
  vérifié une fois par Fable sur l'hôte avant les courses, sur une copie `git archive` de la base, hors dépôt).
- Trois bras, deux répétitions, ordre tiré au sort (graine 20261021) : circuit-haiku#2, circuit-sonnet#1, opus-seul#2, opus-seul#1,
  circuit-sonnet#2, circuit-haiku#1. Une course à la fois.
- Modèles réellement appelés (lus dans les transcriptions Claude Code des six worktrees) : `claude-haiku-5-5`, `claude-sonnet-5-5`,
  `claude-opus-5-5`. Casseur : Codex `gpt-6.1-sol` (6 passages, `consumption.jsonl`).
- Bras circuit : fiche identique sauf `id` et `agent` ; `prepare --base 9e52800 --selfcheck` puis le contrôle propre de `delegate.sh`
  (test caché rouge sur la base, code 1, pour les quatre courses circuit, deux contrôles chacune) ; `attempts: 2` ; casseur
  `--promote` ; ronde de correction si faille retenue. Les deux courses seules n'ont pas ce contrôle (aucun test caché déclaré).
- Bras seul : corps de la fiche, `allowed` large, `verify: "true"`, `attempts: 1`, aucun test caché déclaré ; casseur après coup, sans
  retour à l'agent.
- Verdict unique : test caché d'origine lancé sur l'hôte (`/usr/bin/python3` 3.9) dans le worktree final de chaque course, avec
  `WORKSPACE_TEST_TMP` = `.workspace/campaign-e11/palier1/tmp-<course>/` : sous `.workspace/` du dépôt (ignoré par Git), donc **pas
  hors dépôt** comme l'amendement 4 le prévoyait ; écart déclaré, sans effet sur le registre des tâches.
- Travail conservé sur `workspace-task/e11p1-*` (six branches), tâches closes `abandoned`, rien fusionné dans `main`.
- Début 22:36:14, fin 22:51:21 (15 min 7 s pour six courses).

## Tableau des courses

| ordre | course | bras | issue circuit (tentatives / passages d'acceptation) | casseur retenues/écartées | verdict hôte | tokens exécutant : entrée / cache lu / cache écrit / sortie | délégation s | course s | commit |
|---|---|---|---|---|---|---|---|---|---|
| 0 | e11p1-circuit-haiku-2 | Haiku circuit | done (1 / 1) | 0/0, sans_faille | 4/4, code 0 | 16 / 295 096 / 39 315 / 7 209 | 64 | 144 | 06d7d5a |
| 1 | e11p1-circuit-sonnet-1 | Sonnet circuit | done (1 / 1) | 0/0, sans_faille | 4/4, code 0 | 16 / 275 226 / 36 790 / 6 067 | 90 | 171 | a093d48 |
| 2 | e11p1-opus-seul-2 | Opus seul | done (1 / 0) | 0/0, sans_faille | 4/4, code 0 | 20 / 343 084 / 36 984 / 6 390 | 76 | 161 | 3ef8242 |
| 3 | e11p1-opus-seul-1 | Opus seul | done (1 / 0) | 0/0, sans_faille | 4/4, code 0 | 18 / 314 291 / 34 339 / 5 875 | 71 | 149 | abd25a4 |
| 4 | e11p1-circuit-sonnet-2 | Sonnet circuit | done (1 / 1) | 0/0, sans_faille | 4/4, code 0 | 16 / 250 796 / 32 922 / 7 083 | 71 | 142 | e6466ca |
| 5 | e11p1-circuit-haiku-1 | Haiku circuit | done (1 / 1) | 0/0, sans_faille | 4/4, code 0 | 14 / 367 962 / 57 120 / 7 749 | 67 | 141 | c79eb51 |

Casseur Codex par course (entrée / sortie) : 168 412 / 1 926 ; 256 319 / 1 675 ; 280 360 / 2 222 ; 309 099 / 1 493 ; 216 993 / 1 747 ;
228 745 / 1 300 (`report.sh --task`). Aucune ronde de correction n'a eu lieu : zéro faille retenue sur les six.

## Par bras (moyenne de deux courses)

| bras | livrées | sortie | cache lu | cache écrit | durée de délégation |
|---|---|---|---|---|---|
| Haiku circuit | 2/2 | 7 479 | 331 529 | 48 218 | 65 s |
| Sonnet circuit | 2/2 | 6 575 | 263 011 | 34 856 | 81 s |
| Opus seul | 2/2 | 6 132 | 328 688 | 35 662 | 73 s |

Totaux Claude des six exécutants : sortie 40 373, cache lu 1 846 455, cache écrit 237 470, entrée 100. Codex (casseur) : entrée
1 459 928, sortie 10 363. Fichiers changés : les quatre fichiers du périmètre, dans les six courses (bras seul compris, alors que son
périmètre était large).

## Ce que le palier 1 dit
1. **Le pilote et le circuit tiennent** : six courses sur six jouées sans incident, zéro erreur, zéro conteneur ni processus laissé,
   un seul worktree à la fin, `main` intact (`4488b6d`).
2. **La plus petite fiche ne discrimine rien** : 6/6 livrées à la première tentative, tests cachés 4/4 partout, casseur muet partout.
   `instruments-defauts` est trop petite (une ligne dans `prepare_task.py`, neuf dans `report_check.py`) pour séparer Haiku, Sonnet et
   Opus. Elle reste utile comme plancher, pas comme juge.
3. **À cette taille, les trois modèles consomment le même volume de tokens** (sortie 6 à 8 k, cache lu 250 à 370 k) : la différence de
   coût entre bras se réduit au prix unitaire du modèle, qui n'est pas mesuré ici (`docs/TARIFS-API.json` absent).
4. **Quota** : carte d'usage du compte avant/après (`usage.json`) : fenêtre 5 h 20 % → 26 %, semaine tous modèles 3 % → 4 %, semaine
   Fable 6 % → 7 % (la part Fable est la session de pilotage, pas les courses). Six courses ont donc coûté au plus six points de la
   fenêtre de 5 heures et un point de la semaine.

## Ce qu'il ne dit pas
- Rien sur H1 à H5 : deux répétitions d'une fiche sans variance ne tranchent rien. H4 (le casseur trouve une faille latente sur les
  bras seuls) n'a pas été observée ici ; ce n'est pas une réfutation.
- La durée de course n'est pas comparable entre bras : les bras circuit comptent deux contrôles du test caché en conteneur (celui de
  `prepare --selfcheck` et celui de `delegate.sh`) que le bras seul n'a pas.

## Défauts et écarts relevés (à corriger avant le palier 3)
- `consumption.jsonl` enregistre le modèle Claude sous son alias (`haiku`, `sonnet`, `opus`), pas son identifiant (`claude-haiku-5-5`) :
  l'identifiant exact n'est lisible que dans les transcriptions Claude Code. À consigner dans le journal (fiche courte).
- Les transcriptions des quatre courses Haiku et Sonnet contiennent chacune une ligne `claude-opus-5-5` parmi 28 à 33 lignes du modèle
  attendu. Non expliqué, non mesuré en tokens ; à comprendre avant d'imputer un coût par modèle.
- Le pilote vit hors kit (`.workspace/campaign-e11/palier1/driver.py`) : les bras `<modèle>-seul` et `circuit-<agent>` sur fiche
  existante restent à ajouter dans `tools/campaign/campaign.py` (amendement 3).
- Le bras seul a reçu, comme les autres, la consigne commune du kit et le conteneur ; « seul » veut dire sans porte, sans casseur, sans
  tests cachés ni ronde, pas sans cadre.

## Suite proposée
Fiche `campaign-arms-e11` (Sonnet) pour les bras manquants et l'identifiant de modèle, puis palier 3 : 42 courses sur
`instruments-defauts`, `mesure-humain`, `mesure-lecture`. Ordre de grandeur d'après ce palier : sept bras × trois fiches × deux
répétitions, en séquence, environ deux à trois heures si les fiches plus grosses prennent deux à quatre fois plus longtemps ; quota
Claude de l'ordre de la fenêtre de 5 heures entière si tout est joué d'un trait, donc à répartir sur deux fenêtres ou à jouer en
nuit ; Codex : six passages de casseur ont coûté 1,46 M de tokens d'entrée, 42 courses en coûteraient dix fois plus.
