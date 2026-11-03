# Code Generation Plan: u2-import-rules

## Summary

- Builds: the rules that finish the import: rejected events and refused duplicates with their emails, the 6-hour deadline, the all-or-nothing move with proof of removal, the five outcomes with one log line each, and the record of every import with its findings.
- Touches: the existing modules in `src/model_import/` (`models.py`, `store.py`, `event.py`, `custodian.py`, `notifier.py`, `coordinator.py`, `worker.py`, `logs.py`, `downloader.py`, `hf_client.py`, `retry.py`, `scanner.py`), one new module `deadline.py`, `infra/model_import_stack.py`, the tests under `tests/`, and the README; no file the person had before this work is changed.
- Tests: about 70 new automated tests (unit tests per changed building block, integration tests of the worker loop, the forced-failure proof, end-to-end tests for a rejected event, a duplicate, a time-limit stop, and a failed move, and CDK template tests), checked at 80 percent line coverage for the whole project.

The plan follows the design in `functional-spec.md`, `rules.md` (BR6.1 to BR11.3), `entities.md`, `contract-summary.md`, `performance-design.md`, `security-design.md`, `infrastructure-specification.md`, `unit-of-work.md`, and `requirements.md`, and the other design documents of this Unit (`reliability-design.md`, `observability-design.md`, `scalability-design.md`, `logical-components.md`, `monitoring-design.md`, `cicd-pipeline.md`). This Unit changes code that u1-import-slice built; the code is modified in place. The team writes the code of each layer first and then its tests (test-after), as the Testing Contract below says. Code goes to the project root, not to the record folder.

## Testing Contract

```json
{
  "version": 1,
  "methodology": "test-after",
  "source": "team",
  "ordering": "We implement each testable layer first, then write and run the tests for that layer.",
  "scope": "mvp",
  "test_strategy": "standard",
  "project_type": "greenfield",
  "applicable_notes": [
    {
      "layer": "org",
      "text": "We treat tests as a first-class deliverable in every Bolt. The specific\nmethodology (TDD, BDD, ATDD, or classic test-after) is affirmed at\npractices-discovery and recorded in `team.md` under this heading with explicit\n`Methodology` and `Ordering` fields; Code Generation resolves those fields\nindependently from coverage, tooling, and scope notes.\n\nWhen no posture has been affirmed, our default per scope is:\n- **Methodology**: test-after\n- **Ordering**: implement each applicable testable layer, then write and run\n  that layer's tests.\n- `mvp`, `enterprise`, `feature`, `infra`, `classic` add an 80% line-coverage\n  floor and CI execution before merge.\n- `bugfix`, `security-patch` add a targeted regression for the specific\n  bug/vulnerability and require the existing suite to remain green.\n- `express` uses the Minimal strategy: requirement-driven unit tests (one per\n  requirement, with a happy-path floor per component); existing tests remain\n  green.\n- `poc`, `refactor`, `workshop` add no extra new-test floor and require the\n  existing suite to remain green.\n\nThe active `Test Strategy` still applies in every scope and determines test\nvolume/types. Scope floors are additive; they never reduce or replace the\nselected strategy.\n\nBuild and Test verifies defined coverage floors and affirmed quality targets;\nthey may not be weakened to make a step pass.\n\nAffirm a stricter posture in `team.md` if the team commits to one."
    },
    {
      "layer": "team",
      "text": "- **Methodology**: test-after\n- **Methodology evidence**: The team chose to write the code first and then write tests for it (interview Q4).\n- **Ordering**: We implement each testable layer first, then write and run the tests for that layer.\n- **Coverage floor**: Tests cover at least 80 percent of lines (interview Q5). The team chose this knowingly and accepts that it may be hard to reach by the end-of-tomorrow deadline.\n- **Test language and tooling**: Python (confirmed, feasibility Q5). The test framework is not chosen yet."
    }
  ],
  "obligations": {
    "strategy": "standard",
    "strategy_volume": [
      "Five to eight tests per component.",
      "Unit tests plus integration tests for key boundaries.",
      "Add E2E, performance, or security tests when requirements demand them."
    ],
    "scope_floor": [
      "Meet an 80% line-coverage floor.",
      "Run the selected tests in CI before merge."
    ],
    "combination_rule": "Apply every selected-strategy obligation and every scope-floor obligation; neither replaces the other, and a targeted scope regression may add the narrowest necessary test type beyond the strategy default."
  },
  "plan_profile": {
    "methodology": "test-after",
    "runner_step": "Bootstrap the minimal test runner/configuration and record the exact unit-scoped command.",
    "runner_ready_before_first_test": true,
    "testable_layers": [
      "Data model / database behavior",
      "Repository / data access",
      "Business logic",
      "API / endpoint",
      "Frontend behavior"
    ],
    "steps": [
      "Project structure and production configuration skeleton.",
      "Bootstrap the minimal test runner/configuration and record the exact unit-scoped command.",
      "Data model / database behavior - implement.",
      "Data model / database behavior - write and run its tests after implementation.",
      "Repository / data access - implement.",
      "Repository / data access - write and run its tests after implementation.",
      "Business logic - implement.",
      "Business logic - write and run its tests after implementation.",
      "API / endpoint - implement.",
      "API / endpoint - write and run its tests after implementation.",
      "Frontend behavior - implement.",
      "Frontend behavior - write and run its tests after implementation.",
      "Environment/build configuration.",
      "Documentation and traceability."
    ]
  },
  "input_sha256": "sha256:e750d717fc48af7682406d4df21b48e7d57011c589283971c8804bb89b1d2a63",
  "contract_sha256": "sha256:5a6a1157d8f334fa6b8f695861a1801548ad3ef7724c8317424e42947794adbc"
}
```

