# Entities: u2-import-rules

This is the data model that u2-import-rules adds to, or changes in, the model of u1-import-slice (`construction/u1-import-slice/functional-design/entities.md`). The Unit is defined in `unit-of-work.md`. It keeps the five building blocks and the entity ownership of `components.md`, and it changes the shared record shapes of contract C4 in `contract-summary.md` only by additions that C4's ownership rules allow. The requirements behind the changes are `requirements.md` FR1.3, FR1.4, FR4.2, FR5.1, FR5.3, FR5.5, FR6.1, NFR1, and NFR4. The model is technology-agnostic: how the records are stored was decided in u1's NFR Design (objects under `state/<importId>/` in the holding bucket).

Entities not listed here are unchanged from u1: ModelFile, ModelFileSet, ScannedFile, and ScanFinding.

```yaml
entities:
  - name: ImportRequest
    owner: ImportCoordinator
    description: One start event handled to its single outcome. In u2 it also covers an event that ends before any import starts (a rejected event, or a model version that is already stored).
    changes_from_u1:
      - modelName, exactVersion, and senderEmail may be empty for an event that was rejected, because the event can lack them
      - outcome gets two new values, rejected and refused-already-stored
      - new attributes eventId, deadlineAt, rejectedFields, emailSent, and recipientsNotReached
      - a rejected or already-stored event goes straight to status done and never passes downloading
      - no waiting status is added: the wait for a running import is the queue that u1 built, so contract C4's waiting status is not used
    attributes:
      - { name: importId, type: text, required: true, unique: true, constraints: "derived from eventId (rule BR10.5), so a message received again maps to the same importId" }
      - { name: eventId, type: text, required: true, constraints: "the id the event bus gave the event; it is not a value the sender chose" }
      - { name: eventTime, type: timestamp, required: true, constraints: "the time the event bus gave the event" }
      - { name: modelName, type: text, required: false, constraints: "present only when it passed the check of BR1.3; kept in lowercase in the final location" }
      - { name: exactVersion, type: text, required: false, constraints: "present only when it passed the check of BR1.3" }
      - { name: senderEmail, type: text, required: false, constraints: "present only when it is a well-formed email address; never written to a log, a record, or an outcome" }
      - { name: status, type: enum, required: true, allowed: [downloading, scanning, keeping, removing, done] }
      - { name: startedAt, type: timestamp, required: true, constraints: "the moment the application takes the event off the queue and begins to handle it (BR8.1)" }
      - { name: deadlineAt, type: timestamp, required: false, constraints: "startedAt plus 6 hours; set for every event that passes the content check, and not for a rejected event (BR8.1)" }
      - { name: rejectedFields, type: list of text, required: false, allowed: [modelName, exactVersion, senderEmail], constraints: "set only when outcome is rejected; the names of the fields that were missing or malformed, never their values (BR6.3)" }
      - { name: outcome, type: enum, required: false, allowed: [stored, removed-with-email, removed-email-failed, rejected, refused-already-stored], constraints: "set once, when status becomes done, and never changed (BR10.1)" }
      - { name: failureReason, type: text, required: false, constraints: "plain words; never a secret or personal value; present for every outcome except stored" }
      - { name: emailSent, type: boolean, required: false, constraints: "true only when the email service accepted every email this import had to send (BR10.4); empty for the outcome stored, which sends none" }
      - { name: recipientsNotReached, type: list of text, required: false, allowed: [sender, team], constraints: "the roles whose email was not accepted; never an address (BR10.3)" }
    constraints:
      - outcome is set exactly once and never changed
      - status moves only along the transitions in functional-spec.md
      - when outcome is rejected or refused-already-stored, nothing was downloaded and nothing was written to the holding area or the final location
    relationships: []

  - name: ImportRecord
    owner: ImportCoordinator
    description: The durable history of one import, kept as one object that is rewritten before each step and never removed by a removal (user story US6.1, requirement FR6.1). u1 already writes it; u2 adds the findings and makes it exist for every start event.
    changes_from_u1:
      - written for a rejected event and for an already-stored model too, once at the end, after the outcome object exists (BR11.1)
      - new attributes outcome, failureReason, findings, and findingsTotal
    attributes:
      - { name: importId, type: text, required: true, unique: true }
      - { name: eventId, type: text, required: true }
      - { name: modelName, type: text, required: false, constraints: "only when it passed the check of BR1.3" }
      - { name: exactVersion, type: text, required: false, constraints: "only when it passed the check of BR1.3" }
      - { name: status, type: enum, required: true, allowed: [downloading, scanning, keeping, removing, done] }
      - { name: step, type: text, required: true, constraints: "the step the import is in, in plain words" }
      - { name: startedAt, type: timestamp, required: true }
      - { name: updatedAt, type: timestamp, required: true }
      - { name: outcome, type: enum, required: false, allowed: [stored, removed-with-email, removed-email-failed, rejected, refused-already-stored], constraints: "copied from ImportOutcome once it exists" }
      - { name: failureReason, type: text, required: false, constraints: "plain words; no secret or personal value" }
      - { name: findings, type: list of finding summaries, required: false, constraints: "at most 200, in the order file path then place; each has a cleaned file name, the place, and the kind only (BR11.2)" }
      - { name: findingsTotal, type: integer, required: false, min: 0, constraints: "all findings of the scan, including those not listed" }
    constraints:
      - no attribute holds a matched value, any part of it, the text around it, or any email address
      - the record is history only: no rule reads it to decide what an import does next
    relationships:
      - { to: ImportRequest, cardinality: one-to-one, direction: ImportRecord describes ImportRequest }

  - name: ImportOutcome
    owner: ImportCoordinator
    description: The single, final result of one import, created once and never changed. It is the proof of rule BR10.1 that every import ends in exactly one outcome.
    changes_from_u1:
      - outcome gets the values rejected and refused-already-stored
      - new attributes emailSent and recipientsNotReached
    attributes:
      - { name: importId, type: text, required: true, unique: true }
      - { name: outcome, type: enum, required: true, allowed: [stored, removed-with-email, removed-email-failed, rejected, refused-already-stored] }
      - { name: removalComplete, type: boolean, required: true, constraints: "true when nothing was left behind, and also when nothing had been created (rejected, refused-already-stored)" }
      - { name: finishedAt, type: timestamp, required: true }
      - { name: failureReason, type: text, required: false, constraints: "plain words; no secret or personal value" }
      - { name: emailSent, type: boolean, required: false }
      - { name: recipientsNotReached, type: list of text, required: false, allowed: [sender, team] }
    constraints:
      - created once with a condition that fails when it exists; an existing outcome is never changed
      - removed-email-failed is the outcome only for an import that was removed and whose email was not accepted for every recipient
      - the outcome stored may carry removalComplete false when the holding area could not be fully deleted after the marker was written (BR9.6)
    relationships:
      - { to: ImportRequest, cardinality: one-to-one, direction: ImportOutcome ends ImportRequest }

  - name: FailureNotice
    owner: FailureNotifier
    description: The one email about one start event that did not end in a stored model. In u2 it has three kinds.
    changes_from_u1:
      - new attribute kind, with the two new kinds event-rejected and already-stored
      - new attributes fieldsNamed, leftBehind, leftBehindTotal, and olderFilesRemoved
      - recipients depend on the kind (BR6.2, BR7.2)
    attributes:
      - { name: noticeId, type: text, required: true, unique: true }
      - { name: importId, type: text, required: true, references: ImportRequest.importId }
      - { name: kind, type: enum, required: true, allowed: [import-failed, event-rejected, already-stored], default: import-failed }
      - { name: recipients, type: list of text, required: true, constraints: "import-failed and already-stored: the sender and the team mailbox; event-rejected: the team mailbox, and also the sender when senderEmail is present and well-formed" }
      - { name: reason, type: text, required: true, constraints: "plain words; no secret or personal value; for the time limit: the time limit of 6 hours passed" }
      - { name: fieldsNamed, type: list of text, required: false, allowed: [modelName, exactVersion, senderEmail], constraints: "only for kind event-rejected (BR6.3)" }
      - { name: eventReference, type: text, required: false, constraints: "only for kind event-rejected: the eventId and eventTime, so the team can find the event in the logs (BR6.3)" }
      - { name: findingsListed, type: list of finding summaries, required: false, constraints: "unchanged from u1: at most 20, with a total count line (BR5.6)" }
      - { name: removalComplete, type: boolean, required: false, constraints: "only for kind import-failed" }
      - { name: leftBehind, type: list of Leftover, required: false, constraints: "only for kind import-failed with removalComplete false; at most 20 listed (BR9.4)" }
      - { name: leftBehindTotal, type: integer, required: false, min: 0, constraints: "all leftovers, including those not listed" }
      - { name: olderFilesRemoved, type: integer, required: false, min: 0, constraints: "only for kind import-failed: the number of older unfinished files the removal also deleted from the model version's folder (BR9.5)" }
      - { name: sentAt, type: timestamp, required: false, constraints: "empty until the email service accepts the email" }
    constraints:
      - at most one FailureNotice per ImportRequest
      - no attribute holds a value taken from a rejected event, a matched value, a token, or a credential
    relationships:
      - { to: ImportRequest, cardinality: one-to-one, direction: FailureNotice belongs to ImportRequest }

  - name: RemovalResult
    owner: FileCustodian
    description: What the removal after a failed import achieved and what it could not remove.
    attributes:
      - { name: importId, type: text, required: true, references: ImportRequest.importId }
      - { name: removalComplete, type: boolean, required: true, constraints: "true only when the check after the removal found nothing of this import left (BR9.3)" }
      - { name: leftovers, type: list of Leftover, required: true, constraints: "empty when removalComplete is true" }
      - { name: olderFilesRemoved, type: integer, required: true, min: 0, default: 0, constraints: "older unfinished files deleted under BR9.2; they are not leftovers" }
    constraints:
      - removalComplete is false exactly when leftovers is not empty
    relationships:
      - { to: ImportRequest, cardinality: one-to-one, direction: RemovalResult belongs to ImportRequest }

  - name: Leftover
    owner: FileCustodian
    description: One thing of a failed import that the removal could not delete or that the check afterwards still found.
    attributes:
      - { name: kind, type: enum, required: true, allowed: [object, incomplete-upload, temporary-resource] }
      - { name: area, type: enum, required: true, allowed: [holding-area, final-location, local-disk] }
      - { name: location, type: text, required: true, constraints: "the storage key or resource name, cleaned by the rule of BR5.4; never a value taken from a file" }
    constraints:
      - one Leftover per object, upload, or resource
    relationships:
      - { to: RemovalResult, cardinality: many-to-one, direction: Leftover belongs to RemovalResult }

  - name: StoredModel
    owner: FileCustodian
    description: A model version that passed the scan and is kept in the final location. It counts as stored only while its completion marker exists.
    changes_from_u1:
      - the completion marker `_COMPLETE` is now part of the definition: no marker, no StoredModel
    attributes:
      - { name: storedModelKey, type: text, required: true, unique: true, constraints: "models/<modelName in lowercase>/<exactVersion>/" }
      - { name: modelName, type: text, required: true }
      - { name: exactVersion, type: text, required: true }
      - { name: finalLocation, type: text, required: true }
      - { name: markerWritten, type: boolean, required: true, default: false, constraints: "true only after every file is copied; the marker is the last object written (BR9.1)" }
      - { name: markerImportId, type: text, required: false, constraints: "the importId held inside the marker; it tells a redriven message that the stored model is its own earlier work (BR7.1); the marker holds no sender address" }
      - { name: storedAt, type: timestamp, required: false, constraints: "set when the marker is written" }
    constraints:
      - one StoredModel per modelName and exactVersion
    relationships:
      - { to: ImportRequest, cardinality: one-to-one, direction: StoredModel was created by ImportRequest }
```

