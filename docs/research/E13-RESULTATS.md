# E13 — Résultats : Opus seul contre « Sonnet écrit, Opus dicte » et « Sonnet écrit, Opus corrige », sur T89

Écrit par Fable le 25 octobre 2026 au soir, depuis les journaux du lanceur, les sorties JSON, les transcriptions et les suites rejouées,
**avant la relecture aveugle Codex** (non faite ce soir) et avant toute lecture des diffs autre que les sondes dites au §4. Préenregistrement :
`E13-PREENREGISTREMENT.md` (`ab68418`). Pièces : `Workspace-IA/etudes/E13/` (clones `arm-*/kit`, `measures.json`, `hidden.log`,
`full-verif.log`, `notes.md` par bras, `notes-bilan.md`, `tokens-by-arm.txt`, `corner-probe.txt`). Une course par bras. Consignes, échanges et
rapports entre agents en anglais dans les trois bras, documents du kit en français : langue constante, pas variable.

## 1. Ce qui a tourné

Base `ab68418` (`main`, préenregistrement compris ; tests cachés hors du kit). Clones `git clone --depth 1` sans `.workspace/`, hôte macOS sans
conteneur, lanceur `etudes/E13/run-arm.sh` : **aucun tour d'orchestration par un modèle**, toute la mécanique (clone, branche, appels,
tests cachés, mesures) est faite par le script. Les trois bras ont tourné l'un après l'autre, A, B, C.

| | A — Opus seul | B — Sonnet écrit, Opus dicte (lecture seule), Sonnet applique, Opus confirme | C — Sonnet écrit, Opus relit et corrige |
|---|---|---|---|
| UTC | 16:59:24 → 17:23:07 | 17:23:53 → 18:39:50 | 19:14:28 → 19:55:43 |
| Durée | **23 min 43 s** | **1 h 15 min 57 s** | **41 min 15 s** |
| Appels `claude -p` | 1 | 4 (Sonnet 52 min, Opus 6 min `block`, Sonnet 17 min, Opus 51 s `pass`) | 2 (Sonnet 22 min, Opus 19 min) |
| Commits | `a4bb618` | `5e43cfd`, `a1d29ca`, `6842daf` | `a261213`, `2869a1e` |
| Questions, interventions | 0, 0 | 0, 0 | 0, 0 |
| Empreinte du clone autour des passes Opus en lecture seule | — | identique aux deux passes | — |

## 2. Livré (mesure principale)

| Mesure | A | B | C |
|---|---|---|---|
| Tests cachés de Fable (9, hôte, boîte noire sur `template/scripts/`) | **9/9** | **9/9** | **9/9** |
| Suite complète rejouée par Fable dans le clone (hôte, PATH sans node : 29 sauts) | 1 914, erreur connue seule | 1 950, erreur connue seule | 1 939, erreur connue **+ 1 échec** `test_self_hosting.test_mirror_is_identical_to_template` |
| `docs-check` | OK | OK | OK |
| Miroir `scripts/` | identique | identique | **désynchronisé** |

Règle 1 : **égalité sur « livré »** aux tests cachés, pour la troisième étude de suite. Mais C enfreint le point 10 de la demande (miroir
identique) et sa suite n'est pas verte : à la lettre du préenregistrement, A et B livrent, C livre avec un défaut.

**Pourquoi le miroir de C a manqué.** Le kit livre dans chaque clone son `.claude/settings.json`, dont le crochet `hooks_guard.py` refuse toute
écriture dans `scripts/` sauf la commande de miroir exacte, seule sur sa ligne. Sonnet (C) a toujours écrit `rsync … && diff -rq …` ou
`rsync … && git status` : refusé à chaque essai. Opus (C) a réessayé de même, puis un worktree détaché, refusé aussi ; il s'est arrêté et l'a
écrit dans `LOG.md`. En B, Sonnet a été refusé une fois puis a trouvé la forme exacte. En A, aucun `rsync` dans la transcription et un miroir
en ordre (copie par un autre moyen). Le crochet n'était pas prévu par le préenregistrement : c'est une contrainte réelle du kit, la même pour
les trois bras, mais elle a coûté à C seul. Menace à la validité notée au §7.

