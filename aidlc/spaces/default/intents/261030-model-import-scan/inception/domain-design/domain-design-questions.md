# Domain Design Questions

Domain Design splits the application into building blocks: pieces of code with their own job and their own data. It does not decide how they are deployed, which AWS services run them, or the tech stack. Most of the shape follows from the requirements and stories, so only the two choices below need your input.

## Q1. [Engineering lead] How finely should the application be split into building blocks?

The flow is: an event arrives, the model is downloaded, the files are scanned, then they are kept or removed, and a failure email is sent. Each option below is a different way to group that work.

- Option A, five blocks (recommended): ImportCoordinator (checks the event, refuses duplicates, queues events, tracks each import and the 6-hour limit), ModelDownloader, LeakScanner, FileCustodian (moves clean files to the final location and removes everything on failure), FailureNotifier. Pros: removal has one owner that can be tested by forcing failures, and each block maps to a story. Cons: more pieces for a one-day first version. Reversible: yes, blocks can be merged later.
- Option B, four blocks: the same, but FileCustodian's work is done by ImportCoordinator. Pros: fewer pieces. Cons: the coordinator also handles file moves and removal, so it grows large and is harder to test.
- Option C, three blocks: ImportCoordinator (including file moves, removal, and the failure email), ModelDownloader, LeakScanner. Pros: smallest. Cons: the failure email and removal are tangled with the import lifecycle, so the "always remove everything when an import fails" rule is harder to prove. Reversible: yes, but splitting later means moving tested code.

A. Option A: five blocks
B. Option B: four blocks
C. Option C: three blocks
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q2. [Architect] How should the blocks work together during an import?

An import can run for hours, and any step can fail. The "stop and clean up everything" rule must work whichever step fails.

- Option A (recommended): The ImportCoordinator runs the steps one after another and waits for each to finish. Pros: stopping and cleaning up happens in one place, and the order is easy to follow and test. Cons: the coordinator knows the whole flow.
- Option B: Each block announces when it is done, and the next block reacts to that announcement. Pros: blocks know less about each other. Cons: a failure can happen in any block, so stopping and cleaning up is spread across all of them, which is harder to prove and test in a day.

A. Option A: the coordinator runs the steps in order and waits
B. Option B: each block announces when it is done and the next one reacts
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
