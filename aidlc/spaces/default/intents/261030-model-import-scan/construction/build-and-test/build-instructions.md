# Build Instructions

How to set up, build, and check the model import application as it stands after u1-import-slice and u2-import-rules. Every command below comes from the project (`README.md`, `pyproject.toml`, `Dockerfile`, `cdk.json`, `app.py`) or from the unit test instructions of the two Units. Where a command could not run on this machine, the text says so.

Run all commands from the project root, `c:\Roshin\Kiro\Projects\model-download-scan`. The commands are written for Windows PowerShell and the virtual environment `.venv`.

## Sources used

- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/code-generation/code-generation-plan.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/code-generation/unit-test-instructions.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/code-generation/code-summary.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u2-import-rules/code-generation/code-generation-plan.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u2-import-rules/code-generation/unit-test-instructions.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u2-import-rules/code-generation/code-summary.md`
- Project files: `README.md`, `pyproject.toml`, `Dockerfile`, `.dockerignore`, `cdk.json`, `app.py`, `infra/`.

## Prerequisites

| Tool | Needed for | State on this machine (2026-11-02) |
|------|-----------|------------------------------------|
| Python 3.12 or newer (`requires-python = ">=3.12"`) | Everything | Python 3.13.2 in `.venv`. The target is 3.12; the image uses 3.12 |
| Node.js | The CDK library for Python runs on Node.js. Needed to run the CDK tests and `app.py` | Node.js v24.19.0 and npm 11.17.0 are installed |
| AWS CDK command line (`cdk`) | `cdk synth`, `cdk bootstrap`, `cdk deploy` | **Not installed.** `README.md` installs it with `npm install -g aws-cdk` |
| Docker (or a compatible tool) | Building the container image of the worker | **Not installed** |
| An AWS account | `cdk bootstrap`, `cdk deploy`, a real import | Not used in this stage. No test and no build step here calls AWS |
| Git | Committing | **Not installed** |

## Dependency installation

Create the virtual environment and install the project with its test tools. The packages are pinned to exact versions in `pyproject.toml`.

```text
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

`.[dev]` installs pytest, pytest-cov, moto, respx, Black, Ruff, pip-audit, `aws-cdk-lib`, and `constructs`.

Check that the installed packages fit together:

```text
.venv\Scripts\python -m pip check
```

## Environment setup

- **Tests.** No setting is needed. `tests/conftest.py` sets fake AWS credentials for every test, so a test can never reach a real account. S3, SQS, and SES are moto stand-ins, and Hugging Face is a respx stand-in.
- **CDK app.** `app.py` reads three required settings from CDK context (`-c name=value`) or from environment variables. A context value wins over an environment variable. A missing or invalid setting stops the app with exit code 2 and names every problem at once.

| Setting | Environment variable | Notes |
|---------|----------------------|-------|
| `teamAddress` | `MODEL_IMPORT_TEAM_ADDRESS` | Team mailbox. Required |
| `senderAddress` | `MODEL_IMPORT_SENDER_ADDRESS` | SES sender address. Required |
| `breakGlassRoleArns` | `MODEL_IMPORT_BREAK_GLASS_ROLE_ARNS` | Comma-separated administrator role ARNs. Required, at least one |
| `budgetLimitUsd` | `MODEL_IMPORT_BUDGET_LIMIT_USD` | Optional. Default 100 |
| `stackName` | `MODEL_IMPORT_STACK_NAME` | Optional. Default `staging` |
| `smokeCheckRoleArn` | `MODEL_IMPORT_SMOKE_CHECK_ROLE_ARN` | Optional |

For a synthesis check without an account, set the three required variables to example values in the same PowerShell window:

```text
$env:MODEL_IMPORT_TEAM_ADDRESS='team@example.com'
$env:MODEL_IMPORT_SENDER_ADDRESS='sender@example.com'
$env:MODEL_IMPORT_BREAK_GLASS_ROLE_ARNS='arn:aws:iam::123456789012:role/admin'
```

