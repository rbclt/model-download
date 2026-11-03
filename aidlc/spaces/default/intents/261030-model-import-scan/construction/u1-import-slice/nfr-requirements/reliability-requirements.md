# Reliability Requirements: u1-import-slice

This describes how the thin end-to-end slice behaves when something goes wrong. It details inception NFR4 (exactly one outcome) in `requirements.md` and uses the rules in `rules.md`, the lifecycle in `functional-spec.md`, and the failure behaviour in `contract-summary.md`. Each requirement carries the ID of the inception NFR it details.

| ID | Requirement | Target | Source |
|----|-------------|--------|--------|
| NFR4.1 | Every import ends in exactly one outcome: stored, removed-with-email, or removed-email-failed (BR4.3, BR5.5). The outcome is recorded once and never changed. u2-import-rules adds the refused outcome. | One outcome per import | NFR4 |
| NFR4.2 | If the part of the application that runs an import stops unexpectedly (a crash, a restart, the 7-hour backstop of NFR1.8), nothing from the import lives on. In u1 this is met by three things: the durable record in NFR4.8, the expiry of leftover files in NFR4.9, and the alert in NFR5.8 that tells the team an import stopped. The active cleanup that removes the files at once and sends the failure email for a dead run is built in u2-import-rules together with the 6-hour rule. | Leftovers expire within 2 days; team alerted within 15 minutes | NFR4 |
| NFR4.3 | The removal can be run twice with the same result. Deleting something that is already gone is not an error. | Safe to repeat | NFR4 |
| NFR4.4 | A temporary download problem is retried up to 3 times per file, with a wait that grows between tries (BR2.4). The waits are 5, 10, and 20 seconds, within the budget in NFR4.10. | 3 retries per file; waits 5, 10, 20 s | NFR4 |
| NFR4.5 | An error in the removal or in the email never starts a second removal and never leaves the import open (BR5.5). The email cannot stop the removal. | No loop | NFR4 |
| NFR4.6 | A start event that arrives while the application is being deployed or is briefly unavailable is not lost: the event service keeps trying to deliver it for up to 24 hours, and an event it still cannot deliver goes to the dead-letter queue in NFR4.11. | Delivery retried for up to 24 hours | NFR4 |
| NFR4.7 | A model stored in the final location is as durable as S3 itself. No separate backup is made. | S3 durability | NFR4 |
| NFR4.8 | Before each step, the import record (importId, model name, version, status, and the keys copied so far) is written to a durable place outside the running program, so that a dead run can be cleaned by importId. The place is chosen in NFR Design. | Written before every step | NFR4 |
| NFR4.9 | The holding area expires its objects after 2 days, and unfinished multipart uploads in both locations are aborted after 1 day, by lifecycle rules on the S3 locations. This is the last-resort cleanup for a dead run. | 2 days; 1 day | NFR4 |
| NFR4.10 | The total waiting time spent on retries in one import is at most 30 minutes. A wait never exceeds 60 seconds, and when a 429 answer gives a wait time (Retry-After), that time is used up to that maximum. After the budget is used, the next temporary problem fails the import. | 30 minutes total; 60 s per wait | NFR4 |
| NFR4.11 | Start events that cannot be delivered are kept in a dead-letter queue, and the alert in NFR5.8 fires when one arrives. An event is therefore never silently lost after the 24-hour delivery retries (NFR4.6). | Dead-letter queue with alert | NFR4 |

## How Each Requirement Is Verified

| IDs | Verified by |
|-----|-------------|
| NFR4.1, NFR4.5 | Unit tests that force each failure and check one outcome and no second removal |
| NFR4.2, NFR4.8 | A test that stops a run after each step and checks the record holds the keys needed to clean it; a deployment check of the expiry rule for the rest |
| NFR4.3 | A unit test that runs the removal twice |
| NFR4.4, NFR4.10 | Unit tests with a stand-in host that fails a set number of times; the wait times are recorded in the test |
| NFR4.6, NFR4.11 | A deployment check of the retry period and the dead-letter queue; a test event sent to a stopped target lands in the queue |
| NFR4.7 | Not tested; it follows from S3 |
| NFR4.9 | A deployment check of the lifecycle rules in the synthesised infrastructure |

## Availability

No availability target is set. Imports are triggered by people a few times a week (NFR2.2), and a failed or missed import is shown by an email or by the absence of one and can be sent again. The application has no component that must be always on.

## Recovery

- A failed import is recovered by sending the same event again after the cause is fixed. The second run finds the final location empty because the failed run removed what it created (BR4.1, BR5.2).
- An import whose removal was incomplete is cleaned by hand using the list in the email (the list of leftovers is built in u2-import-rules). In u1 the email states that the removal was not complete (BR5.3).

## Assumptions & Open Questions

- [assumption] The waits in NFR4.4 are the starting plan. NFR Design may change the numbers inside the 30-minute budget of NFR4.10.
- [assumption] The event service redelivers an event for up to 24 hours when the target is not available (a standard behaviour of Amazon EventBridge for its targets). NFR Design confirms how the chosen target behaves.
- Open question: Who is told when an import stopped unexpectedly (NFR4.2) and the failure email also failed? This is covered by the alarm in `observability-requirements.md` (NFR5.5).
