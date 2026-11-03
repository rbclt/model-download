# Security Requirements: u2-import-rules

These are the security requirements that this Unit adds. They detail inception NFR3 (no secret, token, or credential in code or logs) in `requirements.md`, and they use the rules in `rules.md` (BR6.1 to BR6.4, BR7.1 to BR7.2, BR9.1 to BR9.6, BR10.2, BR11.1 to BR11.3), the steps in `functional-spec.md`, and the boundaries in `contract-summary.md` (C1 start event, C3 failure email, C4 locations). All security requirements of u1-import-slice (NFR3.1 to NFR3.16) stay in force and are not repeated. The team's security policy is the only compliance rule (feasibility Q4), and the security team gives input but does not approve the design before deployment (`team-practices.md`). Each requirement carries the ID of the inception NFR it details.

## Authentication and Authorization

| ID | Requirement | Source |
|----|-------------|--------|
| NFR3.17 | This Unit adds no new permission. The one role of u1 (NFR3.4 of u1) already allows read, write, list, and delete under the `holding/`, `state/`, and `models/` prefixes and email through the one verified sender identity, and that is all this Unit needs. The deployment check that searches for `s3:*` and `Resource: *` stays and must still pass. | NFR3 |
| NFR3.18 | The emails for rejected and already-stored events are limited to 20 of the 50 emails a day (NFR3.16 of u1). Each email to one recipient counts as one. The 20 are counted apart from the other 30, which stay for failure emails of imports that ran. An event past the limit still ends with its outcome and the log line, the email problem is recorded (BR10.3), and no email is sent for it. This stops a burst of bad events, or a permitted identity misused, from sending mail to many addresses or from using up the emails that tell the team about failed imports. | NFR3, threat table of u1 |

## Data Protection

| ID | Requirement | Source |
|----|-------------|--------|
| NFR3.19 | No value taken from a rejected event appears anywhere: not in the email, the log, the record, the outcome, or the marker. The email of a rejected event names the failed fields and gives the eventId and eventTime, which the event bus set, and nothing else from the event (BR6.3). The record of a rejected event keeps a model name or version only when it passed the format check (BR11.1). | NFR3 |
| NFR3.20 | The sender's email address is never written to a log line, the record, the outcome, or the completion marker. It exists in memory for the time of the import and in the emails sent to it. The outcome stores only the roles (sender, team) that were not reached (BR10.3). | NFR3 |
| NFR3.21 | The completion marker holds the importId, the model name, the exact version, and the time, and nothing else. A reader of the final location learns nothing about the sender. | NFR3 |
| NFR3.22 | Records and outcomes under `state/` follow the same encryption, TLS, and public-access rules as the other objects (NFR3.5 to NFR3.7 of u1). They expire after 30 days (NFR5.15). | NFR3, Q1 |
| NFR3.23 | A log line, a record, and an email about leftovers or findings carry only cleaned storage locations, file names, places, and kinds (BR9.4, BR11.2, NFR3.8 of u1). Names are cleaned the same way for leftovers as for findings: control characters are replaced and the name is cut to 200 characters. | NFR3 |

## Input and Content Handling

| ID | Requirement | Source |
|----|-------------|--------|
| NFR3.24 | The removal after a failed move deletes only inside the folder `models/<model name in lowercase>/<exact version>/` of the import, and only when that folder has no marker or the marker was written by this import (BR9.2). It never deletes in another version's folder or another model's folder, and never deletes a folder whose marker holds another importId. Both names were checked by BR1.3 before they became part of a path. | NFR3 |
| NFR3.25 | A file of a model that is named `_COMPLETE` must not be able to pass as the completion marker, and the marker is never taken from a file of the model. The marker is written only by the keep step, as the last object, with content that names the importId. A marker that cannot be read or parsed is not trusted as a stored model of this import. The exact result for that case is set in NFR Design. | NFR3 |
| NFR3.26 | The deadline and the clock are read from the application's own clock, not from the event or from any file of the model. | NFR3 |

## Threat Considerations

| Threat | Mitigation |
|--------|------------|
| A burst of bad events sends mail to many addresses or uses up the emails for failed imports | NFR3.18: 20 of the 50 emails a day for rejected and already-stored events; only permitted identities can send events (NFR3.3 of u1) |
| A bad event carries a secret or personal value, and the rejection email or log repeats it | NFR3.19, NFR3.20, BR6.3: no value from the event is repeated |
| The removal after a failed move deletes a complete model, another model, or another version | NFR3.24 and BR9.2: deletes only in this import's folder, and not when a marker of another import is there; tests with sibling folders |
| A hostile model file is named like the completion marker | NFR3.25: the marker is written only by the keep step and carries the importId |
| The sender's address leaks through logs, records, or the marker | NFR3.20, NFR3.21 |
| A permitted identity floods the application with events | One import at a time (NFR2.3 of u1), the daily email limit, and the cost alarm (NFR5.10 of u1) |
| The records of failed imports leak the content of a finding | NFR3.8 of u1, NFR3.23, BR11.2: no values, cleaned names |

## How Each Requirement Is Verified

| IDs | Verified by |
|-----|-------------|
| NFR3.17 | The deployment check of the synthesised policy for `s3:*` and `Resource: *`, run again for the changed stack |
| NFR3.18 | A test with a fake email service and a counter: the 21st email for rejected or refused events is not sent, the 31st failure email is not sent either, and each limit is counted apart |
| NFR3.19, NFR3.20, NFR3.21, NFR3.23 | Tests that send a bad event with a planted secret in every field and a failed import with a planted address, then search every log record, record, outcome, marker, and email text for them |
| NFR3.22 | A deployment check that the settings of the holding bucket apply to `state/` (already built in u1) |
| NFR3.24 | Tests with a sibling version folder, another model folder, and a marker of another importId present: none of them is touched by the removal |
| NFR3.25 | A test where the model holds a file named `_COMPLETE` and a test with a damaged marker |
| NFR3.26 | A unit test that the deadline comes from an injected clock only |

## Assumptions & Open Questions

- [assumption] Anyone who may put an event on the bus can make the application email any well-formed sender address in a rejected event, as in u1's open item R-07. The limit of 20 emails a day (NFR3.18) bounds the harm, and the owner has accepted that any well-formed address is allowed (u1 Q4).
- [assumption] The 20 of 50 split (NFR3.18) is a size chosen by the architect. The owner can change the numbers without changing the rule.
- Open question: What should an import do when the marker exists but cannot be read, or when a model file is named `_COMPLETE`? NFR3.25 requires that neither is trusted; NFR Design fixes the exact result.
- Open question: The common AWS account's SES sandbox status (open since u1) also decides whether emails to a sender address that is not verified can be delivered at all.
