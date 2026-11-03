# Tech Stack Decisions: u1-import-slice

This lists the technology chosen for the thin end-to-end slice and why. It follows the team practices (`team-practices.md`), the requirements (`requirements.md`), and the rules and steps in `rules.md` and `functional-spec.md`, and it uses the boundaries in `contract-summary.md`. The NFR answers are in `nfr-requirements-questions.md`. Choices marked "decided in NFR Design" or "decided in Infrastructure Design" are listed with the options still open so the later stage does not start from nothing.

## Decided

| Area | Choice | Why |
|------|--------|-----|
| Language | Python 3.12 | Team choice (feasibility Q5); supported by all AWS services used here (Q7) |
| Infrastructure as code | AWS CDK v2 in Python | Team choice (feasibility Q6) |
| Start event | Amazon EventBridge event | Team choice and contract C1 |
| Storage | Amazon S3: one holding area and one final location in the common AWS account in us-east-1 | Requirements FR2.2, FR4.2; encrypted with S3-managed keys (Q5) |
| Failure email | Amazon SES, from a verified sender identity | The email must reach a sender address that comes in each event; SNS cannot do that without a confirmation click from the sender (Q8) |
| Operator alert | Amazon CloudWatch alarms (log metric filters, task stopped, dead-letter queue depth) sending to an Amazon SNS topic with the team mailbox subscribed once | Reaches the team mailbox for the signals in NFR5.8; works with SNS because the recipient is fixed |
| Logs | Amazon CloudWatch Logs, structured JSON, 30-day retention | NFR5.4, NFR5.6 |
| AWS access from code | boto3 | Standard Python library for AWS |
| Formatter and linter | Black and Ruff | Team choice (interview Q3) |
| Test tools | pytest and pytest-cov for tests and coverage; moto for AWS fakes; a mocked HTTP layer for Hugging Face | 80 percent coverage (NFR6.1) without a real account in tests |
| Package and dependency files | pyproject.toml with exact version pins | NFR3.12 |
| Dependency vulnerability check | pip-audit, run in the CI pipeline stage | NFR3.12 |
| Durable import record | Chosen in NFR Design (candidates: one object per import in S3, or a DynamoDB table) | NFR4.8 |
| Dead-letter queue and alert topic | Amazon SQS dead-letter queue; Amazon SNS topic for the team mailbox | NFR4.11, NFR5.8 |
| Cost alarm | AWS Budgets, alerting the SNS topic | NFR5.10 |
| CDK command-line tool | Installed with npm (Node.js is on this machine) | Needed to synthesise and deploy; not installed yet |

## Why Not a Standard AWS Lambda Function for the Import

A Lambda function stops after 15 minutes. An import can run for up to 6 hours (NFR1.1) and moves up to 20 GB (NFR2.1). A single 20 GB file in one 15-minute step needs about 22 MB per second, which is above the 20 MB per second planned in NFR1.3, so one Lambda function cannot be the whole import. A workflow of short steps with ranged, resumable downloads could work, but it is more to build and test. A long-running container is the simpler fit. Which one is chosen is decided in NFR Design. A container on AWS Fargate has 20 GiB of local disk by default and can be set up to 200 GiB.

**Correction (owner's question, after review):** a standard Lambda function is limited to 15 minutes, but two newer Lambda options reach further. Lambda MicroVMs run one isolated environment for up to 8 hours (the documented limit of 28,800 seconds, not adjustable), which would fit a 6-hour import. Lambda durable functions can run a workflow for up to one year, but each invocation is still bounded, so they split the work into steps. Neither was checked for disk size, availability in us-east-1, or how an event starts them, and the owner chose a Fargate container in NFR Design (Q1 A), so they are recorded here as alternatives considered, not as impossible.

## Open Choices for Later Stages

| Decision | Options | Decided in |
|----------|---------|------------|
| What runs the long import | An Amazon ECS task on AWS Fargate that runs all steps in one program; or AWS Step Functions calling short steps | NFR Design |
| How one import at a time is enforced (u2) | An SQS queue with one consumer; a Step Functions rule; or a lock record | NFR Design |
| How files move from Hugging Face to S3 | Stream each file into S3 in parts without local disk; or download to local disk first | NFR Design |
| Holding area and final location | Two buckets, or two prefixes in one bucket | Infrastructure Design |
| The Hugging Face client | The huggingface_hub library with no token, or plain HTTPS requests | Code Generation |
| How the time limit of 6 hours is enforced (u2) | A workflow timeout, or a timer in the program | NFR Design |

## Assumptions & Open Questions

- [assumption] Node.js 24 on this machine is new enough for the AWS CDK command-line tool. This is checked at install.
- [assumption] The team practice that names SNS for the failure email is superseded for the sender part by Q8. The recipient list for operator alerts keeps SNS.
- Open question: Which AWS CDK v2 and boto3 versions are pinned? They are chosen when the project is created in Code Generation.
