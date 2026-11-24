# Modèles disponibles : forces, faiblesses, quand les choisir

Brouillon de Fable du 23 octobre 2026, destiné à `docs/MODELES.md` du kit. Chaque ligne « mesuré » vient d'E11 (paliers 1 et 2, 22 courses), des
usines des 19 à 23 octobre, ou de la carte d'usage du compte ; chaque ligne « source » vient du web et reste de seconde main. Règle : une usine
qui observe un comportement nouveau ajoute une ligne datée ; rien n'est écrit sans mesure ou sans source.

## Tableau de choix

| classe de fiche | premier choix | si échec | jamais |
|---|---|---|---|
| petite (1 à 4 fichiers, pas de module nouveau) | Haiku 5.5 | Sonnet 5.5 | Opus (inutile, mesuré 6/6 égal) |
| moyenne (module nouveau, 5 fichiers et plus, règles nombreuses) | Sonnet 5.5 dans le circuit | Opus 5.5 | Haiku seul (0/2 mesuré) |
| grande (plusieurs modules, brief flou) | Opus 5.5, ou découpage | découpage | petits modèles seuls |
| casseur | Codex Sol | Sonnet (même famille que les exécutants, à déclarer) | Luna (non mesuré comme casseur) |
| relecture de rapport | Codex Sol | Antigravity (fichiers seulement) | Claude (même famille) |
| documentation, exploration | Antigravity ou Haiku | Sonnet | Opus |
| plan du jour, fiches, tests cachés | Opus 5.5 (Fable pour le plan) | aucun | Haiku, Luna |

## Tarif du cache (règle publiée par Anthropic, lue le 23 octobre ; à vérifier sur la page des tarifs avant tout chiffrage officiel)
- Lecture de cache : un dixième du prix d'entrée du modèle. Écriture de cache (fenêtre de cinq minutes) : une fois un quart le prix d'entrée.
- Donc, par million de tokens : Opus 5.5 lecture 0,40 $ et écriture 5 $ ; Sonnet 5.5 lecture 0,20 $ et écriture 2,50 $ ; Haiku 5.5 lecture 0,01 $ et écriture 0,125 $.
- Codex (abonnement ChatGPT) : `cached_input` est compris dans `input` ; aucun tarif public pour les modèles Luna et Sol sous ces noms, les prix tiers ci-dessous valent pour l'entrée totale.
- Pourquoi cette section existe : l'usine du 23 octobre a mesuré 153 millions de tokens lus en cache par l'orchestrateur Opus de `usine-3-chantiers`, contre 346 000 en sortie. Sans prix du cache, le coût de l'orchestrateur est sous-estimé d'un facteur dix (6,92 $ hors cache, environ 61 $ de cache lu au tarif ci-dessus).

## Claude Haiku 5.5
- Prix : 0,10 $ / 0,50 $ par million (entrée / sortie), le moins cher de la famille (source : table API du 6 octobre).
- Mesuré : 6/6 sur une petite fiche ; 0/2 seul sur une fiche moyenne (5 et 6 tests sur 29), 1/2 dans le circuit ; une ronde de correction
  a cassé une solution qui passait. 60 à 85 k tokens de sortie par course moyenne, le plus bavard : il itère.
