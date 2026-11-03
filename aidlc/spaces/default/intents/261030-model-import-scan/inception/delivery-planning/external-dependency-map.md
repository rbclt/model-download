# External Dependency Map

This document lists the things outside the code that the work depends on, and which Bolt each one blocks. A **Bolt** is one pass of building over a piece of the work, ending in something that runs and can be checked. The Bolts are in `bolt-plan.md`. The application is otherwise built and tested by AI alone, so the list is short. The answer to Q3 in `delivery-planning-questions.md` is option A: each item below is a setting filled in at deploy time, and the owner supplies the real value before the first deployment to staging.

| # | Item | Owner | Lead time | Blocks | If it slips |
|---|------|-------|-----------|--------|-------------|
| D1 | Team mailbox address (one of the two failure email recipients) | Owner | Unknown | B1 demo on staging (not the build or tests) | Use a test address for the demo; tests use a fake email stand-in |
| D2 | Verified email sender address in the common AWS account (the account must be allowed to send from it) | Owner, with whoever manages the common AWS account | Unknown; address verification can take a day or more | B1 demo on staging | Failure emails cannot be sent in staging; the import still ends as failed and the email problem is logged (FR5.5) |
| D3 | The first model to import: a public Hugging Face model of about 8 billion parameters or fewer, using an architecture Amazon Bedrock Custom Model Import supports | Owner | None | B1 demo on a real model | Pick any listed small public model for the demo |
| D4 | Access to the common AWS account in us-east-1 to deploy with AWS CDK, including a one-time CDK bootstrap of the account | Owner, with whoever manages the common AWS account | Unknown | B1 deployment to staging | Build and test locally; defer the deployment |
| D5 | Hugging Face availability (public files, no account) | Hugging Face; not under our control | None | B1, B2 runs against real models | Retries (3 per file), then the import fails and the email says why |
| D6 | Installing the AWS CDK command-line tool (with npm) and the Python test packages on the build machine | AI agent, with permission from the owner | Minutes | B1 build and test | Without them nothing can be synthesised or tested |

## Assumptions & Open Questions

- [assumption] Lead times marked unknown have not been asked of anyone. The owner is the one person who can say how long D1, D2 and D4 take.
- Open question: Is the common AWS account already bootstrapped for AWS CDK in us-east-1? If not, D4 adds a one-time setup step before the first deployment.
