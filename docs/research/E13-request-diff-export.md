# Request — export a task's diff before merge (E13, T89)

Written by Fable on 25 October 2026 for study E13. Deliberately in English: the study keeps the working language constant across its
arms (every instruction, exchange and report between agents is in English). Given word for word to every arm. The interface is binding:
hidden tests depend on it. Whatever this request leaves open is the implementer's choice and must be justified in the final message.

## Need

The circuit has no command that shows what an executant produced before `task-workspace.sh merge`. On 25 October an orchestrator lost
18 minutes building that diff by hand and left a stray worktree and a stray branch behind. I want one command that writes the complete
diff of a task's clone, against the task's base commit, to a file under `.workspace/`, so that a reviewer can read it, without ever
changing the clone.

## Binding interface

1. `bash scripts/task-workspace.sh diff <id> [--out <path>]`: new subcommand, same entry point as `prepare|check|status|merge|close`.
   Usage error (missing id, unknown option, extra argument): exit code 64, the usage line on stderr, nothing written.
2. Task lookup: `.workspace/orchestration/tasks/<id>/state.json`, fields `worktree` (absolute path of the clone), `base_commit`, `status`.
   Refused, exit code 1, one line `diff : refusé (<reason>)` on stdout and nothing written, when: the state file is missing or unreadable;
   `status` is `merged` or `abandoned`; `worktree` is missing or relative, is not the path the circuit computes for that task
   (`workdir.task_worktree`), does not exist, or is not a Git repository; `base_commit` is missing or unknown in the clone.
3. Content: the difference between `base_commit` and the clone's **working tree**: tracked files modified or deleted, and untracked files
   that are not ignored (`.gitignore` applies), that is the set `git status --porcelain=v1 --untracked-files=all` lists. Nothing under
   `.git/`. Unified format as `git diff` produces, with `--binary`, so binary files are carried in full. Applying the file with `git apply`
   to a clean checkout of `base_commit` must reproduce the clone's working tree byte for byte (tracked, untracked and binary files alike).
4. The clone is never modified: HEAD, the staged content (`git ls-files -s`), the working tree and the set of untracked files are identical
   before and after. No `add`, `commit`, `stash`, `checkout`, `reset` or `update-index` on the clone's own index; a temporary index outside
   the clone is allowed if it is removed afterwards.
5. Symbolic links: if a symbolic link exists anywhere in the clone (outside `.git/`), the command refuses: exit code 1,
   `diff : refusé (lien symbolique : <path relative to the clone>)`, nothing written. The content of the link's target must never appear in
   any file the command writes.
6. Output file: by default `.workspace/orchestration/tasks/<id>/diff/<YYYYMMDDTHHMMSSZ>.patch` (UTC time of the run; if the name exists,
   suffix `-2`, `-3`…), directory created with mode `0700` if missing, file written with mode `0600`, atomically (temporary file then
   rename: no partial file on error). With `--out <path>`: the path, resolved from the caller's current directory, must be inside the
   project's `.workspace/` directory after symlink resolution, otherwise refused `diff : refusé (chemin hors de .workspace)`; a missing parent
   directory is created (mode `0700`); `--out` never overwrites: an existing file is refused `diff : refusé (fichier existant)`.
7. Empty difference: exit code 0, one line `diff : vide`, no file written, no event.
8. Success: exit code 0, one line `diff : <n> fichiers, <absolute path of the file>` where `<n>` is the number of paths in the patch. An event
   `diff_exported` is appended to `.workspace/events.jsonl` with `task_id` = `<id>` and `detail` = `{"path": <absolute path>, "files": <n>,
   "base_commit": <sha>}`. No event on refusal or on an empty difference.
9. Running the command twice on an unchanged clone yields two files with identical content.
10. Same form as the rest of the kit: Python 3.9, standard library only, the logic in `template/scripts/lib/` (a new module), no file over
    800 lines, named constants (no bare number), mirror `template/scripts/` → `scripts/` identical, tests in `tests/test_task_diff.py`, the
    subcommand documented in `docs/ORCHESTRATEUR.md` §3 (between delegation and merge) and an entry in `docs/LOG.md`. The command must be
    accepted by the orchestrator's whitelist (`orchestrator_guard.py`) when run from the project root as
    `bash scripts/task-workspace.sh diff <id>`: add a test proving it.

## What must not break

The full suite (`sh tests/run.sh full`, about 1 900 tests; only the known error
`test_workspace.StarterTests.test_sources_are_pinned_and_bundled_plugin_matches_manifest` is accepted). `prepare`, `check`, `status`, `merge`
and `close` keep their outputs and exit codes. The event vocabulary is frozen by a test: extending it is part of the work. Nothing is written
outside the project's `.workspace/` (a temporary index, if any, is removed).

## Proof of success

In a temporary project whose `scripts/` is a copy of `template/scripts/` and whose `.workspace/orchestration/tasks/t/state.json` points to
a fabricated clone (a Git repository with one base commit, a modified file, a deleted file, a new untracked file, a new binary file and an
ignored file): `diff t` prints `diff : 4 fichiers, …`, the patch applied to a clean checkout of the base reproduces the tree, the clone is
unchanged, the event is in the journal; the same clone without changes prints `diff : vide`; a link in the clone is refused; `--out /tmp/x.patch`
is refused; `--out .workspace/x.patch` is written with mode `0600`; `diff` alone exits 64; `diff unknown` exits 1 with `diff : refusé`.

## Files

`template/scripts/lib/mission.py` (dispatch), new `template/scripts/lib/task_diff.py`, `template/scripts/lib/events.py`,
`template/scripts/task-workspace.sh` (usage line), mirror `scripts/`, `tests/test_task_diff.py`, `tests/test_events.py`, a whitelist test
(`tests/test_guard_level1.py` or a new file), `docs/ORCHESTRATEUR.md`, `docs/LOG.md`.