## 3. Coût et durée

| | A | B | C |
|---|---|---|---|
| Coût liste (`total_cost_usd` du JSON, somme des appels) | **2,97 $** | **7,76 $** (Sonnet 2,45 + 2,92 ; Opus 1,95 + 0,44) | **3,50 $** (Sonnet 1,59 ; Opus 1,91) |
| Rapport à A | 1 | 2,6 | 1,18 |
| Durée murale | 24 min | 76 min (× 3,2) | 41 min (× 1,7) |

Tokens des sessions principales, lus dans les transcriptions (`tokens-by-arm.txt`) :

| Rôle | Messages | Sortie | Cache lu | Cache créé |
|---|---|---|---|---|
| A Opus | 91 | 140 k | 9,68 M | 292 k |
| B Sonnet (écriture + application, une session reprise) | 176 | 240 k | 24,7 M | 527 k |
| B Opus relecteur, passe 1 / passe 2 | 51 / 14 | 68 k / 7 k | 3,83 M / 0,63 M | 290 k / 89 k |
| C Sonnet | 72 | 132 k | 7,94 M | 328 k |
| C Opus correcteur | 58 | 79 k | 4,65 M | 305 k |

Lecture : la relecture d'Opus en lecture seule (B, passe 1 : 1,95 $) coûte les deux tiers d'Opus écrivant tout (2,97 $) ; corriger (C :
1,91 $) coûte autant que dicter. Relire un code qu'on n'a pas écrit, c'est le lire en entier. Sonnet varie du simple au double d'une course à
l'autre (52 min et 2,45 $ en B, 22 min et 1,59 $ en C, même consigne, même demande).

Sessions annexes : chaque dossier de clone contient aussi des petites sessions Opus (A : 3, B : 7, C : 2 ; 4 à 14 messages, 2 à 10 k de sortie
chacune, 329 k de cache lu en A, 1,65 M en B, 216 k en C), hors `total_cost_usd` : vraisemblablement les relectures de sécurité automatiques
du greffon, comme l'amendement d'E12 l'avait relevé. Elles ne sont pas dans le coût ci-dessus ; dites ici telles quelles.

## 4. Solidité : ce qu'Opus a trouvé, et la sonde

**B, relecture 1 (`block`, deux demandes, aucun code).** C1 : le contexte du patch dépendait de la configuration git du clone ; avec
`diff.context=0` le patch ne se réapplique plus. C2 : avec `core.splitIndex=true`, l'index temporaire écrivait des `sharedindex.*` dans
`.git/` du clone. Sonnet a appliqué les deux dans sa session reprise ; la passe 2 les vérifie `done`, aucune régression. **C, correction
Opus :** fins de ligne converties par `core.autocrlf=true` global (corrigé), `.git/info/attributes` réécrivant les octets (corrigé par un
refus du clone), miroir (non corrigé, crochet).

**Sonde de Fable** (`corner-probe.txt`, les trois configurations posées dans le clone fabriqué des tests cachés) :

| Configuration du clone | A | B | C |
|---|---|---|---|
| `diff.context=0` | refuse le clone (`configuration git du clone refusée`) | exporte, le patch se réapplique | refuse le clone |
| `core.splitIndex=true` | refuse, rien dans `.git/` | exporte, rien dans `.git/` | refuse, rien dans `.git/` |
| `core.autocrlf=true` | refuse | exporte, CR conservés | refuse |

Les défauts nommés par Opus relecteur étaient **réels dans le premier diff de Sonnet** (B). Opus auteur (A) les évite sans les avoir cherchés,
par une règle plus large : refuser tout clone dont la configuration git sort de l'ordinaire, en réutilisant la vérification de `merge`. Sûr
par refus plutôt que par traitement ; aucun des trois ne fuit ni ne modifie le clone sur ces cas. Les 9 tests cachés ne provoquent aucun des
trois : ils n'ont pas discriminé.

## 5. Forme (`ast`, `task_diff.py` et tests)

