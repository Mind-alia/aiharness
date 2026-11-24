# Cahier d'expériences
Trace les améliorations du workspace : hypothèse, métriques avant/après, décision.
## Commandes
- `scripts/experiment.sh start "<hypothèse>" --metric <chemin> [--expect up|down]`
- `scripts/experiment.sh note <NNN> "<texte>"`
- `scripts/experiment.sh end <NNN> --decision gardé|abandonné|à-refaire [--reason "<texte>"]`
- `scripts/experiment.sh list`
## Exemple complet
```sh
scripts/experiment.sh start "le conseiller local réduit les questions qui remontent à l'orchestrateur" --metric advisor.answer_rate --expect up --metric advisor.pending_for_orchestrator --expect down
scripts/experiment.sh note 001 "Configuration du prompt local"
scripts/experiment.sh end 001 --decision gardé --reason "Taux de réponse en hausse"
scripts/experiment.sh list
```
