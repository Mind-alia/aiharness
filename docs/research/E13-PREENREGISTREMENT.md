# E13 — Préenregistrement : Opus seul contre « Sonnet écrit, Opus dicte » et « Sonnet écrit, Opus corrige », tâche T89

Écrit par Fable le 25 octobre 2026, avant toute course, à la demande du propriétaire (« tester Opus seul sur une tâche moyenne contre un petit
modèle qui écrit tout le code, corrigé ensuite par Opus qui lui dit seulement les modifications à faire ; et un autre bras où Opus relit et
corrige lui-même le code, pour voir le coût en tokens »). Soumis au propriétaire avant la course ; figé par son commit ; tout changement après la
première course est un amendement daté. Il suit E12, E12 bis et E12 ter (`E12-*.md`), sur une tâche nouvelle.

## 1. Question

Sur une tâche moyenne nouvelle, est-ce que faire écrire tout le code par Sonnet puis le faire relire par Opus — soit en lecture seule,
Opus dictant des demandes de changement que Sonnet applique (bras B), soit Opus corrigeant lui-même le code (bras C) — livre aussi bien
qu'Opus seul (bras A), et pour quel coût ? Toutes les consignes, échanges et rapports entre agents sont en anglais, dans les trois bras.

## 2. Hypothèses, écrites avant

- H1 : les trois bras passent les 9 tests cachés de Fable (E12 : 28/28 partout, la fiche ne discriminait pas ; T89 vise plus de pièges).
- H2 : le coût liste du bras B reste sous celui du bras A ; les deux passes Opus en lecture seule coûtent ensemble moins de 2 $.
- H3 : le bras C coûte moins que le bras B (une passe Opus au lieu de deux, pas de passe d'application Sonnet) mais plus de la moitié d'Opus seul.
- H4 : la première relecture Opus (B) nomme au moins un défaut réel du diff de Sonnet (vérifié dans le code par Fable après la course) ;
  si elle rend `pass` d'emblée, le bras B équivaut à Sonnet seul et la passe Opus était le prix de le savoir.
- H5 : Sonnet applique toutes les demandes de changement (seconde passe : chaque `Cn` marqué `done`, aucune régression `R`).
- H6 : durées : A sous 45 min ; B et C sous 90 min chacun.

## 3. Tâche : T89, export du diff d'une tâche avant fusion

Demande `docs/research/E13-request-diff-export.md`, en anglais, interface imposée, donnée mot pour mot aux trois bras. Besoin réel : le
circuit n'a aucune commande pour voir le travail de l'exécutant avant `merge` (E12 ter : 18 minutes perdues, worktree et branche
orphelins). Classe moyenne : une sous-commande dans `mission.py`, un module nouveau, le vocabulaire des événements figé par un test, le
miroir, la liste blanche de l'orchestrateur, la documentation. Pièges voulus : fichiers non suivis et binaires reproduits à l'octet, clone
jamais modifié (index compris), lien symbolique refusé sans fuite, `--out` borné à `.workspace/` après résolution des liens, écriture atomique.

## 4. Bras, définitions exactes

| Bras | Qui | Comment |
|---|---|---|
| A — Opus seul | une session `claude -p` Opus 5.5, `Agent` interdit, droits complets dans un clone jetable sans historique | consigne `etudes/E13/prompts/solo.md` (anglais), demande lue dans le clone, suite complète avant de dire fini, commits sur `e13-arm-a` |
| B — Sonnet écrit, Opus dicte, Sonnet applique | 1) `claude -p` Sonnet 5.5, mêmes droits et même consigne que A (`writer.md`, branche `e13-arm-b`) ; 2) `claude -p` Opus 5.5 **en lecture seule** (`--permission-mode dontAsk`, outils admis : lecture, `git diff|status|show|log|ls-files|rev-parse`, `cat`, `ls`, `sed -n`, `wc` ; `Edit`, `Write`, `rm`, `git add|commit|stash|checkout|reset` refusés), consigne `reviewer.md` : verdict `pass|block` et au plus huit demandes de changement `Cn` (fichier, fonction, défaut, phrase de la demande, principe du changement, jamais de code) ; 3) si `block` : Sonnet reprend **sa propre session** (`--resume`) avec les demandes telles quelles (`applier.md`), applique, relance la suite, commite ; 4) seconde et dernière passe Opus en lecture seule (`reviewer-final.md`) : `done|not done` par demande, régressions `R`, verdict. Arrêt là quel que soit le verdict. Si `pass` dès la première passe : arrêt après 2) | empreinte du clone (HEAD, index, statut, condensés de tous les fichiers) prise avant et après chaque passe Opus : une différence disqualifie la passe |
| C — Sonnet écrit, Opus corrige | 1) Sonnet comme en B (`writer-c.md`, branche `e13-arm-c`) ; 2) `claude -p` Opus 5.5 avec droits complets, consigne `fixer.md` : relit le diff, corrige lui-même ce qui enfreint la demande ou casse la suite, garde la conception de l'auteur, commite. Une passe | le diff de Sonnet avant correction est conservé (`diff-before-fix.patch`) pour mesurer ce qu'Opus a changé |

Commun aux trois : base = `main` du kit au lancement (préenregistrement compris ; les tests cachés sont hors du kit), clone
`git clone --depth 1` sans `.workspace/`, hôte macOS sans conteneur ni crochet d'exécutant (comme E12 bras A : « 100 % seul », dit),
`--dangerously-skip-permissions` pour les rôles qui écrivent, aucune question possible (personne ne répond ; une question notée vaut
décision prise seul), jamais de push. Ordre : A, B, C, l'un après l'autre (quota), par `sh etudes/E13/run-arm.sh a|b|c`. Une course par bras.

## 5. Tests cachés de Fable

