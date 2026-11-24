# Map

`PROJECT.md`: goal and criteria; `AGENTS.md`: rules; `docs/STATUS.md`: status; `docs/LOG.md`: history, append-only; `docs/QUESTIONS.md`: questions to the owner; `docs/PIEGES.md`: known pitfalls; `docs/plans/`: plans to create.

## `.workspace/`: the circuit's state, ignored by Git

| Folder or file | Contents |
|---|---|
| `events.jsonl` | the event journal, single source; everything else is rebuilt from it (`report.sh --rebuild`) |
| `tasks/<id>.md` | the task sheets |
| `acceptance/<id>/tests/` | the hidden tests, copied into the container at acceptance time only |
| `worktrees/<id>/` | each task's working copy |
| `run/delegate/<id>/` | the evidence: answer, diff, status, journals |
| `orchestration/` | rebuilt views: task registry, orchestrator lock, resume journal |
| `missions/<nom>/` | adopted foreign repositories: `repo/`, `mission.json`, `cadrage.md` |
| `report-check/`, `metrics/`, `breaker/` | report reviews, token readings, breaker results |
| `circuit-roots.txt` | console only: the repositories whose circuit it reads, the kit first |

Any other folder (`campaign*`, `tmp-tests`, `run/lot*`, `run/gemini`) is a leftover from trials and can be deleted safely.
