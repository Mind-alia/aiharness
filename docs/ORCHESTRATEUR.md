# Orchestrator — single instruction file

This document replaces the scattered instructions (`REPRISE-ORCHESTRATEUR.md`, `regles-orchestrateur-codex.md`, night and day instructions).
Any session that orchestrates the Workspace IA starts by reading it in full, then reads `AGENTS.md`, the last three entries of `docs/LOG.md`
and the day plan. Older documents are history, not rules.

## 1. The role

The orchestrator is a role, not a program: a model in session (Sonnet by default, Opus only for very important tasks, Codex possible, Fable only to write the plan and
review the report), a written instruction file, a written plan, and the kit's circuit. It does not code: it frames, writes the task sheets and the hidden tests,
delegates, verifies, merges, reports. The owner decides; the orchestrator proposes and proves.
The owner's decision of 25 October 2026 (T14, `docs/QUESTIONS.md`): every mechanical step a program covers (prepare, clone, delegate, wait,
rerun the tests, merge once the owner has validated it, mirror, journal, report) runs through the program (§9); models keep only the task sheet and hidden tests, the code,
the diagnosis of a failure and the targeted review of risky tasks, each at the cheapest model that is enough. The whole verification stays.

What it does itself, never delegated: the plan, the breakdown, the task sheet, the hidden tests, the report. The merge decision is the owner's (§3, step 6).
What it delegates: all code and all documentation longer than a few lines.

## 2. Who executes what

| Work | Executor |
|---|---|
| Code subject to tests, delicate logic, security, circuit scripts | Sonnet; Opus if Sonnet fails twice; Codex as a last resort |
| Small code from a precise task sheet, ordinary tests, adaptations | Haiku |
| Documentation, exploration, "other family" review, outside view | Antigravity (never to write a plan, task sheet or hidden test) |
| Independent review of a significant diff | Codex or Antigravity, three questions: regression, security, readability |

Delegation threshold: a mechanical change of a few lines with no change in behavior (move, import, text, path)
is made directly by the orchestrator, tests rerun. Any change in behavior or of more than twenty lines goes through a task sheet
and the circuit. Before each delegation, one line: "<task> → <executor>, because <reason>".

## 3. The circuit, in order

1. **Lock**: `scripts/orchestrator.sh claim --session <nom> --model <modèle> --repos <chemins> --by <lanceur>` (`--by`: who launched
   the orchestrator, for example `fable` or `owner`); `status` shows who is orchestrating and who launched it, the active delegations, the
   containers, the processes and the campaign in progress. Two orchestrators on the same repository: no. The lock is also the identity of
   the factory run: as long as it is held, every event in `.workspace/events.jsonl` carries `orchestrator` = its session.
   Heartbeat every 5 minutes: `bash scripts/orchestrator.sh beat` (the lock stays "alive" for ten minutes after the last
   heartbeat, even if its pid is not that of the session that is orchestrating; `status --repair` releases a lock with no live pid and no recent
   heartbeat).
2. **Task sheet** in `.workspace/tasks/<id>.md`: header `id, agent, mode, allowed, verify, verify_profile, attempts, acceptance,
   acceptance_verify`, then need, imposed interface, numbered rules, edge cases, conformity criteria. Explicit `allowed` scope,
   never a broad pattern. In the kit: `verify_profile: off`, and `verify` ends with `tests.test_code_quality` (ruff lint and format, run in the
   container by the pinned ruff of `workspace-python-git:3.14`, T98: `verify: python3 -m unittest tests.test_x tests.test_code_quality`). Never the full gate (`gate.sh`) as the `verify` of a task running in a container:
   it cannot run there. No variable name containing KEY, TOKEN, SECRET or PASSWORD followed by `=` in the text: the guard rail refuses.
   `agent` is an executor (`claude-sonnet`, `claude-haiku`…) or a team name (`team.json` at the root: `agent: Crash`), resolved
   to the registry's executor; an unknown name is refused. The header may carry `class: small|medium|large` (otherwise the class is
   computed) and `acceptance_min: N` with `acceptance_min_reason:` (justified waiver of the minimum of twelve hidden tests).
