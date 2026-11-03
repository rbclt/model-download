# CI Pipeline Configuration

Stage: CI Pipeline (`ci-pipeline`), scope `mvp`. Date: 2026-11-02. This is the last stage of the plan.

This document fixes how the pipeline is built and what each part runs. It is a configuration design: **no pipeline exists yet, none of the commands below has been run in a pipeline, and the pipeline code is not in the repository.** What the pipeline must do is taken from the Infrastructure Design of both Units (`construction/u1-import-slice/infrastructure-design/cicd-pipeline.md` and `construction/u2-import-rules/infrastructure-design/cicd-pipeline.md`), the answers in `ci-pipeline-questions.md`, and the commands that Build and Test ran by hand (`construction/build-and-test/build-and-test-summary.md`, `construction/build-and-test/test-results.md`). The code of both Units is described in `construction/u1-import-slice/code-generation/code-summary.md` and `construction/u2-import-rules/code-generation/code-summary.md`.

## Decisions

| Question | Answer | Meaning |
|----------|--------|---------|
| Q1 CI platform | A | AWS CodePipeline with AWS CodeBuild. The pipeline definition is written with AWS CDK, in its own stack, separate from the application stack |
| Q2 When it runs | A | On every push to the main branch: all checks, then a deployment to staging if they pass. Production stays behind the manual approval and is not part of the first version. No pull-request runs |
| Q3 Blocking checks | A | Every check blocks: a change that fails any check is not deployed |
| Q4 Scan tools | A | Gitleaks for the secret scan, Trivy for the image scan. `pip-audit` stays the dependency check |
| Q5 Image storage | A | The repository that `cdk bootstrap` creates. CDK builds the image during `cdk deploy` and publishes it there. No extra repository |

The branch strategy was not asked: the team works on one main branch and commits small changes often (`aidlc/spaces/default/memory/team.md`, Way of Working).

## Prerequisites (decided by the owner, not by this stage)

None of these exists yet. The pipeline cannot run until rows 1 to 3 are done; the deployment cannot run until rows 4 to 7 are done. They repeat the list of the Infrastructure Design.

| # | Prerequisite | Needed for |
|---|--------------|------------|
| 1 | A Git repository on a hosting service, with a main branch. Git is not installed on this machine, so there is no history yet | The pipeline source |
| 2 | An AWS CodeConnections connection from the account to that hosting service, authorized once by a person in the console | The pipeline source |
| 3 | `cdk bootstrap` of the common account in us-east-1 | The deployment, and the image repository of Q5 |
| 4 | The deployment settings as parameters in the account: team mailbox, verified sender address, break-glass role ARNs, optional budget limit (`infra/config.py`). The break-glass roles are required: the stack refuses to build without them | Synthesis and deployment |
| 5 | A verified SES sender identity and a way out of the SES sandbox or verified recipients (NFR3.16) | The readiness check |
| 6 | Confirmation of the SNS subscription, by clicking the link in the first email | Alerts reaching the team |
| 7 | A small public Hugging Face model for the smoke check, within the size limits (NFR2.1) | The smoke check |

## Pipeline Shape

One CodePipeline with these stages. Stages 1 to 8 need no deployment settings except the three synthesis settings; they can run before the prerequisites 4 to 7 are met. The numbering is the one of the Infrastructure Design.

| # | Pipeline stage | Where it runs | What it runs | Blocks on |
|---|----------------|---------------|--------------|-----------|
| 1 | Source | CodePipeline | Check out the main branch through the CodeConnections connection, on every push | The push |
| 2 to 8 | Checks | One CodeBuild project, `checks` | The commands under "Checks project" below | Any command with a non-zero exit |
| 9 | Readiness | CodeBuild project `readiness` | The script of prerequisite 5 and 4: the settings exist, the break-glass roles exist, the SES identity and its sandbox status, the mailbox (NFR3.16) | The script exits non-zero |
| 10a | Wait for idle | CodeBuild project `wait-idle`, time limit 8 hours | Poll the ECS service and the import queue until the running task count is 0 and the queue holds no message | The time limit; then the stage stays red and an alert goes out, nothing is deployed |
| 10b | Deploy to staging | CodeBuild project `deploy`, time limit 30 minutes | `cdk deploy` of the application stack; CDK builds and publishes the image | The stack update fails (CloudFormation rolls back by itself) |
| 11 | Smoke check | CodeBuild project `smoke`, time limit 90 minutes | Send the test event, poll for the end of the import, read the `_COMPLETE` marker, check the holding bucket is empty, delete the test model | The check fails or times out |
| 12 | Production approval | Not built | Out of the first version, which has no production stack | |