| | A | B | C |
|---|---|---|---|
| Lignes de `task_diff.py` | 423 | 455 | 353 |
| Fonctions / complexité max | 22 / 15 | 23 / 12 | 16 / 14 |
| Fonctions > 10 | 1 | 3 | 2 |
| Nombres nus | 15 | 14 | 11 |
| Tests `def test_` ajoutés | 23 | 59 | 48 |
| Diff total | 13 fichiers, +1 234 | 13 fichiers, +1 683 | 9 fichiers, +932 (miroir absent) |

Le nombre de tests ajoutés est ce que chaque bras a **écrit**, pas ce qui l'a mesuré : les trois bras ont été mesurés par les mêmes 9 tests
cachés et la même suite. Plus de tests écrits ne veut pas dire plus sûr : les trois passent les mêmes tests cachés, et c'est la sonde du §4,
pas leurs tests, qui montre les écarts.

## 6. Verdict, selon la règle écrite avant

1. Livré : égalité 9/9 ; C enfreint le point 10 (miroir) et sa suite est rouge : **A et B livrent, C livre avec un défaut**.
2. Qualité aveugle : **non faite** ce soir (Codex) ; le départage reste ouvert. La sonde du §4 est un substitut partiel : les trois sont sûrs
   sur les trois cas de coin, B par traitement, A et C par refus.
3. Coût : A 2,97 $, C 3,50 $, B 7,76 $. Règle 3 : **aucun des deux régimes ne livre aussi bien qu'A pour moins cher**. B est 2,6 fois plus
   cher pour le même résultat aux tests ; C est 18 % plus cher et livre un défaut. Opus seul reste le choix de qualité **et** de prix sur cette
   tâche. La passe Opus n'a pas « coûté pour rien » (H4 confirmée : défauts réels), mais elle a coûté autant que de tout écrire.
4. Aucune question, aucune intervention, empreintes identiques : les trois courses sont valides.
5. Hypothèses : H1 confirmée (9/9 partout) ; H2 infirmée (B 7,76 $ > 2,97 $ ; passes Opus 2,39 $ > 2 $) ; H3 confirmée (C 3,50 $ < B, et
   > 1,49 $) ; H4 confirmée ; H5 confirmée (`done`, `done`, aucune régression) ; H6 confirmée (24, 76, 41 min).

## 7. Ce qui est intéressant, au-delà de la règle

- **Pour la troisième étude, les tests cachés ne séparent pas les régimes.** Sur une demande à interface imposée, Sonnet seul, Sonnet relu,
  Opus seul passent tous. Ce que la demande nomme est livré par tous ; ce qu'elle ne nomme pas (configuration git du clone, miroir) fait
  la différence, et seul un instrument qui le cherche le voit (sonde, relecture, suite complète). Conclusion pour l'instrument : écrire les
  tests cachés sur ce que la demande ne dit pas, pas seulement sur ce qu'elle impose.
- **Accompagné ne veut pas dire moins d'erreurs.** Opus seul a fait zéro erreur visible ; Sonnet accompagné d'Opus en a fait une (miroir, C)
  et a eu besoin de deux corrections (B). Ce qui a rendu B aussi sûr qu'A, c'est la relecture, au prix d'Opus écrivant tout.
- **La relecture ciblée vaut, la relecture systématique ne paie pas.** Deux défauts vrais trouvés en six minutes (B), mais pour 1,95 $, parce
  que relire c'est tout lire. Cohérent avec la vérification sélective (veille du 25/10) : relire quand la classe ou le périmètre le justifie.
- **E13 est le premier banc sans orchestrateur modèle.** Le lanceur a fait la mécanique pour zéro token. Comparé au circuit d'E12 B (7,9 $, dont
  6,7 $ d'orchestration), le régime C fait le même travail pour 3,50 $, et le régime « Sonnet seul » le ferait pour ≈ 1,6 $ : la décision du 25/10
  (T14, orchestration par programme) est cohérente avec la mesure. Mais le gain par rapport à Opus seul, lui, n'est pas là sur cette tâche.
