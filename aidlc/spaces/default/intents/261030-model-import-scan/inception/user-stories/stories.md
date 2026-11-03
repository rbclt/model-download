# User Stories

The stories are split by step of an import, one story per backlog item (`intent-backlog.md`), written for the Model Importer persona (`personas.md`). Each story has 3 to 6 acceptance criteria and includes its failure path. Requirement IDs refer to `requirements.md`.

## Story Overview

| Story | Title | Priority | Backlog item | Requirements |
|-------|-------|----------|--------------|--------------|
| US1.1 | Start an import with an event | Must Have | B-5 | FR1.1, FR1.2, FR1.3, FR1.4, FR1.5 |
| US2.1 | Download a public model | Must Have | B-1 | FR2.1, FR2.2, FR2.3, FR2.4, NFR2 |
| US3.1 | Scan the files for data leaks | Must Have | B-2 | FR3.1, FR3.2, FR3.3, FR3.4, FR3.5 |
| US4.1 | Keep only files that passed the scan | Must Have | B-2, B-3 | FR4.1, FR4.2 |
| US5.1 | Stop and clean up when an import fails | Must Have | B-3 | FR5.1, FR5.2, FR5.3, NFR1, NFR4, NFR5 |
| US5.2 | Be told by email when an import fails | Must Have | B-4 | FR5.4, FR5.5, NFR3 |
| US6.1 | Keep a record of each import | Could Have | B-6 | FR6.1 |

## US1.1: Start an import with an event

As a Model Importer, I want to start an import by sending an event with the model name, an exact version, and my email address, so that I do not have to download the files by hand.

**Priority:** Must Have

**Acceptance criteria**

- **AC1.1.1:** Given an event with a model name, an exact version, and the sender's email address, and no import running, when the event arrives, then one import starts for that model and version.
- **AC1.1.2:** Given an event with a missing model name, version, or email address, or a version that is not an exact version, when the event arrives, then no download starts and a failure email is sent. The email goes to the team mailbox, and also to the sender when the sender's address is present.
- **AC1.1.3:** Given that the same model and exact version are already stored, when the event arrives, then no download starts and an email says the model is already stored.
- **AC1.1.4:** Given another import is running, when a new event arrives, then the new import does not start at once, the event is not lost, and the import starts after the running one ends.

**Dependencies:** None.

**INVEST notes:** Independent of the other stories, since it only needs a way to start work. Small and testable by sending events.

## US2.1: Download a public model

As a Model Importer, I want the files of the exact version of a public model downloaded into the common AWS account, so that I have them in one safe place without needing a Hugging Face account.

**Priority:** Must Have

**Acceptance criteria**

- **AC2.1.1:** Given an exact version of a public model, when its import starts, then every file of that version is downloaded into a holding area in S3 in the common account in us-east-1, without an account or access token.
- **AC2.1.2:** Given a model that needs a login or does not exist, when its import starts, then the import fails as described in US5.1.
- **AC2.1.3:** Given a file download that is interrupted, when the import goes on, then the half-downloaded file is not scanned or kept as complete, and the import fails as described in US5.1.
- **AC2.1.4:** Given a smaller model of the size the current version targets (about 8 billion parameters or fewer, roughly 16 GB), when its import runs, then the whole import ends inside the 6-hour limit of US5.1.

**Dependencies:** US1.1 (starts the import). The download can be built and tested on its own first, as the backlog sets out.

**INVEST notes:** The riskiest story, so it is built first (`intent-backlog.md`). Estimable with a real download of a smaller model first.

## US3.1: Scan the files for data leaks

As a Model Importer, I want the downloaded files scanned for secrets, credentials, and personal data, so that nothing sensitive reaches the shared account.

**Priority:** Must Have

**Acceptance criteria**

- **AC3.1.1:** Given text and settings files that contain a planted secret, credential, or piece of personal data, when the scan runs, then the import fails.
- **AC3.1.2:** Given large weight files, when the scan runs, then they are not read for content and are only checked to be the expected file type.
- **AC3.1.3:** Given any finding of any kind, when the scan ends, then the import stops and does not go on to US4.1.
- **AC3.1.4:** Given a file that cannot be read or scanned, when the scan runs, then it counts as a finding and the import fails.
- **AC3.1.5:** Given a finding, when it is recorded, then the record names the file, the place in it, and the kind of finding, and never contains the secret or personal value.

