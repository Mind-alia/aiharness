# Demande — sauvegarde de l'état vivant du circuit (T62)

Demande écrite par Fable le 24 octobre 2026 pour l'étude E12. Elle est donnée mot pour mot aux deux bras. L'interface est imposée :
les tests cachés en dépendent. Tout ce qui n'est pas dit ici est au choix de celui qui réalise, et doit être justifié dans son rapport.

## Besoin

Le 19 octobre, le dossier `.workspace/` de la console était la seule copie de son historique ; une suppression l'aurait effacé. Je veux
qu'à chaque fin d'usine le circuit mette de côté, hors du dépôt, ce qui ne se reconstruit pas : le journal des événements, les tests cachés
et les journaux de délégation. Sept sauvegardes gardées, les plus anciennes retirées. Et qu'on puisse remettre une sauvegarde en place dans
un dépôt, par exemple après un accident, sans rien écraser par surprise.

## Interface imposée

1. `bash scripts/orchestrator.sh release` crée la sauvegarde **avant** de rendre le verrou. L'événement `orchestrator_released` est écrit
   dans `events.jsonl` avant la copie, de sorte que la sauvegarde le contient ; puis un événement `archived` est ajouté au journal du dépôt
   avec le champ `path` (chemin absolu de la sauvegarde). Si la sauvegarde échoue (dossier non inscriptible, disque plein), `release`
   rend quand même le verrou, imprime une ligne qui commence par `archive : échec` et écrit un événement `archive_failed` avec `cause` ;
   code de retour 0.
2. Emplacement : `$WORKSPACE_ARCHIVE_DIR` s'il est défini, sinon `~/.workspace-ia/archives/<nom du dossier du dépôt>/`. Créé avec le mode
   `0700` s'il manque. Une sauvegarde est un **dossier** nommé `<AAAAMMJJTHHMMSSZ>-<session>` (horodatage UTC du début de la copie, session
   du verrou ; si le nom existe déjà, suffixe `-2`, `-3`…). Il contient `events.jsonl`, `acceptance/`, `run/delegate/` copiés tels quels
   depuis `.workspace/`, et `manifest.json` : `created_at` (UTC, ISO), `session`, `root` (chemin du dépôt), `files` (liste de
   `{path, sha256, size}` pour chaque fichier régulier copié, `path` relatif au dossier de la sauvegarde), `skipped` (liste des chemins
   relatifs non copiés et la raison). Les liens symboliques ne sont jamais suivis : ils sont ignorés et listés dans `skipped`. Un
   élément absent de `.workspace/` (pas de `run/delegate/`) n'est pas une erreur.
3. Rotation : après la copie, seules les sept sauvegardes les plus récentes **par nom** (pas par date de fichier) sont gardées dans le
   dossier ; les autres sont supprimées. Une entrée du dossier qui n'a pas la forme d'un nom de sauvegarde n'est jamais touchée.
4. `bash scripts/orchestrator.sh claim …` imprime, en plus de ce qu'il imprime déjà, une ligne `archive : aucune` quand le dossier des
   sauvegardes n'existe pas ou ne contient aucune sauvegarde, sinon `archive : <nom de la plus récente>`. Le claim n'échoue jamais pour ça.
5. Nouveau `bash scripts/archive.sh list` : une ligne par sauvegarde, la plus récente d'abord, `<nom>  <created_at>  <nombre de fichiers>`.
   `bash scripts/archive.sh restore <nom> --into <dépôt> [--force]` : vérifie d'abord chaque `sha256` du manifeste contre le contenu de la
   sauvegarde et refuse tout (code 1, ligne `restore : refusé (…)`) si un fichier manque, diffère, ou si un `path` du manifeste sort du
   dossier de la sauvegarde ou de `.workspace/` à l'arrivée (`..`, chemin absolu, lien symbolique à l'arrivée). Sans `--force`, refuse si
   un fichier à écrire existe déjà dans `.workspace/` du dépôt cible. Avec `--force`, écrase seulement les fichiers listés au manifeste,
   rien d'autre. Code 0 et ligne `restore : <n> fichiers` en cas de succès. Code 64 pour un usage invalide.
6. Même forme que le reste du kit : Python 3.9, bibliothèque standard seulement, `scripts/lib/archive.py` appelé par `scripts/archive.sh`,
   constantes nommées (pas de nombre nu), miroir `template/scripts/` et `scripts/` identiques, tests dans `tests/test_archive.py`,
   une entrée dans `docs/ORCHESTRATEUR.md` §3 point 7 et dans `docs/LOG.md`.

## Ce qui ne doit pas casser

La suite complète du kit (`sh tests/run.sh full`, 1 800 tests environ, seule l'erreur connue de `test_workspace` admise). `claim`,
`beat`, `status`, `release` gardent leurs sorties et codes actuels, la nouvelle ligne en plus. Le banc d'attaque (`attack-bench.sh`)
reste vert. Aucun fichier hors du dépôt n'est écrit ailleurs que dans le dossier des sauvegardes. `release` ne doit jamais rester bloqué
par la sauvegarde : si la copie dépasse une durée bornée (constante nommée), elle est abandonnée et traitée comme un échec.

## Preuve de réussite

Dans un dépôt temporaire avec un `.workspace/` fabriqué (événements, deux dossiers de tests cachés, un journal de délégation, un lien
symbolique vers l'extérieur) : `claim` dit `archive : aucune` ; `release` crée la sauvegarde, le manifeste liste les fichiers et le lien
dans `skipped`, le journal du dépôt a `archived` ; huit `release` de suite laissent sept dossiers, le plus ancien parti ; `archive.sh list`
les montre dans l'ordre ; `restore` dans un second dépôt vide remet les fichiers à l'identique ; `restore` sans `--force` sur un dépôt
qui a déjà un `events.jsonl` refuse ; un manifeste dont un `sha256` est faux ou dont un `path` contient `..` est refusé sans rien écrire ;
un dossier de sauvegardes non inscriptible donne `archive : échec` et le verrou est quand même rendu.

## Fichiers

`template/scripts/lib/orchestrator.py`, nouveau `template/scripts/lib/archive.py`, nouveau `template/scripts/archive.sh`, miroir
`scripts/`, `tests/test_archive.py`, `tests/test_orchestrator.py` si nécessaire, `docs/ORCHESTRATEUR.md`, `docs/LOG.md`.
