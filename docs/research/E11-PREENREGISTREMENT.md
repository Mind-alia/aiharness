# E11 — Préenregistrement : même fiche, sept bras

Écrit par Fable le 21 octobre 2026, avant toute course. Figé au commit qui le contient ; toute modification après la première course est
interdite et serait un défaut de l'étude. Signature du propriétaire attendue en bas, avant le lancement.

## 1. Question
À demande identique et tests cachés identiques, un petit modèle (Haiku, Sonnet, Luna) enfermé dans le circuit livre-t-il un code vérifié aussi
souvent qu'Opus seul, et à quel coût ? Et le circuit (périmètre, porte, casseur, une ronde de correction) change-t-il le résultat d'un même
modèle ? La question posée aux agents est **petite** à dessein : trois corrections réelles de deux à quatre fichiers, dix minutes chacune,
dont le verdict est un test qui passe ou non. Le résultat ne vaut pas pour les grandes missions (E3).

## 2. Hypothèses, écrites avant
- H1 : Haiku dans le circuit réussit les tests cachés au moins aussi souvent qu'Opus seul, sur les trois fiches.
- H2 : Haiku dans le circuit réussit plus souvent que Haiku seul ; idem pour Sonnet.
- H3 : le coût en tokens d'Haiku dans le circuit est inférieur au cinquième de celui d'Opus seul, casseur compris.
- H4 : le casseur trouve au moins une faille latente sur les bras « seul » que les tests cachés n'avaient pas vue.
Chaque hypothèse peut être fausse ; le rapport dira laquelle l'est.

## 3. Bras (sept), définitions exactes
| Bras | Entrée | Environnement | Vérification pendant | Sortie |
|---|---|---|---|---|
| A Opus seul | la demande en clair (texte de la section 5), pas la fiche | clone du dépôt au commit de base, dans le conteneur du profil python | aucune : ni porte, ni casseur, ni retour des tests | un diff |
| B Sonnet seul | idem | idem | aucune | un diff |
| C Haiku seul | idem | idem | aucune | un diff |
| D Sonnet dans le circuit | la fiche (périmètre `allowed`, `verify`, deux tentatives) | même conteneur | porte, casseur, une ronde de correction | un diff fusionnable |
| E Haiku dans le circuit | idem | idem | idem | idem |
| F Luna seul (Codex `gpt-6-luna`, ajouté à la demande du propriétaire le 21 octobre) | la demande en clair | idem | aucune | un diff |
| G Luna dans le circuit | la fiche | idem | porte, casseur, une ronde | un diff fusionnable |
Même `max_turns` pour les sept (valeur fixée dans la fiche, identique à celle utilisée par Pôle 10). Le conteneur est une sécurité commune,
pas une pièce du circuit. Aucun bras ne voit les tests cachés. Les bras « seul » n'ont pas de seconde tentative : un diff, un verdict. Un bras
« seul » peut lancer lui-même les tests du dépôt s'il y pense ; ce qui lui manque est la porte, le casseur, le verdict des tests cachés et la
ronde de correction. Les bras F et G sont **exploratoires** : les tokens Codex sont « non mesurés » tant que T64 n'est pas faite ; seuls
« livré / non livré », tentatives et durée sont comparés.

## 4. Fiches (trois), rejouées sur leur commit de base
| Fiche | Base | Taille | Pourquoi |
|---|---|---|---|
| `instruments-defauts` (T47, T48) | commit parent de `31abf69` | 2 fichiers, 2 corrections d'une ligne | la plus petite |
| `breaker-leftovers` | commit parent de `f37096f` | 2 fichiers | petite, logique de filtrage |
| `mesure-humain` | commit parent de `eb20f8e` | 4 fichiers, une option, un événement, deux rapports | petite à moyenne |
Les tests cachés sont ceux écrits par Pôle 9 et Pôle 10, inchangés, vérifiés rouges sur la base avant chaque course (`--selfcheck`).
La solution fusionnée existe dans `main` : chaque course part d'un clone au commit de base, sans `.git` des commits postérieurs (clone peu
profond ou `git checkout --orphan`), pour qu'aucun bras ne puisse la lire.

## 5. Demande en clair (bras A, B, C, F)
Texte identique pour les quatre, par fiche : **le corps de la fiche tel quel** (Besoin, Interface imposée, Règles numérotées, Cas limites,
Critères de conformité), **sans l'en-tête YAML** (donc sans `allowed`, `verify`, `attempts`, `acceptance`). La spécification est donc la même
pour tous ; ce qui change entre « seul » et « circuit » n'est pas l'information reçue mais ce qui est imposé et vérifié autour. Le texte
envoyé est copié tel quel dans le rapport. Il est plus précis qu'une demande client réelle (menace déclarée, section 11) ; c'est voulu :
l'étude mesure « qui livre à spécification égale », pas « qui devine la spécification ».

