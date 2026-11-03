# Code Summary: u2-import-rules

Conversation language: English.

Unit u2-import-rules is built. It finishes the import that u1-import-slice started: bad start events are rejected with an email, a model version that is already stored is refused before any download, each import has a 6-hour deadline, the move to the final bucket is all or nothing, every import ends in one of five outcomes with one log line, and every import leaves a record. The code changes the u1 code in place, in the project root (not in `aidlc/`). All 14 plan steps are done. The team writes each layer's code first and then its tests (test-after), as the Testing Contract says.

## What was built

| Area | Files | What it does |
|------|-------|--------------|
| Deadline | `src/model_import/deadline.py` (new) | `Deadline` (start time plus 6 hours, read against an injected clock), `DeadlineReached`, and the reason text "the time limit of 6 hours passed" (BR8.1 to BR8.4) |
| Data types | `src/model_import/models.py` | The outcomes `rejected` and `refused-already-stored`; the new fields of `ImportRequest`, `ImportRecord` (`eventId`, `outcome`, `failureReason`, `findings`, `findingsTotal`) and `OutcomeRecord` (`emailSent`, `recipientsNotReached`); `Leftover`, `RemovalResult`, `KeepResult`, `FindingSummary`; the new transitions from the start state; the constants `MAX_RECORD_FINDINGS = 200` and `MAX_LEFTOVERS_LISTED = 20` |
| Start event | `src/model_import/event.py` | `classify_message` returns `StartEvent`, `RejectedEvent` (all failed field names, and only values that passed), or `NotAStartEvent`; the event `time` is optional; a file named `_COMPLETE` (letters compared without case) at the root of a model fails the path check; the sender address is hidden from the text form of the event objects. `parse_start_event` is removed |
| Counters and records | `src/model_import/store.py` | Two per-day email counters, `emails` (limit 30) and `notices` (limit 20), each a conditional write; the day is the UTC day |
| Log fields | `src/model_import/logs.py` | The allow-list gains exactly eight fields (`eventId`, `emailSent`, `recipientsNotReached`, `emailProblem`, `olderFilesRemoved`, `leftoversTotal`, `frontDoorSeconds`, `proofSeconds`); `eventId` is shown only when it matches the safe pattern |
| Final bucket | `src/model_import/custodian.py` | Marker reader (`own`, `other`, `missing`, `unreadable`, and a raised `MarkerCheckError` when the check itself fails); `keep` returns a `KeepResult` and never raises after the marker; removal by listing with proof and Leftovers; `olderFilesRemoved`; own-marker finish (`finish_own_marker`); a multipart copy above 5 GiB with a deadline check per part; a second (state) S3 client for the marker reader |
| Deadline check points | `src/model_import/hf_client.py`, `downloader.py`, `retry.py`, `scanner.py` | Each accepts the `Deadline`: a check before each file download, at every chunk, before each retry (the wait is the smaller of the plan and the time left), and before each file scan |
| Emails | `src/model_import/notifier.py` | The rejection email (names the failed fields, never a value; subject "Model import rejected: event <eventId>"), the already-stored email, the leftover and older-file lines, the per-role result, and the two counters |
| Coordination | `src/model_import/coordinator.py` | The front door (outcome, marker check, own marker, refusal), `reject`, the deadline checks, `run.stored` and the guards after the marker, `_finish` in a fixed order (removal, email, log line, outcome object, final record), five outcomes, the findings in the record |
| Queue worker | `src/model_import/worker.py` | Three S3/SES clients with their time-outs; the classifier routes a start event and a rejected event through the same supervised path; a message that is not a start event is logged by field name and deleted; the deadline is made when the message is taken off the queue |
| Stack | `infra/model_import_stack.py` | New filter `EmailNotReached` and alarm (a) repointed to it; the two new outcome values in the count filters; new count and value filters; text of alarms (a), `d-queue`, `d-reminder` and (e). No resource, role, or permission is added |
| Documentation | `README.md` | The five outcomes, the two email limits, the 6-hour limit, how to read a record and an outcome, recovery steps for an unexpected stop, and what has not been verified |
| Tests | `tests/unit/`, `tests/integration/`, `tests/e2e/`, `tests/infra/` | See "Test results". Two new files: `tests/unit/test_deadline.py` and `tests/infra/test_stack_import_rules.py` |

## How to run it

Run from the project root, as in u1 (`unit-test-instructions.md` of this Unit has the same commands):