These example values are for synthesis only. A real deployment needs the owner's own addresses and role ARNs (see `README.md`, "Settings the owner supplies" and "Before the first deployment").

## Build commands

### Code checks

```text
.venv\Scripts\python -m black --check src infra tests app.py
.venv\Scripts\python -m ruff check src infra tests app.py
```

### CDK app synthesis

`cdk.json` sets the app command to `python app.py`, so the CDK command line runs the same file. Without the command line, run the file through Python (with the environment variables above set):

```text
.venv\Scripts\python app.py
```

This builds the stack and synthesises the CloudFormation template. It needs no AWS account and no Docker: synthesis hashes the project folder for the image asset and does not build the image. When the app ran through Python on 2026-11-02, no `cdk.out` folder appeared in the project. With the CDK command line the usual command is:

```text
cdk synth -c teamAddress=team@example.com -c senderAddress=sender@example.com -c breakGlassRoleArns=arn:aws:iam::<account-id>:role/<admin-role>
```

**Cannot run on this machine:** `cdk synth`, `cdk bootstrap`, and `cdk deploy`, because the `cdk` command is not installed. `cdk bootstrap` and `cdk deploy` also need an AWS account.

### Container image

The image is built from the project root with the `Dockerfile`. `.dockerignore` keeps only `pyproject.toml` and `src/` in the build context. The `Dockerfile` documents this command for the pipeline, which supplies a reviewed base image digest:

```text
docker build --build-arg BASE_IMAGE=python:3.12-slim@sha256:<digest> .
```

Without the build argument the base image is the tag `python:3.12-slim`. Through the CDK, the image is built from the same `Dockerfile` when `cdk deploy` runs.

**Cannot run on this machine:** the image build, because Docker is not installed. The plan of u1 said the base image is pinned by digest. The file itself defaults to a tag, and the digest is to come from the pipeline (`code-summary.md` of u1, choice 17).

### Dependency vulnerability check

```text
.venv\Scripts\python -m pip_audit --local
```

`README.md` names `pip-audit` for this check. See "Troubleshooting" for what it reports on this machine.

## Build verification

The build is verified when all of these hold:

1. `pip check` prints "No broken requirements found."
2. Black reports that all files would be left unchanged.
3. Ruff reports "All checks passed!"
4. `app.py` exits with code 0 when the three settings are present.
5. The test commands in `integration-test-instructions.md` pass.

On 2026-11-02 checks 1 to 4 passed on this machine and the tests passed (see `test-results.md`). The image build and the CDK command line were not run.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `app.py` exits with code 2 and prints a list of settings | A required setting is missing or invalid. The message names each one without showing its value. Set the variables listed above |
| `cdk` is not recognised | The CDK command line is not installed. Install it with `npm install -g aws-cdk` (`README.md`). Node.js must be installed first |
| `docker` is not recognised | Docker is not installed. The image can then only be built by the pipeline or on a machine with Docker |
| `cdk synth` cannot find `python` or imports fail | `cdk.json` runs `python app.py` with whatever `python` is first on the path. Activate the virtual environment first (`.venv\Scripts\activate`) |
| Import errors for `aws_cdk` or `constructs` | `.[dev]` was not installed in the active environment. Run the install command again |
| `pip-audit` exits with code 1 and lists `pip` 24.3.1 | `pip` is the installer inside the virtual environment, not a project dependency, and it is not in `pyproject.toml`. On 2026-11-02 the 12 findings were all for `pip`; none were for the pinned project packages. Updating `pip` in the virtual environment clears them. This was not done here |
| Tests behave differently on Python 3.12 | The suite ran on Python 3.13.2 here. The pipeline runs 3.12 and is the first run on the target version |
| The full test run takes several minutes | Expected. It took about 3 minutes on 2026-11-02 |
