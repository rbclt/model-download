# Contract Summary

A contract is a written agreement across a boundary: what data crosses it, in what shape, and what happens when something goes wrong. The application has two units, u1-import-slice and u2-import-rules, built as one application and deployed together (`unit-of-work.md`). u2 depends on u1 and no other unit does, so there is one inter-unit boundary (shared code and shared records). The application also has three boundaries with the outside world: the start event that comes in, the Hugging Face download that goes out, and the failure email that goes out. This document pins all four down, using the answers in `contract-design-questions.md` (Q1 to Q3), `unit-of-work-dependency.md`, `components.md`, and `requirements.md`.

## Contracts

| # | Provider Unit | Consumer | Mechanism | Owner |
|---|---------------|----------|-----------|-------|
| C1 | External: whoever sends the start event | u1-import-slice (ImportCoordinator); u2-import-rules adds the rejection rules | Event (AsyncAPI), delivered by Amazon EventBridge | u1-import-slice |
| C2 | External: Hugging Face model repository | u1-import-slice (ModelDownloader) | HTTPS download of public files, no account or token | u1-import-slice |
| C3 | u1-import-slice (FailureNotifier); u2-import-rules adds the extra email cases | External: the sender and the team mailbox | Email through an email service | u1-import-slice |
| C4 | u1-import-slice (ImportCoordinator and FileCustodian) | u2-import-rules | Shared schema: the import record and the S3 locations | u1-import-slice |

## C1: Start event

One event starts one import (FR1.1). The model name and the exact version are required. The sender's email address is required so the failure email can reach the sender (FR1.2).

```yaml asyncapi
asyncapi: 2.6.0
info:
  title: Model import start event
  version: 1.0.0
channels:
  model-import/start:
    publish:
      summary: Ask the application to import one public model version.
      message:
        name: ModelImportRequested
        payload:
          type: object
          required: [modelName, exactVersion, senderEmail]
          additionalProperties: true   # consumers ignore unknown fields
          properties:
            modelName:
              type: string
              description: Hugging Face repository name, owner/name, for example "org/model".
              pattern: "^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$"
            exactVersion:
              type: string
              description: Full commit hash of the repository. Branch names, tags and short hashes are rejected (Q1, option A).
              pattern: "^[0-9a-f]{40}$"
            senderEmail:
              type: string
              format: email
              description: Person to email when the import fails or is refused. Missing or not an email address means the team mailbox only (Q2, option A).
rules:
  - A valid event starts one import. Only one import runs at a time; an event that arrives while one runs waits and starts afterwards (FR1.5, assumption A2).
  - An event with a missing or malformed modelName, a missing or non-40-character exactVersion, or a missing senderEmail is rejected. Nothing is downloaded, and the rejection email in C3 is sent (FR1.3).
  - An event whose modelName and exactVersion are already stored is refused. Nothing is downloaded, and the "already stored" email in C3 is sent (FR1.4).
  - The event carries no secret, token, or credential.
```

**Errors and timeouts at this boundary:** the event source gets no direct reply. Every outcome, including a rejection or refusal, is reported by email (C3) and in the logs. An import that has not finished 6 hours after it started is stopped and treated as failed (NFR1).

**Where the rules land:** u1 accepts a valid event and runs the import. u2 adds the rejection of bad events, the refusal of duplicates, the wait for a running import, and the 6-hour stop (`unit-of-work.md`).

## C2: Hugging Face download

```yaml
contract: huggingface-download
direction: outbound
consumer: ModelDownloader (u1-import-slice)
provider: Hugging Face public model repository (not under our control)
protocol: HTTPS
authentication: none. No Hugging Face account or access token (FR2.1).
request: every file of the repository at the exact commit hash from C1
success: all files are fully downloaded into the holding area in S3 (FR2.2, FR2.4)
failure_kinds:
  temporary:        # dropped connection, timeout, server error, rate limit
    behaviour: retry the file up to 3 times with a short wait between tries; if the third retry fails, the import fails (Q3, option A)
  permanent:        # model needs a login, does not exist, commit hash not found
    behaviour: fail the import at once with no retry (FR2.3)
  partial_file:
    behaviour: a half-downloaded file is never scanned or kept as complete (FR2.4)
time_limit: retries count against the 6-hour limit of the whole import (NFR1)
```

The provider can change its service without notice, so the application owns this contract only on its own side: what it asks for and how it reacts to each failure.

## C3: Failure email

One email goes to the recipients below. It never contains a secret or personal value (FR5.4, FR3.5).

