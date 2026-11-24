# Veille du 3 novembre 2026 — harnais pour grandes tâches autonomes

Demandée par le propriétaire le 3 novembre (« dis-moi les grandes améliorations à faire, va voir sur Internet »). Lectures faites par Fable,
sources en bas. Ce qui en sort est croisé avec les manques du kit relevés le 2 novembre dans le code (`c34e1dd`).

## Ce que disent les sources

1. **Travail par petites marches, état hors du contexte** (Anthropic, nov. 2025). Un agent initialiseur écrit une liste de
   fonctionnalités en JSON (plus de 200 pour un clone de claude.ai), toutes `passes: false` ; chaque session suivante n'en prend
   qu'une, lit le journal Git et un fichier de progression, relance l'application, joue un test de bout en bout avant d'ajouter
   quoi que ce soit, et finit par un commit et une note. Le JSON est choisi parce que les modèles le réécrivent moins qu'un Markdown.
   Échecs corrigés par cette forme : vouloir tout faire d'un coup, déclarer la victoire trop tôt, marquer fini sans test de bout en bout,
   perdre du temps à redécouvrir comment lancer l'application.
2. **Planificateur, générateur, évaluateur séparés** (Anthropic, mars 2026). L'évaluateur n'est jamais le générateur : le même
   modèle est indulgent avec son propre travail, un évaluateur séparé se règle vers le scepticisme. Il clique dans l'application
   (Playwright MCP), lit l'état de la base, note chaque sprint sur une grille à seuils durs (un critère raté = sprint raté), calibré
   par des exemples notés. Avant chaque sprint, un **contrat** : le générateur propose ce qu'il va livrer et comment on le vérifiera,
   l'évaluateur relit, ils s'accordent avant toute ligne de code (27 critères pour un seul sprint). Chiffres : seul, 20 min et 9 $ ;
   harnais complet, 6 h et 200 $ ; version allégée (Opus 4.6, évaluateur en une passe à la fin) 3 h 50 et 125 $. L'auteur conclut que
   l'évaluateur vaut son prix seulement au-delà de ce que le modèle fait bien seul : c'est la même leçon que nos E12 et E13.
3. **Vérification hybride** (R2E-Gym ; « Agentic Rubrics », 2026). Combiner tests exécutés et jugement sans exécution bat chacun seul.
   Les rubriques agentiques : un agent explore le dépôt, écrit `rubrics.yaml` (critères pondérés 1-3 sur quatre axes : changement
   minimal, conformité à la demande, intégrité sans tricherie, comportement à l'exécution), un juge note le diff ; sur SWE-Bench
   Verified en meilleur-de-16, 40,6 % contre 33,6 % pour des tests générés, pour 0,29 $ contre 0,52 $ par cas. Limites : 2 % de notes
   instables, 46 % des rejets à tort sont des critères sur-spécifiés.
4. **Contexte** (étude empirique, 176 réglages, SWE-Bench Verified et Terminal-Bench 2.1). La gestion du contexte sert surtout à
   éviter le débordement qui tue la course ; élider les vieilles observations avant de résumer est la stratégie la plus efficace ;
   rendre l'élision réversible n'est presque jamais utilisé. Le plan persistant aide les modèles faibles en réussite et les modèles
   forts en coût (moins de vérifications redondantes après l'édition). Bash seul coûte moins cher avec un modèle fort ; des outils
   prédéfinis aident un modèle faible.
5. **Machine à états durable** (plan communautaire 2026, non officiel). Statuts fermés (`queued, ready, in_progress, blocked,
   awaiting_user, done, canceled, failed`), point de contrôle avant et après chaque effet de bord, rejeu idempotent, annulation et
   délai comme événements ; journal d'événements en ajout seul plus instantané typé ; sorties volumineuses hors du contexte (résumé,
   aperçu, référence d'artefact) ; budgets de départ : 50 à 100 pas par tour, 2 à 3 reprises par pas, 20 à 25 % de contexte réservé ;
   métriques dès le premier jour : reprises réussies, taux de reprise, approbations par tâche, temps d'attente d'approbation,
   surcoût d'orchestration contre exécution directe.
6. **Pourquoi les systèmes multi-agents échouent** (MAST, NeurIPS 2025) : 14 modes en trois familles (spécification et conception,
   désalignement entre agents, vérification et terminaison), taux d'échec de 41 à 87 % ; un relecteur ou vérificateur dans la boucle
   donne un faux sentiment de sécurité ; tenir une distribution des échecs étiquetés.
7. **Développement guidé par la spécification** : spécifier, planifier, découper, puis coder, en relisant le plan avant le découpage et
   les tâches avant le code ; consignes courtes (au-delà de 150 à 200 instructions permanentes la fiabilité baisse, règle empirique),
   pas de chemins de fichiers qui pourrissent ; garder la trace de quelle spécification a produit quel comportement.

## Croisement avec le kit (manques du 2 novembre)

| Manque du kit | Ce que la source apporte | Grand travail |
|---|---|---|
| Plan gelé par empreinte, pas de replanification | liste de fonctionnalités JSON, une par délégation, replanification entre étapes | GT1 |
| Exécutant ne lit que sa fiche | dossier de projet (spécification, charte, progression) relu à chaque session | GT1 |
| Toute question arrête le plan ; une validation par fusion | statut `awaiting_user` par tâche, approbations comptées, contrat avant le sprint | GT2 |
| Pause par SIGTERM destructrice, reprise sur clone sale (T103), quota (T55), fantômes (T57) | machine à états, point de contrôle autour des effets de bord, annulation comme événement | GT2 |
| Relecture texte seul (Read, Glob, Grep) | évaluateur séparé qui clique (Playwright), grille à seuils durs, calibrage par exemples | GT3 |
| Casseur à réoutiller (T56), tests cachés faux (T75) | rubriques agentiques en complément des tests cachés ; preuve rouge sur la base et verte sur la référence | GT3 |
| 40 tours, séance orchestratrice à 300 k tokens (T114), sorties longues (T87) | élision avant résumé, artefacts hors contexte, budget réservé, plafond en dollars par grande tâche | GT4 |
| Défauts notés à la main, pas de distribution | étiquetage MAST des échecs dans le journal, métriques de reprise et d'approbation | GT4 |
| `exécutant:` limité aux trois Claude (T106) | bash seul pour un modèle fort, outils prédéfinis pour un faible | GT5 |

## Sources

- Anthropic, « Effective harnesses for long-running agents » (nov. 2025) : https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Anthropic, « Harness design for long-running apps » (mars 2026) : https://www.anthropic.com/engineering/harness-design-long-running-apps
- « Agentic Rubrics as Contextual Verifiers for SWE Agents » (2026) : https://arxiv.org/html/2601.04171v1
- « R2E-Gym: Procedural Environments and Hybrid Verifiers » : https://arxiv.org/pdf/2504.07164
- « An Empirical Study of Harness Design for Coding Agents » (2026) : https://arxiv.org/abs/2609.20804
- « Modern Agent Harness Blueprint 2026 » (gist communautaire, non officiel) : https://gist.github.com/amazingvince/52158d00fb8b3ba1b8476bc62bb562e3
- « Why Do Multi-Agent LLM Systems Fail? » (MAST, NeurIPS 2025) : https://arxiv.org/html/2503.13657v2
- « Harness Engineering: Anatomy, Architecture, and Evolution of Coding Agents » (onze systèmes, 2026) : https://arxiv.org/html/2609.00006v1
- Spec-driven development en 2026 : https://dev.to/krlz/spec-driven-development-in-2026-what-it-is-the-tooling-and-how-teams-actually-use-it-2fk2
