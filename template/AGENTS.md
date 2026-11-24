# __PROJECT_NAME__ project rules

Read `PROJECT.md` (goal, criteria) and `docs/STATUS.md` (state) before working. `docs/MAP.md` says where everything is, including inside `.workspace/`.

## Who reads what

- Executor: its task sheet `.workspace/tasks/<id>.md` and `docs/PIEGES.md`. It only modifies the `allowed` scope of its task sheet.
- Orchestrator: `docs/ORCHESTRATEUR.md` of the Workspace IA kit, single instruction file (role, who runs what, circuit, rules). The team is described in `team.json`.
- No rule is stored in `.claude/rules/`: a README there points back here.

## Rules

- Clarify the goal and the success criteria before developing.
- Code, identifiers and agent instructions in English; user interface and the owner's records in the owner's language; answer the owner in that language.
- Tests first. Never announce a check that was not run; never weaken a test or a threshold.
- No secret in Git or in a task sheet; local settings are ignored by Git.
- Do not push, publish, deploy or delete without the owner's explicit request.
- `scripts/`, `project.profile` and `.workspace/` belong to the circuit: it refuses them in a task's scope.
- Document the real state and the limits; the owner decides what comes next.
- Creating, moving or removing a document follows `docs/RANGEMENT.md` of the Workspace IA kit.
