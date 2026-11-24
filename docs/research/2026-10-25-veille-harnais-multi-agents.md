# Veille — harnais multi-agents : coût, parallélisme, vérification (25 octobre 2026)

Écrit par Fable le 25 octobre 2026 à la demande du propriétaire (« l'objectif est de créer le meilleur harnais possible pour manager une équipe
d'agents en diminuant les coûts, avec plusieurs tâches en même temps depuis le même point ; va voir les études »). Recherche web du soir,
sources en fin de document ; les chiffres cités viennent des sources, les lectures « pour nous » sont de Fable. À confronter aux mesures du kit
(E12, E13) avant toute décision.

## 1. Ce que disent les études, en sept constats

1. **Le multi-agent coûte 3 à 10 fois plus de tokens qu'un agent seul pour un résultat équivalent** (Anthropic, janvier 2026 ; le système
   de recherche multi-agent d'Anthropic en 2025 : ≈ 15 fois une conversation). Anthropic dit avoir vu des équipes bâtir des architectures
   élaborées puis constater qu'un seul agent mieux consigné faisait aussi bien. Trois cas seulement justifient plusieurs agents : protéger le
   contexte (sous-tâche bruyante, > 1 000 tokens inutiles au reste), paralléliser pour la couverture (« la thoroughness, pas la vitesse »),
   spécialiser (trop d'outils, domaines sans rapport, consignes contradictoires). Nos mesures concordent : E12 B 7,9 $ contre 3,23 $ ;
   E13 B 7,76 $ contre 2,97 $.
2. **Les relais perdent de l'information.** Anthropic parle de « téléphone arabe » quand on découpe par rôle (planifier, exécuter, relire,
   itérer) : chaque passage dégrade. Conseil : découper par frontière de contexte, pas par type de problème (« l'agent qui fait la
   fonctionnalité fait aussi ses tests »). Bons points de coupe : recherches indépendantes, composants à contrat d'interface net,
   vérification en boîte noire. Mauvais : phases successives d'une même fonctionnalité, composants couplés, état partagé.
3. **Le harnais fixe surtout la facture, pas la réussite** (« What Does a Harness Buy? Tokens, Mostly », octobre 2026 ; étude reprise par
   VentureBeat). Même modèle, mêmes tâches : jusqu'à 3 fois le coût d'un harnais à l'autre, sans gain régulier de réussite ; changer de
   harnais fait basculer autant de tâches que rejouer le même. Le préambule du premier appel pèse : 16 581 tokens pour Claude Code,
   7 025 pour OpenCode, 829 pour mini-SWE-agent. Une critique tierce note que le préambule n'explique qu'un cinquième au plus et que le nombre
   d'étapes compte davantage. Pour nous : E6 (lecture avant d'agir) est la bonne étude, et le nombre de tours de l'orchestrateur le vrai levier.
