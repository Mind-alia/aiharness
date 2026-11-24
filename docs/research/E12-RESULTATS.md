# E12 — Résultats : Opus seul contre le circuit, une tâche moyenne (T62)
<!-- report-check:debut -->
> **Vérification externe** (2026-10-24 UTC, codex) : 2/20 affirmations vraies, 0 fausses, 18 non vérifiables ; témoins 3/3 ; coût : extraction 52865 tokens, vérification 172755 tokens.
<!-- report-check:fin -->

Écrit par Fable le 24 octobre 2026 au soir, depuis les journaux et les mesures, après la relecture aveugle de Codex et avant toute lecture
des diffs par Fable (sauf les deux vérifications ponctuelles dites au §5). Préenregistrement : `E12-PREENREGISTREMENT.md` ; demande commune :
`E12-demande-sauvegarde.md`. Une course par bras. Pièces hors du kit : `~/Workspace-IA/etudes/E12/`
(`bras-A/`, `bras-B/`, tests cachés, patch de référence, relecture Codex, clé de l'anonymat).

## 1. Ce qui a tourné

| | Bras A — Opus seul | Bras B — circuit |
|---|---|---|
| Base | `main` à `518df97`, 28 tests cachés rouges (14 échecs, 14 erreurs) | même base, rejouée rouge avant la course (`bras-B/hidden-base-518df97.log`) |
| Qui | une session `claude -p` Opus 5.5, `Agent` interdit, clone jetable sans historique | orchestrateur Opus 5.5 (session Claude Code, verrou `usine-e12-circuit`), fiche `e12-archive` à Sonnet 5.5 en conteneur, 24 tests cachés de l'orchestrateur, plan-check Sonnet, relecture Sonnet des tests cachés, suite complète hôte, fusion dans la branche d'étude `e12-bras-b` |
| Début → fin | 14:27:06 → 14:51:34 UTC | claim 15:20:34 → release 16:37:47 UTC |
| Livraison | commit `45b3c2f` sur `e12-bras-a` (clone `etudes/E12/bras-A/kit`) | fusion `63b7419`, miroir `e00989a`, rapport `1abada4` sur `e12-bras-b` du kit ; rien dans `main` |
| Interventions humaines | 0 | 0 |
| Questions | 0 (décisions listées dans son message final) | 1, de l'orchestrateur à Fable, sur la **procédure** du plan (voir §6) ; 0 de l'exécutant |

## 2. Livré (mesure principale)

| Mesure | Bras A | Bras B |
|---|---|---|
| Tests cachés de Fable (28, hôte, `/usr/bin/python3` 3.9) | **28/28** (`bras-A/hidden.log`) | **28/28** (`bras-B/hidden.log`) |
| Suite complète du kit, rejouée par Fable dans un clone | 1 910 tests, `errors=1` (seule l'erreur connue T49), 2 sauts (`bras-A/full-verif.log`) | 1 915 tests, `errors=1` (même erreur connue), 2 sauts (`bras-B/full-verif.log`) |
| Suite complète, dite par le bras lui-même | 1 910 tests, `errors=1` | 1 915 tests, `errors=1` (rapport de l'orchestrateur) |
| `docs-check.sh` | OK | OK |
| Tests ajoutés (`def test_`) | 20 | 25 |
| Fichiers touchés | 14 | 15 (en plus : `tests/test_orchestrator_run.py`) |
| Lignes de `archive.py` | 472 | 451 |

Règle 1 du préenregistrement : les deux bras passent tout → **égalité sur « livré »**.

## 3. Qualité aveugle (Codex `gpt-6.1-sol`, lecture seule, ordre tiré B puis A, `LOG.md` retiré des deux diffs)

Relecture `etudes/E12/blind-review-codex.md` (clé : `blind-review-key.txt`). 180 936 tokens d'entrée (147 456 en cache), 1 247 de sortie.

| Critère (0–2) | Bras A (diff-2) | Bras B (diff-1) |
|---|---|---|
| Régression | 0 | 0 |
| Sécurité | 1 | 0 |
| Lisibilité | 2 | 1 |
| **Somme** | **3** | **1** |

Défaut majeur relevé : A, `_install` crée les dossiers parents manquants avant `check_no_symlinks` (un parent lié pourrait être traversé avant le
refus) ; B, le délai `ARCHIVE_TIMEOUT_SECONDS` est vérifié entre deux fichiers, pas pendant la copie d'un fichier (une lecture bloquée retient
le verrou). Règle 2 : écart de 2 points → la relecture aveugle **donne l'avantage au bras A**.

Réserves de Fable sur cette relecture (vérifiées après coup, §5) :
- Les deux « régression : 0 » reposent sur la même affirmation : l'événement `archived` casserait `kinds[-1] == 'orchestrator_released'` dans
  `tests/test_orchestrator_run.py`. **Elle est fausse** : ce test ne garde que les événements qui commencent par `orchestrator_`, et les deux
  suites complètes sont vertes. Sans ces deux zéros, l'écart reste de 2 points (sécurité et lisibilité).
- « Les deux omettent l'entrée `docs/LOG.md` » : c'est Fable qui l'a retirée des diffs pour l'anonymat ; les deux bras l'ont écrite.
- Le délai de B : exact à la lettre (contrôle entre fichiers) ; A borne la copie par un processus enfant (60 s) et B par une horloge entre
  fichiers (120 s). Le cas « lecture bloquée sur un fichier » n'est couvert que par A.

## 4. Coût (il ne fait pas gagner : il dit le prix)

| | Bras A | Bras B |
|---|---|---|
| Durée | 24 min 28 s | 77 min 13 s (× 3,2) |
| Sortie Opus | 59 436 (dont 29 015 de réflexion) | 71 049 (orchestrateur) |
| Cache lu Opus | 4 887 358 | 18 139 631 |
| Cache créé Opus | 133 457 | 202 505 |
| Sonnet (exécutant + plan-check + relecture + appels directs) | 0 | sortie 40 021 ; cache lu 890 834 ; cache créé 153 292 |
| Total sortie, tous modèles | 59 436 | 111 070 (× 1,9) |
| Total cache lu | 4 887 358 | 19 030 465 (× 3,9) |
| Codex (hors Claude) | 0 | `report-check` du rapport de l'usine : 45 925 + 370 472 tokens |
| Coût liste estimé, Claude seulement | 3,23 $ (valeur SDK) | ≈ 7,9 $ (6,7 $ orchestrateur + 1,2 $ Sonnet) |

Hypothèse de prix : Opus 5.5 4 $/20 $ par Mtok, lecture de cache 0,20 $, écriture de cache 1 h à 8 $ (la valeur SDK du bras A, 3,23 $, se
retrouve exactement avec cette hypothèse) ; Sonnet 5.5 2 $/10 $, lecture 0,20 $, écriture 1 h 4 $. Si les écritures de cache du circuit sont à
5 min (1,25 ×), le bras B coûte ≈ 6,6 $. Dans les deux cas : **le circuit coûte 2 à 2,5 fois plus et dure 3 fois plus**, dans la fourchette
annoncée par H2 (1,5 à 3).

Où passe le temps du bras B (journal des événements) : claim 15:20 → `prepared` 15:52 (32 min : lecture, fiche, 24 tests cachés, relecture
Sonnet, plan-check, selfcheck, et l'attente de la réponse de Fable) ; délégation 15:57 → `acceptance` 16:02 (**4 min 28 s de Sonnet**) ;
vérification hôte et fusion 16:02 → 16:31 (29 min, dont la suite complète 1 108 s) ; rapport, `report-check`, LOG, release 16:31 → 16:37.
L'exécutant a produit le code en moins de cinq minutes ; les 72 autres minutes sont du cadrage et du contrôle.

## 5. Forme (mesures par programme, `ast`, sans radon)

| | Bras A | Bras B |
|---|---|---|
| `archive.py` : fonctions / complexité max | 27 / 17 (`_checked_path`), trois fonctions > 10 | 28 / 12 (`_plan`), deux fonctions > 10 |
| Nombres nus dans `archive.py` (hors 0 et 1, hors constantes de module) | 7 (des `2`, `3` d'indexation) | 5 (idem) |
| Nombres nus dans `test_archive.py` | 18 | 25 |
| Documentation | `ORCHESTRATEUR.md` §3 point 7, `LOG.md` | idem |

Vérifications ponctuelles de Fable après la relecture aveugle (seules lectures du code) : l'assertion `kinds[-1]` filtre bien `orchestrator_*`
dans les deux dépôts ; `_install` du bras A fait ses `mkdir` avant `check_no_symlinks` ; `_check_deadline` du bras B est appelé entre fichiers
et non dans `_copy_stream`.

## 6. Décisions prises sans le propriétaire, et la question du bras B

- **Bras A** (message final) : copie dans un processus enfant borné à 60 s, dossier `.partial-*` renommé à la fin ; `release` sans verrou ne
  sauvegarde rien ; aucune ligne de plus sur un `release` réussi ; session réduite à `[A-Za-z0-9._-]` ; suffixes triés en nombres ; `restore`
  n'écrit que sous les trois chemins copiés ; `events.py`, `test_events.py`, `tests/run.sh` touchés hors liste ; a pris le verrou du clone pour
  faire passer le `rsync` du miroir malgré le crochet (contournement **dit**, pas caché).
- **Bras B** (rapport de l'orchestrateur) : `release` réussi imprime aussi `archive : <chemin>` (interface élargie) ; session hors alphabet
  remplacée par `_`, `inconnue` si absente ; délai 120 s ; `events.py`, `test_events.py`, `tests/run.sh`, `test_orchestrator_run.py` hors
  liste ; banc d'attaque réel non relancé (comme A) ; tests cachés non rejoués sur l'hôte (joués par le circuit dans le conteneur).
- **La question du bras B.** Le plan de Fable exigeait que les tests cachés soient joués verts sur un brouillon dans un clone jetable ; la
  liste blanche de l'orchestrateur refuse `git clone` et `python3` hors du dépôt. L'orchestrateur a inscrit la question dans `QUESTIONS.md`,
  n'a pas contourné le crochet, et Fable a répondu « option 1 » (rouge par `--selfcheck` + relecture Sonnet, règle du plan `restes`). Rien
  sur la tâche n'a transité. **À la lettre de la règle 4 du préenregistrement, une course avec question répondue est exclue.** Fable propose
  un amendement : l'exclusion vise les questions qui portent sur la tâche ; celle-ci portait sur une consigne de procédure infaisable, écrite par
  Fable. Le propriétaire tranche : course valide avec cette réserve, ou rejeu du bras B avec un plan corrigé.

## 7. Verdict, selon la règle écrite avant

1. Livré : **égalité**, 28/28 des deux côtés, suites vertes.
2. Qualité aveugle : **A devant de 2 points** (3 contre 1), avec la réserve que deux des six notes reposent sur une affirmation fausse du
   relecteur ; les notes de sécurité et de lisibilité, elles, tiennent à la lecture.
3. Coût : **B coûte 2 à 2,5 fois plus et dure 3 fois plus.**
4. Hypothèses : H1 confirmée (égalité sur livré) ; H2 confirmée (facteur dans la fourchette) ; **H3 infirmée** sur cette course (le circuit
   n'a pas été jugé meilleur hors tests ; le relecteur l'a jugé moins bon) ; H4 non tranchée (les deux ont pris des décisions hors demande, B
   a même élargi l'interface d'une ligne).

Ce que ça dit, et ce que ça ne dit pas : une course par bras, une tâche bien spécifiée avec interface imposée. Sur une telle tâche, un Opus
seul fait aussi bien, plus vite et moins cher. Le circuit a apporté ce qu'il promet (verrou, conteneur, fiche, 24 tests cachés, relecture,
rapport vérifié, sauvegarde réelle créée au `release`), mais sur cette tâche ces garanties n'ont pas changé le résultat. Ce qu'on ne sait
pas encore : ce qui se passe sur une demande floue, sur une tâche qui touche les chemins protégés, ou sur une série de tâches où les tests
cachés de l'orchestrateur attrapent une régression. C'est la question de la deuxième manche (T56, casseur outillé), et du pilote « 3 tâches ».

## 8. Menaces à la validité (en plus du §8 du préenregistrement)

- Plan B infaisable tel qu'écrit (brouillon à jouer sous liste blanche) : coût en temps et une question répondue par Fable.
- Tokens de l'orchestrateur relevés par Fable depuis la transcription (`bras-B/orchestrator-tokens.json`), pas par `report.sh --usine`, qui
  n'a rien compté : la session tournait depuis `Workspace-IA`, pas depuis le kit (défaut à corriger : T88).
- Le bras A a pris le verrou de son clone pour contourner le crochet du miroir ; dans le kit réel, ce geste serait refusé.
- Le relecteur aveugle s'est trompé sur la régression ; une seule relecture, une seule famille.
- Les deux bras ont eu le même document de demande, écrit par Fable, qui a aussi écrit les tests cachés : un biais de conception commun.

## Amendement du 25 octobre 2026 : le bras A était-il vraiment seul ?

Vérifié par Fable dans les transcriptions du clone (`~/.claude/projects/-Users-<user>-Desktop-Workspace-IA-etudes-E12-bras-A-kit/`).
La session principale (`cfcffb18`, 95 messages, modèle `claude-opus-5-5` seul) n'a appelé ni `Agent`, ni `claude`, ni `codex`, ni `gemini` :
38 commandes Bash, 2 Write, 1 Edit, 1 Read, 2 Grep ; tout le code, les tests et la documentation viennent d'elle.
Trois sessions Opus courtes ont tourné en plus, de 14:50:35 à 14:51:19 UTC, pendant le `git commit` final : « Review this change for security
vulnerabilities » (deux fois) puis « You previously flagged these candidate vulnerabilities » (vérification). Elles ne sont pas lancées par le
modèle : c'est le greffon `security-guidance` des réglages personnels du propriétaire, qui relit chaque commit. Coût : 3 839 tokens de sortie, 81 522
lus en cache, 79 944 créés (≈ 0,45 à 0,75 $ selon l'hypothèse d'écriture de cache). Aucun effet sur le code : le commit `45b3c2f` était fait,
aucune modification ne suit, le message final part quinze secondes après. Le même greffon s'applique à tous les commits des bras B, C et D.
Conclusion : Opus a travaillé seul ; la mesure « Opus seul » inclut une relecture automatique de sécurité que personne n'a demandée et qui n'a
rien changé. Elle est à retirer du coût d'A ou à ajouter à ceux de B, C et D selon ce qu'on compare ; dans les deux cas l'écart entre bras ne change pas.