This Unit has no frontend, so the frontend layer steps do not apply. The data model layer is the record, outcome, and result types and the counters kept in S3. The repository layer is the S3 access of the custodian and the store, and the plumbing of the deadline into the download, retry, scan, and copy code. The business logic is the front door, the coordinator, the notifier, and the event check. The "API / endpoint" layer is the worker loop and the start-event boundary (there is no HTTP endpoint).

## Layout

The project root already holds what u1 built. This Unit changes these paths and adds the files marked new.

| Path | Change |
|------|--------|
| `src/model_import/deadline.py` | New: the `Deadline` value and `DeadlineReached` |
| `src/model_import/models.py` | Outcome values, record and outcome fields, `Leftover`, `RemovalResult`, `KeepResult`, new transitions, constants |
| `src/model_import/store.py` | Counter takes a name and a limit (two counters) |
| `src/model_import/event.py` | Message classifier with its three results, optional `time`, all failed fields, `_COMPLETE` path check, hidden address in the text form |
| `src/model_import/custodian.py` | Marker reader, `KeepResult`, post-marker clean-up that never fails the import, removal by listing with proof and Leftovers, own-marker finish, multipart copy above 5 GiB with deadline, second S3 client |
| `src/model_import/notifier.py` | Rejection and already-stored emails, per-role result, two counters |
| `src/model_import/coordinator.py` | Front door, `reject`, `run.stored`, deadline checks, `_finish` order, five outcomes, final record |
| `src/model_import/worker.py` | Three clients with timeouts, classifier routing, `_process` with a function to run |
| `src/model_import/logs.py` | Eight allow-list fields |
| `src/model_import/hf_client.py`, `downloader.py`, `retry.py`, `scanner.py` | The `Deadline` argument and its check points |
| `infra/model_import_stack.py` | Alert (a) filter and metric, outcome count values, new count and value filters, alarm descriptions |
| `tests/` | New and changed tests in `unit/`, `integration/`, `e2e/`, `infra/` |
| `README.md` | Records, limits, and the recovery steps (the file was created by u1) |

## Steps

