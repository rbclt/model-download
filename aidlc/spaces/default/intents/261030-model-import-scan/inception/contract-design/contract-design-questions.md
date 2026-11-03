# Contract Design Questions

A contract is a written agreement across a boundary: what data crosses it, in what shape, and what happens when something goes wrong. The application has two boundaries with the outside world: the start event that people send in, and the failure email that people receive. The two units, u1 and u2, share code inside one application, so they need no contract of their own beyond the shared records already defined in Domain Design. These questions settle the open points on the two outside boundaries.

## Q1. [Product owner] What counts as an "exact version" in the start event?

The requirements review left this open. Public model repositories on Hugging Face let their owners move a branch or a tag to new files, but a full commit hash always points to the same files.

A. A full commit hash (40 characters of letters a to f and digits); branch names, tags, and short hashes are rejected (recommended)
B. Any Hugging Face revision, including branch names and tags
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q2. [Security engineer] Who gets the email when the start event has no sender address, and who gets the "already stored" email?

The failure email normally goes to both the person who started the import and the team mailbox. Two cases differ: a missing sender address means there is no one to write to, and an "already stored" refusal is not a failure.

A. A missing sender address: the team mailbox only. An "already stored" refusal: the sender and the team mailbox (recommended)
B. A missing sender address: the team mailbox only. An "already stored" refusal: the sender only
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q3. [Platform engineer] What should happen when a download is interrupted by a temporary problem, such as a dropped connection?

A. Try the file again up to 3 times with a short wait between tries, then fail the import (recommended)
B. Fail the import at once, with no retry
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

- Exact version (Q1): a full 40-character commit hash; branch names, tags and short hashes are rejected.
- Missing sender address and "already stored" email (Q2): a missing sender address sends the email to the team mailbox only; an "already stored" refusal goes to the sender and the team mailbox.
- Interrupted download (Q3): retry the file up to 3 times with a short wait between tries, then fail the import.

**Confirm** — Does this all look correct before I generate the artifact?

Looks correct — Generate the artifact from these answers
Request changes — Revise one or more answers before generation

[Answer]: Looks correct
