#!/usr/bin/env bash
# Documentation check (closing ritual). Exit 0 only if the docs are in order.
set -u
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
cd "$(dirname "$0")/.." || exit 2
fail=0
err() { echo "DOCS FAIL: $*"; fail=1; }

# Kit mode: a project declaring `docs-check: kit` in AGENTS.md has no STATUS or plans, only these documents.
if grep -qx 'docs-check: kit' AGENTS.md; then
  [ "$(wc -l < AGENTS.md)" -le 80 ] || err "AGENTS.md longer than 80 lines"
  for doc in docs/LOG.md docs/AMELIORATIONS.md docs/QUESTIONS.md; do
    [ -f "$doc" ] || err "$doc missing"
  done
  [ $fail -eq 0 ] && echo "docs: OK"
  exit $fail
fi

# 1. Local Markdown links resolve and docs/LOG.md only grows (prints its own DOCS FAIL lines).
"${WORKSPACE_PYTHON:-python3}" scripts/lib/docs_links.py . || fail=1

# 2. Every finished plan has its slice spec, journal, log entry and changelog mention.
for plan in docs/plans/done/SLICE-*-plan.md; do
  [ -e "$plan" ] || continue
  id=$(basename "$plan" -plan.md)             # SLICE-01
  num=${id#SLICE-}                             # 01
  [ -f "docs/slices/$id.md" ] || err "$id: missing docs/slices/$id.md"
  [ -f "docs/journal/slice-$num.md" ] || err "$id: missing docs/journal/slice-$num.md"
  grep -qiE "(slice|tranche) $num" docs/LOG.md || err "$id: no entry in docs/LOG.md"
  grep -qiE "(slice|tranche) $num" CHANGELOG.md || err "$id: not mentioned in CHANGELOG.md"
  grep -qE "^\| *$num *\|.*(Done|Terminée)" docs/STATUS.md || err "$id: not marked Terminée in docs/STATUS.md"
done

# 3. A plan cannot be both active and done.
for plan in docs/plans/active/SLICE-*-plan.md; do
  [ -e "$plan" ] || continue
  [ -e "docs/plans/done/$(basename "$plan")" ] && err "$(basename "$plan") is both active and done"
done

# 4. STATUS must be dated and the rules file must stay short.
grep -qE "^Last updated: [0-9]{4}-[0-9]{2}-[0-9]{2}" docs/STATUS.md || err "docs/STATUS.md has no 'Last updated' date"
[ "$(wc -l < AGENTS.md)" -le 80 ] || err "AGENTS.md longer than 80 lines: move detail to docs/"

[ $fail -eq 0 ] && echo "docs: OK"
exit $fail
