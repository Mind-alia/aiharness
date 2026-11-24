# Taux d'erreur du vérificateur

- Date : 2026-10-19
- Commit du kit : 6b08bef
- Tâche : acceptance-selfcheck, attack-bench-on-sandbox-change, attack-interruption, breaker-agent, breaker-vitest5, breaker-vitest5-parse, broker-replay, campaign-false-quota, campaign-quota-scope, campaign-worktrees, claim-durable-pid, claude-hooks-protected-paths, claude-usage-source, codex-prereqs, consolidate-safety, diff-header-fix, events-usine, gate-docs-kit, gemini-relay-sync, hooks-guard-session-match, integration-gate-node-image, lot2-state-fix, mission-git-worktree, note-subagent, orchestrator-launched-by, orphan-agents, post-merge-defects, prepare-task-orphans, questions-answer-cli, reserve-check, resume-cost-study, retry-abandoned, sandbox-init-and-gate-seal, session-lock, split-mission, t1-quota-c3, tap-score, task-type, team-registry, team-subagents, verify-gate-refused, worktree-bound
- Configuration : ../../../.workspace/campaign-light/campagne-complet-3.json, ../../../.workspace/etalons/kit.config.json
- Jeu : 126 étalons (44 bons, 82 mauvais), 42 tâches