Exemple, la plus petite (`instruments-defauts`, T47 et T48), en résumé : une fiche générée porte `attempts: 3` au lieu de 2 ; et le relecteur
de rapport rend « réussi » même quand aucune affirmation du rapport n'est vraie. Règles : `attempts: 2` partout ; refus si zéro vraie, seuil
dans une constante `MIN_TRUE = 1` ; motif écrit sur la sortie d'erreur et dans le bloc du rapport ; un rapport avec une vraie, zéro fausse et
trois témoins attrapés passe comme avant.

### Qui a écrit les tests cachés, et ce qu'ils vérifient
Les tests cachés ont été écrits par les orchestrateurs de Pôle 9 et Pôle 10 (Opus et Sonnet, famille Claude) **avant** l'exécution de ces
fiches en octobre, à partir de la fiche et non d'une solution ; ils sont dans `.workspace/acceptance/<fiche>/tests/`, jamais dans le dépôt
vu par l'agent, et sont vérifiés rouges sur le commit de base avant chaque course. Ils sont réutilisés sans une ligne changée. Un test trouvé
par le casseur lors de la première exécution de `mesure-humain` (minutes de dix mille chiffres) en fait partie et compte pour tous les bras.
Extrait de ce qu'ils vérifient (le reste est dans les fichiers) :
- `instruments-defauts` : une fiche générée par `prepare-task.sh --from-demande` contient `attempts: 2` ; un rapport avec 0 vraie, 5 non
  vérifiables, témoins 3/3 rend le code 1, écrit « aucune affirmation vraie » sur la sortie d'erreur **et** dans le rapport ; avec 1 vraie et
  4 non vérifiables il rend 0 sans ce motif ; une fausse rend toujours 1.
- `breaker-leftovers` : avec `--max-findings 1`, un vrai test et trois fichiers compilés (`__pycache__/x.pyc`, `stray.pyc`, `helper.pyo`),
  seul le vrai test est retenu et aucun nom compilé n'apparaît ; une fermeture de tâche qui échoue une fois est rattrapée dans l'ordre exact
  `close`, `sandbox stop`, `close`, verdict normal et erreur vide ; une fermeture qui échoue toujours donne deux fermetures en tout, verdict
  `erreur`, le mot `close` dans l'erreur, et les deux autres étalons jugés normalement ; une fermeture réussie n'est pas relancée.
- `mesure-humain` : deux notes de 10 et 15 minutes sur `t1` donnent la ligne exacte `Interventions humaines : 2 interventions, 25 min`, une
  seule note donne `1 intervention, 7 min`, aucune donne `0 intervention, 0 min` ; `--minutes 0`, `abc`, `1441`, absent, libellé vide et
  identifiant `../t1` rendent le code 64 et **n'écrivent rien** dans le journal ; la grille de mission affiche
  `3 questions d'agent ; 2 interventions, 25 min`.
Ces tests n'ont pas été écrits pour E11 ; les seuls qui les connaissent sont les orchestrateurs passés, Fable et le propriétaire.

## 6. Plan des courses
Trois fiches × sept bras × deux répétitions = 42 délégations (6 Opus, 12 Sonnet, 12 Haiku sur le quota Claude ; 12 Luna sur le quota Codex).
Ordre tiré au sort une fois (graine écrite dans le rapport) et joué dans cet ordre ; au plus trois courses en parallèle, jamais deux bras de la
même fiche en même temps sur la même image.
Aucune course n'est rejouée : une panne de fournisseur ou un dépassement de `max_turns` est un résultat (« non livré, cause »).

