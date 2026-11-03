# Performance Design: u2-import-rules

This describes how this Unit meets the requirements in `performance-requirements.md`. It uses the components in `logical-components.md`, the rules BR8.1 to BR8.4 and BR9.1 to BR9.4 in `rules.md`, the steps in `functional-spec.md`, and the technology in `tech-stack-decisions.md`. The other requirement documents are `security-requirements.md`, `scalability-requirements.md`, `reliability-requirements.md`, and `observability-requirements.md`. No question to the owner was needed for this document. It changes code that u1-import-slice built, and it says where.

## Design Decisions

| Requirement | Design |
|-------------|--------|
| NFR1.1 (6-hour limit) | One `Deadline` value is made when the ImportCoordinator takes an event off the queue: `startedAt` from the injected clock and `deadlineAt` = `startedAt` + 6 hours. It is passed to the downloader, the scanner, the custodian, and the retry helper. `Deadline.check()` raises `DeadlineReached` when the clock is at or after `deadlineAt`. The ImportCoordinator turns `DeadlineReached` into a failed step with the plain reason "the time limit of 6 hours passed" (BR8.4). The time an event waited in the queue is never part of it, because `startedAt` is set after the receive. The existing 7-hour watchdog of the worker is not changed |
| NFR1.10 (check points, 10 minutes) | The check points of BR8.2 are placed as in the table below. Each is one clock read and one comparison. No check point is placed after the completion marker is written, and none in the removal or the email |
| NFR1.11 (front door 5 seconds) | The front door reads the message body, runs the content check on all three fields, derives the importId, and does one `HEAD` of the marker (and, only when the marker exists, one `GET` of its few hundred bytes). No scan of a listing and no download happens before it. The time it takes is logged as `frontDoorSeconds`. The 5 seconds hold when the storage service answers normally. With the short timeouts of the state client (see "Client Settings"), a failing service makes the front door give up in at most about 45 seconds (3 tries of 15 seconds), which BR7.1 treats as a failed step |
| NFR1.12 (rejected or refused event in 1 minute) | These paths make no download and no copy. They make at most two email sends, two counter updates, one outcome write, and one record write, in that order (rules BR6.1, BR7.1, BR10.1, BR11.1). u1 created the SES and S3 clients with the default settings of the SDK (60 seconds, with retries), so this Unit designs the client settings (see "Client Settings" below): the SES client and the state client get a 5 second connect timeout, a 10 second read timeout, and at most 2 tries (3 for the state client). One send takes well under a second in a normal run. With a failing service the outcome is still recorded, but the time can pass 1 minute; the target holds when the services answer |
| NFR1.13 (removal, proof, and email in 15 minutes) | The removal lists each prefix once with the paginated listing (1,000 keys a page) and deletes in batches of 1,000 (the batch size of u1). The proof of BR9.3 repeats the same listings, so it costs one more pass of 1 to 3 calls for a model of 1,000 files. The email is at most two sends |
| NFR2.7 (+1 minute at 1,000 files) | The marker is one `PUT`. The listing in BR9.2 and BR9.3 is paginated. For 1,000 stand-in objects the added calls are about 6 requests, measured in a test |

## Where the Deadline Is Checked

| Place | Check point (BR8.2) | How |
|-------|---------------------|-----|
| ImportCoordinator, before a move to `scanning` or `keeping` | Before a status change | `deadline.check()` inside the move. The moves into `removing` and `done` are never checked, and neither is the move to `done` after the marker (BR9.6) |
| Downloader, before each file starts | Before each file download | `deadline.check()` before the request for the file |
| Downloader, inside a download | At every chunk written | `deadline.check()` once per chunk of the streaming loop in the Hugging Face client; the chunk size of u1 is kept |
| Retry helper | Before each retry, and the wait | The wait is `min(planned wait, time left)`. If no time is left, the helper raises `DeadlineReached` instead of waiting |
| Scanner, before each file | Before each file is scanned | `deadline.check()` between files. The scan of one text file keeps its own limit of 5 minutes (NFR1.9 of u1) |
| Custodian, before each file copy | Before each file is copied | `deadline.check()` before the copy of each file |
| Custodian, inside the copy of a file larger than 5 GiB | At every part | An explicit multipart copy: one `upload_part_copy` call per part, `deadline.check()` before each part, and an abort of the upload when the deadline is reached. Files of 5 GiB or less keep the single managed copy call of u1 |
| Custodian, just before the marker | The last check point | `deadline.check()` once, then the marker write (BR8.3, BR9.1) |