| Étalon | Nature | Attendu | Obtenu | Évaluation | Périmètre | Verdict |
|---|---|---|---|---|---|---|
| bon-haiku-18 | bon | attendu accept | obtenu accept | 12/12 | périmètre ok | ok |
| bon-opus-nuit-19 | bon | attendu accept | obtenu accept | 12/12 | périmètre ok | ok |
| bon-opus-matin-19 | bon | attendu accept | obtenu accept | 12/12 | périmètre ok | ok |
| mauvais-triche | test qui triche | attendu reject | obtenu reject | 9/12 | périmètre ok | ok |
| mauvais-partiel | correctif partiel | attendu reject | obtenu reject | 11/12 | périmètre ok | ok |
| mauvais-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 12/12 | périmètre hors : scripts/gate.sh | ok |
| kit-acceptance-selfcheck-bon | bon | attendu accept | obtenu accept | 4/4 | périmètre ok | ok |
| kit-acceptance-selfcheck-sans-code | sans implémentation | attendu reject | obtenu reject | 0/4 | périmètre ok | ok |
| kit-acceptance-selfcheck-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 4/4 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-attack-bench-on-sandbox-change-bon | bon | attendu accept | obtenu accept | 6/6 | périmètre ok | ok |
| kit-attack-bench-on-sandbox-change-sans-code | sans implémentation | attendu reject | obtenu reject | 1/6 | périmètre ok | ok |
| kit-attack-bench-on-sandbox-change-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 6/6 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-attack-interruption-bon | bon | attendu accept | obtenu accept | 7/7 | périmètre ok | ok |
| kit-attack-interruption-sans-code | sans implémentation | attendu reject | obtenu reject | 1/7 | périmètre ok | ok |
| kit-attack-interruption-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 7/7 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-breaker-agent-bon | bon | attendu accept | obtenu accept | 4/4 | périmètre ok | ok |
| kit-breaker-agent-sans-code | sans implémentation | attendu reject | obtenu reject | -5/4 | périmètre ok | ok |
| kit-breaker-agent-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 4/4 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-breaker-vitest5-bon | bon | attendu accept | obtenu accept | 2/2 | périmètre ok | ok |
| kit-breaker-vitest5-sans-code | sans implémentation | attendu reject | obtenu reject | 0/2 | périmètre ok | ok |
| kit-breaker-vitest5-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 2/2 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-breaker-vitest5-parse-bon | bon | attendu accept | obtenu accept | 2/2 | périmètre ok | ok |
| kit-breaker-vitest5-parse-sans-code | sans implémentation | attendu reject | obtenu reject | 0/2 | périmètre ok | ok |
| kit-breaker-vitest5-parse-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 2/2 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-broker-replay-bon | bon | attendu accept | obtenu accept | 2/2 | périmètre ok | ok |
| kit-broker-replay-sans-code | sans implémentation | attendu reject | obtenu reject | 0/2 | périmètre ok | ok |
| kit-broker-replay-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 2/2 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-campaign-false-quota-bon | bon | attendu accept | obtenu accept | 3/3 | périmètre ok | ok |
| kit-campaign-false-quota-sans-code | sans implémentation | attendu reject | obtenu reject | 1/3 | périmètre ok | ok |
| kit-campaign-false-quota-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 3/3 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-campaign-quota-scope-bon | bon | attendu accept | obtenu accept | 2/2 | périmètre ok | ok |
| kit-campaign-quota-scope-sans-code | sans implémentation | attendu reject | obtenu reject | 1/2 | périmètre ok | ok |
| kit-campaign-quota-scope-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 2/2 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-campaign-worktrees-bon | bon | attendu accept | obtenu accept | 3/3 | périmètre ok | ok |
| kit-campaign-worktrees-sans-code | sans implémentation | attendu reject | obtenu reject | 0/3 | périmètre ok | ok |
| kit-campaign-worktrees-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 3/3 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-claim-durable-pid-bon | bon | attendu accept | obtenu accept | 5/5 | périmètre ok | ok |
| kit-claim-durable-pid-sans-code | sans implémentation | attendu reject | obtenu reject | 2/5 | périmètre ok | ok |
| kit-claim-durable-pid-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 5/5 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-claude-hooks-protected-paths-bon | bon | attendu accept | obtenu accept | 9/9 | périmètre ok | ok |
| kit-claude-hooks-protected-paths-sans-code | sans implémentation | attendu reject | obtenu reject | -9/9 | périmètre ok | ok |
| kit-claude-hooks-protected-paths-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 9/9 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-claude-usage-source-bon | bon | attendu accept | obtenu accept | 12/12 | périmètre ok | ok |
| kit-claude-usage-source-sans-code | sans implémentation | attendu reject | obtenu reject | 0/12 | périmètre ok | ok |
| kit-claude-usage-source-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 12/12 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-codex-prereqs-bon | bon | attendu accept | obtenu accept | 4/4 | périmètre ok | ok |
| kit-codex-prereqs-sans-code | sans implémentation | attendu reject | obtenu reject | -1/4 | périmètre ok | ok |
| kit-codex-prereqs-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 4/4 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-consolidate-safety-bon | bon | attendu accept | obtenu accept | 3/3 | périmètre ok | ok |
| kit-consolidate-safety-sans-code | sans implémentation | attendu reject | obtenu reject | 0/3 | périmètre ok | ok |
| kit-consolidate-safety-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 3/3 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-diff-header-fix-bon | bon | attendu accept | obtenu accept | 3/3 | périmètre ok | ok |
| kit-diff-header-fix-sans-code | sans implémentation | attendu reject | obtenu reject | 1/3 | périmètre ok | ok |
| kit-diff-header-fix-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 3/3 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-events-usine-bon | bon | attendu accept | obtenu accept | 3/3 | périmètre ok | ok |
| kit-events-usine-sans-code | sans implémentation | attendu reject | obtenu reject | 0/3 | périmètre ok | ok |
| kit-events-usine-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 3/3 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-gate-docs-kit-bon | bon | attendu accept | obtenu accept | 4/4 | périmètre ok | ok |
| kit-gate-docs-kit-sans-code | sans implémentation | attendu reject | obtenu reject | 1/4 | périmètre ok | ok |
| kit-gate-docs-kit-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 4/4 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-gemini-relay-sync-bon | bon | attendu accept | obtenu accept | 2/2 | périmètre ok | ok |
| kit-gemini-relay-sync-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 2/2 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-hooks-guard-session-match-bon | bon | attendu accept | obtenu accept | 2/2 | périmètre ok | ok |
| kit-hooks-guard-session-match-sans-code | sans implémentation | attendu reject | obtenu reject | 1/2 | périmètre ok | ok |
| kit-hooks-guard-session-match-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 2/2 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-integration-gate-node-image-bon | bon | attendu accept | obtenu accept | 7/7 | périmètre ok | ok |
| kit-integration-gate-node-image-sans-code | sans implémentation | attendu reject | obtenu reject | 0/7 | périmètre ok | ok |
| kit-integration-gate-node-image-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 7/7 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-lot2-state-fix-bon | bon | attendu accept | obtenu accept | 3/3 | périmètre ok | ok |
| kit-lot2-state-fix-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 3/3 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-mission-git-worktree-bon | bon | attendu accept | obtenu accept | 3/3 | périmètre ok | ok |
| kit-mission-git-worktree-sans-code | sans implémentation | attendu reject | obtenu reject | 1/3 | périmètre ok | ok |
| kit-mission-git-worktree-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 3/3 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-note-subagent-bon | bon | attendu accept | obtenu accept | 5/5 | périmètre ok | ok |
| kit-note-subagent-sans-code | sans implémentation | attendu reject | obtenu reject | 0/5 | périmètre ok | ok |
| kit-note-subagent-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 5/5 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-orchestrator-launched-by-bon | bon | attendu accept | obtenu accept | 5/5 | périmètre ok | ok |
| kit-orchestrator-launched-by-sans-code | sans implémentation | attendu reject | obtenu reject | -1/5 | périmètre ok | ok |
| kit-orchestrator-launched-by-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 5/5 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-orphan-agents-bon | bon | attendu accept | obtenu accept | 9/9 | périmètre ok | ok |
| kit-orphan-agents-sans-code | sans implémentation | attendu reject | obtenu reject | 1/9 | périmètre ok | ok |
| kit-orphan-agents-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 9/9 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-post-merge-defects-bon | bon | attendu accept | obtenu accept | 5/5 | périmètre ok | ok |
| kit-post-merge-defects-sans-code | sans implémentation | attendu reject | obtenu reject | 1/5 | périmètre ok | ok |
| kit-post-merge-defects-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 5/5 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-prepare-task-orphans-bon | bon | attendu accept | obtenu accept | 3/3 | périmètre ok | ok |
| kit-prepare-task-orphans-sans-code | sans implémentation | attendu reject | obtenu reject | 0/3 | périmètre ok | ok |
| kit-prepare-task-orphans-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 3/3 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-questions-answer-cli-bon | bon | attendu accept | obtenu accept | 5/5 | périmètre ok | ok |
| kit-questions-answer-cli-sans-code | sans implémentation | attendu reject | obtenu reject | 1/5 | périmètre ok | ok |
| kit-questions-answer-cli-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 5/5 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-reserve-check-bon | bon | attendu accept | obtenu accept | 6/6 | périmètre ok | ok |
| kit-reserve-check-sans-code | sans implémentation | attendu reject | obtenu reject | 1/6 | périmètre ok | ok |
| kit-reserve-check-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 6/6 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-resume-cost-study-bon | bon | attendu accept | obtenu accept | 8/8 | périmètre ok | ok |
| kit-resume-cost-study-sans-code | sans implémentation | attendu reject | obtenu reject | 2/8 | périmètre ok | ok |
| kit-resume-cost-study-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 8/8 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-retry-abandoned-bon | bon | attendu accept | obtenu accept | 5/5 | périmètre ok | ok |
| kit-retry-abandoned-sans-code | sans implémentation | attendu reject | obtenu reject | 2/5 | périmètre ok | ok |
| kit-retry-abandoned-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 5/5 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-sandbox-init-and-gate-seal-bon | bon | attendu accept | obtenu accept | 5/5 | périmètre ok | ok |
| kit-sandbox-init-and-gate-seal-sans-code | sans implémentation | attendu reject | obtenu reject | 2/5 | périmètre ok | ok |
| kit-sandbox-init-and-gate-seal-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 5/5 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-session-lock-bon | bon | attendu accept | obtenu accept | 4/4 | périmètre ok | ok |
| kit-session-lock-sans-code | sans implémentation | attendu reject | obtenu reject | 0/4 | périmètre ok | ok |
| kit-session-lock-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 4/4 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-split-mission-bon | bon | attendu accept | obtenu accept | 3/3 | périmètre ok | ok |
| kit-split-mission-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 3/3 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-tap-score-bon | bon | attendu accept | obtenu accept | 5/5 | périmètre ok | ok |
| kit-tap-score-sans-code | sans implémentation | attendu reject | obtenu reject | 2/5 | périmètre ok | ok |
| kit-tap-score-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 5/5 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-task-type-bon | bon | attendu accept | obtenu accept | 4/4 | périmètre ok | ok |
| kit-task-type-sans-code | sans implémentation | attendu reject | obtenu reject | -2/4 | périmètre ok | ok |
| kit-task-type-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 4/4 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-team-registry-bon | bon | attendu accept | obtenu accept | 5/5 | périmètre ok | ok |
| kit-team-registry-sans-code | sans implémentation | attendu reject | obtenu reject | 0/5 | périmètre ok | ok |
| kit-team-registry-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 5/5 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-team-subagents-bon | bon | attendu accept | obtenu accept | 4/4 | périmètre ok | ok |
| kit-team-subagents-sans-code | sans implémentation | attendu reject | obtenu reject | 0/4 | périmètre ok | ok |
| kit-team-subagents-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 4/4 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-verify-gate-refused-bon | bon | attendu accept | obtenu accept | 3/3 | périmètre ok | ok |
| kit-verify-gate-refused-sans-code | sans implémentation | attendu reject | obtenu reject | -2/3 | périmètre ok | ok |
| kit-verify-gate-refused-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 3/3 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |
| kit-worktree-bound-bon | bon | attendu accept | obtenu accept | 6/6 | périmètre ok | ok |
| kit-worktree-bound-sans-code | sans implémentation | attendu reject | obtenu reject | 6/10 | périmètre ok | ok |
| kit-worktree-bound-hors-perimetre | hors périmètre | attendu reject | obtenu reject | 6/6 | périmètre hors : scripts/etalon-hors-perimetre.sh | ok |

