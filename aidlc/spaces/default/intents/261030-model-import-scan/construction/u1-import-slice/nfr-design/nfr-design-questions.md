# NFR Design Questions: u1-import-slice

NFR Design chooses the patterns that meet the targets in the NFR Requirements for this Unit. Most choices follow directly from those targets and are not asked. The four questions below are real choices, and two of them settle gaps the architecture reviewer left open at the end of NFR Requirements: how "one import at a time" is enforced, and what a crashed run leaves in the final location.

## Q1. [Platform engineer] What runs an import?

An import can take up to 6 hours and moves up to 20 GB, which is more than a single AWS Lambda function can do (a Lambda function stops after 15 minutes).

A. One container task on AWS Fargate that runs all the steps in one Python program: download, scan, keep or remove, email. The event starts it. Local disk is set to the size a 20 GB model needs (recommended; the fewest moving parts)
B. A workflow (AWS Step Functions) of short Lambda steps, each under 15 minutes, with downloads in resumable parts. No big local disk, but more parts to build and test
X. Other (please specify)

[Answer]: A

## Q2. [Platform engineer] How is "one import at a time" enforced?

A. A small table (Amazon DynamoDB) holds one record per import. Starting an import writes a lock record only if none is held; the lock is released when the import ends and expires by itself after 7 hours, so a crashed run cannot block every later event. The same table is the durable import record (NFR4.8) (recommended)
B. A queue (Amazon SQS) in front of the container task so a second event waits its turn. This is the "wait" that u2 is meant to add, so it moves that work into u1
X. Other (please specify)

[Answer]: B

## Q3. [Product owner] In u1, what happens to a second event that arrives while an import runs?

This only matters under Q2 option A. Under option B the event waits.

A. It is not run. One alert goes to the team mailbox saying which model was dropped, and one log line records it. u2 replaces this with the wait (recommended)
B. It is not run and only a log line records it; nobody is told
X. Other (please specify)

[Answer]: A

## Q4. [Platform engineer] What is in the final location if a run crashes while copying files into it?

In a normal failure the program removes what it copied (BR5.2). A crash gives it no chance to. Without a rule, half a model would sit in the final location and block a retry of the same version.

A. A completion marker file is written last, after all files are copied. A model without the marker counts as unfinished: readers must check for the marker, the duplicate check looks for the marker, and a retry of the same version may overwrite unfinished files (recommended)
B. Leave the half-copied files until the team removes them by hand, using the import record and the alert
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

- What runs an import (Q1): one container task on AWS Fargate runs all the steps in one Python program. Its local disk is sized for a 20 GB model.
- One import at a time (Q2): an Amazon SQS queue sits in front of the container task. A second event waits in the queue until the running import ends, so the wait that requirements FR1.5 asks for is delivered in u1. The container service runs at most one task, started when a message is waiting. A message is tried once: if the run crashes, the message goes to the dead-letter queue and the team mailbox is alerted, so a 6-hour import is never repeated by itself.
- Dropped second event (Q3): your answer does not apply, because under Q2 option B no event is dropped. It is recorded and not used.
- Crash while copying (Q4): a completion marker file is written last. A model without the marker counts as unfinished. The duplicate check looks for the marker, and a retry of the same version may overwrite unfinished files.
- Import record (follows from Q2 option B, my design choice): the durable record of each import is one small object per import in the holding bucket, written before each step, and not a DynamoDB table.

**Confirm** — Does this all look correct before I generate the artifacts?

Looks correct — Generate the artifacts from these answers
Request changes — Revise one or more answers before generation

[Answer]: Looks correct