- [x] **Step 1: Project structure and production configuration skeleton.** Add `deadline.py` (`Deadline`, `DeadlineReached`, `REASON_TIME_LIMIT = "the time limit of 6 hours passed"`), the constants (`NOTICE_EMAIL_LIMIT = 20`, `FAILURE_EMAIL_LIMIT = 30`, `MAX_RECORD_FINDINGS = 200`, `MAX_LEFTOVERS_LISTED = 20`, `MARKER_DELETE_TRIES = 3`, `COPY_SINGLE_LIMIT = 5 GiB`), and the eight new allow-list fields in `logs.py`. Stories: AC5.1.2, AC5.1.5. Rules: BR8.1, BR10.2.
- [x] **Step 2: Test runner.** The runner was set up by u1. Check that `.venv\Scripts\python -m pytest ...` still runs, and record this Unit's exact command in `unit-test-instructions.md`.
- [x] **Step 3: Data model, implement.** `models.py`: outcome values `rejected` and `refused-already-stored`; the new fields of `ImportRecord` (`eventId`, `outcome`, `failureReason`, `findings`, `findingsTotal`) and of `OutcomeRecord` (`emailSent`, `recipientsNotReached`); `Leftover`, `RemovalResult` (list of Leftovers, `olderFilesRemoved`), `KeepResult` (`stored`, `marker_written`, `holding_emptied`); the transitions from the start to `done` and to `removing`; `store.py`: the counter takes a name and a limit, with the two counters. Stories: AC5.1.4, AC6.1.1, AC6.1.2, AC5.2.3. Rules: BR10.1, BR10.3, BR10.4, BR10.5, BR11.1, BR11.2.
- [x] **Step 4: Data model, write and run its tests.** Tests for the five outcomes, the new record and outcome fields, the size of a record with 200 findings and 1,000 copied files, the two counters with their limits and the UTC day, and the new transitions (about 10 tests).
- [x] **Step 5: Repository and access layer, implement.** `custodian.py`: the marker reader (`own`, `other`, `missing`, `unreadable`, and a distinct raised error for a failed check); `keep` returns `KeepResult`, catches every exception after the marker, and deletes the holding folder without failing the import; the "writing" mark and the set of marker-written imports; `remove` reads the marker first, deletes it with 3 tries only when it holds this importId, deletes the version folder by listing when it has no marker, counts `olderFilesRemoved`, lists the holding folder, the version folder, uploads and the local directory again for the proof, and returns Leftovers; `remove` for a marker-written import does nothing and returns a result the coordinator treats as stored; the own-marker finish method that deletes `holding/<importId>/`; the explicit multipart copy above 5 GiB with a deadline check per part; a second S3 client for the marker reader. `hf_client.py`, `downloader.py`, `retry.py`, `scanner.py`: accept the `Deadline` and check it at the points of `performance-design.md`; the retry wait is the smaller of the plan and the time left. `worker.py`: build the state client, the data client, and the mail client with their timeouts. Stories: AC4.1.2, AC5.1.2, AC5.1.3, AC2.1.4. Rules: BR7.1, BR8.2, BR8.3, BR9.1, BR9.2, BR9.3, BR9.6.
- [x] **Step 6: Repository and access layer, write and run its tests.** The forced-failure proof of `functional-spec.md`: failure at the first, a middle, and the last copy, at the marker write, the marker delete failing, a failing delete, a failing listing, the deadline before the third copy and inside a large copy, older unfinished files with a failed move, and a download failure and a deadline stop before the keep step with older files present (untouched); the holding delete failing after the marker; a failing `HEAD` and a failing `GET` of the marker; a damaged marker; the removal run twice; the retry wait cut by the deadline; the download chunk check (about 25 tests).
- [x] **Step 7: Business logic, implement.** `event.py`: the classifier returning `StartEvent`, `RejectedEvent` (with all failed fields and only values that passed), or `NotAStartEvent`; the optional `time`; the `_COMPLETE` path check (letters compared without case) in the path checks; the address hidden from the text form of the event objects. `notifier.py`: the rejection email (typed inputs, eventId or importId in the subject), the already-stored email, the per-role result, and the two counters. `coordinator.py`: `reject`, the front door order (outcome, check of the marker, own marker, refusal), the `Deadline` and its checks in the moves, `run.stored` and the guards after the marker, the end-of-import order (removal, email, log line, outcome object, final record), the record with findings, and `emailSent` and `recipientsNotReached`. Stories: AC1.1.2, AC1.1.3, AC2.1.4, AC4.1.2, AC5.1.2 to AC5.1.5, AC5.2.3, AC6.1.1 to AC6.1.3. Rules: BR6.1 to BR6.4, BR7.1, BR7.2, BR8.1 to BR8.4, BR9.4, BR9.5, BR9.6, BR10.1 to BR10.5, BR11.1 to BR11.3.
- [x] **Step 8: Business logic, write and run its tests.** Unit tests for each changed block, with at least one test naming each rule ID BR6.1 to BR11.3 and the rule-coverage test extended to list them; tests that plant a sender address and a secret in every field of a bad event and search every log record, record, outcome, marker, and email text; the unexpected exception after the marker in each place part two can fail; the redriven message with its own marker and with no marker; the two email limits; and the changed tests of u1 (a bad event is now rejected with an email) (about 40 tests).
- [x] **Step 9: API / endpoint layer (worker loop and start-event boundary), implement.** `worker.py`: call the classifier; for a `StartEvent` and a `RejectedEvent` run the same `_process` path (`has_outcome` on the importId, heartbeat, stop signal, 7-hour watchdog, message deletion after the outcome) with the function to run; a `NotAStartEvent` is logged as `event_rejected` with the field name and the message is deleted, as in u1; the log is bound with the importId and the eventId (shown only when it matches the safe pattern), plus the model name and version when they passed. Stories: AC1.1.2, AC1.1.3. Rules: BR6.1, BR6.4, BR10.1.
- [x] **Step 10: API / endpoint layer, write and run its tests.** Integration tests of the worker loop with a bad event, a duplicate, a repeated message, a time-limit stop, and a message that is not a start event, and end-to-end tests for a rejected event, a refused duplicate, a deadline stop, and a failed move that leaves nothing in the final location (about 14 tests).
- [x] **Step 11: Deployment artifacts (IaC), implement.** `infra/model_import_stack.py`: the new filter `EmailNotReached` and the repointing of alarm (a), the two new outcome values in the count filters, the new count and value filters of `monitoring-design.md`, and the alarm descriptions for `d-queue`, `d-reminder`, and (e). No resource, role, or permission is added. Requirements: NFR5.13, NFR5.14, NFR5.17 to NFR5.20, NFR4.19, NFR3.18.
- [x] **Step 12: Deployment artifacts, write and run their tests.** CDK template tests: the action and resource list of the task role equals the list recorded for u1 and has no `s3:*` or `Resource: *`; alarm (a) reads `EmailNotReached` while `OutcomeRemovedEmailFailed` stays a count; the count filters include the five outcomes; alert (b) has no condition on the outcome; the `DeadlineStopSeconds` filter matches the `REASON_TIME_LIMIT` text; the lifecycle rules are unchanged; the alarm descriptions (about 10 tests).
- [x] **Step 13: Environment and build configuration.** Update `README.md` (created by u1) with the five outcomes, the two email limits, the deadline, how to read a record and an outcome, and the recovery steps for an unexpected stop (read the record, look for files without a marker, send the message again from the dead-letter queue). No other file is changed. `pyproject.toml` and the Black, Ruff, and coverage settings are not changed, and no threshold is lowered.
- [x] **Step 14: Documentation and traceability.** `code-summary.md` (including every changed test of u1 with the rule that required it, the open points from the reviews of NFR Requirements, NFR Design and Infrastructure Design, and what was not verified against a real account), `source-manifest.json`, and `traceability.json`.

