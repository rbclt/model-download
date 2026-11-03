# Reliability Design: u1-import-slice

This describes how the thin end-to-end slice meets the requirements in `reliability-requirements.md`. It uses the components in `logical-components.md`, the lifecycle and failure rules in `functional-spec.md`, the technology in `tech-stack-decisions.md`, and the failure behaviour in `contract-summary.md`. The answers behind it are Q2 B and Q4 A in `nfr-design-questions.md`. The other requirement documents are `performance-requirements.md`, `security-requirements.md`, `scalability-requirements.md`, and `observability-requirements.md`.

## Design Decisions

| Requirement | Design |
|-------------|--------|
| NFR4.1 (one outcome) | The ImportCoordinator is a state machine with the transitions in `functional-spec.md` and no others. The outcome is written once to `state/<importId>/outcome.json` with a conditional write that fails if the object exists, so a second write cannot change it. Every path out of the machine goes through one function that sets the outcome |
| NFR4.2 (dead run) | A crash, the 7-hour watchdog, or a lost task leaves the message in flight. The heartbeat stops with the process, so the message becomes visible again within 15 minutes and the queue moves it to the queue dead-letter queue at the next receive (max receive count 1), which frees the message group and raises the alert in NFR5.8. The holding area expires after 2 days. The import record lets the team clean the final location by hand. The active clean-up that sends the failure email for a dead run is built in u2-import-rules |
| NFR4.3 (clean-up can be repeated) | Removal lists the keys under the import's prefixes and deletes them in batches. Deleting a key that is not there is not an error. A repeat removal gives the same end state |
| NFR4.4, NFR4.10 (retries) | One retry helper wraps every Hugging Face request: 3 retries per file; waits of 5, 10, and 20 seconds; if the answer gives a Retry-After time it is used, up to 60 seconds; a counter adds up all waits in the import and the next temporary problem after 30 minutes fails the import |
| NFR4.5 (no loop) | The removal step is entered once. Errors inside it are caught per delete and per email recipient, recorded, and the machine moves to `done`. There is no code path from `removing` back to `removing` |
| NFR4.6, NFR4.11 (event delivery) | The rule target is the queue, with a retry policy of up to 24 hours and 185 attempts and an event dead-letter queue. A message in either dead-letter queue raises an alarm |
| NFR4.7 (durability) | S3 standard storage, no extra copy |
| NFR4.8 (import record) | `state/<importId>/record.json` in the holding bucket is rewritten before each step with the status, the step, the model, the version, the time, and, in the keep step, the number of files copied and the last key copied (rewritten after each file). Because the final prefix is fixed (`models/<name>/<version>/`), the full list of copied keys is not stored: removal and hand clean-up work from the prefix. This amends the verification of NFR4.8 from a list of keys to a prefix plus a count. It lives outside the running program. Objects under `state/` expire after 30 days |
| NFR4.9 (expiry) | Lifecycle rules: `holding/` objects expire after 2 days; unfinished multipart uploads abort after 1 day in both locations; `state/` objects expire after 30 days |

## Message Life Cycle

The importId is derived from the event id, so the same message always maps to the same `state/<importId>/` objects.

| Moment | What the worker does with the queue message |
|--------|---------------------------------------------|
| Message received | Start the heartbeat: every 5 minutes set the visibility to 15 minutes again. Check whether `state/<importId>/outcome.json` already exists; if it does, the import finished earlier, so delete the message, log it, and take the next one |
| Event fails the check (BR1.1) | Log the bad field, delete the message at once. A bad event never blocks the queue and never reaches the dead-letter queue |
| Outcome object written (the import is over) | Delete the message at once, then stop the heartbeat |
| Caught fatal error, SIGTERM during an import, or the 7-hour watchdog | First set the message visibility to 0, then exit with a non-zero code. The next receive moves the message to the queue dead-letter queue (max receive count 1) and frees the group, and the alert fires |
| Hard crash (task lost, no chance to act) | The heartbeat stops, so the message is visible again after at most 15 minutes. The next receive moves it to the dead-letter queue, so the queue is blocked for at most about 15 to 20 minutes, not hours |
| Crash after the outcome object but before the delete | The message returns within 15 minutes, goes to the dead-letter queue, and raises an alert for an import that finished. The alert text tells the team to check the outcome object first. This one narrow window is accepted |

A resent event is a new message and is not blocked by a message in the dead-letter queue. Waiting events behind a crashed import start after the stuck message is released (about 15 to 20 minutes).

## Completion Marker (Q4 A)

The keep step copies every file, then writes the object `models/<name>/<version>/_COMPLETE` last. It holds the import id, the list of files with their sizes, and the time. This refines BR4.1 as follows, and Code Generation builds it this way:

- A model counts as complete only when its marker exists.
- The duplicate check (BR4.1) looks for the marker, not for any object. Unfinished files from a crashed run do not block a retry; the retry may overwrite them.
- A reader of the final location must check for the marker. This is a documented rule for readers, and the later step that loads a model into Amazon Bedrock must follow it.
- The normal failure path still deletes what it copied (BR5.2). Removal deletes the marker first and only then the model files, and the keep step writes nothing after the marker except the outcome record, so a marker never outlives its files.
- Reader rule: a reader reads only the files listed in the marker and compares their sizes with the marker. A file that is not listed is ignored.
- Recorded change to NFR4.2: unfinished files in `models/` left by a crashed run do not expire. They stay until a retry overwrites them or a person removes them using the import record and the alert. Only the holding area (2 days) and unfinished uploads (1 day) expire.

## Failure Handling Summary

| Failure | Handling |
|---------|----------|
| A file download fails | Retry rules above, then fail the import, remove, email |
| The scan finds something | Fail the import, remove, email |
| The copy step fails in the program | Remove the copied files and the holding area, email |
| The worker process crashes during the copy | Unfinished files stay without a marker; the alert fires; a retry overwrites them |
| The email service refuses one recipient | The other recipient still gets its own message; the problem is logged |
| The email service is down | The outcome is removed-email-failed; the alert fires |
| The task cannot start | The message stays in the queue; the alarm on the age of the oldest message fires |

## Assumptions & Open Questions

- [assumption] Writing the outcome with a conditional write (`If-None-Match`) is supported by Amazon S3 in the Region. If it is not available in the SDK version pinned, a record-level check in code is used instead.
- [assumption] The queue's one-try setting is right for a 6-hour job: a retry would repeat an import that has already failed once, and the owner can resend the event.
- Open question: A readers' rule for the marker has no owner yet beyond this document. The owner names the future reader (see `security-requirements.md`).