```yaml
contract: failure-email
provider: FailureNotifier
consumers: [the person who started the import, the team mailbox]
mechanism: email through an email service
subject: "Model import <outcome>: <modelName> <first 12 characters of exactVersion>"
body_fields:
  modelName: string
  exactVersion: string
  reason: plain-words reason, one of
    - finding in a file (names the file, the place in it, and the kind of finding; never the value)
    - download problem (including a model that needs a login or does not exist)
    - time limit of 6 hours passed
    - rejected event (names what was missing or malformed)
    - already stored
    - other step failed
  removalComplete: yes | no
  leftBehind: list of what could not be removed, present only when removalComplete is no (FR5.3)
recipients:
  failure_after_start: sender and team mailbox
  rejected_event_with_sender: sender and team mailbox
  rejected_event_without_sender: team mailbox only          # Q2, option A
  already_stored: sender and team mailbox                   # Q2, option A
if_email_cannot_be_sent: the import still ends with its outcome and its files removed; the email problem is recorded in the logs (FR5.5)
```

**Where the rules land:** u1 sends the basic failure email to both recipients. u2 adds the rejected-event email, the "already stored" email, the team-only case for a missing sender address, and the record when the email cannot be sent.

## C4: Shared import record and storage locations (u1 to u2)

The two units share code, so this boundary is a shared schema: the import record that the ImportCoordinator owns, and the two places in Amazon S3. u2 may add fields and states, but may not rename or remove what u1 creates.

```yaml shared-schema
ImportRequest:            # owned by ImportCoordinator, created in u1
  importId: string
  modelName: string
  exactVersion: string
  senderEmail: string | null
  status: waiting | downloading | scanning | keeping | removing | done   # u2 adds "waiting"
  startedAt: timestamp
  outcome: stored | removed-with-email | refused    # exactly one per import (NFR4)
  failureReason: string | null                      # plain words, no secret or personal value
ScanFinding:              # owned by LeakScanner
  fileName: string
  place: string           # where in the file
  kind: string            # kind of finding, never the value
storage:                  # owned by FileCustodian
  holdingArea:  holding/<importId>/<file path>                       # temporary, removed on any failure
  finalLocation: models/<modelName>/<exactVersion>/<file path>       # complete model or nothing (FR4.2)
  alreadyStoredCheck: the final location for the same modelName and exactVersion exists
```

## Contract Ownership Rules

- Each spec is owned by the unit named in the Owner column. u1-import-slice owns all four, because u2 only builds on what u1 creates.
- A breaking change (renaming or removing a field, changing a pattern, changing who gets an email) needs the owner of the spec and the people who send the event or read the email to agree before it is built. Because both units live in one application, a breaking change to C4 means both units are changed and tested together.
- An additive change (a new optional event field, a new email body field, a new record field) is safe: consumers ignore fields they do not know.
- The start event carries the version of its shape in this document (1.0.0). A breaking change to it raises the major number.
- The first version has no other consumer of C4 than the application itself, so no compatibility promise outlives a single release.

## Open Questions

| Contract | Question | Blocks |
|----------|----------|--------|
| C1 | Which event bus, event source name, and detail type do senders use? This is set in infrastructure design. | u1-import-slice |
| C1 | Does the wait for a running import (one at a time) live in the event source or in the ImportCoordinator? The domain design left this open. | u2-import-rules |
| C2 | How long is the "short wait" between retries, and which server errors count as temporary? | u1-import-slice |
| C3 | What is the address of the team mailbox, and which email sender address does the email service use? | u1-import-slice |
| C3 | Is an email service sender address verified in the common AWS account? Until it is, no email can be sent. | u1-import-slice |
| C4 | Are the holding area and final location two buckets or two paths in one bucket? The paths above hold for both. | u1-import-slice |
| C4 | Is a refused duplicate its own outcome ("refused") next to "stored" and "removed with an email"? The requirements name only two outcomes. | u2-import-rules |
| C1 | If a sender gives a malformed email address, is it treated as missing (team mailbox only)? This summary assumes yes. | u2-import-rules |

## Assumptions

- [assumption] The repository name pattern in C1 (`owner/name`) matches how Hugging Face names public models.
- [assumption] "Temporary" download problems are those that can pass on their own (dropped connection, timeout, server error, rate limit). A missing model or a login requirement never passes on its own.
- [assumption] Amazon EventBridge delivers the event (`team-practices.md`), and Amazon SES or an equal service sends the email; the choice is made in infrastructure design.
