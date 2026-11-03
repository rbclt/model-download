# Business Rules: u2-import-rules

These are the rules that finish the thin slice. They are numbered `BRx.y` and continue the numbering of u1-import-slice, whose rules are BR1.1 to BR5.6, so every rule ID in the application is unique. Each rule traces to a requirement (`requirements.md`) and to the acceptance criteria of the stories this Unit implements (`stories.md`, `unit-of-work-story-map.md`). The Unit and its building blocks are in `unit-of-work.md` and `components.md`. The answers in `functional-design-questions.md` (Q1 to Q5, all option A) fix the outcome names, the 6-hour clock, the clean-up of older unfinished files, the content of the rejection email, and the record of every import.

Rules BR1.1 to BR5.6 of u1 still hold, except where a rule below changes one. The u1 rules that u2 changes or restates are:

- BR1.1: an event with a bad content is now rejected with an email as well as logged (BR6.1).
- BR1.2: the importId is derived from the eventId, as u1 built it (BR10.5).
- BR4.1: its first step now fails the import only when the completion marker exists, not when the folder holds any object (BR7.1, BR9.1).
- BR5.2: it may now also delete older unfinished files in a model version's folder that has no marker (BR9.2).
- BR5.5: it is extended to rejected and already-stored events (BR10.3).

