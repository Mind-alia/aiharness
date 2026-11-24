# Veille — OpenHands : ce que le kit peut en tirer (13 novembre 2026)

Écrit par Fable le 13 novembre 2026 à la demande du propriétaire (« note les modifs et les choses à tester sur OpenHands pour améliorer le
système, on le rajoute après »). Source : documentation `docs.openhands.dev` et dépôts `OpenHands/OpenHands` et
`OpenHands/software-agent-sdk`, lus le 12 novembre 2026 (version 1.40.0). Les mentions « (index) » n'ont été lues que dans l'index de
la documentation, pas page par page. Les chiffres d'OpenHands sont les leurs, non vérifiés. Rien ici n'est décidé ni planifié : ce sont
des propositions, à verser dans `docs/TRAVAUX.md` seulement après l'accord du propriétaire. Le gel du 10 novembre tient : aucun mécanisme
nouveau avant deux séances réussies de bout en bout.

## En une phrase
OpenHands est un agent complet avec son interface, qui cherche à faire travailler un agent le plus librement possible ; le kit est
une chaîne de contrôle autour d'agents existants, qui cherche à prouver que ce qui est livré est juste. Les deux ne s'excluent pas.

## Ce que le kit a et qu'OpenHands n'a pas (à garder)
- Tests cachés que l'exécutant ne voit pas, relancés sur l'hôte : chez eux, la fin de tâche est jugée par un modèle (critique, juge).
- Périmètre de fichiers fermé par fiche (`allowed`).
- Exécutant sans réseau, clone sans historique, banc d'attaque : leur documentation n'a ni politique réseau ni modèle de menace.
- Fusion décidée par le propriétaire sur un avis `PRET` : chez eux, une automatisation peut aller du ticket à la fusion.
- Coût en dollars par fiche et par rôle.

## Modifications proposées, par ordre d'intérêt

| # | Idée prise chez OpenHands | Défaut du kit qu'elle vise | Ce qu'il faudrait changer | Comment le tester |
|---|---|---|---|---|
| 1 | Détecteur d'agent bloqué | E15 : le programme a relancé une seconde tentative alors que le diagnostic annonçait le même échec (0,7 $ perdus) | `tools/orchestrator` : ne pas relancer quand le diagnostic conclut à une cause hors exécutant (circuit, fiche, environnement) ; arrêt `FICHE-` ou `ENVIRONNEMENT-` à la place | rejouer le journal de la première course d'E15 : une seule exécution au lieu de deux ; aucune reprise perdue sur les échecs d'exécutant d'E13 |
| 2 | Condenseur de contexte (anciennes observations résumées ou masquées) | l'exécutant d'E15 a relu 955 000 tokens en cache pour 3 minutes de travail | rien dans le circuit : réglage ou consigne de l'exécutant (lectures ciblées, pas de relecture de fichiers entiers) ; à défaut, mesurer seulement | même fiche, avec et sans, trois courses : tokens relus, coût, tests cachés ; la revue du 17 octobre cite une étude où le masquage divise le coût par deux environ |
| 3 | Banc public (SWE-bench) | toutes les fiches, tous les tests cachés et toutes les références sont écrits par Fable : même auteur, même juge | adopter 5 à 10 tickets publics comme missions ; les tests du banc servent de tests cachés | usine contre modèle seul sur ces tickets ; comparaison possible avec les scores publiés |
| 4 | Reprise et duplication d'une conversation | une reprise repart d'un clone neuf et relit tout | reprise dans la même session et le même dossier (déjà l'idée 3 de la veille du 10 novembre, brique de GT1) | E7 rejouée à trois répétitions : coût de la seconde tentative avec et sans reprise |
| 5 | Rejeu d'une course depuis son journal d'événements | un défaut du circuit ne se retrouve qu'en repayant une course | commande qui rejoue `events.jsonl` sans appeler de modèle | rejouer les deux échecs d'E14 et d'E15 : le défaut ressort sans dépense |
| 6 | Compétences déclenchées par le chemin du fichier touché | les consignes sont lues en entier à chaque délégation | consigne courte par dossier, donnée seulement quand la fiche touche ce dossier | E6 : tokens de lecture par rôle, consigne longue contre consigne ciblée |
| 7 | OpenHands comme exécutant de plus | un seul fournisseur d'exécutants de code aujourd'hui (Claude, Codex en secours) | relais `openhands` dans le registre des exécutants, dans le conteneur, sans réseau | une fiche d'E16 rejouée avec cet exécutant et un modèle ouvert : tests cachés, coût |
| 8 | Déclencheurs (ticket, horaire, webhook) | tout part d'une session ouverte par le propriétaire | hors gel, et seulement jusqu'au `PRET` : jamais de fusion automatique | — |

## À ne pas copier
- Le jugement de fin de tâche confié à un modèle : c'est ce que les tests cachés remplacent.
- La fusion automatique.
- L'analyseur de risque qui « demande confirmation sans empêcher » : la liste blanche et le conteneur du kit empêchent.

## Non vérifié
- Le détail des pages lues seulement dans l'index : sous-agents, crochets, compétences, automatisations, intégrations.
- Le score « SWE-bench 77,6 » affiché sur leur dépôt : ni le modèle ni la date ne sont donnés.
- Leur dépôt de bancs d'essai n'a pas été lu.

## Lien avec la veille du 10 novembre (Super Agent de DeepSeek)
Les idées « attention comptée », « mode observe », « question qui ne gèle que sa fiche » et « ticket durable » restent proposées et non
décidées. Les idées 1 et 4 ci-dessus les recoupent.
