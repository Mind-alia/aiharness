# Campagne de mesure

Exporter les courses d'un dossier de résultats en CSV et JSON :
`python3 tools/campaign/campaign.py export <dossier-resultats>`.

Depuis la racine du kit : `python3 tools/campaign/campaign.py tools/campaign/exemple.json`.
Adapter les chemins, le SHA de départ, les demandes et les tests de l'exemple.
Python 3.14, Git et les scripts du circuit doivent être installés dans le projet,
avec les agents et dépendances déjà disponibles. Le projet doit être propre.
Les chemins `project`, `request`, `evaluation_files`, `output` sont relatifs au
JSON ; `evaluation_target` est relatif au projet. Conserver demandes, tests cachés
et résultats **hors du projet** pour ne pas les exposer aux agents. `output`
vaut le dossier du JSON par défaut. Ne pas lancer deux campagnes simultanément.

Chaque bras travaille dans son propre worktree, `<projet>/.workspace/campaign/<uid>`, sur la branche
`campagne/<tâche>/<bras>/<n>-<k>` : le checkout principal ne change jamais de branche et reste utilisable
(porte, fusion) pendant la mesure. `setup` (facultatif, argv ou commande Bash) se lance dans ce worktree avant
les contrats, par exemple `["npm", "ci"]` : un worktree neuf n'a pas les dépendances. Un bras `completed` retire
son worktree ; un bras en échec, en quota ou interrompu le garde (`arm_worktree`, `arm_worktree_kept`), sauf
`--discard-failed`. Un quota n'est reconnu que dans la sortie des scripts qui appellent un agent
(`delegate`, `prepare-task`, `breaker`) ou par un événement `limit_reached`, jamais dans la porte ni l'évaluation.

Les bras sont `opus-seul`, `codex-seul` et `systeme-<agent>` (codex,
claude-haiku, claude-sonnet, claude-opus, antigravity ; Antigravity est hors des bancs de code depuis le 19/10 :
`agy` refuse les commandes sans interaction, il ne fait que des documents). `allowed`, facultatif,
fixe le périmètre des bras seuls ; sinon les fichiers/dossiers de premier niveau
hors infrastructure sont autorisés (le circuit reste juge du périmètre).
Les bras seuls ont une tentative, sans acceptation, avec vérification triviale ;
leur fusion utilise `--no-verify-again --no-integration`. Les bras système
emploient le planificateur Opus, les tentatives/acceptations de ses contrats,
puis le casseur et, pour chaque contrat avec faille retenue, une nouvelle délégation.
Les sous-tâches suivent strictement l'ordre renvoyé par `prepare-task.sh`.

La porte complète précède la copie temporaire des fichiers d'évaluation
(basename conservé, collisions refusées). `evaluation_command` est une liste
argv ou une commande Bash ; pour les comptes exacts, produire sur une ligne
un JSON `{"passed": 11, "total": 12}`. TAP et unittest sont aussi reconnus ;
sinon les comptes restent inconnus. Le code de sortie et les sorties brutes
sont toujours conservés. Le marquage `statusline_binary_only` est conservateur :
toutes les lignes d'échec reconnues doivent nommer `statusline-binary` ; la porte
reste rouge et sa sortie permet de contrôler la classification.

`results.jsonl` est ajouté après chaque exécution, `results.md` est régénéré.
La graine (générée si absente), l'ordre, les commandes, le SHA, les événements
nouveaux et les tokens par fournisseur **et rôle** sont enregistrés, dont
l'orchestration. Les événements bruts restent inchangés dans `consumption_events`.
Dans `consumption`, chaque couple fournisseur/rôle expose `input`, `output`,
`cached_input`, `cache_creation`, `reasoning`, `total_tokens` et
`unknown_token_calls`. Une catégorie native absente ou invalide rend sa somme
`null` ; un zéro explicite reste zéro. Les compteurs natifs observés dans une
tentative partielle restent disponibles, sans constituer un total complet.
`total_tokens` reste `null` dès qu'un appel du groupe est incomplet ;
`unknown_token_calls` compte ces appels, pas leurs tours. Le Markdown affiche
chaque fournisseur/rôle, les catégories et le total, avec `inconnu` et le nombre
d'appels incomplets.

Le calcul partagé `token_total` additionne entrée, sortie, lecture et création
du cache Claude ; pour Codex, entrée + sortie suffisent, car son entrée inclut
le cache et sa sortie inclut le raisonnement. Ces volumes ne sont pas une
facture. Une catégorie facultative inconnue (cache/raisonnement Codex) ne rend
pas à elle seule son total inconnu.