```yaml
rules:
  - id: BR6.1
    statement: A start event whose content fails the check is rejected. Nothing is downloaded, one email is sent, and the import ends with the outcome rejected.
    category: validation
    applies_to: ImportCoordinator
    trigger: A message is received that is a start event (BR6.4) and its model name, exact version, or sender address is missing or fails the check of BR1.1 and BR1.3.
    logic: >
      THEN set the fields that failed in rejectedFields, start no download, write nothing to the holding area or the final location,
      send the rejection email (BR6.2, BR6.3), and end the import with the outcome rejected (BR10.1).
      The sender address fails the check when it is missing or is not a well-formed email address.
      This changes BR1.1, which in u1 drops such an event with one log line: from u2 on the event is rejected with an email as well, and the log line stays.
    violation: Not applicable.
    source: FR1.3, FR5.4

  - id: BR6.2
    statement: The rejection email goes to the team mailbox, and also to the sender when the sender's address is present and well-formed.
    category: policy
    applies_to: FailureNotifier
    trigger: An event is rejected (BR6.1).
    logic: >
      THEN send one email to the team mailbox. IF senderEmail is present and well-formed THEN send one email to that address as well.
      A sender address that is present but not well-formed counts as missing, so only the team mailbox is told (contract C3, C1).
      Each recipient gets its own email, as in u1 (BR5.3). The emails count against the daily limit of u1.
    violation: If an email cannot be sent, see BR10.3.
    source: FR1.3, FR5.4

  - id: BR6.3
    statement: The rejection email names the fields that failed and never repeats a value from the event.
    category: constraint
    applies_to: FailureNotifier
    trigger: The rejection email is written.
    logic: >
      THEN the subject is "Model import rejected: event <eventId>", where the eventId is the id the event bus gave the event. The subject of contract C3 for other emails, which names the model, is not used here because a rejected event has no checked model.
      The body says that the request was rejected and that nothing was downloaded, and lists each field that failed by its field name (modelName, exactVersion, senderEmail), each with the plain words "missing" or "not in the expected form".
      It gives the eventId and the eventTime, so the team can find the event in the logs.
      It never repeats a value from the event, not even a model name or version that passed the check, and never shows the sender's address.
    violation: Any value from the event in the email is a defect.
    source: FR1.3, NFR3

  - id: BR6.4
    statement: A message that is not a start event is not rejected here. It is handled as in u1.
    category: constraint
    applies_to: ImportCoordinator
    trigger: A message is received.
    logic: >
      IF the message does not have the expected event source and detail type, or its body cannot be read as an event, or it has no eventId or eventTime,
      THEN it is not a start event. No email is sent and no outcome is created. It is left to the dead-letter handling of u1, which alerts the team.
      Only a start event with a bad content is rejected (BR6.1).
    violation: Not applicable.
    source: FR1.3

  - id: BR7.1
    statement: A model version that is already stored is refused before any download, unless the stored copy is this same import's own earlier work.
    category: policy
    applies_to: FileCustodian
    trigger: A valid start event has been taken off the queue and its import is about to begin.
    logic: >
      THEN FileCustodian looks for the completion marker of models/<modelName in lowercase>/<exactVersion>/ before ImportCoordinator sets the status to downloading.
      IF the marker exists, read the importId it holds (BR9.1).
        IF that importId is this import's importId, an earlier attempt of this same message stored the model and stopped before its outcome existed (BR10.1). Finish that attempt: apply BR9.6 (remove the holding area), create the outcome stored, write the record and the log line, and send no email.
        OTHERWISE start no download, write nothing, send the already-stored email (BR7.2), and end the import with the outcome refused-already-stored (BR10.1).
      Files without the marker do not count as stored: the import goes on and the keep step deals with them (BR9.1, BR9.2).
      The check happens when the import's turn comes, not when the event was sent, so a second event for the same version that waited in the queue behind the first import is refused once the first has stored the model.
      IF the check itself cannot be made THEN it is a failed step (BR5.1): nothing exists to remove, and the failure email goes out.
      This adds an early check before the download and changes BR4.1: the keep step's own check now fails the import only when the marker exists, and it stays as a second line of defence. A folder with objects but no marker no longer fails the import.
    violation: Not applicable.
    source: FR1.4

  - id: BR7.2
    statement: The already-stored email goes to the sender and the team mailbox, and says where the model is.
    category: policy
    applies_to: FailureNotifier
    trigger: An event is refused because the model version is already stored (BR7.1).
    logic: >
      THEN send one email to the sender's address and one to the team mailbox with the model name, the exact version, the plain reason "this model version is already stored", and the final location models/<modelName in lowercase>/<exactVersion>/.
      Both values passed the check, so they may be shown. No download took place, so the email says nothing about removal.
    violation: If an email cannot be sent, see BR10.3.
    source: FR1.4, FR5.4

  - id: BR8.1
    statement: Every valid event has a deadline 6 hours after its import begins, and waiting in the queue does not count.
    category: constraint
    applies_to: ImportCoordinator
    trigger: A start event passes the content check (BR6.1).
    logic: >
      THEN startedAt is the moment the application took the event off the queue and began to handle it, and deadlineAt is startedAt plus 6 hours (owner's decision in Q2). Both go into the ImportRecord when it is first written.
      The time an event spent in the queue before that moment is not counted. The deadline does not change during an attempt.
      A message that a person redrives from the dead-letter queue after a stop starts afresh: it gets a new startedAt and deadlineAt, and the time of the earlier attempt does not count.
      The 7-hour watchdog of u1 stays as a backstop and is not changed.
    violation: Not applicable.
    source: NFR1, FR5.1

  - id: BR8.2
    statement: The deadline is checked at fixed points, including inside long pieces of work, and an import that has reached it stops there.
    category: policy
    applies_to: ImportCoordinator
    trigger: Any of the check points below is reached.
    logic: >
      The check points are: before the import moves to a new status, except a move that happens after the completion marker exists (BR9.6); before each file starts to download; inside a download, at every chunk written; before each retry of a download, and a retry wait is cut short so it never reaches past the deadline; before each file is scanned; before each file is copied to the final location; and inside the copy of a file larger than 5 GiB, which is a multipart copy, at every part.
      IF the current time is at or after deadlineAt at a check point THEN the import fails (BR8.4) at once, without starting the next piece of work.
      Three kinds of work cannot be interrupted: one copy call of a file up to 5 GiB, the scan of one text file, and the removal and the email (BR8.3). The scan of one text file ends within 5 minutes by u1's own limit (NFR1.9 of u1). A download that only trickles is ended by u1's floor of 1 MB per second over any 10 minutes (NFR1.7 of u1), so it cannot run for hours past the deadline.
      The target is that an import has stopped its work no later than 10 minutes after the deadline. The 7-hour watchdog of u1 (NFR1.8 of u1) is the hard backstop one hour after the deadline.
    violation: Not applicable.
    source: NFR1, FR5.1

  - id: BR8.3
    statement: The deadline never undoes a model that is complete, and it does not apply to the removal and the email.
    category: constraint
    applies_to: ImportCoordinator, FileCustodian
    trigger: The keep step writes the completion marker, or the removal and the email run.
    logic: >
      The last check point of the keep step is the one just before the completion marker is written (BR9.1). Once the marker exists the model is stored and the deadline no longer matters for this import (BR9.6).
      The removal and the email that follow a stop are not checked against the deadline. Their own target of 15 minutes (NFR1.6 of u1) and the 7-hour watchdog bound them.
    violation: Not applicable.
    source: NFR1, FR4.2, FR5.2

  - id: BR8.4
    statement: An import that reaches the deadline fails like any other failed import.
    category: policy
    applies_to: ImportCoordinator
    trigger: A check point finds the deadline reached (BR8.2).
    logic: >
      THEN the import follows BR5.1: status removing, everything removed (BR9.2, BR9.3), and one failure email with the plain reason "the time limit of 6 hours passed" (contract C3).
      The outcome is removed-with-email, or removed-email-failed when the email cannot be sent (BR10.3).
      A smaller model of about 16 GB is expected to end far inside the limit: u1 sets a typical time of 1 hour (NFR1.2 of u1) and a download speed of at least 20 MB per second (NFR1.3 of u1). Reaching the deadline means something is wrong.
    violation: Not applicable.
    source: FR5.1, NFR1

  - id: BR9.1
    statement: The model is moved to the final location in a fixed order, and it counts as stored only when the completion marker exists.
    category: constraint
    applies_to: FileCustodian
    trigger: The keep step runs (BR4.1).
    logic: >
      In this order. First copy every file of the model to models/<modelName in lowercase>/<exactVersion>/, one at a time, checking the deadline before each (BR8.2). Overwriting an older unfinished file of the same name is allowed.
      Then make the last deadline check (BR8.3) and write the completion marker _COMPLETE as the very last object. The marker holds the importId of this import, the model name, the exact version, and the time. It holds no sender address. No other rule writes that marker.
      A failure at any point before the marker write returns success, including the marker write itself, fails the import (BR5.1) and starts the removal (BR9.2).
      Nobody, including a reader of the final location, may treat a folder without the marker as a model.
    violation: The import fails (BR5.1).
    source: FR4.2

  - id: BR9.2
    statement: After a failed keep step, the removal deletes the marker first, then every file of the model version's folder when that folder has no marker.
    category: policy
    applies_to: FileCustodian
    trigger: The import is in status removing and the keep step had started.
    logic: >
      In this order. First, IF the marker write was attempted (it may have taken effect even when it reported an error), delete the marker, trying up to 3 times with a short wait. Then check the folder for a marker.
      IF the marker still exists after the tries and it holds this import's importId THEN delete nothing else in the final location, because the marker is written only after every file was copied, so the folder holds a complete model. Make the marker the first Leftover, with the area final-location and the plain note "the final location may hold a complete copy of this version although the import failed". removalComplete is false. This is the one case where a failed import leaves its files in the final location. A later event for this version is refused by BR7.1 as already stored.
      IF no marker exists THEN delete every object under models/<modelName in lowercase>/<exactVersion>/, the files this import copied and also older unfinished files from earlier imports of the same version (owner's decision in Q3). Count the files that this import did not copy as olderFilesRemoved (BR9.5).
      IF a marker exists that holds another importId THEN a complete copy appeared in the final location while this import ran. u1 prevents two imports from running together: the queue has one message group, and the heartbeat of u1 keeps the message hidden for as long as an import runs. Delete nothing in the final location, write the plain note "a complete copy of this version was found in the final location and was left alone" to the log, and do not count the folder as a leftover.
      This changes BR5.2, which says never to delete an object this import did not create: the exception is an object in a model version's folder that has no marker, because such a folder is never a usable model.
    violation: Not applicable.
    source: FR4.2, FR5.2

  - id: BR9.3
    statement: After the removal, FileCustodian checks that nothing of this import is left, and what is left makes the removal incomplete.
    category: policy
    applies_to: FileCustodian
    trigger: The deletes of the removal have ended (BR5.2, BR9.2).
    logic: >
      THEN list holding/<importId>/, the folder models/<modelName in lowercase>/<exactVersion>/ when the keep step had started and BR9.2 deleted from it, the incomplete uploads of this import, and the temporary files on the local disk.
      Every entry still found, and every entry whose delete failed, becomes a Leftover. A delete that failed does not stop the others (BR5.5).
      removalComplete is true only when there is no Leftover. The check is made for every failed import, whatever step failed. The records under state/<importId>/ are not part of the import's files and are never removed (BR11.3).
      IF the list itself cannot be made THEN removalComplete is false and a Leftover of kind temporary-resource names the area that could not be checked.
    violation: Not applicable.
    source: FR5.2, FR5.3, FR4.2

  - id: BR9.4
    statement: The failure email names the leftovers, at most 20, with the total.
    category: policy
    applies_to: FailureNotifier
    trigger: The failure email is written and removalComplete is false.
    logic: >
      THEN the email says that the removal was not complete and lists the first 20 Leftovers, each with its kind, its area (holding area, final location, or local disk) and its location, in the order area then location.
      IF there are more than 20 THEN a line "N leftovers in total" is added, and the log holds the full list.
      Locations are cleaned by the rule of BR5.4 before they go into the email or the log. The email says that the team should remove the leftovers by hand.
    violation: Not applicable.
    source: FR5.3

  - id: BR9.5
    statement: The failure email says how many older unfinished files were removed.
    category: policy
    applies_to: FailureNotifier
    trigger: The failure email is written and olderFilesRemoved is more than 0.
    logic: THEN the email adds one line saying that N older unfinished files of an earlier import of this version were also removed from the final location. The number is the only thing said about them.
    violation: Not applicable.
    source: FR4.2, FR5.3

  - id: BR9.6
    statement: Once the completion marker is written, nothing that follows can remove the model or change its outcome from stored.
    category: constraint
    applies_to: ImportCoordinator, FileCustodian
    trigger: The marker write has returned success.
    logic: >
      THEN the model is stored. What follows, in order: delete the holding area folder of this import, record the StoredModel, update the ImportRecord, and set the outcome stored (BR10.1). No email is sent.
      A failure in any of these steps never deletes the marker, never removes a file from the final location, and never starts the removal of BR9.2. The error is written to the log.
      IF the holding area could not be fully deleted THEN the outcome is still stored, removalComplete is false in the outcome and in the log line of BR10.2, and the leftovers are written to the log. u1's alert for an incomplete removal watches that field.
      The deadline is not checked after the marker (BR8.2, BR8.3).
    violation: Not applicable.
    source: FR4.1, FR4.2, NFR4

  - id: BR10.1
    statement: Every start event that the application finishes handling ends in exactly one of five outcomes, and the outcome object proves it.
    category: constraint
    applies_to: ImportCoordinator
    trigger: A start event has been handled to its end.
    logic: >
      The outcomes are stored, removed-with-email, removed-email-failed, rejected, and refused-already-stored (owner's decision in Q1).
      The outcome object state/<importId>/outcome.json is created once, after the removal and the email step, with a condition that fails when it already exists. A second creation changes nothing, sends no second email, and writes one log line saying the outcome was already set.
      When a message is received again and its outcome exists, nothing is done (rule of u1).
      The outcome is set on entry to status done, in the same step. While the application runs, no path ends without an outcome and no path sets two.
      The one gap is a stop of the process after work was done and before the outcome exists. u1 sets the maximum receive count of the queue to 1, so the message is not given to a worker again by itself: it goes to the dead-letter queue and the team is alerted. A person can redrive it. The redriven message starts afresh (BR8.1) and finds its own earlier work: a marker with its own importId finishes as stored with no email (BR7.1), and no marker means the import runs again from the start, as the failed attempt's removal or its overwritten files allow.
    violation: A second outcome for one importId is a defect.
    source: NFR4, FR5.1

  - id: BR10.2
    statement: Every outcome is written to the log, with the reason, and without any secret or personal value.
    category: constraint
    applies_to: ImportCoordinator
    trigger: The application is about to create the outcome object (BR10.1).
    logic: >
      THEN write one log line with the event name import.outcome and these fields only: importId, outcome, reason in plain words, removalComplete, emailSent, findingsTotal, olderFilesRemoved, and the durations of the steps that ran. The line is written just before the outcome object is created, so a stop between the two cannot lose it. A stop in that gap can make the same line appear twice after a redrive, with the same content.
      IF creating the outcome object reports that it already existed THEN write one more line with the event name outcome.already_set and the importId, and nothing else.
      The log never holds a sender address, a matched value, a token, a credential, or a value taken from a rejected event. File names are cleaned first (BR5.4).
      This is what makes the outcome of every import findable in the logs when the record of imports is not read (user story US6.1, third criterion).
    violation: Any such value in a log line is a defect.
    source: NFR5, NFR3, FR6.1

  - id: BR10.3
    statement: An email that cannot be sent is recorded in the outcome and in the log, and the import still ends with its single outcome.
    category: policy
    applies_to: ImportCoordinator, FailureNotifier
    trigger: The email service does not accept an email, or the daily limit of u1 is reached.
    logic: >
      THEN this Unit adds no retry of its own: whatever retry the notifier of u1 has stays as it is. The problem is written to the log with the event name email.not_sent, the kind of problem (service-error or daily-limit), and the role that was not reached (sender or team), and never an address.
      The outcome object records emailSent false and recipientsNotReached.
      For a failed import whose removal ended, the outcome is removed-email-failed (BR5.5 of u1). For a rejected event it stays rejected, and for an already-stored event it stays refused-already-stored: in both nothing existed to remove, so the outcome names what happened to the request.
      The import is never left open and the removal is never run again because of an email problem.
    violation: Not applicable.
    source: FR5.5, NFR4

  - id: BR10.4
    statement: emailSent is true only when every email that had to go out was accepted.
    category: constraint
    applies_to: FailureNotifier
    trigger: The email step ends.
    logic: >
      Each recipient gets its own email (BR5.3, BR6.2, BR7.2). IF every one was accepted THEN emailSent is true. IF one or more was not THEN emailSent is false and recipientsNotReached names the roles whose email was not accepted. An accepted email for the other role stays sent.
      For a failed import, the outcome removed-email-failed (BR10.3) follows emailSent false, so the team's alert (a) fires when any role was not reached. A rejected or already-stored event with emailSent false needs alert (a) widened (see the open question below).
    violation: Not applicable.
    source: FR5.5, NFR4

  - id: BR10.5
    statement: The importId is derived from the eventId, so a message that is received again maps to the same importId.
    category: constraint
    applies_to: ImportCoordinator
    trigger: A start event is taken off the queue.
    logic: >
      THEN importId is a fixed function of the eventId that the event bus gave the event, with no character of the eventId reaching a storage path unchanged. The same eventId always gives the same importId, which names state/<importId>/ and holding/<importId>/.
      This is how u1 built it (the import id from the event id in code-summary.md of u1). BR1.2 of u1 says only that the importId is unique, and this rule makes the derivation part of the rules because the checks for a repeated message (BR7.1, BR10.1) depend on it.
    violation: Not applicable.
    source: NFR4, FR1.1

  - id: BR11.1
    statement: Every start event gets an import record and an outcome object, including a rejected event and an already-stored one.
    category: policy
    applies_to: ImportCoordinator
    trigger: A start event is taken off the queue (BR6.4).
    logic: >
      THEN for an event that runs an import, write the ImportRecord under state/<importId>/ before the first step, as u1 does, and update it as the import goes on.
      For a rejected or already-stored event no record exists before the end. After the email step and after the outcome object exists (BR10.1), write the ImportRecord once, with status done and the outcome (owner's decision in Q5). The record therefore never shows done without an outcome.
      The record holds modelName and exactVersion only when each passed the check of BR1.3, so the record of a rejected event may hold neither: this is deliberate, to keep values from a bad event out of storage. It never holds a sender address or a value that failed the check.
      The outcome object is the authority. If a stop leaves an outcome with no record, the outcome stands and the log line of BR10.2 holds the same facts.
    violation: Not applicable.
    source: FR6.1

  - id: BR11.2
    statement: The record lists the findings of a scan without their values.
    category: constraint
    applies_to: ImportCoordinator
    trigger: The scan has ended with one or more findings.
    logic: >
      THEN write the findings into the ImportRecord before the status becomes removing: the first 200 in the order file path then place, each with the cleaned file name, the place, and the kind only, and findingsTotal with the count of all findings. The full list stays in the log (BR5.6).
      No matched value, no part of it, and no text around it is stored (BR3.5).
    violation: A value in the record is a defect.
    source: FR6.1, FR3.5

  - id: BR11.3
    statement: The record and the outcome are history only, and the removal never deletes them.
    category: constraint
    applies_to: ImportCoordinator, FileCustodian
    trigger: A removal runs, or any rule is evaluated.
    logic: >
      THEN the removal deletes objects of the import under holding/<importId>/ and the final location only, never under state/. No rule reads the ImportRecord to decide what an import does next. The record helps a person find out what happened.
    violation: Not applicable.
    source: FR6.1, NFR5
```

