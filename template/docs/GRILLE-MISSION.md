# Grille de jugement d'une mission

Cette grille fixe, avant le test, les onze critères qui jugeront la mission, pour que le jugement ne change pas après coup.

Générer la grille d'une mission : `sh scripts/report.sh --mission <nom>`. Elle s'écrit une seule fois dans `.workspace/missions/<nom>/grille.md` ; une grille déjà présente n'est jamais réécrite, car le jugement humain y est peut-être déjà.

| Critère | Comment juger |
| --- | --- |
| Règles · périmètre | Compte les délégations sorties du périmètre permis ; le bon résultat est zéro. |
| Règles · secrets | Compte les délégations arrêtées parce qu'un secret était présent : le garde a bloqué la fuite. |
| Règles · tests présents | Vérifie que chaque tâche avait ses tests cachés ; le bon résultat est toutes les tâches. |
| Règles · seuils | Regarde si les seuils de durée, de tentatives et de tokens ont été respectés, et si quelqu'un les a relevés en cours de mission. |
| Qualité du cadrage | Juge si les fiches de tâche étaient assez précises pour qu'un agent les réalise sans poser de question. |
| Qualité du livrable | Juge si le résultat final répond au besoin du client, sans relire le code ligne à ligne. |
| Interventions humaines · nombre | Compte les questions d'agent et les interventions humaines notées par `note.sh human` ; moins il y en a, mieux le cadrage a tenu. |
| Interventions humaines · nature | Note chaque intervention humaine et dis si elle était prévue ou si elle a corrigé un blocage. |
| Coût · tokens par rôle | Lis le volume de tokens par rôle pour voir où part la consommation. |
| Coût · euros (tarif API) | Lis le coût estimé au tarif API du jour ; ce n'est pas une facture. |
| Délai | Lis le temps écoulé entre l'adoption de la mission et le dernier événement enregistré. |

Les lignes de jugement portent `À remplir (jugement humain)` : elles restent ainsi tant qu'une personne ne les a pas remplies. Une ligne mesurable porte `—` dans la colonne Jugement.
