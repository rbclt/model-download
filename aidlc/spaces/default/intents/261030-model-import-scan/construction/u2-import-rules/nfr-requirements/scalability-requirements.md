# Scalability Requirements: u2-import-rules

This describes how much work the finished application must carry once this Unit is added. It details inception NFR2 (size) in `requirements.md` and uses the rules in `rules.md`, the steps in `functional-spec.md`, and the boundaries in `contract-summary.md`. The volume and the one-import-at-a-time design stay as u1 set them (about 5 imports a day, a FIFO queue with one message group). Each requirement carries the ID of the inception NFR it details.

| ID | Requirement | Target | Source |
|----|-------------|--------|--------|
| NFR2.8 | Rejected and already-stored events use the same queue and the same one-at-a-time worker as imports. A rejected or refused event takes the worker for a few seconds, not for an import. A burst of such events, for example 100 bad events sent by mistake, is handled one after the other and does not stop a real import for longer than the time to handle them (NFR1.12 gives 1 minute for each). | 1 minute per rejected or refused event | NFR2, FR1.3, FR1.4 |
| NFR2.9 | The emails of rejected and already-stored events are limited to 20 of the 50 emails a day of u1 (NFR3.16 of u1), so a burst of bad events cannot use up the emails that tell the team about failed imports (NFR3.18). Beyond 20, the event still ends with its outcome and the log line, and the email problem is recorded (BR10.3). | 20 emails a day | NFR2, NFR3 |
| NFR2.10 | The record of one import stays small: at most 200 findings are listed in it (BR11.2), so one record stays under 100 KB, whatever the size of the model. The full list stays in the logs. | 200 findings; 100 KB per record | NFR2, FR6.1 |
| NFR2.11 | The records under `state/` grow by 2 objects for each start event: the record and the outcome. The email counters add 2 objects a day (NFR3.18). At 5 imports a day and 30 days of retention (NFR5.15), fewer than 500 objects exist at a time, even if a few bad events are added. | Fewer than 500 objects | NFR2, FR6.1 |
| NFR2.12 | A failure email lists at most 20 leftovers, and the log holds the full list (BR9.4). A removal that leaves more than 20 leftovers still ends and is complete in what it reports. | 20 listed | NFR2, FR5.3 |

## How Each Requirement Is Verified

| IDs | Verified by |
|-----|-------------|
| NFR2.8 | A test that sends 100 stand-in bad events and checks that each ends with one outcome, in order, with the time for each logged |
| NFR2.9 | A test that sends bad events past the limit of 20 and checks that no email beyond 20 is sent, that the outcome is still set, and that the problem is logged |
| NFR2.10, NFR2.12 | Tests with a scan of 500 findings and a removal with 30 leftovers |
| NFR2.11 | A deployment check of the 30-day expiry of `state/` (already built in u1) |

## Scaling Triggers and Capacity

- No scaling trigger is needed. The design stays at one import at a time, with no automatic scaling out beyond what u1 built.
- A larger volume, larger models, or more than one import at a time are new requirements and need a new design.

## Assumptions & Open Questions

- [assumption] A burst of bad events is a mistake or a test, not an attack, because only identities in the account can send events (NFR3.3 of u1). The limit of 20 emails protects the failure emails, and the cost alarm of u1 (NFR5.10) shows unusual volume.
- Open question: Should a burst of rejected events raise an alert of its own (for example more than 10 in an hour)? This version does not add one; the logs hold every rejection.
