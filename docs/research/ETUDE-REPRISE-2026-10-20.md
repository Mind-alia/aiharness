# Étude du coût de la reprise de session — 20 octobre 2026 (T29)

Question : à la deuxième tentative d'une délégation Claude, reprendre la session (`--resume`, seul le retour du test est envoyé, T12)
coûte-t-il moins que renvoyer toute la consigne ?

Protocole : cinq tâches récentes du kit (fiches et tests cachés gardés), rejouées chacune deux fois par Haiku en conteneur, sur un clone
du kit (`main` 362e18b), à la base d'origine de chaque tâche (`prepare --base`). La première vérification est forcée en échec
(`WORKSPACE_STUDY_FORCE_FAIL=1`), ce qui impose une deuxième tentative ; en mode `sans-reprise`, `WORKSPACE_DELEGATE_RESUME=0` coupe la
reprise. Commande : `python3 tools/campaign/resume_study.py --project <clone> --source <kit> --tasks claude-usage-source,quota-before-bench,weekly-report,orchestrator-beat,team-subagents --agent claude-haiku --out <dossier>`
(Pôle 6, 19 octobre, 04:57–05:35 UTC). Jetons : compteurs natifs de Claude Code dans `status.json` (cache lu, cache écrit, entrée, sortie).

| Tâche | Mode | Issue | Tentatives | Durée (s) | 2e tentative (s) | Cache lu | Cache écrit | Entrée | Sortie |
|---|---|---|---|---|---|---|---|---|---|
| claude-usage-source | reprise | done | 2 | 188.3 | 36.2 | 1061367 | 57761 | 32 | 30403 |
| claude-usage-source | sans-reprise | done | 2 | 194.2 | 18.8 | 1247034 | 77882 | 44 | 33699 |
| quota-before-bench | reprise | done | 2 | 355.9 | 50.7 | 3792758 | 97273 | 80 | 51592 |
| quota-before-bench | sans-reprise | acceptance_failed | 2 | 307.3 | 41.1 | 2270203 | 118719 | 58 | 47955 |
| weekly-report | reprise | done | 2 | 223.3 | 38.7 | 1809942 | 72428 | 48 | 32768 |
| weekly-report | sans-reprise | done | 2 | 255.7 | 53.4 | 1499366 | 106710 | 48 | 47182 |
| orchestrator-beat | reprise | done | 2 | 204.0 | 40.4 | 1436560 | 61116 | 42 | 24902 |
| orchestrator-beat | sans-reprise | done | 2 | 213.3 | 37.9 | 1337297 | 109455 | 38 | 33657 |
| team-subagents | reprise | done | 2 | 157.7 | 30.9 | 771148 | 56610 | 24 | 24151 |
| team-subagents | sans-reprise | done | 2 | 154.4 | 16.7 | 865437 | 75996 | 30 | 25644 |
| Total reprise |  |  |  | 1129.1 | 197.0 | 8871775 | 345188 | 226 | 163816 |
| Total sans-reprise |  |  |  | 1124.9 | 167.9 | 7219337 | 488762 | 218 | 188137 |

Lecture, par tâche (cinq paires) : avec reprise, le cache **écrit** est plus bas dans les cinq paires (345 188 contre 488 762 au total,
−29 %) et la sortie plus basse dans quatre sur cinq (163 816 contre 188 137, −13 %). Le cache **relu** est plus bas dans deux paires
seulement ; son total est plus haut avec reprise (+23 %), tiré par une seule tâche (`quota-before-bench` : 3,79 M contre 2,27 M). Les
durées totales sont égales (1 129 s contre 1 125 s). Une issue diffère : `quota-before-bench` sans reprise finit en `acceptance_failed`.

Conclusion prudente : sur cinq tâches et une seule répétition, la reprise semble réduire ce qui est réécrit en cache et un peu la
sortie, sans gain de durée ni de cache relu démontré ; l'écart sur le cache relu tient à une tâche. Cela ne suffit pas pour chiffrer
une économie : il faudrait plusieurs répétitions par tâche (le bruit entre deux passages d'une même tâche n'est pas mesuré ici).
