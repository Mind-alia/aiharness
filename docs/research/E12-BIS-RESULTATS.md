# E12 bis — Résultats : le circuit maigre, Sonnet seul, sur la même tâche (T62)

Écrit par la session mère Opus 5.5 le 25 octobre 2026, à la demande du propriétaire (« lance la relecture aveugle Codex du diff C et écris la page
de résultats »), depuis les journaux et les mesures, après la relecture aveugle et avant toute lecture du code (sauf les vérifications
ponctuelles dites au §3). Préenregistrement : `E12-BIS-PREENREGISTREMENT.md` ; demande : `E12-demande-sauvegarde.md` ; bras A et B :
`E12-RESULTATS.md`. Une course. Pièces hors du kit : `~/Workspace-IA/etudes/E12/bras-C/` et
`etudes/E12/blind-review-codex-c.md`.

## 1. Ce qui a tourné

| | Bras C — circuit Sonnet |
|---|---|
| Base | `6237e66` (`main`), 28 tests cachés rouges (14 échecs, 14 erreurs, `bras-C/hidden-base.log`) |
| Qui | orchestrateur Sonnet 5.5 (session Claude Code ouverte depuis le kit, verrou `usine-e12-circuit-c`), consigne courte ; fiche `e12-archive-c` à Bric (Sonnet 5.5) en conteneur ; aucun autre agent, aucun Opus |
| Premier essai (24/10) | claim 17:52:34 UTC, fiche, 7 tests cachés, plan-check (une reprise), `prepare` à 17:54:58 ; puis délégation impossible (Docker arrêté), attente bornée de 30 min, release 18:26:11 (`3752a84`) |
| Course (relance) | claim 22:16:39 → délégation 22:16:45 → acceptance 7/7 22:19:38 → fusion `48c44b5` 22:38:33 → release 22:52:26 UTC |
| Livraison | branche d'étude `e12-bras-c`, commit final `b65ddbd` (miroir, rapport, LOG) ; rien dans `main` |
| Questions, interventions | 0 et 0 |

La relance a repris la fiche, les tests cachés, le plan-check et le `prepare` du premier essai ; le brief de relance (session mère) n'a changé
que la procédure : `check` seul refait, conteneur d'un autre projet (`aria-sec-pg`) ignoré, rapport daté du 25, attente bornée en cas de question.

## 2. Livré (mesure principale)

| Mesure | A — Opus seul | B — circuit Opus | **C — circuit Sonnet** |
|---|---|---|---|
| Tests cachés de Fable (28, hôte, `/usr/bin/python3`) | 28/28 | 28/28 | **28/28** (`bras-C/hidden.log`, clone de `b65ddbd`) |
| Suite complète rejouée dans un clone | 1 910, `errors=1` | 1 915, `errors=1` | **1 905, `errors=1`** (la même erreur connue), 2 sauts, 792 s (`bras-C/full-verif.log`) |
| Suite dite par le bras | 1 910 | 1 915 | 1 905 (clone de la tâche, puis hôte après fusion) |
| `docs-check.sh` | OK | OK | OK |
| Tests ajoutés (`def test_`) | 20 | 25 | 15 (13 dans `test_archive.py`) |
| Fichiers touchés (hors LOG et journal) | 14 | 15 | 12 |
| Lignes de `archive.py` | 472 | 451 | 301 |

Règle 1 : 28/28 → **égalité sur « livré »** avec A et B.

## 3. Qualité aveugle (Codex `gpt-6.1-sol`, lecture seule, diff C relu seul)

Dossier de relecture : arbre de la base sans historique, sans `docs/journal`, `docs/research/E12-*` ni `docs/LOG.md`, plus `DEMANDE.md` et
`diff-1.patch` (LOG et journal retirés). Consigne : `bras-C/blind-prompt.md`, mêmes trois critères et même format qu'E12 (la consigne d'E12
n'avait pas été gardée : elle est réécrite). 77 674 tokens d'entrée (55 680 en cache), 465 de sortie, 19 s.

| Critère (0–2) | A | B | **C** |
|---|---|---|---|
| Régression | 0 | 0 | 0 |
| Sécurité | 1 | 0 | 0 |
| Lisibilité | 2 | 1 | 1 |
| **Somme** | **3** | **1** | **1** |

Ce que dit le relecteur, vérifié ensuite dans `template/scripts/lib/archive.py` du bras C :
- Régression 0 : `release` garde le verrou pendant une copie que `COPY_TIMEOUT` ne borne pas vraiment. **Exact sur le fond, mal rangé** :
  l'échéance est testée entre deux blocs de 1 Mio dans `_copy_file` (B la testait entre deux fichiers, A coupait un processus enfant), donc
  une lecture qui ne rend jamais la main retient le verrou. Ce n'est pas un comportement existant cassé : les suites sont vertes.
- Sécurité 0 : `_copy_tree` ne teste `is_symlink()` que sur le dernier élément (`run/delegate`, `acceptance`) ; si `.workspace/run` est un
  lien vers l'extérieur, la copie le suit. **Exact.** Conséquence : des fichiers hors du dépôt peuvent être lus dans la sauvegarde (rien n'est
  écrit dehors). Ni les 7 tests cachés de l'orchestrateur ni les 28 de Fable ne couvrent un dossier parent lié. Non vérifié sur A et B.