## Points From Earlier Reviews That This Build Must Apply

These were found by the reviews of the design stages after their verdicts were recorded, so they are not in the design files. The developer applies them and names each in the code summary.

- `remove` for an import whose marker write succeeded must not report "removal complete" to the coordinator; it returns a result the coordinator treats as stored (reliability-design.md, NFR4.14).
- The own-marker finish (BR7.1) calls a custodian method that deletes `holding/<importId>/`.
- `FileCustodian` takes the second (state) S3 client for the marker reader, and the reader has a distinct "check failed" result that becomes a failed step, never `unreadable`.
- The `eventId` in log fields follows the same safe-pattern choice as the email (otherwise the importId is used).
- The `_COMPLETE` path check is part of the path checks in `event.py`.
- The marker keeps the shape u1 built (importId, files with sizes, time) and adds the model name and version.
- The plan names no new dependency. If one turns out to be needed, the developer stops and reports it instead of adding it.

## Story-to-Code Traceability

| Story | Plan steps |
|-------|-----------|
| US1.1 (AC1.1.2, AC1.1.3) | 3, 4, 7, 8, 9, 10 |
| US2.1 (AC2.1.4) | 1, 5, 6, 7, 8 |
| US4.1 (AC4.1.2) | 5, 6, 7, 8, 10 |
| US5.1 (AC5.1.2 to AC5.1.5) | 1, 3, 5, 6, 7, 8, 10 |
| US5.2 (AC5.2.3) | 3, 7, 8 |
| US6.1 (AC6.1.1 to AC6.1.3) | 3, 4, 7, 8 |
| Alerts and monitoring | 11, 12 |

## Assumptions & Open Questions

- [assumption] The Hugging Face client of u1 streams in chunks, so a deadline check per chunk can be added without a change of structure. If it does not, the developer reports it before changing the client.
- [assumption] moto supports the S3 calls used here, including a conditional put, a listing, a multipart copy, and an abort. Where a failure must be forced, a thin wrapper client around the moto client raises the chosen error.
- [assumption] The list of task-role actions recorded for u1 can be produced from the stack as it is before this build's changes; the test records it from the template first and then compares.
- [assumption] The existing `.gitignore` and `AGENTS.md` stay as they are. This build appends nothing to them.
- Open question: Docker is not confirmed on this machine (open since u1), so the image build is checked only in the pipeline.
