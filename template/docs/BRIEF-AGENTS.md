# Instruction for the execution agent

You carry out one task sheet, given below. The sheet is the contract: when you finish, every numbered rule and every listed
edge case holds, including what the sheet only allows ("may", "peut"): make it work and test it.

## How to work
- Read the whole sheet, then the code it touches. Follow the style of the neighbouring code. Code and identifiers in English;
  text shown to the user in the project's language.
- Test first. Write at least one test for each rule and each edge case of the sheet, through the public surface the sheet
  describes. Hidden tests that you cannot see check the same rules the same way.
- Add nothing the sheet does not ask for: no extra option, feature or abstraction. Inside what it asks, be complete, not short.
- Before you answer, reread the sheet rule by rule and check each one against your code and your tests. Fix what is missing.

## Limits
- Change only the files listed under "Périmètre autorisé". If another file must change, leave it and say so in your answer.
- Run only the sheet's verification command. Never report a test you did not run.
- No commit, push, deployment or new dependency; delete nothing the sheet does not name. Never show or copy a real secret.
- If your task uses an isolated environment, run every command with `bash scripts/run-in-sandbox.sh -- <command>` and never
  look outside your workspace.
- If the sheet leaves you blocked, ask with `bash scripts/ask.sh "<question>"`. Do not guess.

## Answer
Modified files; the verification command and its raw result; non-obvious decisions; doubts and what you did not verify.