4. **Le coût d'une boucle d'agent est dominé par la relecture du contexte**, pas par la sortie. Une analyse de 739 M de tokens trouve 93 %
   de lectures de cache, soit environ la moitié de la facture. Chaque tour relit tout l'historique ; le coût croît plus vite que le nombre de
   tours (TurnCost, « tours, pas observations » ; LangWatch sur 873 compactages réels : croissance ≈ taille^2,5). Compacter n'est pas gratuit :
   tout changement du préfixe casse le cache et le re-remplissage coûte 5 à 6 fois la lecture de cache (CliffCompaction : jusqu'à −50 % de
   coût à contexte borné, en ne tronquant jamais autrement qu'en coupant du texte original). Pour nous : bras A d'E13, 9,7 M lus pour 140 k
   écrits ; orchestrateur Opus d'E12 B, 18 M lus. Réduire les tours réduit la facture ; réduire la prose, peu.
5. **Un pipeline sans agent peut battre les agents à prix bas.** Agentless (2024) : localiser, réparer, valider, en trois phases écrites en
   code, plusieurs patchs candidats, tests de reproduction et vote ; 27 à 32 % sur SWE-bench Lite pour 0,34 à 0,70 $ par problème, meilleur
   résultat ouvert de l'époque. Anthropic (« Building effective agents ») dit la même chose : un *workflow* (chemins de code prédéfinis) bat
   un *agent* dès que la structure de la tâche est stable. Pour nous : le circuit du kit est un workflow dont on a confié l'exécution à un
   agent ; T14 (orchestrateur programme) remet les choses à l'endroit.
6. **Vérifier coûte, vérifier partout ne paie pas.** SWE-Verifier (2026) : vérifier chaque étape de cinq candidats passe la réussite de
   75,5 à 76,0 % pour 2,3 fois le coût ; vérifier moins de 4 % des étapes, choisies, donne 77,2 % pour 1,09 fois. Les relecteurs LLM sont
   peu fiables sur les bugs : verdicts justes pour de mauvaises raisons, bugs manqués ou inventés (revue 2026 dans *Automated Software
   Engineering* ; RAND 2026 : aucun juge fiable partout). Anthropic recommande un vérificateur à critères explicites et met en garde contre la
   « victoire précoce » (quelques tests puis « réussi »). Pour nous : les tests cachés restent l'instrument ; une relecture LLM vaut quand elle
   est ciblée (E12 ter : cinq défauts vrais sur cinq, aucun bloquant, 1,09 $ ; E13 B : deux défauts vrais hors tests, 2,39 $).
7. **Plusieurs agents en parallèle depuis la même base : isoler les fichiers ne suffit pas.** Les guides convergent : un worktree par
   agent, propriété des fichiers décidée avant de lancer (les tâches qui se recouvrent passent en série), `git merge-tree` entre paires pour
   détecter les recouvrements avant la fin, intégration dans une branche d'étape testée avant `main`, attention aux ressources partagées
   (`node_modules`, ports, migrations). Deux branches peuvent fusionner sans conflit textuel et être contradictoires (schéma changé d'un côté,
   utilisé de l'autre). Pour nous : le kit a déjà le clone par tâche et les périmètres `allowed` disjoints ; manquent le `merge-tree`
   préventif et une branche d'intégration.

Deux lectures de plus. Le harnais d'Anthropic Labs pour le développement long (mars 2026) sépare planificateur, générateur et évaluateur,
l'évaluateur testant l'application qui tourne et les deux négociant un « contrat de sprint » par fonctionnalité ; l'apport vient du rapport
d'échec renvoyé au générateur, pas d'un mécanisme adverse. Et la taxonomie MAST (NeurIPS 2025, 1 600 traces, 7 cadres) range les échecs
multi-agents en trois familles : spécification (≈ 42 %), désalignement entre agents (≈ 37 %), vérification (≈ 21 %) ; la spécification
d'abord, ce que E12 ter a vu (deux phrases de piège dans la fiche ont donné à Sonnet la solidité d'Opus).

## 2. Ce que ça donne pour le kit

- **Le chef doit être un programme.** Préparer, cloner, déléguer, attendre, relancer les tests, fusionner, resynchroniser : chemins stables,
  donc code (constat 5). Un modèle n'y intervient que pour écrire la fiche et les tests cachés, diagnostiquer un échec, arbitrer une fusion
  risquée. C'est T14 et le second bras d'E10. Cible : zéro tour d'orchestration modèle sur une tâche qui se passe bien.
- **Dépenser les tokens d'Opus dans la fiche, pas dans la relecture ni le git** (constats 2, 6, MAST). La fiche est le seul relais sans
  perte : elle est lue entière par l'exécutant. Une relecture Opus ciblée (sécurité, interface) garde sa place sur les tâches risquées.
- **Compter les tours, pas les mots** (constats 3, 4). Mesure à ajouter au rapport : tours et tokens relus par rôle ; borne `--max-turns`
  et budget par appel dans le circuit ; consignes courtes par rôle (E6). La langue des documents est un levier de second rang.
- **Vérifier là où ça compte** (constat 6) : tests cachés toujours ; relecture modèle seulement quand la classe de la tâche ou le périmètre
  le justifie, jamais systématique ; interdire la « victoire précoce » dans la consigne de l'exécutant (suite complète obligatoire).
- **Parallèle : un clone par tâche, fichiers disjoints, `merge-tree` avant fusion, branche d'intégration** (constat 7). Le gain attendu est la
  couverture et le temps mural, pas le coût : le total de tokens monte avec le nombre d'agents (constat 1).

## 3. L'étude suivante, proposée

Après E13 (bras C en cours), avant de toucher au moteur : **E13 bis, « la fiche à Opus, le code à Sonnet, sans orchestrateur »**, sur T89,
mêmes 9 tests cachés, mêmes consignes anglaises. Opus en lecture seule reçoit la demande et écrit une fiche d'exécution courte : règles,
pièges nommés, cas limites, rien d'autre (≈ 0,5 $). Sonnet seul reçoit demande + fiche et livre (≈ 1 à 2,5 $). Zéro orchestration modèle :
le lanceur fait la mécanique. Ce bras D se compare à A (2,97 $), B (7,76 $) et C. Il répond à la question qui commande T14 : où les tokens
d'Opus rapportent-ils, dans la fiche, dans la relecture ou dans la correction ? Coût ≈ 3 $, une heure, aucun changement du moteur.

En complément, deux essais bon marché, dans l'ordre :
- **Langue** : rejouer le bras A avec demande et consigne en français (≈ 3 $) ; même tâche, même modèle, seule la langue change (question
  du propriétaire du 25/10).
- **Deux Sonnet en parallèle depuis la même base, les tests cachés choisissent** (≈ 2,5 $) : mesure directe de « plusieurs tâches en même
  temps depuis le même point » et du « meilleur de deux » que les études de vérification sélective rendent plausible.

Puis le moteur : T14 (orchestrateur programme), joué comme second bras d'E10 sur le même plan.

## Sources

- Anthropic, « When to use multi-agent systems (and when not to) », 23 janvier 2026 : https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them
- Anthropic, « Building effective agents » (résumé S. Willison) : https://simonwillison.net/2024/Dec/20/building-effective-agents/
- Anthropic, système de recherche multi-agent (résumé S. Willison) : https://simonwillison.net/2025/Jun/14/multi-agent-research-system/
- Harnais Anthropic Labs pour le développement long, lectures tierces : https://www.epsilla.com/blogs/anthropic-harness-engineering-multi-agent-gan-architecture
- « What Does a Harness Buy? Tokens, Mostly » : https://arxiv.org/abs/2610.04433 ; critique : https://github.com/jjakimoto/research-issues/issues/2019 ;
  reprise VentureBeat : https://venturebeat.com/orchestration/a-coding-agents-harness-can-triple-its-cost-without-making-it-more-accurate-study-finds
- « An Empirical Study of Harness Design for Coding Agents » : https://arxiv.org/pdf/2609.20804
- Agentless : https://arxiv.org/html/2407.01489 ; https://github.com/openautocoder/agentless
- SWE-Verifier, vérification sélective : https://arxiv.org/html/2610.30284
- Coût des boucles : TurnCost https://arxiv.org/html/2610.15093v1 ; CliffCompaction https://arxiv.org/pdf/2609.26779 ;
  LangWatch « The Context Tax » https://langwatch.ai/blog/context-tax-when-to-compact ;
  « 93 % of my agent's tokens… » https://dev.to/theagentloop/93-of-my-agents-tokens-were-context-it-had-already-seen-863
- Fiabilité des relecteurs LLM : https://link.springer.com/article/10.1007/s10515-026-00638-5 ; https://arxiv.org/pdf/2604.27727 ;
  https://arxiv.org/pdf/2510.24367
- MAST, « Why Do Multi-Agent LLM Systems Fail? » : https://arxiv.org/abs/2503.13657
- Worktrees et agents parallèles : https://foremerge.com/blog/running-multiple-claude-code-agents-in-parallel/ ;
  https://zylos.ai/research/2026-02-22-git-worktree-parallel-ai-development/ ; https://www.mindstudio.ai/blog/parallel-agentic-development-git-worktrees
- Routage et cascades : FrugalGPT https://arxiv.org/pdf/2305.05176 ; RouteNLP (comparaison) https://arxiv.org/pdf/2604.23577