taille du jeu : 126 étalons, 42 tâches
faux rejets : 0/44
fausses acceptations : 0/82

## Casseur

- Date : 2026-10-19 (avant : nuit du 20, Pôle 6 ; après : jour du 19, Pôle 7, commits `f287d47` et `af5f697`)

Avant T42, sur les six étalons de T05 :

| Exécutant du casseur | Mauvais attrapés | Bons sans faux test | Erreurs |
|---|---|---|---|
| claude-haiku | 0/3 | 1/3 | 0 |
| claude-sonnet | 0/3 | 2/3 | 0 |

Après T42 (test discriminant exigé), mêmes étalons, Haiku seul (Sonnet non relancé : la recherche du discriminant ne dépend pas du modèle) :

| Exécutant du casseur | Mauvais attrapés | Bons sans faux test | Erreurs |
|---|---|---|---|
| claude-haiku | 3/3 | 1/3 | 0 |

| Étalon | Nature | Attendu | Haiku avant | Haiku après |
|---|---|---|---|---|
| kit-claude-usage-source-bon | bon | accept | faux test (3) | faux test (1) |
| kit-claude-usage-source-sans-code | sans implémentation | reject | manqué (0) | attrapé (0, `no_implementation`) |
| kit-hooks-guard-session-match-bon | bon | accept | faux test (1) | propre (0, discriminant `acceptance`) |
| kit-hooks-guard-session-match-sans-code | sans implémentation | reject | manqué (0) | attrapé (0, `no_implementation`) |
| kit-team-subagents-bon | bon | accept | propre (0) | faux test (1) |
| kit-team-subagents-sans-code | sans implémentation | reject | manqué (0) | attrapé (0, `no_implementation`) |

