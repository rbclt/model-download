# Monitoring Design: u2-import-rules

This is the AWS-specific monitoring that implements `observability-design.md` for this Unit. It uses the resources in `infrastructure-specification.md`, the logical components in `logical-components.md`, the requirements in `observability-requirements.md`, the lifecycle in `functional-spec.md`, and the boundaries in `contract-summary.md`. The monitoring of u1-import-slice (the log group, the SNS topic `model-import-alerts`, alarms (a) to (f), the stuck alarm, the budget, the queue and task metrics) stays; this document lists only what changes or is added. The metrics come from CloudWatch Logs metric filters on the JSON fields of the worker log, as in u1, and need no agent and no extra permission.

## Metrics & KPIs

| Metric | Source | Threshold | Why it matters |
|--------|--------|-----------|----------------|
| `ImportOutcome` by outcome, now with `rejected` and `refused-already-stored` | Count filters on the `import_outcome` line (`outcome` field) | Each start event adds exactly one | Shows how many events are bad or repeated, and proves one outcome per start event (NFR4.12, NFR5.20) |
| `FrontDoorSeconds` | Value filter on the field `frontDoorSeconds` | Over 5 seconds is a miss of NFR1.11 | Checks the front door |
| `ProofSeconds` | Value filter on the field `proofSeconds` | Over 900 seconds with the email is a miss of NFR1.13 | Checks the removal proof |
| `DeadlineStopSeconds` (new) | A new value filter with the pattern `event = import_outcome` and `reason = "the time limit of 6 hours passed"`, taking `$.totalSeconds`. The text is the constant `REASON_TIME_LIMIT` of the application code (`performance-design.md`); the filter and the constant are tested together. The existing `TotalSeconds` filter has no condition on the reason and is not changed | Over 22,200 seconds (6 hours 10 minutes) is a miss of NFR1.10; a count of the same pattern shows how many imports hit the limit | Shows how far past the deadline an import ran |
| `EmailNotSentSender` and `EmailNotSentTeam` (new) | Two count filters on `event = email_not_sent` and `recipientsNotReached = sender` or `team` (the stack's count filter has no dimension, so there is one filter for each role) | Any value is visible; alert (a) alarms on the outcome | Shows which recipient was not reached (NFR5.13) |
| `LeftoversTotal` | Value filter on the field `leftoversTotal` | Any value above 0 raises alert (b) through `removalComplete` | Shows the size of an incomplete removal (NFR5.14) |
| `OlderFilesRemoved` | Value filter on the field `olderFilesRemoved` | Any value above 0 is worth a look | Shows that debris of an earlier crashed import was removed |
| `MarkerUnreadable` | Count filter on `event = marker_unreadable` | Any value above 0 | Shows a damaged or foreign marker in the final location |

## Alerts

| Alert | Condition | Severity | Routes to |
|-------|-----------|----------|-----------|
| (a) Email did not reach everyone | New metric `EmailNotReached` from a filter with the pattern `event = import_outcome` and `emailSent = false`, one or more in 5 minutes. Widened from u1, where the alarm read the count of the outcome `removed-email-failed`: it now also covers a rejected or already-stored event, and an import removed with only one of its two emails accepted. The per-outcome count of `removed-email-failed` is kept as it was | High | Alert topic |
| (b) Removal not complete | Unchanged: `event = import_outcome` and `removalComplete = false`, one or more in 5 minutes. It also covers the outcome stored with a holding folder that stayed, and a failed import that left the marker | High | Alert topic. The text tells the team to read the full list in the log (`event = leftover`) |
| (d) Message in a dead-letter queue | Unchanged conditions. New description text for the alarms `d-queue` and `d-reminder` (not for `d-event`): open `state/<importId>/record.json` first; if its step is `keeping`, look for files without `_COMPLETE` for that model version under `models/` and remove them by hand or by running the import again | High | Alert topic |
| (e) Email limit reached | Unchanged condition: `event = email_limit_reached`. The record now has the field `kind` (`failure` or `notice`). The alarm description, which says 50 in u1, names both limits: 30 for failure emails and 20 for the others | Medium | Alert topic |
| (c), (f), (stuck), (cost) | Unchanged | As in u1 | Alert topic |

No new alarm is added. A burst of rejected events and a damaged marker are visible in the metrics above and, for a damaged marker, in the refusal email to the team (the reason says the marker could not be read).

Alerts (a) to (f) keep periods of 1 to 5 minutes, so they reach the team mailbox within the 15 minutes of NFR5.8 and NFR5.21.

## SLIs / SLOs

| SLI | SLO target | Measurement window |
|-----|------------|--------------------|
| Share of start events that end with exactly one outcome object | 100 percent | Every start event |
| Share of rejected and already-stored events whose email reached the team mailbox | 100 percent; a miss raises alert (a) | Every such event |
| Time from the deadline to the end of an import's work, for an import stopped by the time limit | 10 minutes or less | Every import stopped by the time limit |
| Share of failed imports whose removal was proved complete | 100 percent; any miss raises alert (b) | Every failed import |
| Time from a failure to the alert reaching the team mailbox | Under 15 minutes | Every alert |

## Logs & Tracing

- **Logs:** unchanged: one JSON record per line to `/model-import/worker`, 30-day retention, written through the allow-list. The allow-list gains eight fields (`observability-design.md`), and the filters above read only fields on that list. Container start-up output goes to the same group.
- **Events added to the log:** `import_outcome` before the outcome object, `email_not_sent`, `marker_unreadable`, `holding_not_emptied`, and one `leftover` record each (up to 200).
- **Tracing:** none, as in u1. The `importId` is the correlation id, and the `eventId` is added so a rejected event can be found from its email.
- **Dashboards:** none (NFR5.9). A saved Logs Insights query by `eventId` or `importId` goes into the run book written in Build and Test.

## Assumptions & Open Questions

- [assumption] CloudWatch metric filters accept the boolean and numeric fields used above, as they do for u1.
- [assumption] The 22,200-second threshold for a deadline stop is read from the new `DeadlineStopSeconds` metric; it is a check on the metric for the person who reads it, not an alarm.
- Open question: The exact alarm names and message texts are set in Code Generation (open since u1).
