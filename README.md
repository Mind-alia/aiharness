# aiharness

A kit for making AI coding agents (Claude, Codex, Antigravity) work on real code **without taking their word for it**.
Every task goes through a verification circuit: the agent works in a closed container, on a scope written in advance,
and its work is merged only if it passes tests it has never seen. The kit installs into any repository (`adopt`) and
checks itself with its own circuit.

The project grew under the name Workspace IA, which the `./workspace` command and many files still use.

French documentation: [README.fr.md](README.fr.md). Most records and studies under `docs/` are in French; code,
identifiers and agent instructions are in English.

## How a task goes through the circuit

```text
request ──► task sheet ──► container ──► hidden tests ──► breaker ──► merge ──► log
 (human)    (scope,        (the agent    (written first,  (another    (only if    (cost, time,
            rules,          sees only    never shown      agent tries  all green)  attempts,
            criteria)       its worktree) to the agent)   to break it)             defects)
```

1. **Request**: what is wanted, what must not break, the proof of success (`template/docs/DEMANDE.md`).
2. **Task sheet**: the request becomes a task sheet with its scope (`allowed`) and rules (`template/docs/FICHE-TACHE.md`).
3. **Container**: the agent works in a throwaway clone, network off, with no access to the rest of the machine.
4. **Hidden tests**: written before delegation, they must fail on the starting version, or they prove nothing.
5. **Breaker**: a second agent tries to break the work; an accepted flaw sends the task back for one round of fixes.
6. **Merge**: only if everything is green on the host and the owner has approved (a cheap agent then runs the merge);
   otherwise a second attempt, then a reasoned abandon.
7. **Log**: every step is written to `.workspace/events.jsonl`; reports and the console read it.

## What we measured

The kit was built as a research harness and measured against single agents on the same tasks (studies E11 to E19 in
`docs/research/`, protocols written before the runs, hidden tests, blind reviews by another model). The short version:

- On well-specified tasks, once its own checks were fixed, the circuit passed the same hidden tests as a strong model
  working alone, sometimes cheaper when the work was already cut into precise task sheets for a small model. Early
  runs (E14, E15) delivered nothing because the circuit blocked itself.
- On a vague request, a single strong agent in Claude Code matched or beat the circuit, at a lower cost: the circuit's
  cost lies in preparing the task sheets.
- Blind reviewers often ranked the single agent's code higher: nobody holds the overall picture when work is split.

Most results come from one run per arm: read them as indications, not proofs. The follow-up project, a benchmark of
agent configurations, is [aibench](https://github.com/Mind-alia/aibench).

## Install

Requirements: macOS or Linux (WSL works), Git, **Python 3.14**, Docker, Node 24 for the console. Claude Code installed
and signed in; Codex and Antigravity are optional.

```bash
git clone https://github.com/Mind-alia/aiharness.git
cd aiharness
./workspace doctor
docker build -t workspace-python-git:3.14 -f template/docker/python-git.Dockerfile template/docker
```

`./workspace doctor` says what is missing (Python version, Node, agent tools). The kit has no Python dependency at run
time; developing the kit itself uses ruff (through `uvx`) and pyright, pinned in the `Makefile`.

## Use

```bash
./workspace adopt /path/to/a-repo                         # install the circuit in a repo (branch mission/<name>, never main)
bash scripts/prepare-task.sh --from-demande request.md    # a request becomes a task sheet and proposed hidden tests
bash scripts/delegate.sh .workspace/tasks/<id>.md         # hand the task sheet to an agent, in its container
./workspace open <project>                                # open the project's web console
```

## Develop the kit

| Command | Purpose |
|---|---|
| `make check` | everything that must be green before a merge: lint, format, type-error budget, fast suite |
| `make lint` / `make fmt` | ruff: errors, imports, likely bugs, complexity / formatting |
| `make typecheck` | pyright on the engine; the error count can only go down |
| `make test` / `make test-full` | fast suite (≈ 5 min) / full suite (≈ 13 min) |
| `make mirror` | copy `template/scripts` into `scripts/` after any change |

## Where things are

| Folder | Content |
|---|---|
| `template/` | what is copied into a project: the circuit (`scripts/`), Docker images, templates, agent rules |
| `scripts/` | mirror of the circuit for the kit itself (only `scripts/workspace.py` is its own) |
| `tools/` | programmed orchestrator (`orchestrator/`), test bench (`campaign/`), missions on foreign repos (`mission/`) |
| `tests/` | the suite, about 1,900 tests |
| `docs/` | rules (`AGENTS.md`, `docs/ORCHESTRATEUR.md`), studies (`docs/research/`), attack benches (`docs/security/`) |

## Safety

Agents run code on your machine. The circuit runs them in containers with the network off and a written scope, and an
attack bench (`docs/security/`) checks that the sandbox holds. Accounts and keys stay on each machine; nothing in this
repository contains a key.
What protects you, the limits, and how to report a problem: `SECURITY.md`.

## License

MIT for the original work in this repository (see `LICENSE`). Third-party provenance: `THIRD_PARTY.md`.
