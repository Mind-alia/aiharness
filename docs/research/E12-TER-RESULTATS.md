# E12 ter — Résultats : le circuit Sonnet avec une passe de relecture Opus (T62)

Écrit par la session mère Opus 5.5 le 25 octobre 2026, depuis les journaux, les transcriptions et les mesures, après la relecture aveugle
Codex et avant toute lecture du code (sauf les vérifications dites au §3). Préenregistrement : `E12-TER-PREENREGISTREMENT.md` (`2608e33`) ;
bras A, B : `E12-RESULTATS.md` ; bras C : `E12-BIS-RESULTATS.md`. Une course. Pièces : `Workspace-IA/etudes/E12/bras-D/`, `etudes/E12/temoin/`,
`etudes/E12/blind-review-codex-d.md`.

## 1. Ce qui a tourné

| | Bras D — circuit Sonnet + relecture Opus |
|---|---|
| Base | `6237e66` + plan et consigne (`1ee2c0c`), branche d'étude `e12-bras-d` |
| Qui | orchestrateur Sonnet 5.5 (session ouverte depuis le kit, verrou `usine-e12-circuit-d`) ; Bric (Sonnet 5.5) en conteneur ; relecteur Opus 5.5, sous-agent `Explore` en lecture seule |
| Chronologie (UTC) | claim 00:01:39 → `prepared` 00:05:52 (fiche, 8 tests cachés, plan-check, selfcheck) → délégation 00:06:01 → acceptance 00:10:11 → relecture 00:28:31-00:32:47 → fusion `936d010` 00:51:03 → release 00:51:57 |
| Durée | **50 min 18 s** ; cadrage 4 min 13 s, Bric 4 min 10 s, relecture 4 min 16 s |
| Relecture | une passe, verdict **`laisse passer`**, cinq défauts mineurs ; donc aucune reprise : le code fusionné est la tentative 1 |
| Questions, interventions | 0 et 0 |

Deux écarts de procédure, notés par l'orchestrateur lui-même. Le circuit ne commite pas le travail de l'exécutant avant `merge`, donc
`git diff base...branche` était vide : il a ajouté au texte fixe du relecteur une précision de lieu (`git -C <clone> diff HEAD`). Les
18 minutes entre l'acceptance et la relecture sont passées à chercher comment exporter ce diff sous la liste blanche ; elles laissent un
worktree `/private/tmp/e12-bras-d-diff1` et une branche `e12-bras-d-diff1` que le garde l'a empêché de retirer.

## 2. Livré (mesure principale)

| Mesure | A | B | C | **D** |
|---|---|---|---|---|
| Tests cachés de Fable (28, hôte) | 28/28 | 28/28 | 28/28 | **28/28** (`bras-D/hidden.log`, clone de `fa88364`) |
| Suite complète rejouée dans un clone | 1 910, `errors=1` | 1 915, `errors=1` | 1 905, `errors=1` | **1 919, `errors=1`** (la même erreur connue), 2 sauts, 775 s (`bras-D/full-verif.log`) |
| `docs-check.sh` | OK | OK | OK | OK (dit par le bras) |
| Tests ajoutés | 20 | 25 | 15 | 29 |

Règle 1 : **égalité sur « livré »**.

## 3. Solidité : témoins, relecture Opus, relecture aveugle

