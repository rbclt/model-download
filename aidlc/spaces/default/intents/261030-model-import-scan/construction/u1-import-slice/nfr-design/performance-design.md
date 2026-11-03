# Performance Design: u1-import-slice

This describes how the thin end-to-end slice meets the time targets in `performance-requirements.md`. It uses the components in `logical-components.md`, the steps in `functional-spec.md`, and the technology in `tech-stack-decisions.md`. The container choice is Q1 A in `nfr-design-questions.md`. The other requirement documents are `security-requirements.md`, `scalability-requirements.md`, `reliability-requirements.md`, and `observability-requirements.md`, and the boundaries are in `contract-summary.md`.

## Data Path

Up to 4 files move through four steps at the same time, and each local copy is deleted as soon as its upload ends, so the local disk holds at most the 4 files in flight plus working space (bounded by the 20 GB total check):

1. Download from Hugging Face to local disk.
2. Check size and, where the listing gives one, the SHA-256 hash (NFR3.13).
3. Upload to `holding/<importId>/` in S3.
4. Delete the local copy.

The scan then reads from S3: text files in a stream, weight files only for their header (BR3.2). The keep step copies inside S3 from the holding area to the final location, so no file passes through the worker again.

## Design Decisions

| Requirement | Design | Why |
|-------------|--------|-----|
| NFR1.2, NFR1.3 (1 hour, 20 MB/s) | Up to 4 files are downloaded in parallel, each over its own connection, read in 8 MiB parts. Uploads to S3 use multipart upload with 32 MiB parts and 4 parallel parts for each file, so upload buffers use about 0.5 GiB of memory. The worker has 2 vCPU and at least 4 GiB of memory | Parallel files raise throughput beyond one connection's speed; 16 GB at 20 MB/s is about 14 minutes, so the 1-hour target leaves room for retries |
| NFR1.4 (scan 10 minutes for 100 MB) | Text files are read in 1 MiB parts through a streaming decoder; patterns are compiled once and run per line | A single pass at memory speed finishes 100 MB in seconds; the 10-minute target is a ceiling |
| NFR1.5 (keep 15 minutes for 16 GB) | S3 server-side copy with multipart copy and 8 parallel parts | Copies run inside S3 at several hundred MB/s |
| NFR1.6 (removal and email 15 minutes) | Deletes are sent in batches of up to 1,000 keys; the email is sent once per recipient | Removing 1,000 objects takes seconds; emails take a second each |
| NFR1.7 (60 s stall, 1 MB/s floor) | Connection time-out 10 seconds and read time-out 60 seconds on every Hugging Face request; a rolling counter of bytes per 10 minutes checks the 1 MB/s floor and fails the import with the reason "the download is too slow" | The read time-out is the stall rule; the counter is the floor |
| NFR1.8 (7-hour backstop) | A watchdog thread in the program ends the process with a non-zero exit code 7 hours after the import started. This is a deliberate change to NFR1.8, which named a platform stop; a program-level stop works the same on every compute. The queue message stays hidden by a heartbeat that renews its 15-minute visibility every 5 minutes, and the heartbeat stops with the process | The process cannot outlive the backstop; the message is not shown again while the process runs |
| NFR1.9 (scan bounds) | The scanner refuses a text file over 200 MB, stops scanning a file after 5 minutes, and cuts lines over 1 MiB into overlapping 64 KiB windows; the task has at least 4 GiB of memory | A hostile file cannot hang or exhaust the worker; the bound is a finding (BR3.4) |
| NFR2.1 (20 GB) | Local disk is 30 GiB, so the largest single file the worker can handle is about 25 GB; a listing over 20 GB in total fails the import before any download with a plain reason | The disk figure is above the 20 GB target; the early check avoids a late failure |
| NFR1.1 | The 6-hour stop is built in u2-import-rules; the 7-hour backstop above is the u1 safety net | Matches the split in `unit-of-work.md` |
| NFR5.7 | Each phase writes its start time, end time, and byte count to the log | Allows the durations to be checked against the targets |

## Sketch of the Parallel Download

```text
for each file in listing, at most 4 at a time:
    download(file) with read timeout 60 s, retries per NFR4.4
    verify size and hash
    upload to holding/<importId>/<path>, multipart
    delete local copy
fail the import on the first file that exhausts its retries
```

## Assumptions & Open Questions

- [assumption] Hugging Face serves four parallel connections from one client without throttling. The first real import shows whether the number should change.
- [assumption] Moving each file through local disk costs a few minutes for 16 GB and is acceptable inside the 1-hour target. Streaming straight into S3 is a later optimisation.
- Open question: Fargate vCPU and memory sizes above the minimum (2 vCPU, 4 GiB) are chosen in Infrastructure Design from the first real import.