3. **Hidden tests** in `.workspace/acceptance/<id>/tests/`, written by the orchestrator, invisible to the executor. Before delegating:
   `bash scripts/plan-check.sh <fiche> --with-model` must return `verdict : bon` before `prepare` (judged by Sonnet; at least twelve hidden
   tests unless justified waiver), then `task-workspace.sh check <fiche>` (reservations, protected paths), then `prepare <fiche> --selfcheck`: the hidden test must fail on
   the task's base, otherwise it tests nothing (`acceptance_vacuous`).
4. **Delegation**: `env -u WORKSPACE_AGENT_ROOT WORKSPACE_IMAGE_PYTHON=workspace-python-git:3.14 bash scripts/delegate.sh <id>`.
   At most three in parallel, disjoint scopes. A medium task is not split. An abandoned id is resumed with `prepare --retry`.
   Any sub-agent launched outside the circuit is wrapped by `note.sh start` / `end`: `bash scripts/note.sh start "<libellé>" --model <m>
   --kind subagent|check|step` prints an identifier, `bash scripts/note.sh end <id> --outcome <issue>` closes it; without this, the factory
   hub does not see it. An agent's question is answered with `bash scripts/questions.sh answer <tâche>/<numéro> "<texte>" --by <auteur>`.
   An interrupted delegation (SIGTERM, SIGINT or SIGHUP) leaves `status.json` with `outcome: interrupted`, `interrupted_at`, `signal`,
   `worktree_dirty` and `dirty_files` (the task's clone counts as dirty when `git status --porcelain` lists a line or `HEAD` is not the base
   commit; the same keys go into the `outcome` event). A new delegation on a clone that is still dirty after such an interruption is refused
   before any model call (exit 1, `outcome: worktree_dirty`, no attempt counted): resume with `bash scripts/delegate.sh <sheet> --resume-dirty`
   (never with `--cascade`), which tells the model to reread the files the cut attempt left, or close the task `--abandoned` and `prepare --retry`.
   The refusal also applies to `delegate.sh --cascade`, before any stage (exit 1, `outcome: worktree_dirty`, the work kept) (GT2, merged 8/11).
5. **Verification by the orchestrator**, never on trust: in the kit, on the host, `sh tests/run.sh fast`
   during the work, `sh tests/run.sh full` before merging (both pick `python3.14`; they include the lint, format and type checks when the pinned tools of the `Makefile` are installed), `WORKSPACE_TEST_TMP` outside the repository. In the console: the full gate alone,
   `bash scripts/gate.sh`, Node 24, never two gates at the same time.
