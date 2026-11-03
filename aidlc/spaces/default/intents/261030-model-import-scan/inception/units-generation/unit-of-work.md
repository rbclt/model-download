# Units of Work

The application is built in two units, as chosen in `units-generation-questions.md` (Q1, option B). Both are parts of one application that is deployed together (Q2, option A). The five building blocks come from `components.md` (ImportCoordinator, ModelDownloader, LeakScanner, FileCustodian, FailureNotifier), and the stories come from `stories.md`.

## Unit Table

| Unit ID | Directory | Kind | Complexity | Deployment model |
|---------|-----------|------|------------|------------------|
| U1 | u1-import-slice | service | L | Shared: part of the one application deployed together |
| U2 | u2-import-rules | service | M | Shared: part of the one application deployed together |

## U1: u1-import-slice

**Description:** A thin end-to-end slice of the whole import. It uses all five building blocks in their simplest form and proves the riskiest path first, the download and scan of a smaller model. The current version targets smaller models, so the 140 GB case is not part of this unit or of U2.

**Boundaries and what it owns:**

- ImportCoordinator in its simplest form: accepts a start event with a valid model name, exact version, and sender address, runs one import, and decides the outcome.
- ModelDownloader: downloads every file of the exact version of a public model into the holding area, without an account or token, and never treats a half-downloaded file as complete.
- LeakScanner: scans text and settings files, checks weight files only for the expected file type, treats any finding or unreadable file as a finding, and records findings without their values.
- FileCustodian: moves a passed model to the final location and removes the holding area files and temporary resources after a failure.
- FailureNotifier: sends one failure email to the sender and the team mailbox with the reason and whether the removal was complete, without any secret or personal value.

**Responsibilities and delivers:** One import that runs from event to a single outcome for the normal paths: a clean model is kept in the final location, and a finding or a failed step stops the import, removes everything, and sends the email.

**Implementation notes and constraints:**

- Python and AWS CDK, with the team's formatter and linter (`team-practices.md`).
- Starts with the riskiest part, the download and scan of a smaller model, about 8 billion parameters or fewer (`intent-backlog.md`, B-1 and B-2; requirements NFR2).
- Never puts a secret, token, or credential in code or logs.
- Rules that are left to U2 are not needed for U1 to run end to end.

## U2: u2-import-rules

**Description:** Completes the rules around the slice. It adds the less common paths that the requirements and stories describe.

**Boundaries and what it owns:**

- ImportCoordinator additions: reject a bad start event, refuse a duplicate of a stored model and version, hold a second event while an import runs, and stop an import 6 hours after it started.
- FileCustodian additions: leave nothing from a failed import in the final location (all or nothing), and report what a failed removal left behind.
- FailureNotifier additions: record when the failure email cannot be sent, and handle the rejected-event and "already stored" emails.
- The optional record of each import and its result (could have, `intent-backlog.md`, B-6).

**Responsibilities and delivers:** All of the failure and edge rules, so that every import ends in exactly one outcome.

**Implementation notes and constraints:**

- Builds on U1's code, so U2 changes code written in U1.
- Several rules depend on answers still open from earlier stages: what an exact version is, who gets the "already stored" and missing-address emails, and the kinds of data the scan looks for.
- The optional record is built only if the must-have work in U1 and U2 is done.

## Assumptions & Open Questions

- [assumption] Complexity estimates (L and M) are rough, relative sizes, and no effort has been estimated.
- Open question: Whether the rejected-event paths (a bad event, an already-stored model) belong to U2 as planned or to U1. They are placed in U2 because U1 only needs a valid event to run end to end.