## 7. Mesures, par course
Livré (tests cachés verts, oui/non) ; tentatives (1 ou 2 pour D et E ; 1 pour A, B, C) ; tokens par type (entrée, cache lu, cache écrit,
sortie) depuis le compte rendu JSON de la délégation ; durée ; failles du casseur (pour D et E pendant ; pour A, B, C **après coup, en
instrument**, sans retour à l'agent) ; lecture avant d'agir (`reading_measured`) ; violations de périmètre ou de secret ; « non mesuré » pour
toute valeur absente, jamais zéro. Tout sort de `campaign.py export` (courses.csv), rien n'est recopié à la main.

## 8. Seuils de décision, écrits avant
- Haiku « équivalent à Opus » si son nombre de courses livrées (bras E) est supérieur ou égal à celui d'Opus (bras A) sur les six courses ;
  « inférieur » sinon, avec l'écart. Même règle pour Sonnet (D contre A) et Luna (G contre A).
- Le circuit « utile » pour un modèle si D livre plus que B, E plus que C, ou G plus que F ; « sans effet » si égalité ; « nuisible » si moins.
- Le coût se lit en tokens par course livrée ; en euros seulement si `docs/TARIFS-API.json` existe au moment du rapport.
- Avec deux répétitions, un écart d'une seule course n'est pas une différence : il est écrit « non tranché, à répéter ».

## 9. Exclusions et arrêt
Exclue toute course où le conteneur n'a pas démarré (cause infrastructure), remplacée par une course identique une seule fois, notée.
Arrêt de l'étude si le quota Claude hebdomadaire dépasse 60 % avant la fin : le rapport publie les courses faites et dit « incomplet ».

## 10. Rôles
- Préenregistrement : Fable. Signature : le propriétaire.
- Exécution : un orchestrateur Sonnet (Pôle 11), par `campaign.py`, qui ne lit aucun diff et ne corrige rien.
- Verdicts : les tests cachés, par script. Casseur : Codex `gpt-6.1-sol` (autre famille que les bras Claude ; même famille que F et G,
  menace déclarée).
- Rapport : écrit depuis `courses.csv` par l'orchestrateur, passé par `report-check.sh` (Codex) avec témoins ; Fable ne touche pas au rapport
  avant la relecture ; le propriétaire contrôle dix affirmations tirées au sort.
- Fable pendant les courses : rien.

## 11. Menaces à la validité, connues d'avance
Fiches et tests cachés écrits par le système lui-même, famille Claude (biais en faveur du circuit et des bras Claude, déclaré) ; fiches
petites (le résultat ne vaut pas pour les grandes) ; deux répétitions seulement ; tokens Codex non mesurés (T64), donc F et G hors
comparaison de coût ; casseur de la même famille que F et G ; l'orchestrateur Sonnet a un coût propre non imputé aux bras ; la demande
« en clair » est le corps de la fiche, donc plus précise qu'une demande client réelle.

## 12. Livrables
`.workspace/campaign-e11/courses.csv` et `.json` ; `docs/research/E11-RESULTATS.md` (tableau, hypothèses tranchées une par une, menaces,
décisions commandées : modèle par défaut des exécutants, point de départ de la cascade T03/T65) ; entrée `docs/LOG.md`.

## Signature
Le propriétaire : ……………………… (date, « lu et accepté » ou modifications demandées avant lancement)

## Amendements du 21 octobre 2026 (écrits avant toute course, validés par le propriétaire en session)

Le propriétaire a lu les deux réserves de Fable et les a acceptées par message le 21 octobre au soir (« je suis d'accord sur les réserves […] je te
fais confiance ») ; la ligne de signature ci-dessus reste à remplir de sa main. Rien d'autre que ces quatre points ne change.

1. **Fiches.** `mesure-lecture` (commit parent de `cb254bb`, cinq fichiers, Sonnet après deux échecs Codex) remplace `breaker-leftovers`.
   Raison : les trois fiches initiales avaient toutes été livrées par Haiku ; la sélection favorisait H1. Les trois fiches sont donc
   `instruments-defauts`, `mesure-humain`, `mesure-lecture`. La fiche `mesure-lecture` rejouée est celle qui a été précisée après les
   échecs Codex, pas la première version : la difficulté mesurée est celle de la fiche précisée (menace déclarée).
2. **Hypothèse H5** : sur la fiche la plus dure (`mesure-lecture`), Haiku dans le circuit livre moins souvent qu'Opus seul.
3. **Outil.** `tools/campaign/campaign.py` ne connaît que `opus-seul`, `codex-seul` et `systeme-<agent>` (ce dernier refait la fiche par
   un planificateur Opus). Les bras de cette étude (modèle seul autre qu'Opus, et « circuit » sur une fiche déjà écrite) n'y existent pas.
   Ils seront ajoutés par une fiche du kit avant les 42 courses. En attendant, le palier 1 ci-dessous est joué par un pilote hors kit,
   conservé avec ses résultats.
4. **Palier 1, répétition générale (exploratoire, non comptée dans les 42 courses).** Six courses sur `instruments-defauts` seule :
   A Opus seul, D Sonnet dans le circuit, E Haiku dans le circuit, deux répétitions, ordre tiré au sort (graine 20261021), joué en
   séquence (une seule course à la fois : les réservations de périmètre du registre interdisent deux tâches sur les mêmes fichiers).
   Procédure par course : contrat écrit depuis la fiche d'origine (bras circuit : fiche identique sauf `id` et `agent` ; bras seul :
   corps de la fiche, en-tête réduit à `allowed` large, `verify: "true"`, `attempts: 1`, sans tests cachés), `task-workspace.sh prepare
   --base 9e52800 --selfcheck`, `delegate.sh`, casseur Codex `gpt-6.1-sol` (`--promote` et une ronde de correction pour le circuit ;
   sans `--promote`, en instrument seulement, pour le bras seul, à condition que la délégation ait abouti), puis **verdict unique pour
   tous les bras** : le test caché d'origine lancé sur l'hôte (`/usr/bin/python3` 3.9, `WORKSPACE_TEST_TMP` hors dépôt) dans le worktree
   final, en plus du verdict du circuit lu dans `status.json`. Le travail de chaque bras est commité sur sa branche `workspace-task/<id>`
   et la tâche close `abandoned` : rien n'est fusionné dans `main`. Tokens lus dans `status.json` (compte rendu JSON de `claude -p`),
   jamais estimés. Pilote et résultats : `.workspace/campaign-e11/palier1/` ; synthèse : `docs/research/E11-PALIER1-RESULTATS.md`.
   Exécution par Fable (dérogation à la section 10, déclarée) ; Fable ne lit aucun diff avant le verdict et ne corrige rien.
   Ce que le palier 1 peut dire : si le pilote et le circuit tiennent, et un premier chiffre par bras ; il ne tranche aucune hypothèse.

## Palier 2 (écrit le 21 octobre 2026 à 23 h 45, avant toute course ; demandé par le propriétaire après le palier 1)

Constat du palier 1 : `instruments-defauts` ne discrimine rien (6/6, zéro faille). Le propriétaire demande une fiche difficile mais courte,
douze tests cachés au moins, la même rigueur, et chaque modèle avec et sans circuit.

- **Fiche** : `reconstruct-events` (Pôle 12, 20 octobre), base `b2b171e` (parent de `c43c7e7`). Un module de 209 lignes à écrire
  depuis rien, treize règles numérotées, sept cas limites, une section de documentation. À l'origine, Codex `gpt-6-luna` y a échoué
  deux fois (acceptance) et `gpt-6.1-sol` a réussi au premier passage.
- **Tests cachés : 29.** Les 15 d'origine (Pôle 12, écrits avant l'exécution de la fiche) et 14 nouveaux écrits par Fable le 21 octobre
  depuis la fiche seule, sans lire la solution (`.workspace/campaign-e11/palier2/acceptance/tests/test_acceptance_reconstruct_events_e11.py`
  : fusion atteinte par le second parent, sujet sans identifiant, branche archivée `-tentativeN`, doublon de premier commit, synonymes
  d'en-tête, dernier mot entre accents graves, formes `2 min` / espace insécable / `2/3` / `1h`, tableau sans colonne tâche, tâche
  citée seulement par un rapport, un résumé par rapport, résumé git daté du dernier événement, rapport non commité ignoré, instants
  égaux, repli sur HEAD). Contrôle fait avant les courses, sur l'hôte, `/usr/bin/python3` 3.9 : les 29 échouent sur la base
  (outil absent), les 29 passent sur `main` (`94adebb`).
