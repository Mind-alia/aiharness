# Workspace IA kit rules

docs-check: kit

This repository is the engine: `template/` is copied into every project, `scripts/` is its mirror (the kit runs on itself), `tests/` is the suite (Python 3.14, standard library). The `docs-check: kit` line makes `scripts/docs-check.sh` check this file (80 lines at most) and the existence of `docs/LOG.md`, `docs/AMELIORATIONS.md`, `docs/QUESTIONS.md`.

## Read according to your role, nothing more

- Executor (Haiku, Sonnet, Codex): its task sheet `.workspace/tasks/<id>.md` and `template/docs/PIEGES.md`. Nothing else.
- Orchestrator: `docs/ORCHESTRATEUR.md` (single instruction file), then the last three entries of `docs/LOG.md`, `docs/TRAVAUX.md` and the day plan in `docs/journal/<date>/`.
- Human installing or discovering the kit: `README.md`.
- Past plans and reports are in `docs/journal/<date>/`, obsolete material in `docs/archive/`: open them only if the task requires it.
- Anyone creating, moving, renaming or removing a document: `docs/RANGEMENT.md` first.

## What does not change

- Python 3.14 (`.python-version`), standard library only at runtime; ruff and pyright are pinned development tools (`Makefile`, `ruff.toml`, `pyrightconfig.json`). No `pyproject.toml`: the kit is not a package, and the circuit would try to install it.
- `make check` (lint, format, type budget, fast suite) must be green before a merge; `tests/test_code_quality.py` enforces the same rules inside the suite.
- Language: code, identifiers and agent instructions in English (this file, `docs/ORCHESTRATEUR.md`, the `template/` files agents read). The owner's records stay in French: `docs/TRAVAUX.md`, `docs/QUESTIONS.md`, `docs/LOG.md`, `docs/RECHERCHE.md`, `docs/journal/`, `docs/research/`. Task sheets stay in French until T92 (`plan_check.py` and the prompts in `template/scripts/` expect French headings). Always answer the owner in French.
- No symbolic links in the repository: the circuit refuses them (script seal, worktree check).
- `scripts/` is a byte-for-byte copy of `template/scripts` (except `scripts/workspace.py`); after any change to `template/scripts`, resync: `rsync -a --exclude __tests__ --exclude __pycache__ template/scripts/ scripts/`. `tests/test_self_hosting.py` checks it.
- Never weaken a test or a threshold; a `skipUnless` on a missing tool is the only allowed skip.
- Never push, publish or delete a branch without the owner's explicit request. No secret in Git or in a task sheet (the guard also refuses variable names containing KEY, TOKEN, SECRET).

## Code structure (enforced: `ruff.toml`, `tests/test_code_quality.py`, `tests/test_code_structure.py`)

- One module, one responsibility, opened by a docstring saying what it is for; its tests in `tests/test_<module>.py`.
- Functions: complexity 10, 5 parameters and 50 statements at most; PEP 8 names (`snake_case`, `CamelCase` classes).
- Files: 800 lines at most. A file above it is split, never grown (`OVERSIZED` in `tests/test_code_structure.py`).
- The engine (`template/scripts/lib`) never imports the study tools (`tools/`); the circuit runs without them.
- No copied code: shared logic lives in one module, security primitives in `template/scripts/lib/safety.py`.
- Existing offenders carry `# noqa` or sit in a ratchet list until they are fixed (T76, T97); the numbers only go down.

## How a kit task is done

1. Task sheet in `.workspace/tasks/<id>.md` (template: `template/docs/FICHE-TACHE.md`), hidden tests in `.workspace/acceptance/<id>/tests/`, `verify_profile: off` for the kit.
2. `bash scripts/task-workspace.sh prepare .workspace/tasks/<id>.md` then `WORKSPACE_IMAGE_PYTHON=workspace-python-git:3.14 bash scripts/delegate.sh .workspace/tasks/<id>.md`.
3. The agent checks inside the container module by module (`bash scripts/run-in-sandbox.sh -- python3 -m unittest tests.test_x`); the full suite in one go exceeds the broker timeout.
4. The orchestrator reruns on the host `sh tests/run.sh fast` during the work and `sh tests/run.sh full` before merging (`python3.14`; `make` is for humans, the orchestrator's allowlist refuses it), then tells the owner the task sheet is ready; after the owner's validation (decision of 26/10), the orchestration program runs the merge (`docs/ORCHESTRATEUR.md` §9), or by hand a cheap agent (Haiku) runs `WORKSPACE_IMAGE_PYTHON=workspace-python-git:3.14 bash scripts/task-workspace.sh merge <id>`. In an already delegated worktree, run the suite with `WORKSPACE_TEST_TMP=<directory outside the repository>`.
5. A task closed `abandoned` is retried under the same id with `prepare <sheet> --retry`; the previous attempt is archived under `.workspace/orchestration/archive/`.

The orchestrator's Claude roots are declared one per line in `.workspace/metrics/claude-roots.txt` so that `bash scripts/metrics.sh --markdown` counts their tokens.

## Protected paths

`scripts/`, `project.profile` and `.workspace/` cannot be in an agent task's scope: they are the circuit's controls (`tests/` stays editable by a task, never removing a test). A change there goes through the orchestrator, outside the circuit, in a manual worktree, reviewed and tested before merging.

## Document owners (one file per fact)

| Fact | File |
|---|---|
| Kit rules | `AGENTS.md` (this file) |
| Orchestration instructions: role, who runs what, circuit, rules, plan and report | `docs/ORCHESTRATEUR.md` |
| Remaining work, steering unit | `docs/TRAVAUX.md` |
| History | `docs/LOG.md` (append only); dated plans and reports: `docs/journal/<date>/` |
| Improvements | `docs/AMELIORATIONS.md` points to `TRAVAUX.md` and `LOG.md` |
| Questions for the owner | `docs/QUESTIONS.md`, read by `scripts/questions.sh` |
| Research question; studies; attack benches | `docs/RECHERCHE.md`; `docs/research/`; `docs/security/` |
| Measured error rates of the verifier and the breaker | `docs/VERIFICATEUR-taux.md` |
| Agent team | `team.json`, identical to `template/team.json` |
| Templates shipped to projects (task sheet, request, brief, grid, pitfalls, map) | `template/docs/` |
| Housekeeping: where files go, names, retiring a document, end-of-session checklist | `docs/RANGEMENT.md` |
| Antigravity files-mode report, kept because `tools/campaign/campaign.py` prints its path | `docs/ANTIGRAVITY-SANS-INTERACTION.md` |
| Obsolete | `docs/archive/` |
