# Units Generation Questions

A unit of work is a piece of the application that is built and tested as one round. In the build phase, several design and test steps repeat once for each unit, so the number of units sets how many rounds of those steps you go through. The five building blocks from Domain Design (ImportCoordinator, ModelDownloader, LeakScanner, FileCustodian, FailureNotifier) can be grouped into units in different ways.

## Q1. [Engineering lead] How should the work be grouped into units?

- Option A, one unit: all five building blocks built together. Pros: the fewest rounds of repeated steps. Cons: no early checkpoint, and it does not follow your practice of building a thin end-to-end slice first.
- Option B, two units (recommended): u1 is a thin end-to-end slice that uses all five building blocks in their simplest form (event, download, scan, keep or remove, failure email). u2 completes the rules around it (duplicate refusal, waiting events, the 6-hour limit, reporting what a failed removal left behind, and the optional record of imports). Pros: matches your thin-slice-first practice, proves the riskiest path early, and keeps the repeated steps to two rounds. Cons: u2 changes code written in u1.
- Option C, five units, one per building block. Pros: the clearest ownership. Cons: five rounds of repeated steps, which is hard to fit before the end of tomorrow, and no unit works end to end on its own until the last one is done.

A. Option A: one unit
B. Option B: two units
C. Option C: five units, one per building block
D. Not yet defined
X. Other (please specify)

[Answer]: B

## Q2. [Platform engineer] How should the application be deployed?

- Option A (recommended): The whole application is deployed together as one application. Pros: one deployment to build and test in a day. Cons: a change to one part redeploys everything.
- Option B: Each building block is deployed on its own. Pros: independent changes. Cons: more to set up and test, with no gain for a first version that one person owns.

A. Option A: deployed together as one application
B. Option B: each building block deployed on its own
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