- Forces : coût, tâches courtes et précises, tests ordinaires. Faiblesses : explore beaucoup (99 appels d'outils sur une fiche), tente des
  commandes hôte quand un outil manque, fragile sur les modules entiers. Terminal-Bench 39 % contre 71 % pour Sonnet (source éditeur).

## Claude Sonnet 5.5
- Prix : 2 $ / 10 $ par million.
- Mesuré : 6/6 petite fiche ; 0/2 seul sur la fiche moyenne (6 et 23 sur 29), 2/2 dans le circuit. Exécutant de toutes les usines du 22 et 23
  octobre : quinze fiches livrées, presque toutes en une tentative. 40 à 60 k tokens de sortie par course moyenne.
- Forces : le meilleur rapport fiabilité sur prix dès qu'il reçoit le retour des tests cachés ; code soumis à tests, scripts du circuit.
  Faiblesses : seul, sans retour de tests, il rate les fiches moyennes.

## Claude Opus 5.5
- Prix : 4 $ / 20 $ par million.
- Mesuré : 2/2 seul et 2/2 dans le circuit sur la fiche moyenne ; le circuit ne lui apporte rien et coûte 60 % de tokens en plus. 44 k tokens
  de sortie par course seul. Orchestrateur de toutes les usines : plans, fiches, tests cachés, décisions.
- Forces : seul modèle Claude qui tient une fiche moyenne sans échafaudage ; jugement pour les plans. Faiblesses : prix ; sans intérêt sur les
  petites fiches.

## Codex Luna (gpt-6-luna)
- Prix : 0,10 $ / 0,50 $ par million (sources tierces, OpenAI ne publie pas ce nom tel quel) ; sur ton abonnement ChatGPT, pas à l'API.
- Mesuré : 2/2 seul sur la fiche moyenne en 1 à 2 minutes, 1/2 dans le circuit (échec sur une majuscule dans une phrase imposée, non corrigée
  après le retour d'erreur) ; 8 à 26 k tokens de sortie. En octobre : `export-courses` réussie, `reconstruct-events` échouée deux fois sur la
  première version de la fiche. Ignore `max_turns`. Son bac à sable ne fermait pas la lecture de l'hôte avant le 23 octobre (corrigé).
- Forces : vitesse et prix sans équivalent, écrit un module d'un trait. Faiblesses : la lettre exacte des consignes, les corrections après
  retour ; non mesuré comme casseur.

## Codex Sol (gpt-6.1-sol)
- Prix : 2 $ / 10 $ par million (sources tierces).
- Mesuré : casseur de toutes les courses E11 (2 à 5 tests retenus à chaque passage, 200 à 300 k tokens d'entrée, 0,10 $ environ) ; relecteur
  de tous les rapports (10 à 18 affirmations vraies sur 20, témoins 2 à 3 sur 3) ; exécutant de secours réussi sur `reconstruct-events`.
- Forces : précision en relecture (la lignée Sol est la mieux placée sur le banc de revue de code Macroscope, 86 % de précision) ; autre
  famille que les exécutants Claude. Faiblesses : même famille que Luna ; ses trouvailles répètent parfois une ambiguïté de fiche.

## Antigravity (Gemini)
- Mesuré : refuse toute commande sans interaction ; ne reçoit que des fichiers ; relectures et documentation (quatre relectures le 18 octobre).
- Forces : regard extérieur bon marché, autre famille. Faiblesses : aucune exécution, aucun test ; jamais pour un plan, une fiche ou un test caché.

## GPT-6 Astra
- Prix : 10 $ / 50 $ par million (sources tierces) ; accessible sur ChatGPT Plus avec un quota très serré, plus large sur Pro.
- Mesuré : rien, jamais utilisé dans le kit.
- Sources : 58 à 59 % Terminal-Bench 4.0, 74,1 % DeepSWE ; recommandé par OpenAI pour « le travail le plus dur ». Candidat à mesurer comme
  exécutant de secours sur fiche grande ou casseur de secours.

## Fable (Claude Fable 5.1)
- Rôle fixé : plan du jour, préenregistrement des études, relecture des rapports, décisions à remonter ; deux sessions par jour.
- Mesuré : la part « Fable » de la semaine est la plus lourde du compte (24 % contre 15 % pour tous les modèles réunis au 22 octobre), à cause
  des longues sessions de pilotage ; ne jamais lui faire exécuter.

## Ce que les éditeurs publient eux-mêmes (pages Anthropic lues le 23 octobre ; OpenAI d'après son billet de lancement, rapporté)

Règle de lecture : un banc ne vaut que comparé sur la même page, au même réglage. Les tableaux ci-dessous viennent chacun d'une seule page ;
on ne mélange pas deux pages. Aucun n'a de barre d'erreur publiée, sauf mention. L'éditeur choisit ses comparaisons : c'est une menace.

Page Anthropic « Sonnet 5.5 » (Opus 5.5 à l'effort xhigh, Sonnet 5.5 à max ou xhigh selon la ligne) :

| banc | Sonnet 5.5 | Opus 5.5 | GPT-6 Sol |
|---|---|---|---|
| Terminal-Bench 4.0 (terminal, multi-étapes) | 70,6 % | 66,4 % | non publié |
| FrontierCode 1.1 (code, difficile) | 46,2 % (max) / 52,1 % (xhigh) | 54,4 % | 49,3 % |
| CursorBench 4.0 (édition de code) | 55,5 % | 57,8 % | non publié |
| OSWorld 2.1 (ordinateur) | 80,1 % | 81,8 % | non publié |

Page Anthropic « Haiku 5.5 » (réglages non précisés) :

| banc | Haiku 5.5 | Sonnet 5.5 | GPT-6 Luna |
|---|---|---|---|
| Terminal-Bench 4.0 | 39,2 % | 70,6 % | 16,4 % |
| FrontierCode 1.1 | 46,4 % | 52,1 % | 42,4 % |
| OSWorld 2.1 | 72,4 % | 83,9 % | 48,9 % |

Page Anthropic « Fable » : aucun chiffre lisible (tableau en image) ; en note, sur Terminal-Bench-Science 0.1, Fable 5 est **sous** Opus 5
(24,7 % contre 29,0 % dans la reproduction d'Anthropic, ±4 points). Fable 5.1 n'a aucun banc de code publié. Prix 10 $ / 50 $.

OpenAI, billet de lancement de GPT-6 Sol et Luna (rapporté par la presse spécialisée, non relu à la source) : DeepSWE v1.1, Astra 74,1 %,
Sol 68,8 %, Luna 66,6 % ; aucun Terminal-Bench ni SWE-bench publié par OpenAI pour ces modèles. Terminal-Bench 4.0 mesuré par Artificial
Analysis : Sol ~43 %, Luna ~13 %, Astra ~58 % (tiers, non relu). GPT-6.1 Sol : aucun chiffre primaire trouvé sous ce nom.

Ce que ces pages permettent de dire, et rien de plus :
- Sonnet 5.5 et Opus 5.5 sont au coude à coude en terminal et en édition de code ; Opus garde 2 à 8 points sur le code difficile.
- Haiku 5.5 est loin derrière en terminal (39 contre 71) mais proche en code difficile (46 contre 52) : il code bien, il explore mal.
- Luna est sous Haiku partout sur la page d'Anthropic (16 contre 39 en terminal) ; mais chez nous, sur une fiche écrite, il livre.
- Fable n'a aucun chiffre de code publié et le seul banc publié le place sous Opus : rien ne justifie de le prendre pour exécuter.

## Règle de choix, pour que le document serve à choisir
1. On choisit d'abord par nos mesures (taux de livraison par classe de fiche, tokens, durée), jamais par un banc public.
2. Un banc public ne sert qu'à départager deux modèles que nous n'avons pas encore mesurés sur la même classe, et il est cité avec sa page.
3. Fable et Opus ne sont jamais le choix par défaut d'une exécution : Opus seulement sur fiche grande ou après deux échecs de Sonnet ;
   Fable jamais en exécution.
4. Tout modèle non mesuré (Astra, Sol comme exécutant sur fiche moyenne, Luna comme casseur) est « à mesurer » avant d'entrer dans le tableau
   de choix, par une étude courte du type E11.

## Écarts entre les bancs et nos mesures
- **Luna** : dernier sur la page d'Anthropic, 2/2 seul chez nous sur une fiche moyenne. Les deux sont vrais : notre fiche était entièrement
  spécifiée et tenait dans un module ; Luna livre ce qui est écrit, et rate ce qu'il faut explorer ou enchaîner. Sa faiblesse mesurée chez
  nous (la lettre des consignes) n'apparaît dans aucun banc.
- **Sonnet 5.5** : proche d'Opus sur les bancs, en dessous seul chez nous, égal dans le circuit. Le circuit comble l'écart ; c'est l'argument
  chiffré du moteur.
- **Haiku 5.5** : bancs et mesures d'accord : petites fiches oui, moyennes non, sauf avec le retour des tests et une seconde tentative.
- **Sol** : seul chiffre de code primaire, FrontierCode 49,3 % entre Sonnet et Opus ; chez nous, casseur fiable et exécutant de secours réussi.
- **Astra** : jamais mesuré ; au prix d'Opus ; à essayer seulement comme secours sur fiche grande.