**Tests témoins** (écrits avant la course, dossier parent lié vers l'extérieur) : A 2/2, B 2/2, C 1/2, **D 2/2**.

**Relecture Opus** (avis tel quel dans le rapport du bras). Les cinq points, vérifiés dans `template/scripts/lib/archive.py` et
`orchestrator.py` du bras D :

| Point d'Opus | Vérifié |
|---|---|
| `_make_dir` refait `chmod 0700` sur des dossiers qui existent déjà à la restauration | vrai, mineur |
| `_copy_entry` : `listdir` par chemin après `lstat`, un dossier remplacé par un lien pendant la copie serait suivi | vrai, seulement par une course |
| `create` : rotation et nettoyage hors du délai ; une rotation qui échoue après le `rename` écrit `archive_failed` alors que la sauvegarde existe | vrai, mineur |
| `release` imprime `archive : <nom>` en cas de succès | vrai ; la demande admet « la nouvelle ligne en plus », B faisait de même |
| `.workspace` lui-même lié : toute la sauvegarde échoue, signalée | vrai, mineur |

Cinq sur cinq vrais, aucun faux, aucun bloquant : le verdict `laisse passer` est juste. Coût de la passe : ≈ 1,09 $.

**Relecture aveugle Codex** (même consigne, même dossier anonymisé que C ; 144 831 tokens d'entrée, 565 de sortie) :

| Critère (0–2) | A | B | C | **D** |
|---|---|---|---|---|
| Régression | 0 | 0 | 0 | **2** |
| Sécurité | 1 | 0 | 0 | **0** |
| Lisibilité | 2 | 1 | 1 | **1** |
| **Somme** | 3 | 1 | 1 | **3** |

Le zéro de sécurité de D : entre le contrôle et l'écriture, `_write_one` ouvre des chemins (`os.mkdir`, `os.open`) qu'un lien posé entre-temps
ferait sortir de `.workspace/`. Vrai, mais seulement par une course pendant la restauration, de la même famille que le deuxième point
d'Opus. Le défaut majeur nommé par Codex (rotation et nettoyage hors délai) est le troisième point d'Opus.

## 4. Coût et durée

| | A | B | C | **D** |
|---|---|---|---|---|
| Durée | 24 min | 77 min | 36 min | **50 min** |
| Coût liste estimé, Claude | 3,23 $ | ≈ 7,9 $ | ≈ 1,34 $ | **≈ 4,28 $** |

Détail D, fenêtre claim → release (`report.sh --usine`, Opus séparé par la transcription du sous-agent) :

| Rôle | Cache lu | Cache créé | Sortie | Coût |
|---|---|---|---|---|
| Orchestrateur Sonnet (55 messages) | 7,79 M | 82 k | 47,0 k | 2,36 $ |
| Relecteur Opus (10 messages) | 504 k | 81 k | 17,1 k | 1,09 $ |
| Bric Sonnet | 757 k | 73 k | 32,6 k | 0,77 $ |
| Plan-check Sonnet | 0,6 k | 13 k | 1,2 k | 0,06 $ |

Hypothèse de prix d'E12 (écritures de cache à 1 h) ; à 5 min, ≈ 3,79 $. Dans les deux cas, plus qu'Opus seul. La plus grosse part est
l'orchestrateur : 55 tours sur un contexte de ~140 k, dont l'export du diff (18 min) ; la passe Opus seule dépasse le dollar prévu.

## 5. Forme (même script `ast`)

| `archive.py` | A | B | C | **D** |
|---|---|---|---|---|
| Lignes | 472 | 451 | 301 | 479 |
| Fonctions / complexité max | 27 / 17 | 28 / 12 | 17 / 16 | 25 / 19 (`read_manifest`) |
| Fonctions > 10 | 3 | 2 | 2 | 3 |
| Nombres nus (code / tests) | 7 / 18 | 5 / 25 | 6 / 5 | 9 / 25 |

## 6. D'où vient la solidité de D : un biais de la consigne

La fiche écrite par l'orchestrateur D dit : « Un lien symbolique est ignoré à tous les niveaux : `events.jsonl`, `acceptance`, `run`,
`run/delegate`, ou un élément en dessous », et « la copie tourne dans un processus enfant que le parent tue au-delà du délai (une simple
vérification entre deux fichiers ne suffit pas) ». La fiche de C ne disait ni l'un ni l'autre. La transcription de l'orchestrateur ne montre
aucune lecture interdite (ni résultats, ni pièces de C, ni `etudes/`). La source probable est la consigne : le texte fixe du relecteur, que
l'orchestrateur lit avant de cadrer, demandait de chercher les « liens symboliques à tous les niveaux » et un « délai réellement borné ».
Ces deux mots, écrits par la session mère après avoir vu les défauts de C, ont orienté le cadrage. Le préenregistrement avait prévu que
l'écart puisse venir de la variance de Sonnet ; il n'avait pas prévu cette fuite. Elle est dite ici telle quelle.

## 7. Verdict, selon la règle écrite avant

1. Livré : **égalité**, 28/28.
2. Solidité : D passe les 2 témoins et obtient 3 points à l'aveugle → **plus solide que C** à la lettre. Mais la relecture n'a rien changé au
   code (tentative 1 fusionnée), donc **l'écart ne vient pas de la relecture** ; il vient très probablement de la fiche, orientée par la consigne (§6).
3. Coût : ≈ 4,28 $ > 3,23 $ → **Opus seul reste préférable** à ce régime, sur cette tâche.
4. Aucune question : course valide ; deux réserves de procédure (précision de lieu ajoutée au texte du relecteur, consigne qui oriente).
5. Hypothèses : H1 confirmée ; **H2 infirmée** (4,28 $, passe Opus 1,09 $) ; H3 confirmée (cinq défauts réels, tous mineurs) ; H4 confirmée
   à la lettre (2/2, 3 points) mais pas par l'effet de la relecture ; H5 confirmée (50 min).

Ce que ça dit : une passe Opus relit juste (cinq points vrais sur cinq, aucun faux, verdict juste), mais elle coûte environ un dollar et,
sur un diff déjà bon, elle ne change rien. Le circuit n'a pas de commande pour exporter le diff d'une tâche avant la fusion : l'orchestrateur
y a perdu 18 minutes et une part de son coût. Ce que la course suggère, sans le prouver : nommer les pièges dans la fiche (« liens à tous les
niveaux », « délai par processus enfant ») a donné à Sonnet seul un code aussi solide qu'Opus seul, pour un coût de cadrage quasi nul. C'est à
tester proprement : C refait avec ces deux phrases dans la consigne et sans relecteur, contre C tel quel.

## 8. Menaces à la validité (en plus du §6 du préenregistrement)

- La consigne a orienté le cadrage (§6) : la comparaison D contre C mesure à la fois la relecture et une fiche plus précise.
- Une course ; un `laisse passer` ne dit rien de la reprise après un `bloque`, jamais jouée.
- Le texte du relecteur a été complété par l'orchestrateur (lieu du diff) ; l'avis reste en lecture seule.
- Coût de l'orchestrateur gonflé par l'export du diff, un défaut du circuit et non du régime.
- Codex a relu C et D seuls, avec une consigne réécrite ; A et B l'avaient été ensemble.