## Rules Summary

| Group | Rules | What they cover |
|-------|-------|-----------------|
| 6 Rejected event | BR6.1 to BR6.4 | A start event with a bad content is rejected with an email naming the fields, never the values; a message that is not a start event is left to the dead-letter handling |
| 7 Already stored | BR7.1 to BR7.2 | The check for the completion marker before any download, including a marker from this import's own earlier work; the email to the sender and the team |
| 8 Time limit | BR8.1 to BR8.4 | The 6-hour deadline from the start of the import, its check points inside and between pieces of work, the exceptions, and the failure it causes |
| 9 All or nothing | BR9.1 to BR9.6 | The order of the move with the marker last, the removal after a failed move including a marker that cannot be deleted, the check afterwards, the list of leftovers, the count of older files removed, and what happens after the marker |
| 10 One outcome | BR10.1 to BR10.5 | Five outcomes and one outcome object, redelivery, one log line, how a failed email is recorded, and the importId |
| 11 Record | BR11.1 to BR11.3 | A record and outcome for every start event, the findings without values, and records that stay |

## u1 Facts This Unit Relies On

These come from u1-import-slice and are cited here so a reader need not search for them. Where a u1 document and u1's code differ, the code as built is the fact.

| Fact | Where it is set |
|------|-----------------|
| The importId is derived from the eventId | The code of u1 (`code-summary.md` of u1, `models.py`); made a rule here as BR10.5 |
| At most 50 emails a day, counted with a conditional write | NFR3.16 of u1 (`store.py`) |
| A download with no bytes for 60 seconds is a temporary problem, and a download under 1 MB per second over any 10 minutes fails | NFR1.7 of u1 |
| A text file larger than 200 MB, or one that takes more than 5 minutes to scan, is a finding | NFR1.9 of u1 |
| A 7-hour watchdog ends whatever runs an import | NFR1.8 of u1 |
| One message group in a FIFO queue, a maximum receive count of 1 with a dead-letter queue, and a heartbeat that keeps a running import's message hidden | NFR Design of u1 (`nfr-design-questions.md`, Q2 and Q4) and `code-summary.md` of u1, items 10 and 11 |
| The outcome object is created with a condition, and the import record is rewritten before each step | NFR4.1 and NFR4.8 of u1 (`store.py`) |
| The log accepts only allow-listed fields | `logs.py` of u1 |

