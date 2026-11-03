# Observability Design: u2-import-rules

This describes how this Unit meets the requirements in `observability-requirements.md`. It uses the components in `logical-components.md`, the rules in `rules.md` (BR9.3, BR9.4, BR10.1 to BR10.5, BR11.1 to BR11.3), the lifecycle in `functional-spec.md`, the technology in `tech-stack-decisions.md`, and the boundaries in `contract-summary.md`. The other requirement documents are `performance-requirements.md`, `security-requirements.md`, `scalability-requirements.md`, and `reliability-requirements.md`. The logging, metrics, and alert design of u1-import-slice (allow-list JSON logger, Embedded Metric Format, one SNS topic, alarms (a) to (f), cost and stuck alarms) is kept. This document lists only what changes. No question to the owner was needed.

## Log Event Names

The Functional Design and the requirements write the log events with dots (`import.outcome`, `outcome.already_set`, `email.not_sent`). The logger of u1 writes snake_case event names (`import_outcome`, `outcome_already_set`, `email_send_failed`, `email_limit_reached`). The code names are kept, because the alarms of u1 filter on them. The two spellings mean the same thing.

| Name in the requirements | Event in the code | Written when |
|--------------------------|-------------------|--------------|
| `import.outcome` | `import_outcome` | Just before the outcome object is created, for every start event that ends (NFR5.11) |
| `outcome.already_set` | `outcome_already_set` | When the outcome object already existed (NFR5.12) |
| `email.not_sent` | `email_not_sent` | For each role whose email was not accepted (NFR5.13). The events `email_send_failed` and `email_limit_reached` of u1 stay as the detail of why; `email_not_sent` is the one record per role that the outcome refers to |
| (new) | `marker_unreadable` | The front door found a marker it could not read (see `security-design.md`) |
| (new) | `holding_not_emptied` | The holding area could not be fully deleted after the marker (BR9.6) |
| (new) | `leftover` | One record for each Leftover, up to 200, with the cleaned location (NFR5.14) |

## Design Decisions

| Requirement | Design |
|-------------|--------|
| NFR5.11 Outcome line | `_finish` writes `import_outcome` before it creates the outcome object (u1 writes it after). The record carries `eventId`, `importId`, `outcome`, `reason`, `removalComplete`, `emailSent`, `findingsTotal`, `olderFilesRemoved`, `leftoversTotal`, and the step durations. It is written by the same function for rejected, refused, and own-marker events |
| NFR5.12 Already set | When the conditional create reports that the outcome existed, `_finish` writes `outcome_already_set` with the `importId` and `eventId` only |
| NFR5.13 Email not sent | The notifier result names each role not reached and the kind of problem (`service-error` or `daily-limit`). The coordinator writes one `email_not_sent` record for each, with the fields `recipientsNotReached` (one role name) and `emailProblem` |
| NFR5.14 Leftovers | One `leftover` record each, using the allowed fields `kind`, `place` (the area), and `fileName` (the cleaned location), then the total in `leftoversTotal` of the outcome line |
| NFR5.15 30 days | No change: the `state/` expiry of 30 days and the log retention of 30 days are as u1 built them |
| NFR5.16 Allow-list | The allow-list gains exactly these fields: `eventId`, `emailSent`, `recipientsNotReached`, `emailProblem`, `olderFilesRemoved`, `leftoversTotal`, `frontDoorSeconds`, and `proofSeconds`. No field for an address or an event value is added. A test checks that the list equals the old list plus these fields |
| NFR5.17 Durations | `frontDoorSeconds` and `proofSeconds` join `downloadSeconds`, `scanSeconds`, and `keepSeconds` as plain numeric fields of the outcome line. The coordinator of u1 writes these durations as plain fields and no `_aws` envelope, and the stack of u1 turns them into metrics with value metric filters on the numeric field; Infrastructure Design adds two such filters for the new fields |
| Finding the event from its email | The rejection email gives the eventId (BR6.3) and every `import_outcome` line carries `eventId`, so a person finds a rejected event's line by the eventId in the email. (The review of the NFR Requirements pointed out that u1's lines had no eventId.) |

## Alerts

| Requirement | Change in the stack (done in Infrastructure Design) |
|-------------|-----------------------------------------------------|
| NFR5.18 Alert (a) | The filter changes from `outcome = removed-email-failed` to `event = import_outcome` and `emailSent = false`. The alarm keeps its name, period, and topic. A test produces the line for a rejected event with `emailSent` false |
| NFR5.19 Alert (b) | No change is needed. The filter of u1 already counts any `import_outcome` line with `removalComplete = false`, whatever the outcome, so it also covers the outcome stored with a holding folder that stayed. Infrastructure Design confirms it in the template test |
| NFR5.20 Counts by outcome | The per-outcome count filters get two more values, `rejected` and `refused-already-stored`. No alarm is added for their number |
| NFR5.21 15 minutes | Unchanged |
| Alert (e) | The record `email_limit_reached` gets the field `kind` (`failure` or `notice`) to say which counter was reached, using the allowed field `kind`. The alarm stays as it is |
| Alert (d) text | The message of the dead-letter alarm says to open `state/<importId>/record.json` first and to look for files without a marker in the final location if the step was `keeping` (see `reliability-design.md`) |

## Quality Targets (NFR6)

- **NFR6.5** The recorded project check stays `.venv\Scripts\python -m pytest --cov --cov-fail-under=80`.
- **NFR6.6** The rule-coverage test of u1 lists the rule IDs BR6.1 to BR11.3 as well and fails if one has no test that names it.
- **NFR6.7** Each forced-failure case of `functional-spec.md` is a named test with an injected clock and a stand-in storage client that can fail a chosen call. No test waits for real time or uses a real account.
- **NFR6.8** Black and Ruff are run as before. Any change to a test of u1 is listed in the code summary with the rule that required it.

## Dashboards

None, as in u1 (NFR5.9).

## Assumptions & Open Questions

- [assumption] The metric filters of u1 (count filters and value filters on numeric log fields) can carry the new counts and durations without a new agent.
- [assumption] The field `recipientsNotReached` holds one role name in an `email_not_sent` record, and a comma-separated list of role names (`sender,team`) in the outcome line. The logger keeps both as plain text.
- Open question: A burst of rejected events has no alarm of its own (NFR5.20). The counts by outcome are there for the owner to look at.