- **Le crochet du kit a piégé deux modèles sur une commande de deux lignes.** Un programme fait le miroir sans jamais se tromper de forme :
  argument de plus pour T14, et correction à faire dans `PIEGES.md` ou dans la consigne (la forme exacte, seule).
- **La variance de Sonnet** (× 2 en temps et en coût entre B et C) est du même ordre que l'écart entre régimes : une course par bras ne tranche
  pas finement.

## 8. Menaces à la validité

Une course par bras, une tâche ; les tests cachés, la demande et la référence sont de Fable ; les consignes du relecteur et du correcteur
nomment les zones à regarder, tirées de la demande que tous ont lue. Le crochet du kit a pesé sur C seul par hasard de forme. Hôte sans
conteneur. La relecture aveugle Codex manque. Les sessions annexes du greffon ne sont pas chiffrées en dollars. Première étude en anglais :
coûts non comparables à E12 sans correction de langue (le bras A d'E13, 2,97 $, et le bras A d'E12, 3,23 $, portent sur deux tâches).

## 9. Suite proposée

E13 bis (préenregistrée dans la veille du 25/10) : la fiche à Opus, le code à Sonnet, sans orchestrateur, même T89, mêmes 9 tests ; bras D :
Opus seul avec le seul paragraphe « Besoin », mesuré par relecture aveugle et décisions non demandées ; relecture aveugle Codex des diffs
A, B, C, D ensemble. Puis T14. Si T89 est retenu, fusion par le circuit, pas par l'étude ; A ou B sont candidats, C non sans le miroir.

## 10. Bras D et D′ : le programme d'orchestration sur T89 (27/10)

Ajouté le 27 octobre 2026 par Opus (orchestrateur), à la demande du propriétaire. Le « bras D » d'ici n'est pas celui du §9 : c'est le circuit
piloté par le programme de T14 (`tools/orchestrator/run.py`, aucun modèle d'orchestration). Protocole écrit avant le lancement et journal
complet des erreurs : `Workspace-IA/etudes/E13/arm-d/PROTOCOLE.md` et `NOTES.md` ; pièces dans le même dossier (`run1/`, `run2/`, `dp/`,
`correction/`, `probe-d.txt`, `form-d.txt`). Une course par bras.

### 10.1 Ce qui a tourné

| | D — programme, Sonnet écrit, relecture Opus ciblée, correction Haiku | D′ — programme, Sonnet écrit, sans relecture |
|---|---|---|
| Fiche | `e13-diff-d` : la demande d'E13 mot pour mot, `plan-check --with-model` « bon » en trois passes | identique à D à l'identifiant près |
| Tests cachés | les 9 de Fable (seule la ligne `REPO` change, pour tourner en conteneur) | mêmes |
| Base, langage, lieu | `main` du 27/10 (`ce05b0a`), Python 3.14, conteneur `workspace-python-git:3.14` | `0b14927` (seul écart : §10.4), même conteneur |
| Exécutant | `claude-sonnet` ; correction `claude-haiku` (correcteur par défaut du programme) | `claude-sonnet` |
| Vérification | suite rapide rejouée par le programme sur l'hôte ; suite complète rejouée après coup | même |
| Fusion | aucune : arrêt à « prêt à fusionner » | aucune |

D a été joué en morceaux, par la faute de l'environnement et du circuit, pas du protocole : premier passage arrêté par la disparition de
l'image du conteneur (`docker image prune -a` lancé par une autre session), deuxième coupé par une pause demandée (coupure d'internet),
troisième complet jusqu'à la relecture ; la relecture a raté (§10.4) et a été rejouée après correction du programme ; la ronde de
correction a été rejouée à la main dans le circuit, hors du programme, parce que le clone avait été rendu (§10.4). D′ a tourné d'un trait.

### 10.2 Livré