Les deux « faux tests » d'après sont les défauts réels déjà retenus la nuit du 20 sur ces diffs anciens : code 120 sur une sortie fermée
(`claude-usage-source`, corrigé depuis par `statusline-closed-stdout`) et erreur d'encodage brute de `team_agents` (toujours ouverte).

Tout le jeu du kit sans modèle (`breaker_score.py … --agent none`, tests cachés et tests du projet touchés seulement, 1 553 s) :

| Passage | Sans implémentation attrapés | Bons acceptés | Erreurs |
|---|---|---|---|
| T42 seul (`f287d47`) | 37/37 | 39/40 (faux rejet : `kit-gemini-relay-sync-bon`) | 2 |
| T42 + correctif (`af5f697`) | 37/37 | 40/40 | 0 (2 étalons écartés) |

Lecture : le faux rejet venait d'un livrable rangé comme fichier de test (`template/scripts/__tests__/gemini-relay.test.ts`) que la
« base » gardait ; le correctif `breaker-acceptance-full-base` juge les tests cachés sur la base complète. Les deux étalons
`kit-consolidate-safety-*` sont écartés : leur base contient les anciens liens symboliques de `scripts/`, que le casseur refuse (avant même
T42). Les trois bons étalons de la console ne sont pas rejoués (autre projet) ; le vérificateur, lui, n'a pas changé (0/44, 0/82 ci-dessus).
Défaut vu en passant, non corrigé : les fichiers `__pycache__/*.pyc` écrits par l'agent dans `__breaker__/` comptent comme candidats et
prennent les places de `--max-findings` (des tests réels finissent en « limite dépassée »).

