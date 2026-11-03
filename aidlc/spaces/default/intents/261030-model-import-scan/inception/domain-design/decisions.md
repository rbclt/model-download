# Architecture Decisions

Decisions made in Domain Design. Each follows the structure the Inception rules require: Context, Decision, Consequences, Alternatives Rejected. They trace to `requirements.md`, `stories.md`, and the answers in `domain-design-questions.md`.

## ADR-001: Split the application into five building blocks

- **Context:** The flow is event, download, scan, keep or remove, failure email. The rule "always remove downloaded files and temporary resources when an import fails" must hold whichever step fails (FR5.2). The first version is due at the end of tomorrow, so fewer pieces would be quicker, but the removal rule has to be provable by tests (NFR6).
- **Decision:** Use five building blocks: ImportCoordinator, ModelDownloader, LeakScanner, FileCustodian, FailureNotifier (Q1, option A).
- **Consequences:**
  - Positive: Removal has one owner that can be tested by forcing failures. Each block maps to a story: US1.1 and US5.1 to ImportCoordinator, US2.1 to ModelDownloader, US3.1 to LeakScanner, US4.1 and US5.1 to FileCustodian, US5.2 to FailureNotifier.
  - Negative: More pieces than strictly needed for a one-day first version.
  - Reversibility: High. Blocks can be merged later.
- **Alternatives Rejected:**
  - Four blocks, with FileCustodian's work done by ImportCoordinator: the coordinator would grow large and be harder to test.
  - Three blocks, with the failure email and the removal also in ImportCoordinator: the removal rule would be harder to prove because it is tangled with the import lifecycle.

## ADR-002: The ImportCoordinator runs the steps in order and waits for each

- **Context:** An import can run for hours and any step can fail. Stopping and cleaning up must work the same way whichever step fails (FR5.1, NFR4). The limit of 6 hours covers the whole import (NFR1).
- **Decision:** The ImportCoordinator calls each other block, waits for its answer, and decides what happens next. The other blocks do not call each other (Q2, option A).
- **Consequences:**
  - Positive: Stopping, removal, and the failure email happen in one place. The order is easy to follow and to test.
  - Negative: The coordinator knows the whole flow, so a change to the flow changes the coordinator.
  - Reversibility: Medium. Moving to announcements between blocks later would spread failure handling into every block.
- **Alternatives Rejected:**
  - Each block announces when it is done and the next block reacts: failure handling would be spread across all blocks, which is harder to prove and test in a day.

## ADR-003: FileCustodian owns both keeping and removing files, and the "already stored" check

- **Context:** The final location must hold either the whole model or nothing from this import (FR4.2), and a failure must remove partly moved files too (FR5.2, requirements review R-05). The duplicate check needs to know what is stored (FR1.4).
- **Decision:** FileCustodian moves a passed model to the final location, removes everything after a failure, and answers whether a model and exact version are already stored. The ImportCoordinator asks; it does not touch the files.
- **Consequences:**
  - Positive: One block holds all knowledge of where files are, which makes the all-or-nothing rule and the removal rule testable together.
  - Negative: FileCustodian has two jobs that could grow apart. If it gets large, the duplicate check can move to a block of its own.
  - Reversibility: High.
- **Alternatives Rejected:**
  - Let the ImportCoordinator check for duplicates by itself: it would need to know where files are kept, which spreads that knowledge across two blocks.
  - Put the removal in ModelDownloader: it would not know about partly moved files in the final location.

## ADR-004: Every entity has one owner, and the ImportRequest is owned by the ImportCoordinator

- **Context:** Each import produces a few records: the request, the downloaded file set, the findings, the stored model, and the failure notice. Ambiguous ownership is a design smell.
- **Decision:** ImportCoordinator owns ImportRequest. ModelDownloader owns ModelFileSet, LeakScanner owns ScanFinding, FileCustodian owns StoredModel, and FailureNotifier owns FailureNotice. The other four entities point back to the ImportRequest by its `importId`.
- **Consequences:**
  - Positive: The status and single outcome of an import live in one place (NFR4). Findings never contain values (FR3.5), which is easy to enforce because ScanFinding has no value attribute.
  - Negative: Reading the whole story of an import means joining five records by `importId`.
  - Reversibility: High at this level, since only the shape of the records is set here. The full schema is decided in Functional Design.
- **Alternatives Rejected:**
  - One shared record for the whole import: several blocks would write to it, so ownership would be unclear.

## ADR-005: A failure always takes one path: stop, remove, then email

- **Context:** Every failure kind needs the same handling: a bad event after validation, a download problem, a finding, an unreadable file, and the time limit (FR5.1 to FR5.5). The rule "never leave files behind" would break if some failure kinds skipped the removal.
- **Decision:** Any failure ends in the same sequence run by the ImportCoordinator: stop the import, ask FileCustodian to remove everything, then ask FailureNotifier to send the email. The email states whether the removal was complete. A failure of the removal or the email does not stop the rest of the sequence, and each is recorded.
- **Consequences:**
  - Positive: One path to test. An unreadable file and a finding behave the same way (FR3.4).
  - Negative: A bug in this path affects every failure kind, so it needs the strongest tests.
  - Reversibility: High.
- **Alternatives Rejected:**
  - A separate handling path for each failure kind: more code to test and more chances to skip the removal.
  - Stop at the first problem in the sequence, such as a failed removal: the person would then get no email, which breaks the rule that every failure is reported.

## Assumptions & Open Questions

- [assumption] A rejected event (a bad or duplicate request) never reaches the download, so only ImportCoordinator and FailureNotifier act on it. Whether a rejection goes through the same removal step is a detail for Functional Design.
- Open question: Which recipients get the "already stored" and "missing sender address" emails? This was left open by the requirements review (R-03).
