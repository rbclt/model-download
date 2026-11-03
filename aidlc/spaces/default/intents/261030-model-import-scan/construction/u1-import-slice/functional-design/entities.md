# Entities: u1-import-slice

This is the data model for the thin end-to-end slice. The Unit is defined in `unit-of-work.md`. It uses the five entities that `components.md` assigns to the five building blocks and the shared record shapes agreed in contract C4 of `contract-summary.md`. Each entity has one owning building block. The model is technology-agnostic: how the records are stored is decided in later stages. Fields that u2-import-rules adds (the waiting status and the refused outcome) are listed in the notes, not in the u1 values.

```yaml
entities:
  - name: ImportRequest
    owner: ImportCoordinator
    description: One import, from the moment a valid start event is accepted to its single outcome.
    attributes:
      - { name: importId, type: text, required: true, unique: true, default: generated when the event is accepted }
      - { name: modelName, type: text, required: true, constraints: "owner/name form from the start event, checked by rule BR1.3; kept in lowercase in the final location" }
      - { name: exactVersion, type: text, required: true, constraints: "full 40-character lowercase hexadecimal commit hash, checked by rule BR1.3" }
      - { name: senderEmail, type: text, required: true, constraints: "an email address; u1 only handles events that carry one" }
      - { name: status, type: enum, required: true, allowed: [downloading, scanning, keeping, removing, done] }
      - { name: startedAt, type: timestamp, required: true, default: time the import starts }
      - { name: outcome, type: enum, required: false, allowed: [stored, removed-with-email, removed-email-failed], constraints: "set once, when status becomes done; removed-email-failed is added in u1 for a failed email (rules BR5.5)" }
      - { name: failureReason, type: text, required: false, constraints: "plain words; never a secret or personal value; present only when outcome is removed-with-email" }
    constraints:
      - outcome is set exactly once and never changed
      - status moves only along the transitions in functional-spec.md
    relationships: []

  - name: ModelFile
    owner: ModelDownloader
    description: One file of the model version, as listed by Hugging Face and as stored in the holding area.
    attributes:
      - { name: path, type: text, required: true, constraints: "path inside the model repository, checked by rule BR1.3: no leading slash, no backslash, no control character, no empty, . or .. segment, at most 1024 characters" }
      - { name: expectedSizeBytes, type: integer, required: true, min: 0, constraints: "size given in the listing for this version; a file with no size fails the import (rule BR2.3)" }
      - { name: receivedSizeBytes, type: integer, required: true, min: 0, default: 0 }
      - { name: complete, type: boolean, required: true, default: false, constraints: "true only when receivedSizeBytes equals expectedSizeBytes" }
    constraints:
      - unique within one ModelFileSet by path, and also unique when letters are compared without case (rule BR1.3)
    relationships:
      - { to: ModelFileSet, cardinality: many-to-one, direction: ModelFile belongs to ModelFileSet }

  - name: ModelFileSet
    owner: ModelDownloader
    description: All files of one downloaded model version, held in the holding area while they are scanned.
    attributes:
      - { name: holdingAreaPath, type: text, required: true, unique: true, constraints: "holding/<importId>/" }
      - { name: modelName, type: text, required: true }
      - { name: exactVersion, type: text, required: true }
      - { name: totalSizeBytes, type: integer, required: true, min: 0 }
      - { name: downloadComplete, type: boolean, required: true, default: false, constraints: "true only when every ModelFile is complete" }
    constraints:
      - at least one ModelFile, and at least one of them ends in .safetensors (rule BR2.6 fails the import otherwise)
    relationships:
      - { to: ImportRequest, cardinality: one-to-one, direction: ModelFileSet belongs to ImportRequest }

  - name: ScannedFile
    owner: LeakScanner
    description: The result of the scan for one file of the ModelFileSet. It keeps the class the scan gave the file, so the ModelDownloader's records stay untouched.
    attributes:
      - { name: importId, type: text, required: true, references: ImportRequest.importId }
      - { name: path, type: text, required: true, references: ModelFile.path }
      - { name: fileClass, type: enum, required: true, allowed: [weight, scanned-text, ignored, disallowed-weight], constraints: "set from the last part of the path; see rules BR3.1" }
      - { name: findingsRecorded, type: integer, required: true, min: 0, max: 100, default: 0, constraints: "at most 100 per file (rule BR3.7)" }
      - { name: matchesTotal, type: integer, required: true, min: 0, default: 0, constraints: "all matches, including those not recorded" }
    constraints:
      - one ScannedFile per ModelFile
    relationships:
      - { to: ImportRequest, cardinality: many-to-one, direction: ScannedFile belongs to ImportRequest }

  - name: ScanFinding
    owner: LeakScanner
    description: One thing the scan found or could not check. It never holds the secret or personal value itself.
    attributes:
      - { name: findingId, type: text, required: true, unique: true }
      - { name: importId, type: text, required: true, references: ImportRequest.importId }
      - { name: fileName, type: text, required: true }
      - { name: place, type: text, required: true, constraints: "for example line 12, columns 5 to 30; or header for a weight file; file names are cleaned before use in a log or email (rule BR5.4)" }
      - { name: kind, type: enum, required: true, allowed: [access-key, private-key, token, password, email-address, phone-number, government-id, street-address, unreadable-file, disallowed-weight-type, bad-weight-header] }
    constraints:
      - no attribute holds the matched value, any part of it, or the text around it
    relationships:
      - { to: ImportRequest, cardinality: many-to-one, direction: ScanFinding belongs to ImportRequest }

  - name: StoredModel
    owner: FileCustodian
    description: A model version that passed the scan and is kept in the final location.
    attributes:
      - { name: storedModelKey, type: text, required: true, unique: true, constraints: "the final location path models/<modelName>/<exactVersion>/" }
      - { name: modelName, type: text, required: true }
      - { name: exactVersion, type: text, required: true }
      - { name: finalLocation, type: text, required: true }
      - { name: storedAt, type: timestamp, required: true }
    constraints:
      - one StoredModel per modelName and exactVersion
    relationships:
      - { to: ImportRequest, cardinality: one-to-one, direction: StoredModel was created by ImportRequest }

  - name: FailureNotice
    owner: FailureNotifier
    description: The one failure email for a failed import.
    attributes:
      - { name: noticeId, type: text, required: true, unique: true }
      - { name: importId, type: text, required: true, references: ImportRequest.importId }
      - { name: recipients, type: list of text, required: true, constraints: "the sender's address and the team mailbox address" }
      - { name: reason, type: text, required: true, constraints: "plain words; no secret or personal value" }
      - { name: findingsListed, type: list of finding summaries, required: false, constraints: "at most 20, in the order file path then place; each has file name, place and kind only; a line with the total count is added when there are more (rule BR5.6)" }
      - { name: removalComplete, type: boolean, required: true }
      - { name: sentAt, type: timestamp, required: false, constraints: "empty until the email is sent" }
    constraints:
      - at most one FailureNotice per ImportRequest
    relationships:
      - { to: ImportRequest, cardinality: one-to-one, direction: FailureNotice belongs to ImportRequest }
```