## Lecture (Pôle 6, nuit du 20 octobre)

- Jeu (T31) : 126 étalons sur 42 tâches. 120 viennent des preuves gardées du kit depuis le 18 octobre
  (`campaign.py etalons --harvest <kit> --out tools/campaign/etalons --configs .workspace/etalons --label kit --since 2026-10-18`) :
  41 bons (diff réel d'une tâche fusionnée), 38 « sans implémentation » (les seules sections de test du même diff), 41 « hors périmètre »
  (le même diff plus un fichier `scripts/` ajouté) ; s'y ajoutent les 6 étalons console du 19 octobre. La récolte écarte les tâches dont le
  `diff.txt` est masqué par `***` (22 à une relance de contrôle), sans tests cachés (2) ou sans diff (2). Aucun échec réel exploitable : les tentatives en
  `acceptance_failed` n'ont pas laissé leur diff. Côté console, les fiches des tâches e14 et pilot ont été retirées de `tasks/` et les
  six fiches restantes n'ont pas de tests cachés : rien de récoltable.
- Défaut trouvé par la récolte : le `diff.txt` du circuit n'était pas un diff Git (`diff --git/<chemin>`, modes bruts `100600`) ;
  corrigé par `diff-header-fix` (en-têtes Git dans `build_diff`, anciens diffs réparés à la récolte).
- Rejeu : `PATH=<Node 24>:/usr/bin:$PATH python3 tools/campaign/campaign.py etalons --write …`, code 0, 1 084 s. **Faux rejets 0/44,
  fausses acceptations 0/82.** Limite : les mauvais étalons du kit sont fabriqués (tests seuls, fichier hors périmètre) ; ils mesurent
  que les tests cachés exigent bien une implémentation et que le périmètre est contrôlé, pas la finesse contre une triche subtile (les
  deux étalons console « triche » et « partiel » restent les seuls de ce type).
- Casseur (T05), sur 6 étalons de 3 tâches, un passage par exécutant : il ne retient **aucun** test sur un diff « sans implémentation »,
  par construction : la vérification de la fiche échoue d'abord, et tous ses tests sont alors classés « ne compile pas ». Sur les bons
  diffs, les tests retenus ne sont pas des faux : Haiku a trouvé sur `claude-usage-source` un code de sortie 120 quand la sortie standard
  est fermée (la fiche exige toujours 0), un `Infinity` réécrit dans des réglages JSON et une année non complétée avant l'an 1000 ; sur
  `hooks-guard-session-match`, une écriture protégée qui passe par le préfixe `command` ; Sonnet, sur `team-subagents`, une erreur
  d'encodage brute au lieu du message prévu. La colonne « bons sans faux test » compte donc des défauts réels contre le casseur : à
  relire à la main avant de lire un score.