| Mesure | D, après Sonnet | D, après correction Haiku | D′ |
|---|---|---|---|
| Tests cachés (9, hôte) | **9/9** | **9/9** | **9/9** |
| Suite rapide sur l'hôte, par le programme | rouge au 1er essai (format ruff), verte au 2e | **rouge** : `test_ruff_lint_is_clean`, C901 dans `_load_state` (11 > 10) | **verte** du premier coup |
| Suite complète rejouée (hôte, `python3.14`) | 1 970 tests, OK | — | 1 971 tests, OK |
| Relecture Opus | « à corriger » : 2 failles de sécurité (§10.3) | « à corriger » : C901, ordre des contrôles | — |
| Issue selon les règles du programme | ronde de correction | **deuxième échec : tâche arrêtée, rien de livré** | **prête à fusionner** |

Le miroir `scripts/` est hors tâche dans le circuit (chemin protégé, recopié par le programme à la fusion) : pas de défaut de miroir possible ici.

### 10.3 Solidité : relecture et sonde

**Relecture Opus de D** (après correction de son plafond de tours) : deux failles vraies. `task_diff.py` lance `git read-tree`, `add` et
`diff` sur l'hôte dans le clone de l'exécutant sans `workdir.check_clone_config`, et construit sa commande git à la main, sans le durcissement
commun ; plus un dossier par défaut non contrôlé contre un lien symbolique. **Ni les 9 tests cachés, ni la suite rapide, ni la suite complète
(1 970 tests) ne les voient.**

**Sonde** (`probe-d.txt`, script `probe_d.py`) : les trois configurations de Fable (§4) plus les deux attaques nommées par Opus, dans le
clone fabriqué des tests cachés, sur les cinq bras ; D avant correction et D′ reconstruits depuis leurs patchs.

| Configuration du clone | A | B | C | D avant | D corrigé | D′ |
|---|---|---|---|---|---|---|
| `core.autocrlf=true` | refuse | exporte | refuse | exporte | refuse | exporte |
| `diff.context=0` | refuse | exporte | refuse | exporte | refuse | exporte |
| `core.splitIndex=true` | refuse | exporte, `.git` intact | refuse | exporte, **écrit dans `.git`** | refuse | exporte, **écrit dans `.git`** |
| `core.fsmonitor=<commande>` | refuse | exporte, rien exécuté | refuse | **commande exécutée sur l'hôte** | refuse | exporte, rien exécuté |
| `filter.<x>.clean=<commande>` + `.gitattributes` | refuse | exporte, rien exécuté | refuse | **commande exécutée sur l'hôte** | refuse | refuse |

