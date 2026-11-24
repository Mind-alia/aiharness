# E12 ter — Préenregistrement : le circuit Sonnet avec une passe de relecture Opus, sur la même tâche

Écrit par la session mère Opus 5.5 le 25 octobre 2026, à la demande du propriétaire (« prépare l'essai circuit Sonnet + une passe de relecture
Opus du diff, préenregistrement d'abord, à me soumettre avant de lancer »), après les résultats d'E12 bis (`E12-BIS-RESULTATS.md`).
Soumis au propriétaire avant la course ; figé par son commit, fait au lancement ; tout changement après la course est un amendement daté.

## 1. Question

Une seule passe de relecture du diff par Opus, qui peut bloquer la fusion, rend-elle le circuit Sonnet (bras C : 28/28, ≈ 1,34 $, 36 min,
relecture aveugle 1 point sur 6) aussi solide qu'Opus seul (bras A : 28/28, 3,23 $, 24 min, 3 points), sans dépasser son coût ?

## 2. Hypothèses, écrites avant

- H1 : le bras D passe les 28 tests cachés de Fable.
- H2 : le coût Claude du bras D reste sous celui d'Opus seul (3,23 $) ; la passe Opus coûte moins de 1 $.
- H3 : la relecture Opus nomme au moins un défaut réel du premier diff (vérifié dans le code par la session mère après la course).
- H4 : le diff fusionné passe les 2 tests témoins (§4) et obtient au moins 2 points à la relecture aveugle Codex (C : 1/2 et 1 point).
- H5 : la durée reste sous 60 minutes, reprise comprise.

## 3. Bras D, définition exacte

| Qui | Comment |
|---|---|
| Orchestrateur **Sonnet 5.5**, session Claude Code ouverte depuis le dossier du kit, verrou `usine-e12-circuit-d` | consigne courte `docs/journal/2026-10-25/CONSIGNE-usine-e12-circuit-d.md`, même circuit que C, **cadrage compris dans la course** : fiche `e12-archive-d`, 6 à 8 tests cachés, `plan-check --with-model` une fois, `check`, `prepare --selfcheck`, délégation, `fast`, relecture, `full`, fusion, miroir, rapport sans `report-check`, `release` |
| Exécutant **Bric** (Sonnet 5.5), en conteneur | inchangé ; l'orchestrateur peut confier un morceau à un autre membre de `team.json`, jamais à `claude-opus` |
| **Relecteur Opus 5.5**, une passe | sous-agent de l'orchestrateur (outil `Agent`, type `Explore` : lecture seule, `model: opus`), encadré par `note.sh start "relecture opus" --model claude-opus-5-5 --kind check` / `end` ; il reçoit la demande, la fiche et le diff de la tâche (`git diff` base → branche de la tâche), rien d'autre ; consigne fixe (§3 de la consigne) ; verdict `laisse passer` ou `bloque`, au plus cinq défauts, chacun avec fichier, fonction et la phrase de la demande qu'il enfreint |
| Après un `bloque` | la branche de la tentative 1 est gardée (`git branch e12-bras-d-t1 <branche de la tâche>`), tâche close `--abandoned`, l'avis est ajouté à la fiche sous `## Relecture`, `prepare --retry`, délégation, `fast`, **seconde passe** du même relecteur sur le nouveau diff ; un second `bloque` : arrêt sans fusion, clone gardé |
| Régime | orchestrateur seul : pas de question hors question bloquante (`docs/QUESTIONS.md`, attente bornée à 30 min, personne ne répond) |

Base : `6237e66` (la même que C, sans `E12-BIS-RESULTATS.md` ni l'entrée de `LOG.md` qui décrivent les défauts de C), branche d'étude
`e12-bras-d` créée au lancement, plus le commit de la consigne et du plan. Le préenregistrement reste dans `main` seulement. Une course.

## 4. Mesures

- **Livré** (principale) : 28 tests cachés de Fable sur le commit final de `e12-bras-d`, hôte `/usr/bin/python3`, dans un clone ; suite
  complète rejouée dans le clone ; `docs-check`.
- **Tests témoins** (secondaire, écrits le 25/10 avant cette course, `Workspace-IA/etudes/E12/temoin/test_e12_temoin.py`) : un dossier parent
  de ce qui est copié (`.workspace/run`, `.workspace/acceptance`) est un lien vers l'extérieur ; la demande (§2) dit que les liens ne sont
  jamais suivis. Rejoués avant la course : **A 2/2, B 2/2, C 1/2** (`.workspace/run` suivi), base rouge. Joués sur la tentative 1 de D
  (si elle existe) et sur le diff fusionné.
- **Relecture Opus** : verdicts, défauts nommés, chacun vérifié vrai ou faux par la session mère dans le code ; tokens de la passe.
- **Qualité aveugle** : Codex `gpt-6.1-sol`, le diff D seul, **même consigne et même dossier anonymisé que C** (`bras-C/blind-prompt.md`,
  arbre de la base sans historique, sans `docs/journal`, `docs/research/E12-*`, `docs/LOG.md`).
- **Coût et durée** : `report.sh --usine usine-e12-circuit-d` (orchestrateur, exécutant, sous-agents) ; durée claim → release ; prix
  d'E12 (Opus 4 $/20 $, cache lu 0,20 $, écrit 1 h 8 $ ; Sonnet 2 $/10 $, 0,20 $, 4 $). Forme : même script `ast` qu'E12 bis.

## 5. Règle de décision, écrite avant

1. Livré : 28/28, sinon D perd et on compte ce qui manque.
2. Apport de la relecture : D est **plus solide que C** si le diff fusionné passe les 2 témoins **et** obtient au moins 2 points à
   l'aveugle ; une seule des deux conditions : non tranché ; aucune : la relecture n'a rien apporté sur cette course. Si la tentative 1
   passait déjà les témoins, l'écart vient de la variance de Sonnet et non de la relecture : c'est écrit tel quel.
3. Coût : si D est plus solide que C et coûte ≤ 3,23 $, le « circuit Sonnet + relecture Opus » devient le régime proposé pour les tâches
   moyennes bien écrites ; s'il coûte plus, Opus seul reste préférable ; si la relecture n'apporte rien, le circuit Sonnet seul (C) reste le
   choix économique, et Opus seul le choix de qualité.
4. Question répondue ou intervention humaine : course exclue, sauf question de procédure (écrite en réserve).

## 6. Menaces à la validité

Une course, une tâche, la quatrième fois sur T62 ; les témoins sont écrits après avoir vu le défaut de C (ils mesurent ce défaut-là, pas la
solidité en général). Sonnet varie d'une course à l'autre : D peut éviter le défaut sans relecture, d'où la mesure sur la tentative 1. La
reprise repart de la base (`prepare --retry`), elle ne corrige pas le premier diff. `.workspace/` du kit est partagé entre branches : la fiche,
les tests cachés et les journaux de C y sont lisibles ; la consigne interdit de les ouvrir. `E12-RESULTATS.md` (A et B, avec leurs défauts)
est dans la base, comme pour C. Le relecteur est de la même famille que l'exécutant (Claude) ; la relecture aveugle est Codex.

## Signature

Préparé par la session mère Opus 5.5 le 25 octobre 2026 ; à lancer seulement sur l'accord du propriétaire.
