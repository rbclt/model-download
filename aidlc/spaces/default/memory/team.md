# Team-Level Rules

> This team's affirmed practices and corrections. Loaded after `org.md` as
> strict-additive guidance; contradictions with broader policy are rejected.
> Populated by the practices-discovery affirmation gate. Edit at the gate,
> not directly.

## Way of Working

- **Branching and merging**: We work directly on one main branch and commit small changes often.
- **Version control**: Git was not found on the machine during discovery, so the repository still has to be set up before the first commit.
- **Decision ownership**: One owner decides scope and priorities, and the security team gives input (`ideation/intent-capture/intent-statement.md`, Initial Scope Signal).
- **Time limit**: The first version is due at the end of tomorrow and must stay small (`ideation/scope-definition/scope-document.md`).

## Walking Skeleton

- **Thin end-to-end slice first**: Yes. We build a thin end-to-end slice first (a walking skeleton is a minimal version that runs the whole way through, built first to prove the pieces connect).
- **Fit with the scope**: The slice starts with the riskiest part, downloading and scanning a model of about 140 GB. It runs from the start event through the download into S3, the scan, keeping or removing the files, and the failure email.

## Testing Posture

- **Methodology**: test-after
- **Methodology evidence**: The team chose to write the code first and then write tests for it (interview Q4).
- **Ordering**: We implement each testable layer first, then write and run the tests for that layer.
- **Coverage floor**: Tests cover at least 80 percent of lines (interview Q5). The team chose this knowingly and accepts that it may be hard to reach by the end-of-tomorrow deadline.
- **Test language and tooling**: Python (confirmed, feasibility Q5). The test framework is not chosen yet.

## Guard Policy

<!-- Affirmed by the team. Mode: strict, relaxed, or off. Strict here holds for every intent, whatever one person asks in chat; relaxed or off here applies to every intent whose policy came from its scope, unless project.md or the person sets another; changing this line changes it. A section under the retired Change Control heading, written by an earlier release, is still read. -->

## Deployment

- **Infrastructure tool**: We manage AWS resources with AWS CDK (`ideation/feasibility/feasibility-questions.md`, Q6).
- **Target account**: The common AWS account already exists and is used as is (feasibility Q1).
- **Release flow**: A merged change deploys automatically to staging. Production deploys only after a manual approval (interview Q6).
- **Approvals before deploy**: No blocker is known, and the security team does not have to approve the design before deployment (feasibility Q8).
- **Compliance**: Only the company's own security policy applies (feasibility Q4).
- **Application shape**: A backend, event-driven application with no user interface. An Amazon EventBridge event starts an import and Amazon SNS sends the failure email (intent statement).

## Code Style

- **Language**: Python (feasibility Q5). Infrastructure code is written with AWS CDK in Python.
- **Formatter and linter**: Black formats the code and Ruff checks it (interview Q3).
- **Naming**: Python conventions (snake_case).
## Forbidden

<!-- Team-specific forbidden patterns -->

## Mandated

<!-- Team-specific mandates -->

## Corrections

<!-- Self-learning loop appends here. -->