```powershell
.venv\Scripts\python -m pytest tests/unit tests/integration tests/e2e tests/infra -q
.venv\Scripts\python -m pytest --cov --cov-fail-under=80
.venv\Scripts\python -m black --check src infra tests app.py
.venv\Scripts\python -m ruff check src infra tests app.py
```

## Test results

Run on this machine (Python 3.13.2, Windows) at the end of Step 14:

| Check | Before (end of u1) | After (end of u2) |
|-------|-------------------:|------------------:|
| `pytest --cov --cov-fail-under=80` | 306 passed; coverage 94.54 percent | 540 passed in 293.89 s; coverage 95.88 percent (floor 80) |
| `black --check src infra tests app.py` | 41 files unchanged | 44 files would be left unchanged |
| `ruff check src infra tests app.py` | All checks passed | All checks passed |

The net gain is 234 tests, which is more than the roughly 100 that the plan estimated across its test steps (the plan's figures were "about"). `pip-audit` was not run again; this Unit adds no dependency and `pyproject.toml` is unchanged.

Tests per file (540 in total):

| Area | Files (tests) |
|------|---------------|
| Unit | `test_coordinator.py` (52), `test_custodian.py` (49), `test_deadline.py` (6), `test_downloader.py` (19), `test_event.py` (68), `test_hf_client.py` (25), `test_logs.py` (21), `test_models.py` (48), `test_notifier.py` (22), `test_retry.py` (24), `test_rule_coverage.py` (4), `test_scanner.py` (62), `test_settings.py` (17), `test_store.py` (17), `test_worker_support.py` (10) |
| Integration | `test_worker.py` (19) |
| End to end | `test_end_to_end.py` (7) |
| CDK template | `test_config.py` (6), `test_stack_import_rules.py` (21), `test_stack_monitoring.py` (15), `test_stack_resources.py` (13), `test_stack_security.py` (15) |

Coverage per module:

| Module | Statements | Missed | Cover |
|--------|-----------:|-------:|------:|
| `infra/config.py` | 69 | 0 | 100% |
| `infra/model_import_stack.py` | 228 | 2 | 99% |
| `model_import/coordinator.py` | 347 | 9 | 97% |
| `model_import/custodian.py` | 361 | 22 | 94% |
| `model_import/deadline.py` | 30 | 0 | 100% |
| `model_import/downloader.py` | 101 | 3 | 97% |
| `model_import/event.py` | 147 | 4 | 97% |
| `model_import/hf_client.py` | 179 | 7 | 96% |
| `model_import/logs.py` | 127 | 10 | 92% |
| `model_import/models.py` | 323 | 2 | 99% |
| `model_import/notifier.py` | 177 | 2 | 99% |
| `model_import/retry.py` | 100 | 2 | 98% |
| `model_import/scanner.py` | 257 | 9 | 96% |
| `model_import/settings.py` | 42 | 0 | 100% |
| `model_import/store.py` | 132 | 15 | 89% |
| `model_import/worker.py` | 342 | 35 | 90% |
| Total | 2,963 | 122 | 95.88% |

The instructions for this Unit set a target of 90 percent for the changed modules. All reach it except `store.py`, which is at 89 percent, one point under. Its missed lines are error branches of reads and writes (the same kind of lines that kept u1 at 88 percent). The project floor of 80 percent is met with a wide margin. Nothing was lowered to pass.

## Changed tests of u1

The tests of u1 pass unchanged except where a changed u1 rule required a change (NFR6.8). Each change is named here with the rule that required it. The names were checked against the files.

| File | Test | Rule | Why it changed |
|------|------|------|----------------|
| `tests/unit/test_custodian.py` | `test_br4_1_keep_copies_every_file_writes_the_marker_last_and_empties_holding` | BR9.6 | `keep` now returns a `KeepResult`, not a bare stored model |
| `tests/unit/test_custodian.py` | `test_br5_2_removal_deletes_the_marker_first_then_the_files` | BR9.6, NFR4.14 | `remove` for an import whose marker was written deletes nothing and returns a result that says the model is stored |
| `tests/unit/test_custodian.py` | `test_nfr4_3_removal_run_twice_gives_the_same_end_state` | BR9.6, NFR4.14 | Same reason: the end state of a removal run twice is now "model still stored" for a marker-written import |
| `tests/unit/test_custodian.py` | `test_br5_5_a_marker_never_outlives_its_files_and_a_failed_delete_is_not_complete` | BR9.2, BR9.6 | The marker delete now has 3 tries, and a marker that cannot be deleted becomes a Leftover |
| `tests/unit/test_coordinator.py` | The stand-in downloader, the stand-in scanner, and `CountingCustodian.keep` | BR8.2 | They take the `deadline` argument that the real classes now take |
| `tests/unit/test_coordinator.py` | `test_br5_1_records_are_rewritten_before_each_step_and_moves_are_allowed_ones` | BR7.1 | The second event uses another model, because the first model's version is now stored and the front door would refuse the same version |
| `tests/unit/test_coordinator.py` | `test_br5_1_a_failed_step_goes_to_removing_once_then_done_with_one_email` (case `keep-duplicate`) | BR7.1, BR4.1 | The marker is planted during the download, because the front door now catches a marker that exists before the download; the keep step stays the second line of defence |
| `tests/unit/test_coordinator.py` | `test_nfr4_1_the_outcome_is_written_once_and_a_repeat_deletes_nothing_stored` | BR10.1, BR7.1 | A repeated event now stops at the front door with the outcome of the stored model |
| `tests/unit/test_coordinator.py` | `test_br6_1_br6_2_br11_1_a_bad_event_is_rejected_with_two_emails_and_one_record_at_the_end` (u1 name began `test_br1_1_an_event_that_fails...`) | BR6.1 | A bad event is no longer dropped with one log line: it is rejected with an email and an outcome |
| `tests/unit/test_event.py` | `test_br1_1_br1_2_a_valid_event_starts_one_import_with_its_own_identity` | BR7.1 | The request now starts in status `received` |
| `tests/unit/test_event.py` | The tests that called `parse_start_event` (through the helpers `start_event_of` and `first_failed_field`) | BR1.1, NFR3.14 | `parse_start_event` is removed; the tests use `classify_message` |
| `tests/integration/test_worker.py` | `test_br1_1_a_bad_event_is_deleted_at_once...` replaced by `test_br6_4_a_message_that_is_not_a_start_event_is_deleted_at_once_and_only_the_field_is_logged` | BR6.1, BR6.4 | A start event with bad content is now rejected with an email; only a message that is not a start event is deleted at once |
| `tests/integration/test_worker.py` | `FakeImport` and `Env.worker` | BR8.1 | The import function is called with `(event, deadline)`; the worker takes a separate function for a rejected event |
| `tests/unit/test_worker_support.py` | The "no import should start" function | BR8.1 | It takes the `deadline` argument |
| `tests/e2e/test_end_to_end.py` | `Flow` | BR6.1 | It wires both runners (`run_import` and `reject_import`) the way `main` does |
| `tests/unit/test_store.py` | `test_nfr3_16_nfr3_18_the_31st_failure_attempt_of_a_day_is_not_allowed_and_not_counted` | NFR3.18 | The main counter now stops at 30, not 50 |
| `tests/unit/test_notifier.py` | `test_nfr3_16_nfr3_18_the_31st_failure_email_of_a_day_is_not_sent_and_logs_the_limit` | NFR3.18 | Same reason, at the email level |
| `tests/infra/test_stack_monitoring.py` | `test_alarms_a_b_and_e_count_log_metrics_over_five_minutes` | NFR5.18 | Alarm (a) reads `EmailNotReached`, no longer the count of the outcome `removed-email-failed` |
| `tests/infra/test_stack_monitoring.py` | `test_metric_filters_make_the_outcome_count_and_duration_metrics` | NFR5.20 | The set of filter names gains the two new outcome counts |

The rows for `test_store.py` and `test_notifier.py` were not in the earlier dispatch reports. They were found by reading the test files for the comment "amends NFR3.16". I could not compare the files with their u1 versions, because Git is not installed on this machine, so other small edits of u1 tests (for example added `deadline` arguments in `test_downloader.py`, `test_hf_client.py`, `test_retry.py` and `test_scanner.py`, which are changed files per their modification time) are not listed row by row.

## Points from earlier reviews that this build had to apply

These were found by reviews after the verdicts of the design stages and are in no design file. Each is applied.

1. **`remove` for a marker-written import is not "removal complete".** `RemovalResult` has `model_stored`, and `removal_complete` is false when it is set. The coordinator (`_remove_and_notify`) then finishes the import as `stored` and sends no failure email. (`models.py`, `custodian.py`, `coordinator.py`; NFR4.14.)
2. **The own-marker finish deletes `holding/<importId>/`.** The custodian method `finish_own_marker` does it, and the coordinator calls it when the marker holds this import's own importId. (BR7.1, BR9.6.)
3. **A second (state) S3 client for the marker reader, and a distinct "check failed" result.** `FileCustodian` takes `state_client`. An S3 error other than not-found on the `HEAD` or the `GET` raises `MarkerCheckError`, which is a failed step with nothing to remove. Only a body that arrived and is not a valid marker is `unreadable`. (`custodian.py`, `worker.py`, `coordinator.py`; NFR3.25.)
4. **The `eventId` in log fields follows the safe-pattern choice of the email.** `logs.py` shows `eventId` only when it matches `[A-Za-z0-9._:-]{1,128}`; otherwise the field is left out and the importId identifies the import. The record and the subject of the rejection email use the same choice (`event_reference`). (NFR3.19.)
5. **The `_COMPLETE` check is part of the path checks in `event.py`.** A file at the root of the version folder named `_COMPLETE`, in any letter case, fails the path check. (NFR3.25.)
6. **The marker keeps the u1 shape and adds the model name and version.** It holds `importId`, `modelName`, `exactVersion`, `files` (path and `sizeBytes`), and `completedAt`. It holds no sender address. (NFR3.21.)
7. **No new dependency.** `pyproject.toml` is unchanged. The multipart copy uses the S3 calls of the SDK that was already pinned.

## Design points the build honoured

- The marker is written last and is the one commit point. After it, `keep` never raises, nothing deletes the model, and the deadline is not checked (BR8.3, BR9.1, BR9.6).
- The front door runs in a fixed order: an existing outcome does nothing; a bad event is rejected; the marker is read; the own marker finishes as stored with no email; another marker refuses; no marker runs the import (BR6.1, BR7.1, BR10.1).
- The end of every import is one function in a fixed order: removal and email, the `import_outcome` log line, the outcome object (created once, with a condition), then the final record. The record never shows `done` without an outcome (BR10.1, BR10.2, BR11.1).
- A rejected or already-stored event writes its record once, at the end. A value that failed the check is never stored, logged, or sent in an email (BR6.3, BR11.1).
- The removal looks at the final bucket only when the keep step had started writing. A download failure or a deadline stop before the keep step touches nothing there, and reports 0 older files (BR9.2).
- After a removal, the holding folder, the version folder, the incomplete uploads, and the local disk are listed again. What is found, and every failed delete, is a Leftover. The email lists at most 20, with the total (BR9.3, BR9.4).
- The records under `state/` are never removed by a removal (BR11.3).
- The log allow-list gains exactly eight fields. A test compares it with the u1 list plus those eight (NFR5.16).
- No resource, role, or permission is added to the stack. A test compares the action and resource list of the task role with the list recorded for u1 and finds no `s3:*` and no `Resource: *` (NFR3.17).
- The lifecycle rules are unchanged: `holding/` expires after 2 days, `state/` after 30 days, unfinished uploads are aborted after 1 day, and the final bucket has no expiry (NFR4.9, NFR5.15).
- The rule-coverage test lists BR6.1 to BR11.3 as well as BR1.1 to BR5.6, and each rule is named by at least one test (NFR6.6).
- Tests use an injected clock and stand-ins. No test waits for real hours or calls a real AWS account.
- Planted sender addresses and secrets, placed in every field of a bad event, are searched for in every log record, record, outcome, marker, and email text (NFR3.19, NFR3.20).

## Decisions made while building

The design left these open, or two documents read differently.

1. **When is a failed import `removed-with-email`?** Only when every email owed was accepted (BR10.4). If any one was not, it is `removed-email-failed`. u1 used "at least one email was sent". The sender-only or team-only case therefore changes from u1.
2. **The marker check cannot be made.** The import is a failed step with nothing to remove, so no removal is called. The failure email goes out (BR7.1).
3. **`proofSeconds` is the time of the whole `remove` call**, which is the removal and its proof listing together, not the listing alone.
4. **The description of alarm (a) is widened** to name a failed import, a rejected event, and an event for a model that is already stored.
5. **The text of alert (b) is unchanged**, although `monitoring-design.md` says it should point to `event = leftover`. The condition already covers the outcome `stored` with `removalComplete` false. Changing the text is a one-line change in `infra/model_import_stack.py` if the person wants it.
6. **`EmailNotSentSender` and `EmailNotSentTeam` are count filters with no dimension.** Each matches the log record `email_not_sent` for its role.
7. **`parse_start_event` is removed.** Nothing but its own tests used it once the worker called `classify_message`.
8. **A rejected event that arrives when the worker has no reject function** is handed back, so it reaches the dead-letter queue and the team is alerted, and is not dropped.
9. **An unfinished file in the final folder that is not one of the model's files stays after a successful keep.** This is the existing behaviour of the custodian: a successful keep overwrites files of the same name and deletes nothing else. A failed move removes everything in a folder that has no marker (BR9.2).
10. **Log event names use underscores**, as u1 does (`import_outcome`, `email_not_sent`, `outcome_already_set`, `event_rejected`, `email_limit_reached`). Several design documents write them with dots (`import.outcome`). The metric filters in the stack use the underscore names.
11. **The `leftover` log records are capped at 200**, and the total is always logged (`leftoversTotal`). This follows the assumption in `scalability-design.md`. The functional design says "the full list"; for more than 200 leftovers the log has the first 200 and the count.
12. **The record keeps `filesCopied` and `lastKeyCopied`**, not the list of keys, so a record with 200 findings and 1,000 copied files stays under 100 KB (NFR2.10). A test builds that record.
13. **Client settings.** The state S3 client has a 5-second connect time-out, a 10-second read time-out, and 3 attempts. The mail client has the same time-outs and 2 attempts. The data S3 client keeps the SDK defaults. These numbers come from `performance-design.md`; they are not tried against the real services.
14. **The marker that cannot be read** gives the outcome `refused-already-stored`, with a reason that tells the team to check the marker (NFR3.25).

## Open points from the earlier reviews

Only points that are still open are listed. Those the build closed are in the section above.

**NFR Requirements, `review-01.md` (verdict READY; the requirement files were not revised after it).**

- NFR4.19 in `reliability-requirements.md` still says the leftover files expire within 2 days. That holds for the holding bucket only. Files copied to the final bucket do not expire (R-02). `reliability-design.md` and `infrastructure-specification.md` say so plainly, and the README says so. The requirement text itself is unchanged. This build did not edit it.
- NFR3.18 and NFR2.9 do not state that they amend NFR3.16, or name the UTC day (R-03). The code and the tests do both. The requirement text is unchanged.
- NFR1.10 does not exempt the move after the marker, and NFR1.1 says "hard limit" while NFR1.10 allows 10 minutes of overrun (R-04). The code follows BR8.2. The text is unchanged.
- NFR3.25 still says the exact result is "set in NFR Design" (R-01). NFR Design set it and the build follows it. This is a wording point only.
- The copy time of one file up to 5 GiB has never been measured. NFR1.10 rests on an assumption (suggestion in the review). The first real import gives the first value.
- The models stored by u1 before this Unit have a marker, because u1 wrote one. No real import was made under u1, so BR9.2 cannot meet an unmarked stored model.

**NFR Design, `review-02.md`.** R-07, R-08, and R-09 are the three Minor points listed above as points 1 to 3. The three suggestions are applied (the worker's `has_outcome` owns the "delete the message" check, the log `eventId` follows the safe pattern, and the `_COMPLETE` check is in `event.py`). Nothing from this review is open.

**Infrastructure Design, `review-02.md`.**

- R-07: `infrastructure-specification.md` still says in its opening paragraph and in the Shared Infrastructure row that the Unit changes "the pattern of one metric filter". The stack adds a filter and repoints alarm (a); no existing pattern changed. A wording point in a design file.
- R-08: the `NFR3.16` row of that stage's `traceability.json` says "alert (e) unchanged", but the description of alert (e) now changes. A wording point.
- For the CI Pipeline stage: make the pre-run clean-up of the smoke-check test model explicit (a marker left behind makes the next run take the already-stored path and send an email), and assign the grant that u1's holding-bucket-empty check needs (suggestions in the review).
- The functional spec's open question about widening alert (a) and about alert (b) for the outcome `stored` with an incomplete removal is answered: (a) reads `EmailNotReached`, and (b) already has no condition on the outcome.

## Corrections to earlier documents

`functional-spec.md` lists, under "Corrections to Earlier Documents", seven documents that need a correction. They were not edited by this Unit, and this stage edits none of the design files. The list is:

1. `unit-of-work.md`, the U2 boundaries (the wait for a running import).
2. `unit-of-work-story-map.md` (AC1.1.4 is delivered by u1).
3. `contract-summary.md`, C4 (the status `waiting`; the new outcomes; the marker content).
4. `contract-summary.md`, C3 (the subject of the email for a rejected event).
5. u1 `functional-spec.md`, "What This Unit Does Not Do".
6. u1 `rules.md` (BR1.1, BR1.2, BR4.1, BR5.2, BR5.5 as changed or restated by u2).
7. `bolt-plan.md` (Bolt 2 loses the wait).

One more point found while documenting, not on that list: the NFR documents and several design documents write log event names with dots (`import.outcome`, `email.not_sent`, `outcome.already_set`), and the code and the stack use underscores (decision 10).

## Not verified against a real account

- No deployment was made and no event was sent. Everything in the stack is checked only in the synthesised template.
- The Docker image was not built. Docker is not confirmed on this machine (open since u1). The image check is left to the pipeline.
- `cdk synth` through the CDK command line was not run; the template tests build the stack in Python.
- SES sending, the sandbox status, and the verified sender identity are not tested. Moto stands in for SES.
- No real Hugging Face download was made. The tests use a stand-in host.
- The multipart copy above 5 GiB is tested only with a lowered limit (10 bytes, parts of 16 bytes) and a stand-in for the four multipart calls, because moto rejects the small parts that such a test needs. The real 5 GiB limit, the real 256 MiB part size, and the real S3 multipart calls are not exercised.
- The conditional writes (`IfNoneMatch`, `IfMatch`) used by the outcome and the counters are tested against moto, not against real S3.
- The metric filter patterns (for example `emailSent = false`) and the alarm settings are checked in the template only, not in CloudWatch.
- The client time-outs and attempt counts of the worker are not tried against the real services.
- The time targets (5 seconds for the front door, 1 minute for a rejected event, 15 minutes for the removal, and 10 minutes after the deadline) are tested with injected clocks, not with real waiting.
- The recovery steps in the README, including the "Start DLQ redrive" action of the SQS console, were written from the code and the design documents. They were not tried.
- The tests ran on Python 3.13.2. The target is Python 3.12 and the image uses 3.12.
- Git is not installed on this machine, so nothing was committed.

## Story criteria covered

| Criterion | Covered by |
|-----------|-----------|
| AC1.1.2 a bad event is rejected with an email, and no download starts | `event.py`, `coordinator.py` (`reject`), `notifier.py`, `worker.py` |
| AC1.1.3 a version already stored is refused before the download | `custodian.py` (`read_marker`), `coordinator.py` (front door), `notifier.py` |
| AC2.1.4 an import ends inside 6 hours | `deadline.py`, with check points in `hf_client.py`, `downloader.py`, `retry.py`, `scanner.py`, `custodian.py`, `coordinator.py` |
| AC4.1.2 a failed move leaves nothing in the final location | `custodian.py` (`keep`, `remove`), `coordinator.py` |
| AC5.1.2 the 6-hour limit fails the import and removes everything | `deadline.py`, `coordinator.py` |
| AC5.1.3 a failed removal is named in the email | `custodian.py` (Leftovers), `notifier.py` |
| AC5.1.4 one outcome per import | `coordinator.py` (`_finish`), `store.py` (`create_outcome`), `models.py` |
| AC5.1.5 outcome and reason in the logs, without secrets | `coordinator.py` (`import_outcome`), `logs.py` |
| AC5.2.3 an email that cannot be sent is recorded and the import still ends | `notifier.py`, `coordinator.py`, `models.py` (`emailSent`, `recipientsNotReached`) |
| AC6.1.1 a record holds the model name, version, and outcome | `coordinator.py`, `store.py`, `models.py` |
| AC6.1.2 the record lists findings without values | `coordinator.py`, `models.py` (`FindingSummary`) |
| AC6.1.3 the outcome of every import can be found in the logs | `coordinator.py`, `logs.py` |

Each business rule BR6.1 to BR11.3, and the NFRs of this Unit, are mapped to a source file in `traceability.json`.

## Assumptions & Open Questions

- [assumption] The u1 tests whose only change was a new `deadline` argument (`test_downloader.py`, `test_hf_client.py`, `test_retry.py`, `test_scanner.py`) are not listed row by row in "Changed tests of u1", because there is no Git history on this machine to compare them with. The lists in the earlier dispatch reports are taken as complete for the other files.
- [assumption] moto supports the S3 calls used here. Where a call must fail, a thin wrapper client raises the chosen error, and the real behaviour of S3 is not tested.
- Open question: Does the person want the one-line change to the text of alert (b) (decision 5)?
- Open question: Does the person want the corrections to the earlier documents made (list above)? They are left as they are until asked.
- Open question: Which real model is imported first, and how long does the copy of its largest file take (open since u1)? Its measured times are the first real values for the time targets.