Lecture : la sonde confirme par un essai les deux failles nommées par Opus dans D (un clone piégé fait exécuter un programme par
l'orchestrateur). Après la ronde Haiku, D refuse tout, comme A et C. D′, **sans relecture**, arrête les deux attaques : il a écrit son
propre contrôle (liste de préfixes interdits) au lieu de réutiliser celui du circuit (liste de clés admises), et il écrit dans `.git` du
clone sous `core.splitIndex` (le défaut C2 de B). Même fiche, même modèle : un tirage livre une faille, l'autre non. Une lecture du code de
D′ faite avant la sonde avait conclu à tort que la faille y restait (`NOTES.md`) : la sonde, pas la lecture, fait la preuve.

### 10.4 Coût et durée

`total_cost_usd` des réponses JSON (exécutants : `events` de la délégation ; diagnostic et relecture : réponses gardées par le programme).

| Étape | D | D′ |
|---|---|---|
| Sonnet, 1re tentative | 0,58 $ (3 min 27 s, machine libre) | 0,74 $ (5 min) |
| Diagnostic Sonnet de l'échec de format | 0,13 $ | — |
| Sonnet, 2e tentative complète (pour deux mises en forme) | 0,52 $ | — |
| Relecture Opus, plafond de 3 tours : `error_max_turns`, aucun verdict | 0,51 $ | — |
| Relecture Opus rejouée, plafond de 12 tours | 0,68 $ (6 tours, 130 s) | — |
| Ronde de correction Haiku (six corrections appliquées) | **0,014 $** (61 s) | — |
| Relecture Opus de la correction | 0,56 $ (10 tours, 83 s) | — |
| **Chemin du programme corrigé** (jusqu'au deuxième échec) | **≈ 1,92 $**, ≈ 29 min, rien de livré | **0,74 $, 10 min 48 s, livré** |
| Dépensé en tout aujourd'hui | 2,99 $ + premier passage 0,85 $ (environnement) + tentative coupée (inconnu) | 0,74 $ |

Non comptés, pour D et D′ comme pour A, B, C : le `plan-check` (trois passes de Sonnet, non chiffrées en dollars) et l'écriture de la fiche
(D et D′ partent d'une fiche, A, B, C de la demande brute). Écart de lieu : conteneur et Python 3.14 ici, hôte et Python 3.9 le 25/10.

### 10.5 Forme (`form-d.txt`, `form_d.py` ; complexité de ruff, C901)

| | A | B | C | D avant | D corrigé | D′ |
|---|---|---|---|---|---|---|
| Lignes de `task_diff.py` | 423 | 455 | 353 | 218 | 226 | 296 |
| Fonctions / complexité max (ruff) | 22 / 10 | 23 / 10 | 16 / 11 | 11 / 10 | 11 / **11** | 16 / 7 |
| Nombres nus | 15 | 14 | 11 | 6 | 6 | 8 |
| Tests `def test_` ajoutés | 23 | 59 | 48 | 23 | 25 | 24 |
| Diff total (hors miroir pour D, D′) | 13 fichiers, +1 234 | 13, +1 683 | 9, +932 | 9, +521 | 9, +547 | 9, +606 |

Les lignes, fonctions et nombres nus retrouvent ceux du §5 ; la complexité du §5 venait d'une autre mesure, celle-ci est la règle du kit.

### 10.6 Verdict, selon la règle 3 (« livrer aussi bien qu'A pour moins cher »)

1. **D ne livre pas.** Le circuit a trouvé et fait corriger les failles, mais la correction a introduit un dépassement de complexité que
   personne n'a vu avant l'hôte (ruff absent du conteneur) : deuxième échec, arrêt. Au chemin du programme, ≈ 1,92 $ pour rien de fusionnable.
2. **D′ livre, presque aussi bien qu'A, pour un quart du prix et la moitié du temps** : 9/9, suites vertes, sûr sur les deux attaques ; mais
   il écrit dans `.git` du clone sous `core.splitIndex` (A refuse) et double le contrôle du circuit au lieu de le réutiliser.
3. **Donc pas encore** : une course par bras, la variance de Sonnet (une faille dans un tirage, aucune dans l'autre ; ruff rouge sur l'hôte
   dans trois livraisons sur cinq) est aussi grande que l'écart cherché, et la préparation de la fiche n'est pas comptée.

### 10.7 Ce qui est intéressant

- **La relecture Opus est le seul contrôle du circuit qui a vu une faille réelle**, confirmée par un essai. Elle coûte 0,56 à 0,68 $, autant
  que tout le travail de Sonnet ; à garder pour les fiches qui touchent à la sécurité (`relecture: opus`), comme le §7 le disait déjà.
- **Corriger ne coûte presque rien** : Haiku applique six corrections précises en 61 s pour 0,014 $. Ce qui coûte, c'est trouver.
- **Les échecs de D viennent du circuit, pas des modèles** : plafond de 3 tours qui empêche Opus de conclure, garde-fou des secrets qui
  refuse les corrections d'Opus parce qu'elles citent une ligne `secret = …` (la ronde de correction du programme aurait échoué là),
  ruff absent du conteneur (trois livraisons sur cinq rouges sur l'hôte, format ou complexité), deuxième tentative complète pour deux
  mises en forme. Chacun est dans `docs/TRAVAUX.md` (T98 à T111).
- **Les tests cachés ne discriminent toujours pas** (quatrième fois) ; la sonde, si. Proposition : mettre les cinq cas de la sonde dans les
  tests cachés de la prochaine étude sur T89.

### 10.8 Suite proposée

Corriger d'abord T98 (qualité dans la boucle de l'exécutant), T100 (corrections de relecture passées au masquage) et T99 (format corrigé par
programme) ; puis rejouer D et D′ trois fois chacun, sonde dans les tests cachés, coût de la fiche compté, avant de conclure sur la règle 3.
