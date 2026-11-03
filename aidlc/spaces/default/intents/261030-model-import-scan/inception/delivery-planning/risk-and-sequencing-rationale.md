# Risk and Sequencing Rationale

This document explains why the Bolts are in the order they are. A **Bolt** is one pass of building over a piece of the work, ending in something that runs and can be checked. The plan is in `bolt-plan.md`. The answers behind it are Q1 to Q4 in `delivery-planning-questions.md`.

## Chosen Order

B1 (u1-import-slice) first, then B2 (u2-import-rules).

## Why This Order

1. **The dependency leaves only one order.** u2-import-rules changes the ImportCoordinator, FileCustodian and FailureNotifier that u1-import-slice creates, so u2 cannot build or pass its tests without u1's code (`unit-of-work-dependency.md`). The order is also the topological order of the dependency graph, so there is no deviation to justify.
2. **Walking skeleton first** (Cockburn, *Crystal Clear*). The team asked for a thin end-to-end slice first (`team-practices.md`). B1 is that slice: it runs from the start event through the download, the scan, and keeping or removing the files to the failure email, in the simplest form.
3. **Risk first** (Boehm, Spiral Model). The riskiest part is the download and scan of a real model, which was the answer to Q2 (option A). B1 starts there, so the biggest unknowns (download time, size, and what the scan finds in real files) are known before B2 adds rules on top.
4. **No scoring model.** The owner chose no WSJF scoring (Q4, option A). WSJF (Weighted Shortest Job First) divides value, urgency and risk reduction by job size. With two Bolts and one possible order, a score cannot change the result.

## Risk Register

| # | Risk | Likelihood | Impact | Mitigation | Bolt |
|---|------|------------|--------|------------|------|
| R1 | The deadline (end of tomorrow) is too tight for both Bolts with 80 percent test coverage | High | High | B1 is a complete working slice on its own. The optional record of imports (US6.1) is dropped first. The coverage floor is a team choice already accepted as hard to reach | B1, B2 |
| R2 | A real smaller model takes longer to download or scan than expected, or its files are large | Medium | High | B1 tests on a real smaller model early. The 6-hour limit and retries (3 per file) bound the run | B1 |
| R3 | The scan fails real models on harmless findings, for example an email address in a README, because any finding fails the import (FR3.3) | Medium | Medium | Findings name the file, place and kind, so the owner can revisit the rule after real tries | B1 |
| R4 | Clean-up after a failure leaves something behind | Medium | High | FileCustodian owns all removal; B2 forces failures at each step and checks nothing remains; the email names anything left | B1, B2 |
| R5 | The email sender address or the team mailbox is not ready, so no failure email can be sent in staging | Medium | Medium | Both are deploy-time settings (Q3, option A); tests use a fake email stand-in; the owner supplies real values before the first staging deployment | B1 |
| R6 | The tools to run the checks are not installed on this machine: Python 3.13 is present, but pytest and the AWS CDK command-line tool are not | High | Low | Code Generation adds the Python test packages as project dependencies; the CDK command-line tool is installed with npm (Node is present) before the first synth | B1 |
| R7 | The contract review findings (R-01 to R-07 in the Contract Design review) were accepted as risk and may cause rework in B1 or B2 | Medium | Medium | The two major findings (the finding record fields and the timing of the wait) are settled in Functional Design | B1, B2 |
| R8 | Open design questions on one-at-a-time handling (where the wait lives) | Medium | Medium | Decided in Infrastructure Design before B2 starts | B2 |

## Verification Command

Each completed Unit is checked with one project command. The proposal is:

`python -m pytest --cov --cov-fail-under=80`

It runs the unit tests, the integration tests, the one end-to-end test with fake stand-ins for S3, Hugging Face and email, and the 80 percent line-coverage check required by `team-practices.md`. It does not run an import against the real AWS account or the real Hugging Face site; those need the deployed staging environment and the deploy-time settings, and are checked by hand at the demo. pytest and its coverage package are not installed yet; Code Generation adds them.

## Assumptions & Open Questions

- [assumption] Likelihood and impact levels are the delivery lead's judgement, not measured values.
- Open question: Does the end-to-end test with fake stand-ins count as enough proof of the skeleton for the owner, or is a manual demo on a real model also needed before B2 starts? This plan assumes the manual demo happens once the deploy-time settings are supplied and does not block B2.