- Lisibilité 1 : le test du délai simule une échéance déjà dépassée sans copie bloquée (même reproche qu'à B en E12).

Règle d'E12 appliquée à la lettre : C égale B et reste à 2 points derrière A.

## 4. Coût et durée

| | A | B | **C** |
|---|---|---|---|
| Durée | 24 min 28 s | 77 min 13 s | **35 min 47 s** (course) + ~2 min 24 s de cadrage le 24 |
| Temps de l'exécutant | — | 4 min 28 s | **2 min 54 s** |
| Sortie, tous modèles | 59 436 | 111 070 | **~31 000** (orchestrateur 8 254, exécutant 20 370, plan-check 2 391) |
| Cache lu | 4,89 M | 19,0 M | **~3,16 M** (orchestrateur 2,78 M, exécutant 0,37 M) |
| Coût liste estimé, Claude | 3,23 $ | ≈ 7,9 $ | **≈ 1,34 $** (orchestrateur 0,74, exécutant 0,49, plan-check 0,11) |

Tokens : `report.sh --usine usine-e12-circuit-c` (orchestrateur compté cette fois, session ouverte depuis le kit : T88 contourné). Il additionne
les deux fenêtres ; la part de la course est la différence avec le rapport du premier essai (2,95 M lus, 46 k créés, 16,6 k sortis, surtout
l'attente de Docker). Borne haute, essai arrêté compris : ≈ 2,28 $. Hypothèse de prix d'E12 : Sonnet 5.5 2 $/10 $, cache lu 0,20 $, écrit
(1 h) 4 $. Où passe le temps : exécutant 3 min ; vérification et suite complète avant fusion 19 min ; miroir, suite sur l'hôte, rapport, LOG
et release 14 min.

## 5. Forme (même script `ast` qu'E12, qui retrouve les chiffres de A et B)

| `archive.py` | A | B | **C** |
|---|---|---|---|
| Fonctions / complexité max | 27 / 17 | 28 / 12 | **17 / 16** (`_plan`) |
| Fonctions de complexité > 10 | 3 | 2 | 2 |
| Nombres nus (code / tests) | 7 / 18 | 5 / 25 | 6 / 5 |

## 6. Décisions prises là où la demande se tait (rapport de l'orchestrateur)

Un `release` réussi n'imprime rien (comme A ; B ajoutait une ligne) ; `skipped` est une liste `{path, reason}` ; session hors
`[A-Za-z0-9._-]` remplacée par `_`, `inconnue` si absente ; délai 120 s ; `events.py`, `test_events.py`, `test_orchestrator_run.py` mis dans
`allowed` par l'orchestrateur ; le `release` final a écrit une vraie sauvegarde dans `~/.workspace-ia/archives/workspace-ia/`
(`WORKSPACE_ARCHIVE_DIR` non défini). Le garde de l'orchestrateur a refusé un sous-shell et un `mkdir` hors temporaire ; contournés par des
commandes simples, sans forcer.

## 7. Verdict, selon la règle écrite avant

1. Livré : **égalité**, 28/28, suite verte.
2. Coût : C ≈ 1,34 $ (≤ 2,28 $), sous les 3,23 $ d'Opus seul à livraison égale → **le circuit a une raison d'être économique** ; il a maigri
   (≈ 6 fois moins que B).
3. Aucune question, aucune intervention : course valide ; réserve de procédure, la relance a été cadrée par une session mère Opus (sans aide
   sur la tâche) et le cadrage s'est fait la veille.
4. Hypothèses : H1 confirmée (28/28) ; H2 confirmée (moins qu'A, moins du tiers de B) ; H3 confirmée (36 min : plus qu'A, bien moins que B) ;
   **H4 infirmée** (aucune délégation à un autre agent : sur une fiche, le choix d'exécutant ne sert pas).

Ce que ça dit : sur une tâche moyenne bien écrite, avec interface imposée, le circuit Sonnet livre autant qu'Opus seul pour ~2,4 fois moins
cher et ~1,5 fois plus long. Ce que ça ne dit pas : la qualité hors tests reste en dessous d'Opus seul (1 contre 3 à la relecture aveugle),
avec un vrai trou (dossier parent lié) que personne n'a testé ; le régime maigre prévu le 24 au soir mettait une relecture Opus d'une passe
justement là. Une course, une tâche, la même qu'E12.

## 8. Menaces à la validité (en plus du §6 du préenregistrement)

- C a été relu seul, A et B ensemble en E12 ; la consigne du relecteur est réécrite (l'originale n'a pas été gardée) : comparaison des notes fragile.
- Cadrage fait la veille, course jouée après une attente et une relance ; le temps de cadrage est mesuré (2 min 24 s) mais séparé.
- Coût de l'orchestrateur calculé par différence entre deux rapports ; écritures de cache supposées à 1 h.
- Le rapport du bras C est daté du 25 alors que la course s'est jouée le 24 au soir en UTC.
