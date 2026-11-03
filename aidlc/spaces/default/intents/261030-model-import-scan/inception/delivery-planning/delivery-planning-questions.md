# Delivery Planning Questions

Delivery Planning decides the order in which the work is built. The unit of work in that order is a Bolt, one pass of building over a piece of the work, ending in something that runs and can be checked. Several points are already settled and are not asked again: the team builds a thin end-to-end slice first (`team-practices.md`), u2-import-rules depends on u1-import-slice so only one order is possible (`unit-of-work-dependency.md`), and the work is built one Unit at a time (the current Construction setting is serial, unit by unit). These questions cover what is still open.

## Q1. [Delivery lead] How big should one Bolt be?

A. One Bolt per Unit: Bolt 1 is u1-import-slice (the thin end-to-end slice), Bolt 2 is u2-import-rules (recommended)
B. One Bolt for both Units: everything is built in one pass and checked once at the end
C. Several small Bolts that cut across the two Units, for example one per story
X. Other (please specify)

[Answer]: A

## Q2. [Product owner] What worries you most about this build, so it is tackled early?

A. The download and scan of a real model: size, time, and what the scan finds in real files (recommended; u1 already starts here)
B. Clean-up after a failure: making sure nothing is ever left behind
C. The deadline: end of tomorrow is tight for this much design and testing
D. Email: the sender address and the team mailbox may not be ready in time
X. Other (please specify)

[Answer]: A

## Q3. [Platform engineer] Several things outside the code are needed to run an import for real: the team mailbox address, a verified email sender address in the common AWS account, and a model to try first. How should the plan treat them?

A. Build with settings that are filled in at deploy time, and have the owner supply the real values before the first deployment to staging (recommended)
B. The owner supplies all three values before Code Generation starts
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q4. [Delivery lead] Should the plan rank the work with a scoring model such as WSJF (value and urgency divided by size)?

A. No. With two Units and one possible order, the order comes from the dependency and the riskiest-first argument (recommended)
B. Yes, score both Bolts and record the scores
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

- Bolt size (Q1): one Bolt per Unit. Bolt 1 is u1-import-slice, the thin end-to-end slice. Bolt 2 is u2-import-rules.
- Biggest worry (Q2): the download and scan of a real model, which u1 already starts with.
- Outside needs (Q3): the team mailbox address, the verified email sender address, and the first model are settings filled in at deploy time. The owner supplies the real values before the first deployment to staging.
- Scoring (Q4): no scoring model. The order comes from the dependency and the riskiest-first argument.

**Confirm** — Does this all look correct before I generate the artifacts?

Looks correct — Generate the artifacts from these answers
Request changes — Revise one or more answers before generation

[Answer]: Looks correct
