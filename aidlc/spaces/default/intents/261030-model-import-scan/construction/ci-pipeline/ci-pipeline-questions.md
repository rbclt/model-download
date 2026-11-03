# CI Pipeline Questions

The CI Pipeline stage turns the checks that Build and Test ran by hand into a pipeline that runs them on every change. Most of it is already fixed. The team works on one main branch and commits small changes often (`team.md`, Way of Working). A merged change deploys to staging by itself and production waits for a manual approval (`team.md`, Deployment). The Infrastructure Design of both Units lists the twelve pipeline stages and what each must check (`construction/u1-import-slice/infrastructure-design/cicd-pipeline.md` and `construction/u2-import-rules/infrastructure-design/cicd-pipeline.md`). Build and Test showed 540 tests passing at 95.88 percent coverage, and listed what it could not check on this machine, such as the container image build and a secret scan (`construction/build-and-test/build-and-test-summary.md`). Five points remain that need your decision. The branch strategy is not asked again, because the team already chose one main branch.

## Q1. [Platform engineer] Which CI platform should run the pipeline?

The pipeline needs a platform to run on. Git is not installed on this machine and no repository exists yet, so this also decides where the repository will live. The Infrastructure Design assumed AWS CodePipeline with AWS CodeBuild, because they run inside the same AWS account that the application is deployed to and need no stored access keys.

A. AWS CodePipeline with AWS CodeBuild; the pipeline definition is written with AWS CDK, like the rest of the infrastructure (recommended)
B. GitHub Actions; the pipeline is a workflow file in the repository and signs in to AWS with a role instead of keys
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q2. [Platform engineer] When should the pipeline run?

The team commits small changes directly to one main branch. A pipeline can run only on pushes to that branch, or also check changes before they reach it.

A. On every push to the main branch: run all checks, then deploy to staging if they pass; production stays behind the manual approval (recommended; it matches the one-branch practice)
B. On every push to the main branch as in A, and also run the checks, without deploying, on every pull request
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q3. [Quality engineer] Which checks must block a change?

A blocking check stops the change from being deployed until it is fixed. The Infrastructure Design lists these checks: Black and Ruff (formatting and lint), the tests with at least 80 percent line coverage, the dependency vulnerability check (`pip-audit`), a secret scan, the template tests of the AWS CDK stack, and an image scan before anything is published. The team accepted that 80 percent coverage may be hard to reach by the deadline (`team.md`, Testing Posture); the suite reaches 95.88 percent today.

A. All of them block: a change that fails any check is not deployed (recommended)
B. Formatting, tests with coverage, and the stack template tests block; the dependency, secret, and image scans only report and do not stop the deployment
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q4. [Security engineer] Which tools should do the secret scan and the image scan?

No scan tool has been chosen yet, and the requirement that no secret reaches the repository (NFR3.1) cannot be checked without one. Both scans run inside the build project and need no extra service in the account.

A. Gitleaks for the secret scan and Trivy for the image scan; `pip-audit` stays the dependency check (recommended; both are free command-line tools)
B. Amazon Inspector and Amazon ECR scanning for the image after it is published, and Gitleaks for secrets; this scans after publication, not before
C. Not yet defined; the pipeline definition leaves the two scan steps as marked placeholders and the secret-scan requirement stays unchecked
X. Other (please specify)

[Answer]: A

## Q5. [Platform engineer] Where should the container image and the build outputs be stored?

The Infrastructure Design says AWS CDK builds the container image itself during the deployment and publishes it to the asset repository that `cdk bootstrap` creates in the account. The application is a single container, and its Python packages come from the public package index at build time.

A. Use the repository that `cdk bootstrap` creates; no extra repository is set up (recommended)
B. Create a separate Amazon ECR repository for the application image, with its own cleanup rule for old images
C. Not yet defined
X. Other (please specify)

[Answer]: A