- **Bras (huit)** : Opus seul, Opus circuit, Sonnet seul, Sonnet circuit, Haiku seul, Haiku circuit, Luna seul, Luna circuit.
  Définitions des sections 3 et « Amendements » point 4 ; Luna = Codex `gpt-6-luna` (`CODEX_MODEL`) ; casseur Codex `gpt-6.1-sol`
  pour tous (même famille que Luna, menace déclarée). `max_turns: 40` (valeur de la fiche) pour les bras Claude ; Codex l'ignore.
- **Courses** : 8 bras × 2 répétitions = 16, ordre tiré au sort (graine 20261022), une à la fois. Verdict unique : les 29 tests sur
  l'hôte dans le worktree final, `WORKSPACE_TEST_TMP` hors dépôt (dossier de session de Fable) ; verdict du circuit lu en plus dans
  `status.json`. « Livré » = 29/29. Aucune course rejouée ; quota = « non livré, cause quota », reprise des courses manquantes après
  la remise à zéro, dans le même ordre.
- **Ce que le palier peut dire** : avec deux répétitions, un écart d'une course est « non tranché » ; un écart de deux courses sur
  deux (0/2 contre 2/2) est une différence observée, à confirmer au palier 3. Les tokens sont lus dans `status.json` ; l'identifiant
  exact du modèle Claude est lu dans la transcription Claude Code du worktree, celui de Codex dans `CODEX_MODEL`.
- **Exécution** : pilote `.workspace/campaign-e11/palier2/driver.py` (même procédure que le palier 1, généralisée), Fable, qui ne
  lit aucun diff avant les verdicts. Résultats `courses.jsonl` / `courses.csv`, synthèse `docs/research/E11-PALIER2-RESULTATS.md`,
  vérifiée par `report-check.sh --verifier codex`.
