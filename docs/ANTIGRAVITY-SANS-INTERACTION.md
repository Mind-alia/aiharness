# Antigravity sans interaction : ce qui passe, ce qui est refusé (18 octobre 2026, soir)

Chantier 7 du plan du soir, exploration seulement : aucun réglage ni relais modifié. Essais réels faits par l'orchestrateur Opus
(Antigravity ne peut pas lancer lui-même les commandes d'un essai : c'est précisément ce qui est exploré). Outil `agy` du
18 octobre (`~/.local/bin/agy`), modèle `gemini-3.8-flash-medium`, relais `template/scripts/gemini.sh` recopié tel quel dans un
projet jetable (`/private/tmp/claude-<uid>/agy-essais`, hors du kit), journaux dans son `.workspace/run/gemini/`.

## Essais

| N° | Appel | Demande | Code relais | Durée | Enveloppe `agy` | Résultat |
|---|---|---|---|---|---|---|
| 1 | `gemini.sh write` (`--mode accept-edits --sandbox`) | créer `notes.txt`, corriger `calc.py`, « n'exécute aucune commande » | 0 | 20 s | `SUCCESS`, aucun refus | les deux fichiers écrits correctement |
| 2 | `gemini.sh write` | lancer `echo bonjour-commande` | 2 | 7 s | `SUCCESS`, `response` vide, `denied_actions: [RunCommand]`, 1 tour | rien ; le tour s'arrête au premier refus |
| 3 | `gemini.sh review` (`--mode plan`) | lire deux fichiers, résumer | 0 | 11 s | `SUCCESS` | réponse juste |
| 4 | `agy -p … --mode auto --sandbox` (direct) | lancer `echo bonjour-commande` | 0 (agy) | 6 s | avertissement « `--mode auto` is not available for this account », retour au mode par défaut, `RunCommand` refusé | rien |
| 5 | `gemini.sh write` | lancer `bash scripts/run-in-sandbox.sh -- python3 -c "print(1)"` (consigne actuelle du circuit) | 2 | 7 s | `RunCommand` refusé, réponse vide | rien : c'est l'échec du bras Antigravity du banc |
| 6 | `gemini.sh write` | **par fichiers** : écrire `.workspace/exchange/requests/001.json` (`{"command": [...]}`), puis relire `results/001.json` jusqu'à son apparition ; un courtier factice répond après 3 s | 0 | 14 s | `SUCCESS`, aucun refus | requête écrite au bon format, résultat lu, réponse `42` juste |
| 7 | comme 6, courtier factice qui répond après **45 s** | idem, « continue à relire tant qu'il est absent » | 0 | 59 s | `SUCCESS`, 1 tour, 216 321 tokens (relectures) | a attendu, réponse `56` juste |

Message exact du refus (essais 2, 4, 5) : « a tool required the "command" permission that headless mode cannot prompt for, so it
was auto-denied. Add an allow-rule under permissions.allow in settings.json (e.g. command(<target>)). Alternatively, re-run with
--dangerously-skip-permissions to auto-approve all tools. »

## Constats

1. Sans interaction, `agy` lit et écrit des fichiers dans le projet (y compris sous `.workspace/`, ignoré par Git), mais refuse
   **toute** commande. Le refus termine le tour : pas de seconde tentative, réponse vide, enveloppe pourtant `SUCCESS`.
2. Le relais classe déjà correctement ce cas en échec (code 2, ligne `DENIED: RunCommand` sur la sortie d'erreur), mais
   `delegate.py` le range ensuite en `verify_failed` : la cause (permission) se perd.
3. Deux portes d'entrée pour les commandes existent dans `agy` : une règle `permissions.allow` (forme `command(<binaire et
   sous-commande>)`) dans `~/.gemini/antigravity-cli/settings.json` ou dans la configuration propre au projet
   (`~/.gemini/config/projects/`), ou `--dangerously-skip-permissions` (tout autoriser). **Non essayées** : ce sont des
   changements de réglage ou de sécurité, réservés au propriétaire. Le mode `auto` n'est pas ouvert sur ce compte.
4. Le protocole d'échange par fichiers du circuit (`.workspace/exchange/requests/NNN.json` → `results/NNN.json`, déjà servi par
   `exchange.run_broker` pour les autres agents) **fonctionne avec Antigravity sans aucune permission nouvelle** : il écrit la
   requête, attend en relisant, lit le résultat. Coût : chaque relecture renvoie le contexte (216 000 tokens pour 45 s d'attente).

## Recommandation (à valider par le propriétaire avant toute modification)

- **Option A, sans réglage (recommandée)** : pour `agent: antigravity`, la consigne de délégation remplace « lance
  `bash scripts/run-in-sandbox.sh -- …` » par le protocole par fichiers : écrire `requests/NNN.json`, relire `results/NNN.json`
  jusqu'à son apparition, une seule commande ciblée par requête, pas plus de deux ou trois requêtes par tentative. La vérification
  finale reste celle du circuit. Changement dans `delegate.py` (consigne) et le prompt seulement, pas dans le relais ni les
  permissions. Le coût des relectures est à mesurer sur une vraie tâche du kit avant un banc.
- **Option B, réglage** : une règle `permissions.allow` limitée à `command(bash scripts/run-in-sandbox.sh)` dans la configuration
  `agy` du projet. Plus simple pour la consigne, mais c'est un réglage de sécurité de l'outil Google : décision du propriétaire.
- Dans les deux cas : `delegate.py` doit classer un refus de commande (`DENIED:` du relais) en `permission_denied` et non en
  `verify_failed`, pour que le rapport dise la vraie cause.
- Jamais `--dangerously-skip-permissions`.

Conséquence pour ce soir : le bras `systeme-antigravity` n'est **pas** inclus dans le banc complet (point 8), puisque la consigne
actuelle lui demande des commandes qu'il ne peut pas lancer et qu'aucune modification n'est autorisée ce soir.
