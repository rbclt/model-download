# Performance Requirements: u1-import-slice

These are the time and speed targets for the thin end-to-end slice. They detail the inception NFRs in `requirements.md` (NFR1 time limit, NFR2 size) and use the steps in `functional-spec.md`, the rules in `rules.md`, and the boundaries in `contract-summary.md`. The typical-time target comes from Q2 in `nfr-requirements-questions.md`. Each requirement carries the ID of the inception NFR it details.

| ID | Requirement | Target | Measured how | Source |
|----|-------------|--------|--------------|--------|
| NFR1.1 | An import that has not finished 6 hours after it started is stopped and treated as failed. u1 builds nothing that stops an import earlier than 6 hours; the 6-hour stop itself is built in u2-import-rules. | 6 hours, hard limit | A test run with a forced slow step is stopped at the limit (u2) | NFR1 |
| NFR1.2 | A typical import of a smaller model (about 16 GB of files) goes from the start event to its outcome in 1 hour or less. | 1 hour (target), 6 hours (limit) | Time from the event to the outcome, logged for every import | NFR1, Q2 |
| NFR1.3 | The download runs at an average of at least 20 MB per second. At this speed 16 GB takes about 14 minutes, which leaves room for retries inside the 1-hour target. | 20 MB/s average | Total bytes divided by download time, logged for every import | NFR1, NFR2 |
| NFR1.4 | The scan of the text and settings files of a model finishes in 10 minutes or less for up to 100 MB of such files. Weight files are not read for content (BR3.2), so their size does not change the scan time. | 10 minutes for 100 MB of text | Scan duration, logged for every import | NFR1 |
| NFR1.5 | The keep step (copying every file from the holding area to the final location) finishes in 15 minutes or less for 16 GB. | 15 minutes for 16 GB | Keep duration, logged for every import | NFR1 |
| NFR1.6 | After the import is stopped, the removal and the failure email take 15 minutes or less. | 15 minutes | Time from the stop to the outcome, logged | NFR1 |
| NFR2.1 | The application handles a model of up to 20 GB of files in total with the same targets as above, except that NFR1.2 may be exceeded in proportion to size. The 6-hour limit still applies. | 20 GB per model | A size-only test shows the application accepts a listing of 20 GB and 1,000 files; the time targets for 20 GB are proved by one staged run on a real model of at least 10 GB before the first release | NFR2 |
| NFR1.7 | A download that gets no bytes for 60 seconds counts as a temporary problem (BR2.4). If the average download speed over any 10 minutes is below 1 MB per second, the import fails with the plain reason "the download is too slow". This stops a stuck or slow host from running up cost with no limit. | 60 s stall; 1 MB/s floor over 10 minutes | A test with a stand-in host that stalls and one that sends slowly | NFR1 |
| NFR1.8 | As a backstop in u1, the platform stops whatever runs an import after 7 hours at the latest, even if no other rule fired. The full 6-hour rule with its email is built in u2-import-rules (NFR1.1). | 7 hours | Deployment check of the configured limit | NFR1 |
| NFR1.9 | The scan is bounded against hostile files: a text file larger than 200 MB, or a file that takes more than 5 minutes to scan, counts as an unreadable-file finding (BR3.4). Lines longer than 1 MiB are scanned in overlapping windows of 64 KiB, so a pattern cannot take unbounded time on one huge line. The weight header is limited to 100 million bytes (BR3.2) and the compute has at least 4 GiB of memory. | 200 MB per text file; 5 minutes per file; 1 MiB line; 4 GiB memory | Tests with a 201 MB text file, a 2 MiB single line, and a slow-pattern stand-in | NFR1 |

## How Each Requirement Is Verified

| IDs | Verified by |
|-----|-------------|
| NFR1.1, NFR1.8 | NFR1.1 is tested in u2-import-rules; NFR1.8 is a deployment check of the configured limit |
| NFR1.2 to NFR1.6 | The durations logged for every import (NFR5.7); a pass is every import of a model up to 16 GB meeting NFR1.2 over the first 10 imports |
| NFR1.7, NFR1.9 | Automated tests with stand-ins that stall, send slowly, or hold hostile files |
| NFR2.1 | The size-only test and the staged run described in the table |

## Resource Constraints

- The compute that runs an import has at least 4 GiB of memory and enough local disk for what the chosen design keeps on disk. A container on AWS Fargate has 20 GiB of local disk by default and can be set up to 200 GiB, so a 20 GB model needs either the larger setting or streaming (decided in NFR Design).
- The application runs in the common AWS account in us-east-1, close to the holding area and the final location in S3, so the copy in the keep step stays inside one Region.
- A file is written to S3 as it is received or copied in parts, so the time and memory used do not grow with the size of the largest file. The way this is done is chosen in NFR Design.
- A model of 20 GB must not need more local disk than the compute chosen in Infrastructure Design offers. If it does, the chosen design must stream the files instead.

## Assumptions & Open Questions

- [assumption] The 20 MB per second average (NFR1.3) is a planning figure for the Hugging Face download from AWS, not a promise from Hugging Face. It is checked on the first real import and the target can change then.
- [assumption] The scan, keep, and removal times (NFR1.4 to NFR1.6) are targets set by the architect for a model of this size. No one has measured them.
- Open question: Which real model is imported first? Its actual size sets the first measured value for NFR1.2 and NFR1.3.