```python
class Deadline:
    def __init__(self, started_at, clock, limit=timedelta(hours=6)):
        self.at = started_at + limit; self._clock = clock
    def remaining(self):
        return self.at - self._clock()
    def check(self):
        if self.remaining() <= timedelta(0):
            raise DeadlineReached(REASON_TIME_LIMIT)   # "the time limit of 6 hours passed"
```

## How Far Past the Deadline an Import Can Run

| Work in progress at the deadline | Longest time before the import stops that work | Bound |
|----------------------------------|-----------------------------------------------|-------|
| A download | One chunk, plus the read timeout of 60 seconds if the host sends nothing | The chunk check and NFR1.7 of u1 |
| A retry wait | Zero: the wait is cut short | The wait is the smaller of the plan and the time left |
| The scan of one text file | 5 minutes | NFR1.9 of u1 |
| A copy of one file of 5 GiB or less | The time of one server-side copy within one Region, expected to be a few minutes | A target; not measured (assumption) |
| A copy of a file larger than 5 GiB | One part | The part check |
| Writing the marker | One request | Not interrupted by design (BR8.3) |

The longest work that cannot be interrupted is therefore the scan of one file or one copy call, so the target of NFR1.10, work stopped no later than 10 minutes after the deadline, holds if the copy call is under 5 minutes. The removal and the email follow within the 15 minutes of NFR1.13. The 7-hour watchdog is one hour after the deadline and is the hard bound.

## Client Settings

`worker.py` (`main`) builds the clients with `boto3` default settings in u1. This Unit builds three clients on purpose:

| Client | Used for | Settings | Why |
|--------|----------|----------|-----|
| State client (S3) | `ImportStore` (records, outcomes, counters) and the marker `HEAD` and `GET` of the front door and the removal | connect 5 s, read 10 s, standard retry mode, 3 attempts | Short calls that must fail fast (NFR1.11) |
| Data client (S3) | Downloads to the holding bucket, copies, listings, deletes | SDK defaults, as in u1 | Calls that can legitimately take a long time; the deadline checks bound the work, not the socket |
| Mail client (SES) | The three kinds of email | connect 5 s, read 10 s, standard retry mode, 2 attempts | Keeps NFR1.12 meaningful when the service is slow |

The queue client is unchanged: its long poll needs a read timeout longer than the wait.

## Changes to Code Built in u1

- `worker.py`: build the three clients above; call the new first parse of `event.py`; route a rejected event to the coordinator (see `logical-components.md`).
- `coordinator.py`: build the `Deadline`; check it in the moves; map `DeadlineReached` to a failed step before the generic error handler; add the front door steps.
- `hf_client.py`, `downloader.py`, `retry.py`, `scanner.py`, `custodian.py`: accept the `Deadline` and place the checks above. The behaviour without a `Deadline` stays as in u1, so u1's tests still run unchanged.
- `custodian.py`: replace the single managed copy for files above 5 GiB by the explicit part loop.

## Assumptions & Open Questions

- [assumption] A server-side copy of one file up to 5 GiB inside one Region finishes within 5 minutes. This is not measured. If the first real import shows otherwise, the part size or the threshold is lowered so that the longest uninterrupted copy is shorter.
- [assumption] The chunk of the streaming download is small enough (megabytes) that a check per chunk costs nothing.
- Open question: Which real model is imported first (open since u1)? Its measured copy and download times are the first real values for NFR1.10 and NFR1.13.
