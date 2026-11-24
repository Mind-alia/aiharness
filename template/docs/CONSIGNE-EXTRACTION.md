# Claim extraction instruction

You extract verifiable claims from a work report. You do not judge whether they are true.

A verifiable claim is a short sentence stating a fact that can be checked in files, Git or journals:
a number (tests, duration, tokens, lines, score), a state (merged, pushed, clean, failed, stopped), an identifier (commit, task, file),
an asserted cause ("the cause is..."), an order of events. Ignore opinions, recommendations and future plans.

Rules:
- One claim = one single fact. Split sentences that contain several.
- Reuse the report's words (faithful quotation, at most 40 words) and keep numbers and identifiers exact.
- Cover the report's summary and tables, from start to finish. Aim for completeness on numbers, commits and causes.
- Output: one JSON line per claim, nothing else (no text before or after, no code block):
{"rapport": "<file name>", "section": "<section title>", "citation": "<exact text>", "type": "chiffre|etat|identifiant|cause|ordre"}
