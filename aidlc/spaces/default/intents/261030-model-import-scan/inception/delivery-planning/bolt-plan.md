# Bolt Plan

A **Bolt** is one pass of building over a piece of the work, ending in something that runs and can be checked. This plan has two Bolts, one for each Unit of Work (a Unit is a part of the application that is designed, built and tested together). It follows `delivery-planning-questions.md` (Q1, option A: one Bolt per Unit) and the Units in `unit-of-work.md`. The order is fixed by the dependency in `unit-of-work-dependency.md`: u2-import-rules builds on code that u1-import-slice creates. The requirements (FR1 to FR6, NFR1 to NFR6) are in `requirements.md`, and the five building blocks each Bolt uses are in `components.md`.

## Bolt Sequence

| Bolt | Unit of Work | Walking skeleton | Depends on | Owner |
|------|--------------|------------------|------------|-------|
| B1 | u1-import-slice | Yes | None | aidlc-developer-agent (AI) |
| B2 | u2-import-rules | No | B1 | aidlc-developer-agent (AI) |

A **walking skeleton** is a minimal version that runs the whole way through, built first to prove the pieces connect (`team-practices.md`, Walking Skeleton).

## B1: u1-import-slice (walking skeleton)

**Stories covered:** US1.1 (AC1.1.1), US2.1 (AC2.1.1 to AC2.1.3), US3.1 (all), US4.1 (AC4.1.1, AC4.1.3), US5.1 (AC5.1.1), US5.2 (AC5.2.1, AC5.2.2), as split in `unit-of-work-story-map.md`.

**Parts of the architecture it proves:** all five building blocks in their simplest form (ImportCoordinator, ModelDownloader, LeakScanner, FileCustodian, FailureNotifier), the start event (C1), the Hugging Face download (C2), the failure email (C3), and the shared import record and S3 locations (C4) from `contract-summary.md`.

**Definition of Done:**

- A valid start event with a full commit hash starts one import of a smaller public model (about 8 billion parameters or fewer, roughly 16 GB).
- A clean model ends up complete in the final location in S3, and the holding area is empty.
- A finding in a text or settings file, or a failed step, stops the import, removes the downloaded files and the temporary resources, and sends one failure email to the sender and the team mailbox.
- No secret, token, or credential appears in code, logs, findings, or emails.
- The infrastructure is written with AWS CDK in Python and the code passes Black and Ruff.
- Tests pass and cover at least 80 percent of lines, including one test that runs the whole flow from event to outcome with fake stand-ins for S3, Hugging Face and email.

**Confidence hypothesis:** The download, scan, keep-or-remove, and email steps work together end to end on a real smaller model, and the riskiest part (download size and time, and what the scan finds in real files) fits inside the 6-hour limit.

**Expected demo:** Send a start event for a real smaller public model. Show the model files appear in the final location and the holding area is empty. Then run the same flow with a test file that holds a fake secret, and show the import stops, nothing remains in S3 from it, and the failure email arrives.

**Real project check:** the verification command recorded at the end of this stage (see `risk-and-sequencing-rationale.md`, Verification command).

**Build order inside B1** (from `unit-of-work-story-map.md`): download, then scan, then keep or remove, then the failure email, then the start event that ties them together.

## B2: u2-import-rules

**Stories covered:** US1.1 (AC1.1.2 to AC1.1.4), US2.1 (AC2.1.4), US4.1 (AC4.1.2), US5.1 (AC5.1.2 to AC5.1.5), US5.2 (AC5.2.3), US6.1 (could have).

**Definition of Done:**

- A bad start event is rejected with no download and the rejection email described in C3. A missing sender address sends the email to the team mailbox only.
- A model and version that is already stored is refused with the "already stored" email to the sender and the team mailbox.
- A second event that arrives while an import runs waits and starts afterwards.
- An import stops 6 hours after it starts and is treated as failed.
- A failure during the move to the final location leaves nothing from this import there.
- When removal fails, the email names what was left behind. When the email cannot be sent, the import still ends as failed and the email problem is recorded.
- Every import ends in exactly one outcome and the outcome and reason are in the logs.
- The optional record of imports (US6.1) is built only if all the above is done and time remains.
- Tests pass and cover at least 80 percent of lines across both Units.

**Confidence hypothesis:** The failure and edge rules hold under forced failures, so every import ends in exactly one outcome and nothing is ever left behind.

**Expected demo:** Send a bad event, a duplicate, a second event during a running import, and an import that is forced to fail during the move. Show one outcome each, with the right email and nothing left in S3.

**Build order inside B2:** the 6-hour limit and the all-or-nothing move first, then the rejected-event paths and the waiting event, then the failed-email record, then the optional record of imports last.

## Assumptions & Open Questions

- [assumption] Bolt size follows Q1 option A. The could-have record of imports (US6.1) is part of B2 and is dropped first if time runs short.
- [assumption] The skeleton demo with a real model needs the deploy-time settings (team mailbox address, verified email sender address, first model) from `external-dependency-map.md`. Until the owner supplies them, B1 is checked with fake stand-ins only.
- Open question: Which public model is imported first? It must fit the smaller-model size in NFR2 and use an architecture that Amazon Bedrock Custom Model Import supports.
