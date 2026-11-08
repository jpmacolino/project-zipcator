---
description: Run all quality gates, then an independent QA review of the current phase
argument-hint: "[phase number or focus area]"
---
1. Run `uv run python scripts/check.py` and fix anything trivially broken first.
2. Use the `qa-reviewer` subagent to review phase/focus: $ARGUMENTS
   Give it the phase number, the spec sections in scope, and the list of files
   changed in this phase (`git diff --stat` against the previous phase commit).
3. Triage every finding: fix it with a regression test, or record in
   `docs/QA_REPORT.md` why it is deferred. Re-run the gates.
4. Append a short phase entry to `docs/QA_REPORT.md` (findings, fixes, gate results).
5. Commit each fix separately as you go, then commit the QA report update and
   run `git tag phase-<N>`.
