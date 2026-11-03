# Observability Design: u1-import-slice

This describes how the thin end-to-end slice meets the requirements in `observability-requirements.md`. It uses the components in `logical-components.md`, the lifecycle in `functional-spec.md`, the technology in `tech-stack-decisions.md`, and the boundaries in `contract-summary.md`. The other requirement documents are `performance-requirements.md`, `security-requirements.md`, `scalability-requirements.md`, and `reliability-requirements.md`. The design needs no answer from `nfr-design-questions.md` beyond the queue and dead-letter queues in Q2 B.

## Logging Design

- One logger writes one JSON record per line to the worker's CloudWatch Logs group, kept for 30 days (NFR5.4, NFR5.6).
- **Allow-list logging (NFR5.5).** A record can contain only these fields: `time`, `level`, `event`, `importId`, `modelName`, `exactVersion`, `status`, `step`, `outcome`, `reason`, `removalComplete`, `fileName` (cleaned), `place`, `kind`, `bytes`, `durationSeconds`, `findingsTotal`, `matchesTotal`, `emailsSentToday`, the per-phase durations `downloadSeconds`, `scanSeconds`, `keepSeconds`, `totalSeconds`, the metric envelope field `_aws` with the metric values it names, and `error` (a short code, not a message from a library). Any other field is dropped before writing. This stops a secret or a matched value reaching a log by accident.
- **Correlation (NFR5.1).** Every record carries the `importId`, which is derived from the event id. The queue message id is logged once at the start of the import.
- **Status changes (NFR5.1).** One record per transition in `functional-spec.md`.
- **Outcome (NFR5.2).** One record with `event = import_outcome` per import, written together with the conditional write of the outcome object (NFR4.1), so it is written exactly once.
- **Findings (NFR5.3).** One record per finding (file, place, kind), up to the cap of 100 per file, plus one record per file with `matchesTotal` when the cap applies (BR3.7).

## Metrics

Metrics come from the logs, so there is no second code path:

- The worker writes metric values in the CloudWatch Embedded Metric Format inside the same JSON records, for these names (NFR5.7): outcome count by outcome, total duration, bytes downloaded, download duration, scan duration, keep duration, findings count, and emails sent.
- Metric filters count `outcome = removed-email-failed` and `removalComplete = false`.

## Alerts (NFR5.8, NFR5.10)

All alarms send to one SNS topic with the team mailbox subscribed once.

| Signal | Source | Alarm |
|--------|--------|-------|
| (a) outcome removed-email-failed | Metric filter on the logs | One or more in 5 minutes |
| (b) removal not complete | Metric filter on `removalComplete = false` | One or more in 5 minutes |
| (c) the worker task stopped with an error, or hit the backstop | ECS task state change event for a stopped task with a non-zero exit code, sent to the alert topic; plus the queue dead-letter queue | One or more |
| (d) a message in a dead-letter queue | Visible messages in the queue dead-letter queue or the event dead-letter queue | One or more for 1 minute |
| (e) the 50-emails-a-day limit was reached | Metric filter on the log record `event = email_limit_reached` (the durable counter is in `security-design.md`) | One or more in 5 minutes |
| (f) an SES bounce or complaint | SES configuration set event destination | Each event |
| (cost) monthly cost | AWS Budgets | At 100 percent of the limit the owner sets; default 100 US dollars |
| (stuck) oldest message waits too long | Age of the oldest message in the import queue | More than 8 hours |

Alerts (a) to (f) reach the team mailbox within 15 minutes (NFR5.8) because their alarm periods are 1 to 5 minutes. The (stuck) alarm is outside that target on purpose: it covers a task that cannot start, and waiting 8 hours avoids false alarms behind a long import.

## Quality Targets (NFR6)

The design adds one test that lists every rule ID in `rules.md` and checks that a test names it (NFR6.4), and runs the formatter, the linter, and the coverage check as part of the recorded project check (NFR6.1 to NFR6.3).

## Dashboards

None in this version (NFR5.9).

## Assumptions & Open Questions

- [assumption] The Embedded Metric Format is written by the application's own logger; no extra agent is needed on Fargate.
- [assumption] The ECS task state change event is available on the default event bus of the account for rule (c).
- Open question: The log group name and the alarm names are chosen in Infrastructure Design.
