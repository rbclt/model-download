<!-- INVARIANT: examples are single-line HTML comments so a fresh template parses to total=0 (MEMORY_EMPTY). Do NOT un-comment or split across lines. t100 guards this. -->
> This file is kept up to date automatically while the stage runs. Add observations at the review step, not by editing here directly.

## Interpretations
<!-- example: 2026-05-29T10:14:32Z — chose REST over GraphQL; the consuming team only needs CRUD, revisit if subscriptions land -->

## Deviations
<!-- example: 2026-05-29T10:14:32Z — skipped the optional caching layer the stage prose suggested; the dataset is small enough that it adds risk -->

## Tradeoffs
<!-- example: 2026-05-29T10:14:32Z — picked TDD over BDD this run; the team is unit-first and the domain is well-understood -->

## Open questions

- 2026-11-02T03:40:00Z [unit u2-import-rules] The review came back READY with three Minor points that are not in the files, because the verdict was recorded first. Code Generation must apply them: (1) `remove` for an import whose marker write succeeded must not report "removal complete" to the coordinator; it must return a result the coordinator treats as stored; (2) the own-marker finish (BR7.1) must call a custodian method that deletes `holding/<importId>/`; (3) `FileCustodian` takes the second (state) S3 client for the marker reader, and the reader has a distinct "check failed" result that becomes a failed step and is never mapped to unreadable. Also: apply the same safe-pattern choice to the eventId in log fields, and put the `_COMPLETE` path check in the path checks of `event.py`.
<!-- example: 2026-05-29T10:14:32Z — confirm the retention window with compliance before the next stage hardens the schema -->
