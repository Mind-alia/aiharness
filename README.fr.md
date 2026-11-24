# aiharness

Un kit pour faire travailler des agents IA (Claude, Codex, Antigravity) sur du vrai code **sans leur faire confiance sur parole**.
Chaque tâche passe par un circuit de vérification : l'agent travaille dans un conteneur fermé, sur un périmètre écrit d'avance,
et son travail n'est fusionné que s'il passe des tests qu'il n'a jamais vus. Le kit s'installe dans n'importe quel dépôt
(`adopt`) et se vérifie avec son propre circuit. Comptes et clés restent sur chaque poste. Protections, limites et signalement d'une faille : `SECURITY.md` (en anglais).

Le projet s'appelait Workspace IA pendant son développement : la commande `./workspace` et beaucoup de fichiers gardent ce nom.

English: [README.md](README.md).

## Comment une tâche traverse le circuit

```text
demande ──► fiche ──► conteneur ──► tests cachés ──► casseur ──► fusion ──► journal
 (humain)  (périmètre,  (l'agent ne    (écrits avant,   (un autre    (seulement    (coût, durée,
           règles,      voit que son   jamais montrés   agent essaie  si tout est   tentatives,
           critères)    worktree)      à l'agent)       de casser)    vert)         défauts)
```

1. **Demande** : ce qu'on veut, ce qui ne doit pas casser, la preuve de réussite (`template/docs/DEMANDE.md`).
2. **Fiche** : la demande devient une fiche de tâche avec son périmètre (`allowed`) et ses règles (`template/docs/FICHE-TACHE.md`).
3. **Conteneur** : l'agent travaille dans un clone jetable, réseau coupé, sans accès au reste de la machine.
4. **Tests cachés** : écrits avant la délégation, ils doivent échouer sur la version de départ, sinon ils ne prouvent rien.
5. **Casseur** : un second agent cherche à faire tomber le travail ; une faille retenue renvoie la tâche en correction (une ronde).
6. **Fusion** : seulement si tout est vert sur la machine hôte, et après la validation du propriétaire (un agent bon marché lance alors
   la fusion) ; sinon une seconde tentative, puis abandon motivé.
7. **Journal** : chaque étape est écrite dans `.workspace/events.jsonl` ; les rapports et la console le lisent.

## Installer

Prérequis : macOS ou Linux (WSL accepté), Git, **Python 3.14**, Docker, Node 24 pour la console. Claude Code installé et
connecté ; Codex et Antigravity facultatifs.

```bash
git clone https://github.com/Mind-alia/aiharness.git
cd aiharness
./workspace doctor
docker build -t workspace-python-git:3.14 -f template/docker/python-git.Dockerfile template/docker
```

`./workspace doctor` dit ce qui manque (version de Python, Node, outils des agents). Le kit n'a aucune dépendance Python à
l'exécution ; pour développer le kit lui-même, ruff (via `uvx`) et pyright sont utilisés aux versions fixées dans le `Makefile`.

## Utiliser

```bash
./workspace adopt /chemin/vers/un-depot                  # installer le circuit dans un dépôt (branche mission/<nom>, jamais main)
bash scripts/prepare-task.sh --from-demande demande.md   # une demande devient une fiche et des tests cachés proposés
bash scripts/delegate.sh .workspace/tasks/<id>.md        # confier la fiche à un agent, dans son conteneur
./workspace open <projet>                                # ouvrir la console web du projet
```

## Développer le kit

| Commande | Rôle |
|---|---|
| `make check` | tout ce qui doit être vert avant une fusion : lint, format, budget d'erreurs de types, suite rapide |
| `make lint` / `make fmt` | ruff : erreurs, imports, bugs probables, complexité / mise en forme |
| `make typecheck` | pyright sur le moteur ; le nombre d'erreurs ne peut que baisser |
| `make test` / `make test-full` | suite rapide (≈ 5 min) / complète (≈ 13 min) |
| `make mirror` | recopier `template/scripts` dans `scripts/` après toute modification |
| `make clean` | effacer les caches laissés par Python, ruff et les tests (jamais un fichier source ; pas pendant une suite de tests) |

Les mêmes règles sont vérifiées dans la suite (`tests/test_code_quality.py`) : un import inutile ou une fonction trop
complexe rend la suite rouge. Les fonctions complexes déjà là portent `# noqa: C901` jusqu'à leur découpage (T76).
À chaque commit, `pre-commit` (installé le 27/10) relance ruff sur les fichiers Python du commit et refuse un fichier
mal écrit ou mal formaté ; les commits du circuit ne passent pas par ce crochet.

## Où est quoi

| Dossier | Contenu |
|---|---|
| `template/` | ce qui est copié dans un projet : circuit (`scripts/`), images Docker, gabarits, règles des agents |
| `scripts/` | miroir du circuit pour le kit lui-même (seul `scripts/workspace.py` lui est propre) |
| `tools/` | orchestrateur programme (`orchestrator/`), banc d'essai (`campaign/`), missions sur dépôt étranger (`mission/`) |
| `tests/` | la suite, environ 1 900 tests |
| `docs/` | voir le tableau ci-dessous |
| `.workspace/` | état vivant du circuit (journal, fiches, tests cachés, preuves), ignoré par Git |

| Pour… | Lire |
|---|---|
| comprendre les règles du dépôt | `AGENTS.md` |
| savoir ce qui reste à faire | `docs/TRAVAUX.md` |
| savoir ce qui a été fait, et quand | `docs/LOG.md` |
| les questions en attente du propriétaire | `docs/QUESTIONS.md` |
| la question de recherche et les études | `docs/RECHERCHE.md`, `docs/research/` |
| orchestrer (consigne des agents, en anglais) | `docs/ORCHESTRATEUR.md` |
| ranger la doc et le projet | `docs/RANGEMENT.md` |

La console web (dépôt séparé `workspace-console`) affiche le journal de chaque dépôt déclaré dans son
`.workspace/circuit-roots.txt`. Provenance et licences tierces : `THIRD_PARTY.md`.