## Decisions Taken

- Two new outcomes, `rejected` and `refused-already-stored` (Q1, option A).
- The 6-hour clock starts when the application takes the event off the queue (Q2, option A).
- A failed move removes all files of the model version's folder when it has no marker, and the email says how many older files were removed (Q3, option A).
- The rejection email names fields and never repeats a value from the event (Q4, option A).
- The record of imports is built, with findings and with rejected and already-stored events (Q5, option A).
- The wait for a running import (AC1.1.4) needs no rule in this Unit. u1 delivered it as a queue with one message group (`code-summary.md` of u1), and u1's functional spec said u2 would add it. Nothing is added for it here.
- A failure after the completion marker never removes the model (BR9.6). The marker is therefore the one commit point of an import.

## Assumptions & Open Questions

- [assumption] A crash between the email and the creation of the outcome leaves no outcome. The message then goes to the dead-letter queue and the team is alerted by u1's alarms. A person redrives it or decides by hand. A second email for the same import is possible when it is run again.
- [assumption] "Well-formed" for a sender address is the check that u1 already uses for the start event. No new definition is made here.
- [assumption] The numbers in these rules (20 leftovers listed, 200 findings in the record, 10 minutes after the deadline, 3 tries to delete the marker) are size and time guards. They can change without changing the rules.
- [assumption] Anyone who may put an event on the bus can make the application email any well-formed sender address in a rejected event, as in u1's open item R-07. The daily limit of 50 emails bounds the harm. Who may publish the event is decided in Infrastructure Design.
- Open question: Alert (a) in the stack watches only the outcome removed-email-failed. BR10.3 and BR10.4 need it to fire for any outcome with emailSent false. This is a change to the stack for Infrastructure Design.
- Open question: u1's alert (b) for an incomplete removal must fire for the outcome stored with removalComplete false (BR9.6). Infrastructure Design checks this.
- Open question: The logger of u1 only accepts fields from an allow-list. Code Generation must add the fields of BR10.2 and BR10.3 to that list, and nothing else.