### Checks project (`checks`, stages 2 to 8)

Runs on a CodeBuild image that provides Python 3.12 and Node.js, in privileged mode (the image check needs Docker). The exact image is chosen when the project is created. The buildspec below is the design; it has not been run.

```yaml
version: 0.2
env:
  parameter-store:
    MODEL_IMPORT_TEAM_ADDRESS: /model-import/staging/team-address
    MODEL_IMPORT_SENDER_ADDRESS: /model-import/staging/sender-address
    MODEL_IMPORT_BREAK_GLASS_ROLE_ARNS: /model-import/staging/break-glass-role-arns
phases:
  install:
    runtime-versions:
      python: 3.12
    commands:
      - python -m pip install --upgrade pip
      - pip install -e ".[dev]"
      - pip check
  build:
    commands:
      - python -m black --check src infra tests app.py
      - python -m ruff check src infra tests app.py
      - python -m pytest --cov --cov-fail-under=80
      - pip-audit --local
      - gitleaks detect --no-git --source .
      - python app.py
      - docker build -t model-import:ci .
      - trivy image --severity CRITICAL --exit-code 1 model-import:ci
```

Notes on the commands:

- Install, format, lint, test and coverage are the commands Build and Test ran (`test-results.md`, runs R1 to R3 and R6), written with the Linux form of the paths. The test command is the Construction Verification Command recorded for both Units.
- `pip-audit --local` audits the packages installed in the build environment. In the local run of Build and Test it reported 12 findings, all in `pip` 24.3.1 inside the local environment and none in the pinned project packages (`test-results.md`, run R7). The `pip install --upgrade pip` line is there so the build environment starts with a current `pip`; whether the check is then clean is only known after the first pipeline run.
- `python app.py` is what `cdk.json` runs for synthesis, and it needs the three settings above. Build and Test ran it with example settings (run R4). The synthesis tests (role permissions, bucket settings, lifecycle rules, alarms) are part of the pytest run; they are the 70 CDK template tests.
- The Gitleaks scan covers the checked-out files, not the Git history, because the source action may deliver the files without history. Gitleaks and Trivy are installed in the build from pinned releases; the versions are chosen when the project is created. `gitleaks detect --no-git --source .` is the form to confirm against the pinned version.
- The Dockerfile takes the base image as a build argument `BASE_IMAGE`, with the tag `python:3.12-slim` as default. See "Open points" for the digest.

### Wait for idle, deploy, and smoke check

- **Wait for idle (10a).** The service has a maximum healthy percent of 100 and a minimum of 0, so a new task is never started beside a running one, and ECS does not stop a task that has task protection on. The step therefore polls until the running task count is 0 and the queue is empty. Its own time limit is 8 hours, because an import can run for hours. When the wait times out the stage stays red, an alert goes to the team, and the next merge tries again.
- **Deploy (10b).** `cdk deploy` with the deployment settings as CDK context or environment variables (`stackName`, `teamAddress`, `senderAddress`, `breakGlassRoleArns`, optional `budgetLimitUsd` and `smokeCheckRoleArn`; `infra/config.py`). The deployment role is the CDK bootstrap role the CodeBuild role may assume; the pipeline stores no access keys.
- **Smoke check (11).** Sends a start event for the model of prerequisite 7 on the `model-import` bus and polls up to 90 minutes. The check passes when the model is in the final bucket with a `_COMPLETE` marker that holds the model name, the exact version, and the file list with sizes that match the files, and the holding bucket is empty. It then deletes the test model so the next run starts clean. The bad-event and duplicate paths are not sent: each sends an email, and the limit of 20 notice emails a day should not be used up by merges; those paths are proved by the tests of stage 4.
- **Rollback.** CloudFormation rolls back a failed stack update by itself. If the smoke check fails, the last successful commit is deployed again and the smoke check runs again; if that fails too, the pipeline stops and alerts. The first deployment has no last successful commit, so it only stops and alerts. The buckets are never touched by a rollback.