6. **Merge** (the owner's decision of 26 October 2026): after a green host verification, the orchestrator **stops and tells the owner** that the task
   sheet is ready, with its proofs (tests, diff, cost). Only after the owner's written validation is the merge run: by the program (§9) once the line
   `- fusion validée: <id>` is in the plan, or by hand by a cheap agent (Haiku) that executes exactly: `task-workspace.sh merge <id> --no-verify-again --no-integration`; in the kit, resynchronize
   the mirror `rsync -a --exclude __tests__ --exclude __pycache__ template/scripts/ scripts/` and commit. The cost line displayed at merge
   goes into the report. Then `close`. Never merge or resynchronize `scripts/` while another delegation is running: its seals
   break (`control_tampered`). Never `commit --amend` after a `prepare`.
7. **End of session**: `docker ps` empty, no orphaned `claude -p`, a single worktree per repository, the checklist of `docs/RANGEMENT.md` §8,
   `orchestrator.sh release`, report.

## 4. Non-negotiable rules

- No agent modifies `scripts/`, `project.profile`, `.workspace/`; if a task requires it, it stops and the matter is noted.
- No test removed or weakened, no threshold raised (duration thresholds 100 ms and 1 s, median of five in the console gate's `perf` step, unchanged), no symbolic link.
- No secret in a task sheet, a commit, a conversation or a report; keys stay in `~/.config/workspace-ia/secrets.env` (0600).
- Two successive failures on a task: stop the task, keep its work (full diff exported, clone closed, branch and evidence kept; T120, confirmed by the owner on 10 November 2026), continue the plan. Never three.
- A task sheet whose text changes takes the suffix `-v2` (then `-v3`…) and starts again at zero attempts; `prepare --retry` keeps the same id only for an unchanged sheet and refuses an id already failed twice (T127, the owner's decision of 10 November 2026; the refusal in `prepare --retry` is still to code). The program keeps the work as a full diff
  (`.workspace/orchestrator-run/<id>-echec.patch`) and closes the clone, evidence and branch kept, so the next sheets' scopes are free (T120);
  by hand, export the diff before any `close --force`.
- Quota reached: clean stop, report, end. No speculative waiting.
- Push, branch deletion, merge into the console's `master`, measurement campaign: only on the owner's explicit request, in writing
  in the day plan or in the owner's session. An approval holds for the day it names, not for the next one.
- The owner's decisions are written once (`docs/QUESTIONS.md`, the console's `docs/decisions.md`) and are not reopened.
- The campaign closes failures without discarding the work; the evidence folders `.workspace/run/delegate/<id>/` are never deleted:
  they are what lets a false negative be demonstrated.
- Closed workshop (the owner's decision of October 22): the executor sees only its working folder, a depth-1 clone with no
  remote outside the project tree (`WORKSPACE_WORK_DIR`, otherwise `~/.workspace-ia/work/<projet>-<empreinte>/<id>`), and its container.
  Its hook `hooks_guard.py executor` refuses the rest and logs `tool_refused`; what it lacks, it asks for
  (`ask.sh`), the orchestrator decides and answers (`questions.sh answer`). Codex runs with a `CODEX_HOME` reduced to `auth.json`,
  network cut. An executor's refusal is read in the log before blaming the model: the task sheet or the hook may be at fault.
- Orchestrator allowlist: the session that holds the lock runs only the commands admitted by `orchestrator_guard.py`
  (circuit scripts, git without network, tests, mirror, `docker ps|stop`, reading); no `rm`, no `--force` outside
  `.workspace/` (only exception: `task-workspace.sh close <id> --abandoned --force`, which applies to one task, evidence kept),
  no network outside the relays. Level 1: `cd` and `cp` to a temporary folder, `$(…)` on a read command, `if`/`!`,
  `set --` and `awk` with no write and no command are admitted; a `$…` expansion outside apostrophes is admitted only as an argument to a
  pure read command (`echo`, `cat`, `ls`, `grep`, `test`…), as an assignment value or in the header of a `for`. A refusal is not worked around: it is noted, and what is missing is asked of the owner. The owner's instruction
  of 22/10: a command that this instruction file requires and that the allowlist refuses makes the circuit widen the allowlist, never disable
  the hook. Since 22/10, the hook is active for any session that holds the kit's lock (`~/.claude/settings.json`).
- A hidden test does not borrow the methods of a fixture that the task is allowed to modify: it carries its own helpers
  (22/10: two attempts lost on `task-clone-no-history` because of a hidden test coupled to `tests/test_task_workspace.py`).

## 5. Rules learned the hard way (do not relearn)

- A wrong hidden test costs an agent attempt: the prior check (`--selfcheck`) is mandatory.
- A task sheet is never modified during its delegation: it is sealed (`control_tampered`), and its scope is frozen at `prepare`
  (a file added afterwards makes the merge refuse; only way out: `close --abandoned` then `prepare --retry`). Before `prepare`,
  look for existing tests that compare exactly what the task sheet changes and put them in `allowed` (23/10, two resumptions).
- During a delegation, watch the questions (`.workspace/orchestration/tasks/<id>/context/questions/`), not only the outcome:
  an answer given after `ask.sh` has stopped waiting is useless (23/10: `failure-cause` ended in `waiting_answer`, the answer arrived
  too late for the second attempt of `report-day-facts`).
- The exchange broker replayed all of a task's requests at each delegation (October 18 bench): a symptom outside the scope can
  come from the circuit, not from the executor. Before blaming a model, reread `scope.txt`, `answer.md`, `diff.txt`.
- Secret masking in `diff.txt` has already replaced ordinary code with `***`: a starred diff is not an agent's diff.
- Antigravity refuses every command in non-interactive mode: give it files, not commands, until this is fixed.
- A test batch run from a task worktree wrote into the kit's real registry: `WORKSPACE_TEST_TMP` outside the repository, always.
- The console gate is sensitive to load: a duration overrun under load is remeasured in the `perf` step (run alone), never raised.
- Run the attack bench from a checkout outside `/tmp` and `/private/tmp`: Codex's sandbox may read temporary directories, so a
  repository under them makes `host-codex-reads-host` report a false flaw (26/10: 27/28 from `/private/tmp`, 28/28 from `~/.workspace-ia`).
- During a bench, the targeted repository changes branch: no gate, merge or push on that repository while the bench runs.
- An unbounded wait loop `until … ; do sleep … ; done` outlives the factory run: when it exceeds the tool's timeout, Claude Code does not
  kill it, it moves it to the background, and if the condition never arrives it runs for hours (October 19: three loops of six to seven
  hours, at the owner's as at a second user's). Every wait has a written bound (`for i in $(seq 1 N)`), an exit code on overrun,
  and the factory run checks before `release` that no shell of its session survives, not only that no container does.

## 6. The plan and the report

Day plan `docs/journal/<date>/PLAN-JOUR-<date>.md`, written by Fable or the owner: numbered workstreams, executor, dependencies, waves, expected proof,
and what is authorized that day (push, campaign). The orchestrator does not invent workstreams: "moving forward" means delivering the list.
The day plan is judged by `bash scripts/plan-check.sh --plan <plan>` (Opus).

The owner's rule of October 22: before the first delegation, the orchestrator gives the owner the path of the day plan and a ten-line
summary (workstreams, executors, what is authorized), and waits for the owner's written approval: a message in the owner's session, or "read and accepted"
in the plan. Without approval, it prepares the task sheets and the hidden tests but does not delegate. Exceptions: a factory run that the owner launched
directly while supplying the plan, or a night the owner authorized in writing in advance. Open questions always go through
`docs/QUESTIONS.md` and `questions.sh`.

The owner's rule of October 22 (2): a blocking and important question, whether it comes from the orchestrator or from an executor (outcome
`waiting_answer`), goes up to the owner, even if production slows down or stops: entry in `docs/QUESTIONS.md`, message to the
parent session when one exists, and the task waits instead of the orchestrator deciding in the owner's place. Only a relevant question
goes up: the task sheet, the instruction file and the repository do not answer it, and the rest of the work depends on it; the orchestrator answers by itself
what falls within its role. A minor decision stays with
the orchestrator and is listed in the report. An answer to an executor is given while `ask.sh` is waiting: given afterwards, it does not reach
the resumption (22/10, `class-execution`, three questions repeated).

Report `docs/journal/<date>/RAPPORT-JOUR-<date>.md`, same structure every time: at the top, the line "Défauts trouvés après fusion cette semaine : n"
(`bash scripts/report.sh --defauts`; a defect is noted with `bash scripts/note.sh defect "<libellé>" --task <id>`); table of workstreams
with commit and proof; delegations with
executor, outcome, attempts, duration, tokens; defects encountered and decisions taken in the owner's place, with the reason; "Banc" section
if there was a measurement; tokens by provider and by role (`bash scripts/metrics.sh --markdown`); state of the repositories (`git status`, gap with
GitHub); what the orchestrator would do next. Every claim rests on a figure or a command; what is not verified is
stated as unverified. Before being committed, the report goes through `bash scripts/report-check.sh <rapport>` (verifier from another family, in
read-only mode, three planted false claims (decoys), verdict and cost added at the top). Entry in `docs/LOG.md`; delivered work leaves
`docs/TRAVAUX.md` (the list keeps only what remains to be done).

The factual part of the day report (task sheets, attempts, costs by role, questions, attack bench) starts from
`bash scripts/report.sh --day <date> --usine <nom>`: it comes from the circuit's logs. The model does not touch the blocks between
`<!-- généré: début -->` and `<!-- généré: fin -->`; it writes only `## Décisions` and `## Constats` (and what follows). The report then goes
through `report-check.sh`.

Final answer to the owner: ten lines. Delivered, not delivered and why, tests, push, decisions taken in the owner's place. The details are in the report.

Night roles (T137, the owner's decision of 10 November 2026): the night watcher applies the plan and decides; the plan's author (Fable) proposes and never runs a circuit command by hand while the program runs. Day plans (decision of 10 November 2026): one program at a time, the owner alone watches and writes the `- fusion validée` lines, warned at each `PRET` with the proofs; the spending cap is the sheet's own (two attempts). Freeze (10 November 2026): no new mechanism in the circuit before two sessions succeed end to end; only fixes of observed defects, by short task sheet.

## 7. Readings and weekly report

- Claude quota: `./workspace statusline-install` (also run by `./workspace setup`) sets Claude Code's status bar
  (`~/.claude/settings.json`) to `scripts/statusline.sh`, which displays the model and the 5 h / weekly windows and appends each reading to
  `~/.workspace-console/claude-statusline.jsonl` (timestamp and two windows only, 0600 file, read by the console). Another
  status bar already configured is kept; `--replace` replaces it.
- Before a bench, `campaign.py` checks the quota of each provider in the bench and refuses to start (code 75) if one is dry;
  `--ignore-quota` to override.
- Weekly report: `bash scripts/weekly-report.sh` writes `docs/RAPPORTS/semaine-<date>.md` (defects after merge, cost by
  provider, benches and reference diffs, work removed from the table) then passes it to `report-check.sh`. Claude Code's scheduled tasks do not
  run outside a session: launch it on Sunday evening via `cron` (`crontab -e`):
  `0 21 * * 0 cd <chemin du kit> && bash scripts/weekly-report.sh >> .workspace/weekly-report.log 2>&1`.
- Written request → task sheet: `bash scripts/prepare-task.sh --from-demande <demande.md> <id> --executor <agent>` (template
  `template/docs/DEMANDE.md`) writes a proposal in `.workspace/proposals/<id>/`, never a delegable task; reread it before
  copying it into `.workspace/tasks/` and `.workspace/acceptance/`.

## 8. Skills

- Check of a candidate Claude Code skill before the owner reads it: `bash scripts/skill-check.sh <dossier>` (`--trust` only).
- Three categories of patterns, case-insensitive: `écriture` (out of scope, symbolic links included), `réseau`
  (download, HTTP calls, sockets), `secret` (credential files, words such as token or password); `structure` if `SKILL.md` is missing,
  if a file exceeds 1 MB or is not UTF-8.
- Verdict on the last line: `verdict : refus (…)` (code 1, no evaluation run) or `verdict : accepté …` (code 0); invalid usage: 64.
- Pattern found → refusal. Otherwise, an `evals/` suite runs `claude plugin eval --no-publish` in a temporary folder, with `--trust-plugin`
  only if `--trust` is given; timeout `SKILL_CHECK_TIMEOUT` (1800 s by default).
- `--trust` is only for a skill that the owner has read in full. The check installs, copies and publishes nothing; the folder stays identical.

## 9. Orchestration program (T14)

- Command: `python3 tools/orchestrator/run.py <plan> --detach [--root <projet>] [--session S] [--by B] [--model M]`; the program detaches from the window (output in `.workspace/orchestrator-run/run.log`). No orchestration model stays open: `scripts/delegate.sh` drives the executors, and the program itself calls a model only for a failure diagnosis (Sonnet) and a targeted review (Opus), both read-only, declared with `note.sh` and counted in the consumption log. Every Claude call in that log keeps the dollars the provider reports (`cost_usd`, from `total_cost_usd`); `report.sh --task` adds them per role, and the program's report adds, for each task sheet, every call since the start of the run (all delegations and attempts, diagnosis, reviews), in tokens and dollars (T102).
- Plan: `.json` or Markdown file; `session:`, `by:`, `model:`, `verify:`, `image:`, `report:`, `report_check:`, `resync:`, `commit_report:`, `diagnostic:` (default `oui`), `correcteur:` (executor of a correction round, default `claude-haiku`). One line per task sheet: `- fiche: <chemin>`, or `- demande: <chemin> | id: <id>` for a written request. After a `|`, the line says how the work is split: `vague: N` (task sheets of one wave are delegated together, three at most, disjoint scopes; without `vague`, one task sheet after the other), `exécutant: claude-haiku|claude-sonnet|claude-opus` (replaces the task sheet's executor), `relecture: opus` (targeted review). The orchestrator model writes this split in the plan; the program applies it.
- The owner's decisions are written in the plan, during or between passes, and do not change its digest: `- fusion validée: <id>` and `- proposition acceptée: <id>`.
- Steps for each wave: each task sheet is prepared in turn (`prepare --selfcheck`), then delegated (together when the wave has several); then for each one, `verify` on the host in a disposable clone, the targeted review if asked, and the merge decision. No merge or mirror copy happens while a delegation of the wave runs.
- Host verification (T115, T117): it gets a whitelisted environment (`steps.verify_env`: paths, language, Git identity, Docker access; no `WORKSPACE_*`, `ORCHESTRATOR_*` or secret-like name), never the launcher's. A red verification that is not a known quality failure is run again on the task's base (once per base and pass) and classified: red on the base too is the environment (no failure counted; the plan stops with `ENVIRONNEMENT-<id>.md` and a line in `docs/QUESTIONS.md`; once the environment is fixed, the rerun replays the same verification, no new delegation); new red tests all in existing test files outside `allowed`, untouched by the task and outside the quality suite, are the sheet (no failure counted; `FICHE-<id>.md` and a question; a sheet is fixed by `close --abandoned` then `prepare --retry`); anything else is the executor's and counts.
- Merge (the owner's decision of 26/10): the program stops before each merge and writes `.workspace/orchestrator-run/PRET-<id>.md` (files changed, verification, review verdict, diff, cost). Once the owner has written `- fusion validée: <id>` in the plan, the rerun launches `task-workspace.sh merge <id> --no-verify-again --no-integration` itself, copies the mirror and commits. Nothing in the code changes after the owner's validation.
- Failure: a first failure is diagnosed by Sonnet from the red test blocks of the run's logs, in one turn (`diagnostic-<id>.md`, T119; when the model fails, the retry still gets those blocks as they are), given to the retry as stage context (`WORKSPACE_STAGE_CONTEXT`); the task is abandoned and resumed once (`prepare --retry`), unless the diagnosis's last line `CAUSE: fiche|environnement|circuit` blames something other than the executor (E15, 12/11): then nothing is counted and there is no retry, the plan stops with `CIRCUIT-<id>.md` (circuit) or `DIAGNOSTIC-<id>.md` (sheet, environment) and a line in `docs/QUESTIONS.md`, and the rerun replays the failed step on the same clone (a missing or unreadable `CAUSE:` line means `executant`); a second failure marks it failed, its diff exported and its clone closed (T120). One counter per task sheet: a blocking review counts like a failure. A verification whose only red tests are the quality suite's (`tests/test_code_quality.py`) gets no diagnosis (T99, T114, T118): format alone is fixed by the program (`ruff format`, version pinned like the suite, on the task's Python files in its clone, verification rerun, no failure counted); lint goes, with the tool's output as is, to a correction round of the same clone by the `correcteur`, which counts as a failure; a type budget over the limit goes once to such a round with the pyright errors of the task's files and no failure counted (pyright is in the image since T116), a second time it counts like the lint.
- Targeted review (`relecture: opus`): after a green verification, Opus reads the task sheet, the diff and the proofs, working in the task's clone and read-only (tools Read, Glob, Grep only; no settings, hooks or MCP servers of the working folder, which the executor could write). A failed review (`error_max_turns`…) is listed in the report's defects and is done by hand. Its prompt carries the whole content of the diff's new files (caps `NEW_FILE_LIMIT` and `NEW_FILES_LIMIT` in `review.py`) and the circuit primitives to reuse (`workdir.py`, `safety.py`), for a verdict in one or two turns (T112). `VERDICT: bon` lets the task sheet through; `VERDICT: à corriger` sends its corrections (`relecture-<id>.md`) to a correction round of the same task by the `correcteur`, then everything is verified and reviewed again; that second review only gets the round's diff and the list of corrections it asked for, and says whether each one is done (T113). Corrections and diagnoses are masked with the circuit's `secrets.mask_secrets` before they enter a prompt, so `delegate.py` does not refuse the round for a quoted assignment (T100).
- Executor question (`waiting_answer`): the question goes into `docs/QUESTIONS.md` (committed, text masked) and the circuit's alert; the plan stops after the current step, without any merge. Answer with `questions.sh answer`, rerun: the task resumes with the answer.
- Request (`- demande:`): `prepare-task.sh --from-demande` writes a proposal, `plan-check.sh --with-model` must say `bon`, the program writes `PROPOSITION-<id>.md` and waits for `- proposition acceptée: <id>`; it then copies the task sheet and the hidden tests into `.workspace/tasks/` and `.workspace/acceptance/` and continues like any task sheet.
- Exit codes: 0 finished, 1 a task sheet failed, 2 error, 3 lock or pass held, 4 waiting for the owner (merge to validate, proposal to accept, question, environment or sheet stop), 64 usage or plan, 75 quota, 143 SIGTERM. A stop that waits for the owner writes the report only; the report check, its commit and the `docs/LOG.md` entry (appended, committed) come at the real end.
- Resume log: `.workspace/orchestrator-run.json`. After `kill -9` or `SIGTERM`, rerun the same command: the program waits for the children left running without killing them (all of a wave), rereads the circuit's actual state and resumes at the current step; a finished plan rerun does nothing, a plan modified during a pass is refused (decision lines excepted).
- Task states (GT2, merged 8/11): each task in `.workspace/orchestrator-run.json` carries a `state` and a dated `transitions` list (`{at, from, to, reason}`). Six states: `queued`, `in_progress`, `blocked`, `awaiting_user`, `done`, `failed`; allowed transitions `queued → in_progress`; `in_progress → awaiting_user | blocked | done | failed`; `awaiting_user → in_progress`; `blocked → in_progress`; `done` and `failed` are final. The first step of a task makes it `in_progress` (reason: the step name); a validation, an acceptance or an executor question makes it `awaiting_user`, and the pass that resumes the work moves it back (`validation reçue`, `proposition acceptée`, `réponse reçue`); a second failure makes it `failed` (reason: the cause); the mirror step ends it `done` (`fusionnée`); a stop by quota, signal or error makes every `in_progress` task `blocked`, and the next pass resumes them (`reprise`). A pass that changes nothing writes no transition; each effective transition also emits an orchestrator `note` event with `detail.run = "state"`, `task`, `from`, `to`, `reason`; a journal written before this version reads as `queued`. `python3 tools/orchestrator/run.py <plan> --status [--root <project>]` is read-only (no claim, nothing written): it prints `plan : <plan> ; passage : <status> (<stop_reason or en cours>) ; reprises : <n>`, then one tab-separated line per task in plan order (`<id> <state> <last step or -> <last transition time or -> <last reason or ->`); without a journal `aucun passage pour <plan>`, with another plan's journal `journal d'un autre plan : <path>`, both exit 0; `--status` with `--detach` or a missing plan exits 64.
- Lock: `scripts/orchestrator.sh claim` at start and a regular heartbeat; an interruption leaves the lock in place and the rerun takes it back (`--force`) only if it carries the same session; it is released at the end, on quota and on a stop that waits for the owner.
- What remains for the orchestrator model: write the plan (with its split into waves and executors), the task sheets and the hidden tests, then read the notices and the report.

## 10. Missions for a foreign repository (preparation of the "freelance mission" test)

- Adoption: `python3 scripts/workspace.py adopt <url-ou-chemin> [--dir <dossier>] [--name <nom>]` clones the repository into
  `.workspace/missions/<nom>/repo/`, creates the branch `mission/<nom>` (the main branch is never written), detects the profile
  (`node`, `python`, `docker`, `c`, `docs`), installs the circuit in a single commit on the mission branch and writes
  `.workspace/missions/<nom>/mission.json` (the repository's gate — `make test`, `npm test` or `python3 -m pytest` — as the default `verify`).
  No tracked file is modified: additions to an existing `.gitignore` or `.gitattributes` go into `.git/info/`. Tasks are
  prepared and merged from `repo/`. Warning: a delivery to the client will have to remove the circuit commit (T45, to do).
- C profile: image `workspace-c-norm:bookworm` (`docker build -t workspace-c-norm:bookworm -f template/docker/c-norm.Dockerfile
  template/docker`, `WORKSPACE_IMAGE_C` to replace it); gate: `make`, then `make test` if the target is declared, then
  `norminette` on the `*.c`/`*.h` outside hidden folders.
- Design office: `bash scripts/prepare-task.sh --from-brief <brief.md> <nom>` (template `template/docs/BRIEF.md`) writes
  `.workspace/missions/<nom>/cadrage.md` (questions, "When…, then…" criteria, estimate, plan) without building anything; after
  `reponses.md`, `--with-answers --executor <agent>` goes through `--from-demande` and writes proposals to be reread.
- Scoring grid: `bash scripts/report.sh --mission <nom>` pre-fills `.workspace/missions/<nom>/grille.md` (template
  `template/docs/GRILLE-MISSION.md`); the human judgment is still to be filled in; euros require `docs/TARIFS-API.json`, written by a
  human (the kit ships no rates).

## 11. Reconstructed history

Reconstructed history: labeled, never counted in a study.
Command: `python3 tools/mission/reconstruct_events.py <depot> --out <fichier> [--branch <nom>]`.
Sources: Git history of the main branch, local task branches and reports
`docs/design/RAPPORT-*.md` and `docs/journal/**/RAPPORT-*.md` read from Git.
Each event carries `reconstructed: true`; missing values stay "non mesuré".
The output must stay outside `.workspace/`; the source repository is only read.