## Summary

Seven entities change or are new for this Unit. The ImportCoordinator owns ImportRequest, ImportRecord, and ImportOutcome: the first is the live handling of one event, the second its history, the third its single final result. The FailureNotifier owns FailureNotice, which now has three kinds. The FileCustodian owns RemovalResult and Leftover, which carry what a failed removal left behind, and StoredModel, which now depends on the completion marker. ModelDownloader and LeakScanner own nothing new.

## Notes

- The shared record of contract C4 listed a `waiting` status for u2. It is not used: u1's NFR Design put the wait in a queue, so an event that waits is not yet an ImportRequest.
- The separate outcome names (Q1, option A) let the team count and alert on rejected events and on duplicates apart from failed imports.
- `emailSent` and `recipientsNotReached` make a failed email visible in the outcome object, not only in a log line (rule BR10.3). They hold roles (sender, team), never addresses.
- A rejected event keeps `modelName` or `exactVersion` only when that value passed the check of BR1.3. A value that failed is never stored anywhere (Q4, option A).
- Findings are listed in the ImportRecord up to 200 so the object stays small. The logs keep the full list, as in u1 (BR5.6).

## Assumptions & Open Questions

- [assumption] The limit of 200 findings in the ImportRecord is a size guard chosen here. The owner can change the number without changing the rules.
- [assumption] The event bus always gives an event an id and a time, so `eventId` and `eventTime` are never empty. An event without them is not a start event (BR6.4).
- Open question: Infrastructure Design for this Unit must widen alert (a) in the stack, which today watches only the outcome removed-email-failed, so that it also fires when a rejected or refused event could not be told to anyone (`emailSent` false).
