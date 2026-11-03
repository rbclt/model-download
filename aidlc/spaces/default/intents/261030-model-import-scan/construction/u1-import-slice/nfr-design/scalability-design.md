# Scalability Design: u1-import-slice

This describes how the thin end-to-end slice meets the requirements in `scalability-requirements.md`. It uses the components in `logical-components.md`, the steps in `functional-spec.md`, the technology in `tech-stack-decisions.md`, and the boundaries in `contract-summary.md`. The queue and the single worker come from Q2 B in `nfr-design-questions.md`. The other requirement documents are `performance-requirements.md`, `security-requirements.md`, `reliability-requirements.md`, and `observability-requirements.md`.

## Design Decisions

| Requirement | Design |
|-------------|--------|
| NFR2.2 (5 imports a day) | The queue absorbs bursts of events. At 5 imports a day with one at a time and a typical import time of about an hour, the worker runs at most a few hours a day |
| NFR2.3 (one at a time) | The import queue is FIFO with one message group, so messages are taken in order. The worker service has a maximum of 1 task, so one import runs at a time. A second event waits in the queue (it is not dropped), which delivers the wait of requirements FR1.5 in u1. Queue retention is 4 days, so an event waits that long at most |
| NFR2.4 (1,000 files, 20 GB) | The file list is checked before any download: more than 1,000 files, or a total over 20 GB, fails the import with a plain reason (an addition to BR1.3 recorded here and built in Code Generation) |
| NFR2.5 (holding area size) | The holding area holds one running import at a time. Objects expire after 2 days by lifecycle rule, which bounds the leftovers of a crashed run |
| NFR2.6 (no limit on the final location) | No expiry on `models/`. The monthly cost alarm (NFR5.10) shows growth in cost |

## Transfer of the Wait from u2 to u1

Choosing the queue (Q2 B) moves the wait for a second event, requirements FR1.5 and story criterion AC1.1.4, from u2-import-rules to u1-import-slice. The queue delivers it in u1, with no `waiting` status in the import record. u2 only verifies it with a test. Files that still say otherwise are corrected when Code Generation updates the design documents: `unit-of-work.md`, `unit-of-work-story-map.md`, `bolt-plan.md` (the B2 definition of done), the statement "concurrent events cannot occur in u1" in `functional-spec.md`, the `waiting` status in contract C4 of `contract-summary.md`, and the text of NFR2.3 in `scalability-requirements.md` (corrected in this stage).

## Scaling Rules

- **Scale out:** none. The design never runs two imports at the same time.
- **Scale from zero:** the service starts at 0 tasks. A CloudWatch alarm on the number of visible messages (one or more for one minute) sets the desired count to 1. Task start takes about a minute, which is small against an import.
- **Scale to zero:** when the queue shows no visible and no in-flight messages for 5 minutes, the service goes back to 0. A missing metric counts as no messages. The worker sets task scale-in protection as soon as it starts, with an expiry of 8 hours, renews it every 5 minutes while it works or waits for a message, and releases it after the queue has been empty for 2 minutes. A scale-in therefore cannot stop a running import, and it cannot catch a task between two messages. A scale-in stop reaches the worker as SIGTERM, which it answers with a clean exit (exit code 0) when idle, so alert (c) does not fire.
- **Capacity limits:** one task, 2 vCPU, at least 4 GiB of memory, 30 GiB of local disk. Raising any of these is a deployment setting.

## Why a Queue and Not a Lock Table

The owner chose the queue (Q2 B). A lock table would drop a second event; the queue keeps it and is the same mechanism that u2 would otherwise have to add. The queue needs no lock release and no lock expiry, because a message that is still being worked on is hidden by the heartbeat, and a message whose run crashed becomes visible again within 15 minutes and goes to the dead-letter queue at the next receive, which also frees the message group (see the message life cycle in `reliability-design.md`).

## Assumptions & Open Questions

- [assumption] A message that waits behind a running import is in the queue for as long as that import runs, at most the 7-hour backstop. Its own 6-hour clock starts when its import starts, not when the message arrived (this settles the open question of the Contract Design review, finding R-02).
- [assumption] The 15-minute visibility, renewed every 5 minutes, applies to the message being worked on only; waiting messages are not hidden.
- Open question: How the scale-from-zero alarm interacts with a crash that leaves a message in flight is settled in Infrastructure Design with a test deployment.