La collecte Codex conserve les compteurs entiers positifs ou nuls de tous les
tours mesurés et indique `tokens_complete` par appel et dans le statut de la
délégation. Usage requis absent/invalide, tour commencé sans fin, échec,
JSON illisible ou arrêt de processus non nul rendent la tentative incomplète.
Le journal ajoute alors `tokens.total: null`, compris par les rapports existants,
sans effacer les compteurs observés. Une tentative mesurée ultérieure ne rétablit
pas la complétude des tentatives précédentes. Les lignes historiques sans ce
marqueur utilisent leurs seuls compteurs disponibles ; leur complétude passée
ne peut pas être reconstruite. Les anciens résultats de campagne ne sont pas
réécrits : une colonne nouvelle absente est affichée comme inconnue. Les tentatives comptent les délégations d'exécution,
y compris la correction après casseur. Les sorties doivent être conservées
localement car elles peuvent contenir du code du projet.

Relancer le même JSON reprend : les résultats terminés (même échoués) sont
sautés ; quota/interruption sont rejoués entièrement, avec nouveaux identifiants,
et leurs coûts restent dans le journal. Une configuration modifiée est refusée
pour un dossier de résultats existant. Une reprise réinitialise uniquement la
branche `campagne/<tâche>/<bras>/<n>` au SHA de départ ; les missions actives sont
closes comme abandonnées. Les contrats sont commités sur ces seules branches
pour satisfaire l'arbre propre exigé par le circuit ; les fusions y créent leurs
commits. Aucun push, aucune modification de master. La branche initiale est
restaurée ; les éventuels résidus sont préservés dans un stash identifié dans
le résultat. Une interruption SIGINT/SIGTERM est enregistrée (un SIGKILL ne
permet pas de garantir le nettoyage).

**Contrôle de quota** : avant le premier bras, chaque fournisseur du banc est contrôlé une fois (`quota_check.py`) :
Codex et Antigravity par un appel léger de leur relais (`review`, délai 180 s), Claude sans appel par sa barre d'état et le journal du projet.
Un fournisseur à sec refuse le banc (une ligne par fournisseur, code `75`, aucun bras lancé), reprise comprise ;
`--ignore-quota` passe outre sans aucun contrôle.

Codes : `0` campagne évaluée (des tests peuvent être rouges), `1` échec du
circuit/configuration, `64` usage, `75` quota (arrêt immédiat), `130` interruption.

## Évaluer un diff sans lancer la campagne

`python3 tools/campaign/evaluation.py <config.json> --task <id> --diff <fichier> --base <commit>`
mesure le travail livré d'un bras, même si le circuit l'a refusé. La commande clone le
projet dans un dossier temporaire, extrait `--base` en détaché, applique le diff
(`git apply`), lance le champ facultatif `setup` de la configuration (liste argv ou
commande Bash, par exemple `npm ci`), copie les `evaluation_files` de la tâche dans son
`evaluation_target` (jamais d'écrasement), puis lance `evaluation_command` (sortie
d'erreur fusionnée). Le projet d'origine (fichiers, branches, worktrees, `HEAD`) n'est
jamais modifié et le clone est toujours supprimé.

La sortie brute de l'évaluation va sur la sortie d'erreur ; la dernière ligne de la
sortie standard est le score `passed/total`, ou `?/?` s'il est inconnu. Codes : `0` score
complet (`passed == total`, `total > 0`, évaluation en code 0) ; `1` score partiel ou
inconnu, `setup` en échec, fichier d'évaluation qui écraserait un fichier ; `2` diff qui
ne s'applique pas (vide, corrompu, ou une ligne de contenu contenant `***`, marque du
masquage des secrets) ; `64` usage, configuration, tâche inconnue ou `--base` introuvable.

## Étalons du vérificateur

`python3 tools/campaign/campaign.py etalons [<manifeste>] [--write <fichier.md>]` rejoue des diffs dont on connaît le
bon verdict (manifeste JSON, par défaut `tools/campaign/etalons/etalons.json`) : chacun passe par `evaluation.py` puis par le
contrôle de périmètre (`allowed` de la tâche). Il est accepté seulement si le score est complet, le code d'évaluation nul
et aucun chemin hors périmètre ; tout autre refus d'évaluation (diff masqué, qui ne s'applique pas, fichier d'évaluation
écrasé) compte comme rejet. Deux taux sortent en dernières lignes : les **faux rejets** (un bon étalon rejeté) et les
**fausses acceptations** (un mauvais étalon accepté, le plus grave). `--write` ajoute un rapport Markdown, même si un taux n'est
pas nul. Codes : `0` aucun des deux, `1` au moins un, `64` usage, manifeste, configuration ou commit inutilisable.
