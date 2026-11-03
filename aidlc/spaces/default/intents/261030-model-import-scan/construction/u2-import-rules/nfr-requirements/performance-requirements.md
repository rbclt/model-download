# Performance Requirements: u2-import-rules

These are the time and speed targets that this Unit adds to the application. They detail the inception NFRs in `requirements.md` (NFR1 time limit, NFR2 size) and use the steps in `functional-spec.md`, the rules in `rules.md` (BR8.1 to BR8.4, BR9.1 to BR9.4), and the boundaries in `contract-summary.md`. The targets of u1-import-slice (typical import in 1 hour, download at 20 MB per second, scan, keep and removal times, and the 7-hour backstop) stay as they are. Each requirement carries the ID of the inception NFR it details. NFR1.1 was written in u1 and is built here; its ID is kept so that one requirement has one ID.

| ID | Requirement | Target | Measured how | Source |
|----|-------------|--------|--------------|--------|
| NFR1.1 | An import that has not finished 6 hours after it began is stopped and treated as failed. The 6 hours count from the moment the application takes the event off the queue (BR8.1); time spent waiting in the queue does not count. | 6 hours, hard limit | Tests with an injected clock and a forced slow step: the import fails with the reason "the time limit of 6 hours passed", and a test where the event waited 7 hours in the queue still runs | NFR1 |
| NFR1.10 | The deadline is checked before each status change, before each file download and each retry, inside a download at every chunk written, before each file scan, before each file copy, and inside a copy of a file larger than 5 GiB at every part (BR8.2). The import has stopped its work no later than 10 minutes after the deadline. Work that cannot be interrupted is one copy call of a file up to 5 GiB, the scan of one text file (at most 5 minutes by NFR1.9 of u1), and the removal and email. | 10 minutes after the deadline; 7-hour backstop of u1 as the hard bound | Tests with an injected clock that pass the deadline during a download, during a large copy, and during a scan, and check where each stops | NFR1 |
| NFR1.11 | The checks at the front door (reading the event, the content check, and the look for the completion marker) take 5 seconds or less in total. | 5 seconds | The duration of the front door is logged for every import and checked in a test with a stand-in storage service | NFR1 |
| NFR1.12 | An event that is rejected or refused as already stored reaches its outcome, with its emails sent, in 1 minute or less from the moment it is taken off the queue. | 1 minute | The time from the start of handling to the outcome, logged for every import and checked in a test | NFR1 |
| NFR1.13 | After a failed import, the removal, the check that proves nothing is left (BR9.3), and the failure email take 15 minutes or less in total, for a model of up to 1,000 files. This extends NFR1.6 of u1, which covered the removal and the email, with the listing. | 15 minutes | Time from the stop to the outcome, logged; a test with 1,000 stand-in objects | NFR1 |
| NFR2.7 | The application still handles a model of up to 20 GB and 1,000 files (NFR2.1 of u1). The keep step with the marker, and the listing after a removal, add no more than 1 minute for 1,000 files. | +1 minute at 1,000 files | A test with 1,000 stand-in objects | NFR2 |

## How Each Requirement Is Verified

| IDs | Verified by |
|-----|-------------|
| NFR1.1, NFR1.10 | Automated tests with an injected clock and stand-ins that stall or run long; no test waits for real hours |
| NFR1.11, NFR1.12, NFR1.13, NFR2.7 | The durations logged for every import (NFR5.7 of u1) and tests with stand-in storage and a fake email service |

## Resource Constraints

- The deadline checks add a clock read at each check point and no extra network call, so they add no cost to a normal import.
- The stored-copy check at the front door is one read of one small object. The rejected and already-stored paths download nothing and need no local disk.
- The listing after a removal (BR9.3) lists at most the objects of one import, which is at most 1,000 files plus a few extra, so it needs no paging beyond what the storage client does.

## Assumptions & Open Questions

- [assumption] The 10-minute target after the deadline (NFR1.10) is the architect's target. The scan of one text file (5 minutes) and one copy call of a file up to 5 GiB (a few minutes within one Region) are the longest pieces that cannot be interrupted. Nobody has measured the copy.
- [assumption] The 5-second and 1-minute figures (NFR1.11, NFR1.12) assume the storage and email services answer normally. A slow email service can pass 1 minute without breaking the rule that the outcome is still recorded.
- Open question: Which real model is imported first (open since u1)? Its measured times are the first real values for NFR1.2 and NFR1.10.
