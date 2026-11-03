# Scalability Requirements: u1-import-slice

This describes how much work the thin end-to-end slice must carry. It details inception NFR2 (size) in `requirements.md` and uses the rules in `rules.md`, the steps in `functional-spec.md`, and the boundaries in `contract-summary.md`. The volume comes from Q1 in `nfr-requirements-questions.md`. Each requirement carries the ID of the inception NFR it details.

| ID | Requirement | Target | Source |
|----|-------------|--------|--------|
| NFR2.2 | The application handles up to 5 imports a day. | 5 imports per day | NFR2, Q1 |
| NFR2.3 | At most one import runs at a time, whatever the mechanism, and this is enforced by the design, not by the owner's discipline. A second event that arrives while an import runs waits and starts afterwards; it is not dropped (requirements FR1.5). NFR Design chose a FIFO queue in front of a worker limited to one task, so u1 delivers the wait. No automatic scaling out is needed. | 1 import at a time | NFR2, NFR Design Q2 B |
| NFR2.4 | A model has up to 1,000 files and a total of up to 20 GB (NFR2.1). A file can be as large as the whole model. | 1,000 files, 20 GB | NFR2 |
| NFR2.5 | The holding area holds the files of one running import at a time, so its size is at most the size of one model plus the files of any failed import whose removal did not finish. | 20 GB plus leftovers | NFR2 |
| NFR2.6 | The final location grows by the size of each stored model. No limit is set in this version. The cost grows with the models kept, and the owner removes models by hand. | No limit | NFR2 |

## How Each Requirement Is Verified

| IDs | Verified by |
|-----|-------------|
| NFR2.2 | Not tested directly; the monthly cost alarm (NFR5.10) shows if volume is far above plan |
| NFR2.3 | A test that starts two imports and shows the second does not run in parallel; a deployment check of the concurrency limit |
| NFR2.4 | A test with a listing of 1,000 files and 20 GB; a listing of more than 1,000 files fails the import with a plain reason |
| NFR2.5, NFR2.6 | A deployment check of the S3 lifecycle rules in NFR4.9; the size of the locations is read from S3 |

## Scaling Triggers and Capacity

- No scaling trigger is needed at 5 imports a day with one import at a time.
- Larger models and many imports a day are out of scope for this version (`requirements.md`, NFR2). Changing either is a new requirement and a new design.

## Assumptions & Open Questions

- [assumption] The 1,000-file limit is an upper bound chosen by the architect; typical models have fewer than 100 files. A model listing with more than 1,000 files fails the import with a plain reason (to be added in Code Generation as part of BR1.3).
- Open question: Should the final location expire old models after some time? This version keeps models until someone removes them.
