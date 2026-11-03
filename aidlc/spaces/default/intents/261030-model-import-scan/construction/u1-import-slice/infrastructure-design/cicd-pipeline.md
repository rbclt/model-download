# CI/CD Pipeline: u1-import-slice

This describes the delivery pipeline for the thin end-to-end slice. It uses the resources in `infrastructure-specification.md`, the monitoring in `monitoring-design.md`, the designs in `security-design.md`, `reliability-design.md`, `performance-design.md`, `scalability-design.md`, and `observability-design.md`, the components in `logical-components.md`, `components.md` and `functional-spec.md`, and the boundaries in `contract-summary.md`. The team practice is: a merged change deploys to staging automatically, and production only after a manual approval. The pipeline itself is built in the CI Pipeline stage after Build and Test; this document fixes what it must do.

## Blocks the First Deployment

One list of everything that must be true, by the stage that needs it. Rows 1 to 3 are needed for the pipeline to run at all. Rows 4 to 6 are needed before the readiness check (stage 9) and the deploy (stage 10). Row 7 is done after the deploy. Row 8 is needed only for the smoke check (stage 11).

| # | Prerequisite | Who | Notes |
|---|--------------|-----|-------|
| 1 | A Git repository on a hosting service, with a main branch | Owner | Git is not installed on this machine yet, and the practice is to work on one main branch |
| 2 | A CI platform | Owner, with the CI Pipeline stage | Default AWS CodePipeline with AWS CodeBuild; confirmed in the CI Pipeline stage |
| 3 | Docker (or a compatible tool) on the machine that builds the image | Owner | CDK builds the image from the project; the CI build project provides it in the pipeline |
| 4 | `cdk bootstrap` of the common AWS account in us-east-1 | Owner (D4) | Creates the asset repository and roles the deployment uses |
| 5 | Team mailbox and SES sender identity verified; SES out of the sandbox or recipients verified | Owner (D1, D2) | Checked by the readiness script before the deployment |
| 6 | Break-glass administrator role ARNs | Owner | Required deployment setting; the deployment fails if empty |
| 7 | Confirmation of the SNS subscription | Owner | After the first deployment, by clicking the link in the email |
| 8 | A small public model for the smoke check | Owner (D3) | Must fit NFR2.1 and use an architecture Amazon Bedrock Custom Model Import supports |

## Pipeline Stages

| Order | Stage | What it runs | Gate to pass |
|-------|-------|--------------|--------------|
| 1 | Source | Check out the repository on the main branch | A merged change |
| 2 | Install | Install the pinned dependencies from `pyproject.toml` (Python 3.12) | Install succeeds |
| 3 | Format and lint | `black --check` and `ruff check` | No findings (NFR6.2) |
| 4 | Test | `python -m pytest --cov --cov-fail-under=80` | All tests pass, coverage at least 80 percent (NFR6.1, NFR6.3, NFR6.4) |
| 5 | Dependency check | `pip-audit` on the pinned dependencies | No known critical vulnerability (NFR3.12) |
| 6 | Secret scan | A scan of the repository for credentials | No finding (NFR3.1) |
| 7 | Synthesise | `cdk synth` for the stack; a test searches the template for `s3:*` and `Resource: *` in the task role and checks the bucket policies, encryption, lifecycle rules, and removal policy | No such permission (NFR3.4); bucket settings present (NFR3.5 to NFR3.7, NFR4.9) |
| 8 | Image check | Build the container image locally with the same Dockerfile CDK uses and scan it with an image scanner before anything is published. The Dockerfile pins its base image by digest, so CDK's later build in stage 10 starts from the same base | No critical vulnerability in the image (NFR3.12). CDK builds and publishes the image itself in stage 10, so no separate push is made |
| 9 | Readiness check | A script confirms the deployment settings, the break-glass roles, the SES identity and sandbox status, and the team mailbox (NFR3.16) | The check passes. It runs before the deploy because the deployment needs the settings |
| 10 | Deploy to staging | `cdk deploy` of the staging stack, which builds and publishes the image asset; the step has a time limit of 30 minutes in the pipeline (there is no deployment time-out setting on the ECS service itself) | Stack update succeeds, or CloudFormation rolls it back |
| 11 | Smoke check | Send a test event for the model from prerequisite 8 and poll up to 90 minutes: the event reaches the queue, the scale-out alarm starts the task (about 3 minutes), and the import ends. A clean model must be in the final bucket with a marker and the holding bucket must be empty. The pipeline assumes a dedicated smoke-check role defined in the stack. The role may only put events on the `model-import` bus, list and read the final bucket, and delete under the test model's prefix; it is on the bucket-policy exemption list. The check lists and reads the marker, then deletes the test model so that every run starts clean and the same test version can be used again. A check that times out while a real import is running or queued is retried after the wait-for-idle step and does not start a redeploy | The check passes within 90 minutes |
| 12 | Production approval | A person approves | Manual approval by the owner. Not part of the first version, which has no production stack |

## Deployment Strategy and Rollback

- **A deploy while an import runs.** The service has a maximum healthy percent of 100 and a minimum of 0 (NFR2.3), so a new task is never started beside a running one. An import can run for hours with task protection on, and ECS does not stop a protected task during an update. The pipeline therefore checks the service before stage 10, in a separate polling step that has its own time limit of 8 hours (so it does not share the time limit of the other steps). While a task is running, or the queue still holds messages, the step waits for the task count to reach 0. If the wait times out, the stage stays red, an alert goes to the team, and the next merge tries again; nothing is deployed. The automatic redeploy after a failed smoke check uses the same step, and if its wait times out it also alerts and stays red, so a bad commit can stay deployed until the import ends; the alert says so. This avoids a deploy that hangs for hours and then rolls back.
- **Strategy.** A replacement of the service at desired count 0 to 1: the next task, started by the next message, uses the new image. There is no live traffic to shift.
- **Bad image.** At desired count 0 the circuit breaker has nothing to check, so the real protection against a bad image is the smoke check in stage 11. The deployment circuit breaker is on with rollback on, for the case where a task is running during an update.
- **Rollback.** CloudFormation rolls back a failed stack update by itself. If the smoke check fails, the pipeline redeploys the last successful commit automatically and then runs the smoke check again; if that fails, the pipeline stops and sends the alert. The buckets are never touched by a rollback, so stored models stay.

## Environment Promotion

- Staging is the only environment in the first version (Q1 A of Infrastructure Design). A production stack uses the same code with a different stack name, deploys only after the manual approval in stage 12, and uses its own verified SES identity and team mailbox.

## Secrets Management in CI/CD

- The application holds no secrets (`security-design.md`). The pipeline needs credentials only to deploy to the AWS account; it gets them from the CI platform's role-based access to the account and does not store keys in the repository or in the logs.
- The pipeline never prints a deployment setting that holds an address or an ARN beyond what the build log needs.

## Assumptions & Open Questions

- [assumption] The CI platform defaults to AWS CodePipeline with AWS CodeBuild. The CI Pipeline stage confirms it with the owner.
- [assumption] Until the prerequisites 4 to 8 are met, the pipeline runs stages 1 to 8 only (checks without a deployment). The first deployment has no last good commit, so on a failed smoke check it stops and alerts and does not redeploy.
- [assumption] The image scanner in stage 8 is a command-line scanner that runs inside the build project. Which tool is chosen in the CI Pipeline stage.
- Open question: Whether the smoke check should use the real Hugging Face site on every merge or only once a day, to limit cost and time. It runs on every merge in this version.