## Summary

The Unit has seven entities. ImportRequest is the central record, owned by the ImportCoordinator. ModelFileSet and its ModelFile entries belong to the ModelDownloader. ScanFinding and ScannedFile belong to the LeakScanner, StoredModel to the FileCustodian, and FailureNotice to the FailureNotifier. All of them point back to one ImportRequest.

## Notes

- `ModelFile` is new in this stage. `components.md` lists only the file names in `ModelFileSet`; the per-file size and completeness are what let the application tell a half-downloaded file from a whole one (FR2.4).
- `ScanFinding` carries `findingId` and `importId`, as `components.md` and the Contract Design review (R-01) require. The contract's record in C4 lacked them.
- `ScannedFile` is new after the architecture review: the class a file gets from the scan is the LeakScanner's result, so the ModelDownloader's `ModelFile` is not written by another building block.
- u2-import-rules adds the `waiting` status to ImportRequest, the `refused` outcome, and the `leftBehind` list on FailureNotice. u1 adds the outcome `removed-email-failed`, which is an additive change to the shared record in contract C4.
- Where the records live: ScanFinding records are written to the logs of the import and shown in the failure email. A separate store is the optional record of imports (US6.1) in u2.

## Assumptions & Open Questions

- [assumption] The listed finding kinds are the whole set for u1. Which exact patterns detect each kind is a detail for Code Generation.
- [assumption] `receivedSizeBytes` is compared with the size Hugging Face reports for the file. No checksum is compared; the contract left this open (Contract Design review R-05).
- [assumption] After a failure only the ImportRequest outcome and the findings are kept, in the logs. ModelFile and ScannedFile records are not kept.