## Smoke-check role

The Infrastructure Design leaves the grants of the smoke-check role to this stage, for the checks of both Units together. The Infrastructure Design of u1 says the role is "defined in the stack". As built, the application stack does not create it: it takes the role's ARN as the setting `smokeCheckRoleArn` and only adds that ARN to the exemption list of the bucket policies (`infra/model_import_stack.py`). This document therefore puts the role in the pipeline stack, which passes its ARN to the application stack. That is a decision of this stage to confirm. The grants it needs:

| Action | Resource | Why |
|--------|----------|-----|
| `events:PutEvents` | The `model-import` event bus | Send the test event |
| `s3:ListBucket` | The final bucket and the holding bucket | Poll for the marker and check the holding bucket is empty. Listing the holding bucket is the grant that u1's check needs and u1's role description did not state |
| `s3:GetObject` | The final bucket, `models/<test model>/*` | Read the marker |
| `s3:DeleteObject` | The final bucket, `models/<test model>/*` | Delete the test model after the check |

No other permission, no `s3:*`, and no `Resource: *`. The bucket policies deny object data actions to everyone except an exemption list that already contains this role when its ARN is given. Whether `s3:ListBucket` on the holding bucket passes the bucket policy as written was not checked here; the pipeline code needs a template test for it.

## Open points

1. **The image CDK builds is not pinned by digest.** The Infrastructure Design assumed the pipeline would pass the reviewed base image digest to both the image check and CDK's build. The stack builds the image with `ContainerImage.from_asset(<project root>, platform=LINUX_AMD64)` and passes no build arguments (`infra/model_import_stack.py`, `_asset_image`), so CDK's build always uses the Dockerfile default, the tag `python:3.12-slim`. Two ways to close it: pass `build_args` in the stack (a code change, with a template test), or accept the tag and have the pipeline scan the image built with the default argument, so the image that is scanned is the image that is deployed. This document uses the second until the person decides. Until then the base image can change between two builds.
2. **The pipeline code is not written.** The CodePipeline, the CodeBuild projects, the smoke-check role, the readiness script, the wait-for-idle script and the smoke-check script are not in the repository. Writing them is a code change that needs its own tests and a way to run them; the plan has no stage after this one.
3. **The smoke test model needs cleaning before each run.** The code summary of u2-import-rules asks this stage to make the pre-run clean-up explicit: a marker left behind by an earlier run makes the next run take the already-stored path and send an email (`construction/u2-import-rules/code-generation/code-summary.md`). The smoke-check script deletes the test model before the check as well as after it, so every run starts clean.
4. **Delivery of a rollback.** How the last successful commit is deployed again (a second pipeline execution, or a stage rollback feature of CodePipeline) is chosen when the pipeline is created.
5. **Nothing was verified.** No pipeline, build project, scanner, or deployment ran. The tool versions, the Linux form of the commands, the Gitleaks form, and the CodeBuild image are all unconfirmed until a first run.

## Assumptions & Open Questions

- [assumption] The CodeBuild image provides Python 3.12 and Docker, and the project is created in the same account and region (us-east-1) as the stack.
- [assumption] The deployment settings are kept as parameters in the account (Systems Manager Parameter Store), not in the repository. The names above are examples chosen here.
- [assumption] The pipeline source action delivers the files without Git history, so the secret scan covers the files and not the history.
- [Q1] Should the base image be pinned by digest in CDK's build (a stack change), or is the tag accepted for the first version? See open point 1.
- [Q2] Who writes the pipeline stack, since the plan ends with this stage? See open point 2.
