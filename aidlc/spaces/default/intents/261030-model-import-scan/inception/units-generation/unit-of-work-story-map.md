# Unit and Story Map

One row for each story in `stories.md`. A unit is named on a row only when it implements part of that story.

| Story | Unit ID | Directory |
|-------|---------|-----------|
| US1.1 | U1; U2 | u1-import-slice; u2-import-rules |
| US2.1 | U1; U2 | u1-import-slice; u2-import-rules |
| US3.1 | U1 | u1-import-slice |
| US4.1 | U1; U2 | u1-import-slice; u2-import-rules |
| US5.1 | U1; U2 | u1-import-slice; u2-import-rules |
| US5.2 | U1; U2 | u1-import-slice; u2-import-rules |
| US6.1 | U2 | u2-import-rules |

## How the Stories Split

| Story | U1 implements | U2 implements |
|-------|---------------|---------------|
| US1.1 | AC1.1.1: a valid event starts one import | AC1.1.2 a bad event is rejected, AC1.1.3 a duplicate is refused, AC1.1.4 a second event waits |
| US2.1 | AC2.1.1: download into the holding area; AC2.1.2 and AC2.1.3: a missing or interrupted download fails the import | AC2.1.4: the import of a smaller model ends inside the 6-hour limit |
| US3.1 | All of AC3.1.1 to AC3.1.5 | None |
| US4.1 | AC4.1.1 and AC4.1.3: clean files are kept, and a failed scan keeps nothing in the final location | AC4.1.2: nothing from this import is left in the final location after a failure during the move |
| US5.1 | AC5.1.1: a finding or a failed step stops the import and removes everything | AC5.1.2 the 6-hour limit, AC5.1.3 leftovers named when removal fails, AC5.1.4 exactly one outcome, AC5.1.5 outcome in the logs |
| US5.2 | AC5.2.1 and AC5.2.2: the failure email is sent without any secret or personal value | AC5.2.3: a failed email is recorded and the import still ends as failed |
| US6.1 | None | All of AC6.1.1 to AC6.1.3 (could have) |

## Cross-Cutting Concerns

- **Never put secrets or personal values in logs, findings, or emails:** applies to both units (US3.1, US5.1, US5.2).
- **Exactly one outcome per import:** completed in U2 (AC5.1.4), but U1's paths must already follow it.

## Implementation Order Within Each Unit

- **U1:** download, then scan, then keep or remove, then the failure email, then the start event that ties them together. This follows the riskiest-first order in `intent-backlog.md`.
- **U2:** the 6-hour limit and the all-or-nothing move first, then the rejected-event paths and the waiting event, then the failed-email record, then the optional record of imports last.

## Coverage Verification

- All 7 stories are assigned to at least one unit.
- Both units have rows: U1 on six stories, U2 on six stories.
