<!-- INVARIANT: examples are single-line HTML comments so a fresh template parses to total=0 (MEMORY_EMPTY). Do NOT un-comment or split across lines. t100 guards this. -->
> This file is kept up to date automatically while the stage runs. Add observations at the review step, not by editing here directly.

## Interpretations
<!-- example: 2026-05-29T10:14:32Z — chose REST over GraphQL; the consuming team only needs CRUD, revisit if subscriptions land -->

## Deviations

- 2026-11-01T23:10:00Z [unit u2-import-rules] u1's NFR4.2 expected an automatic clean-up for a stopped run to be built in u2. The owner chose to keep the approved design (alert, 2-day expiry, send the message again), so NFR4.19 replaces that sentence of NFR4.2 and says so.
<!-- example: 2026-05-29T10:14:32Z — skipped the optional caching layer the stage prose suggested; the dataset is small enough that it adds risk -->

## Tradeoffs
<!-- example: 2026-05-29T10:14:32Z — picked TDD over BDD this run; the team is unit-first and the domain is well-understood -->

## Open questions

- 2026-11-02T03:20:00Z [unit u2-import-rules] The review came back READY with two Major points that are not fixed in the requirement files, because the verdict was recorded first: (1) NFR3.25 leaves the result for a model file named `_COMPLETE` or an unreadable marker to NFR Design, and a planted file can make removalComplete read true while files remain; (2) NFR4.19 says leftover files expire in 2 days, but only the holding area expires, so a stop during the keep step leaves unfinished files in the final location that nothing expires. NFR Design for this Unit settles both and says so.
<!-- example: 2026-05-29T10:14:32Z — confirm the retention window with compliance before the next stage hardens the schema -->