**Dependencies:** US2.1.

**INVEST notes:** Testable by planting known values in sample files. The exact kinds of data and the definition of a weight file are open (see Open Questions).

## US4.1: Keep only files that passed the scan

As a Model Importer, I want the files kept in the final S3 location only when the whole model has passed the scan, so that I can trust anything I find there.

**Priority:** Must Have

**Acceptance criteria**

- **AC4.1.1:** Given that every file passed the scan, when the scan ends, then all files are moved to the final location in S3 and kept.
- **AC4.1.2:** Given a failure during the move to the final location, when the import ends, then the final location holds nothing from this import.
- **AC4.1.3:** Given a scan that did not pass, when the import ends, then no file from this import is in the final location.

**Dependencies:** US3.1.

**INVEST notes:** Small. Testable by forcing a failure during the move.

## US5.1: Stop and clean up when an import fails

As a Model Importer, I want a failed import to stop and remove everything it created, so that I never have to clean up large files or leftovers myself.

**Priority:** Must Have

**Acceptance criteria**

- **AC5.1.1:** Given a finding in the scan or a failure in any step, when the import fails, then the import stops and all downloaded files and temporary resources created for it are removed.
- **AC5.1.2:** Given an import that has not finished 6 hours after it started, when the limit is reached, then the import is treated as failed and handled as in AC5.1.1.
- **AC5.1.3:** Given that the removal itself fails, when the import ends, then the failure email names what is left behind.
- **AC5.1.4:** Given any import, when it ends, then it ended in exactly one outcome: stored, or removed with an email, or rejected before the download.
- **AC5.1.5:** Given any import, when it ends, then its outcome and the reason can be found in the logs, without any secret or personal value.

**Dependencies:** US2.1, US3.1.

**INVEST notes:** Required by the rule "always remove downloaded files and temporary resources when an import fails" (`team-practices.md`). Testable by forcing each kind of failure.

## US5.2: Be told by email when an import fails

As a Model Importer, I want an email when an import fails, with the reason, so that I know what went wrong and whether anything is left to clean up.

**Priority:** Must Have

**Acceptance criteria**

- **AC5.2.1:** Given an import that failed, when it ends, then one email goes to both the person who started it and the team mailbox, giving the model name, the exact version, the reason in plain words, and whether the removal was complete.
- **AC5.2.2:** Given a finding in a file, when the email is written, then it contains no secret or personal value.
- **AC5.2.3:** Given that the email cannot be sent, when the import ends, then the import still ends as failed with its files removed, and the problem with the email is recorded.

**Dependencies:** US5.1.

**INVEST notes:** Small and testable with a fake mail service.

## US6.1: Keep a record of each import

As a Model Importer, I want a record of each import and its result, so that I can look back at what was imported and why something failed.

**Priority:** Could Have

**Acceptance criteria**

- **AC6.1.1:** Given any import, when it ends, then a record holds the model name, the exact version, and the outcome.
- **AC6.1.2:** Given an import that failed on findings, when the record is written, then it lists the findings without their values.
- **AC6.1.3:** Given that this story is not built in the first version, when the first version ships, then the outcome of every import can still be found in the logs, as AC5.1.5 requires.

**Dependencies:** US3.1, US5.1. Built only if the Must Have stories are done (`scope-document.md`).

**INVEST notes:** The only extra chosen for the first version (scope definition Q1).

## Story Dependencies

US2.1 and US1.1 start the flow. US3.1 follows US2.1. US4.1 and US5.1 follow US3.1. US5.2 follows US5.1. US6.1 follows US3.1 and US5.1. The riskiest path, US2.1 then US3.1, is built first.

## Assumptions & Open Questions

- [assumption] When an event is rejected because the sender's address is missing, the failure email goes to the team mailbox only (AC1.1.2). The requirements review (R-03) left this open.
- [assumption] AC1.1.4 follows the requirements' assumption that a second event waits and is not refused (requirements assumption A2).
- Open question: Which files count as weight files, and what is their expected type? (requirements review R-02)
- Open question: Which kinds of secrets and personal data must the scan find? (requirements review R-02)
- Open question: What makes a version "exact", and who receives the "already stored" email in AC1.1.3? (requirements review R-03)
- NFR6 (tests cover at least 80 percent of lines) applies to the work behind every story rather than to one story, so it is not traced to a story.