`~/Workspace-IA/etudes/E13/acceptance/test_e13_diff.py` : **9 tests** en trois groupes (usage et refus ;
export : vide, patch rejoué sur la base reproduit le clone à l'octet, clone intact, événement et second export identique ; sûreté : lien
symbolique refusé sans fuite, `--out` hors de `.workspace/` refusé y compris par un lien, `--out` dedans écrit une fois en `0600`). Boîte noire
par `bash scripts/task-workspace.sh diff`, projet temporaire construit depuis `template/scripts/` du dépôt candidat (`E13_REPO`), hôte
`/usr/bin/python3` 3.9. Rejoués le 25/10 : **base `51f8cf7` : 9 rouges** ; **référence de Fable `d7a3700`** (clone jetable, patch
`etudes/E13/reference.patch`, jamais montré aux bras) : **9 verts** au premier passage. La référence fait tomber `tests/test_events.py`
(vocabulaire figé) : piège connu, laissé, comme en E12. La référence n'a pas été jugée sur la suite complète : elle prouve que les tests sont
satisfaisables, elle ne concourt pas.

## 6. Mesures, par bras

- **Livré** (principale) : 9 tests cachés sur le commit final du clone (`hidden.log`) ; suite complète rejouée par Fable dans le clone ;
  `docs-check`.
- **Relecture** (B) : demandes de changement, chacune vérifiée vraie ou fausse par Fable dans le code ; verdicts des deux passes ; empreintes
  avant/après. (C) : différence entre le diff de Sonnet et le diff final, défauts qu'Opus dit avoir corrigés, vérifiés de même.
- **Coût et durée** : sortie JSON de chaque `claude -p` (`total_cost_usd`, tokens entrée, sortie, cache lu, cache créé, tours, durée API),
  agrégée dans `measures.json` par bras ; durée murale début → fin de chaque étape (`*.start`, `*.end`). Prix liste de l'outil, même grille
  pour les trois bras.
- **Qualité aveugle** : relecture Codex `gpt-6.1-sol` des trois diffs finals anonymisés, ordre tiré au sort, même grille qu'E12 (régression,
  sécurité, lisibilité, 0 à 2 chacune), si le quota le permet.
- **Forme** : même script `ast` qu'E12 bis (lignes, fonctions, complexité max, nombres nus).
- **Langue** : part des sorties de raisonnement et des messages en anglais, relevée dans les transcriptions, à titre indicatif.

## 7. Règle de décision, écrite avant

1. Livré : un bras qui ne passe pas les 9 tests a perdu sur « livré » ; égalité si tous passent.
2. À égalité, la qualité aveugle départage ; un écart d'un point est « non tranché ».
3. Coût rapporté à part, il ne fait pas gagner. Régime proposé : si B ou C livre et vaut A en qualité pour un coût inférieur, il devient le
   régime proposé pour les tâches moyennes bien écrites ; si la première relecture rend `pass` (B) ou qu'Opus ne change rien (C), la passe
   Opus a coûté sans apporter et Sonnet seul reste le régime économique ; si A seul passe les 9 tests, Opus seul reste le choix de qualité.
4. Une intervention humaine ou une question répondue exclut la course ; une passe Opus qui modifie le clone (B) exclut le bras B.

## 8. Menaces à la validité

Une course par bras, une tâche. Fable a écrit la demande, les tests cachés et la référence : les tests mesurent la conformité à cette
demande, pas la conception. La consigne du relecteur nomme les zones à regarder (liens, clone intact, `.workspace/`…) : elles viennent de la
demande elle-même, lue par tous les bras, mais elles orientent la relecture (E12 ter §6 l'avait montré). Le bras B reprend la session de
Sonnet (`--resume`) : moins cher qu'une session neuve, et l'auteur connaît son code ; c'est un choix, dit. Les documents du kit lus par les
agents (`AGENTS.md`, `ORCHESTRATEUR.md`) restent en français : même handicap pour les trois bras, mais les coûts ne sont pas comparables
à ceux d'E12 (consignes françaises) sans correction. Hôte sans conteneur : plus de liberté que dans le circuit, voulu. Relecteur et auteur
de la même famille (Claude) ; la relecture aveugle est Codex. Variance de Sonnet d'une course à l'autre : B et C partent chacun de leur
propre diff Sonnet, qui peut différer ; mesuré, pas contrôlé.

## 9. Budget attendu et lancement

Prix liste estimés d'après E12 : A ≈ 3 à 4 $ (E12 : 3,23 $) ; B ≈ 3 à 4 $ (Sonnet 1 à 1,5 $, deux passes Opus 1,5 à 2,5 $, application
Sonnet ≈ 0,5 $) ; C ≈ 2,5 à 4 $ ; total ≈ 9 à 12 $, 2 à 3 heures en tout. Essai à blanc du 25/10 avec Haiku : les droits en lecture seule
tiennent (`Write` refusé), les champs de coût sont présents dans le JSON (0,008 $).

```
sh ~/Workspace-IA/etudes/E13/run-arm.sh a
sh ~/Workspace-IA/etudes/E13/run-arm.sh b
sh ~/Workspace-IA/etudes/E13/run-arm.sh c
```

## 10. Livrables

`docs/research/E13-RESULTATS.md` écrit depuis les journaux et les mesures, sans lire les diffs avant la relecture aveugle ; entrée
`docs/LOG.md` ; les trois clones et leurs pièces gardés sous `etudes/E13/arm-*/` ; rien fusionné dans `main` avant lecture par le propriétaire.
Si T89 est retenu, la meilleure réalisation est fusionnée par le circuit, pas par l'étude.

## Signature

Préenregistré par Fable le 25 octobre 2026. À lancer seulement sur l'accord du propriétaire (demande, bras, budget).
