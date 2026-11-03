# Developer Brief: u1-import-slice Code Generation

Conversation language: English.
Sensors: on. Learnings: on. Summary Confirmation: off. Plan Approval: on. Collaborators: off. Protocol modules: reviewer, reviewer-unit, reviewer-repair, ensemble, construction, loopback, learnings, sensors. These files were in this project before this work started and are the person's: `.gitignore`, `AGENTS.md`. Leave them as they are unless this work's plan changes one of them.
Plain-words rule for any AI-DLC command text you write: use plain words on one line in single quotes, with no $, backtick, >, <, &, @( or @{ (write "and" for &, "to" for ->, "5 dollars" for $5): a command holding one asks the person to approve it first. The person's own words still go in exactly as they wrote them.

## What you are doing

You are the developer agent building Unit u1-import-slice of a Python application. The approved plan is `aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/code-generation/code-generation-plan.md` (14 steps; tick each step's checkbox `- [ ]` to `- [x]` in that file as you finish it, and change nothing else in the plan). The test instructions are `unit-test-instructions.md` in the same folder. Read both first. The team writes each layer's code first and then its tests (test-after).

Application code goes in the project root `c:\Roshin\Kiro\Projects\model-download-scan` (never in `aidlc/`). Do not edit `AGENTS.md`. `.gitignore` is the person's; the plan allows appending only Python and CDK lines (Step 13).

## Read these design inputs (all under `aidlc/spaces/default/intents/261030-model-import-scan/`)

- `construction/u1-import-slice/functional-design/` : `functional-spec.md`, `rules.md` (BR1.1 to BR5.6, these are the exact rules to implement and to name in tests), `entities.md`
- `construction/u1-import-slice/nfr-requirements/` : the NFR numbers
- `construction/u1-import-slice/nfr-design/` : `performance-design.md`, `security-design.md`, `scalability-design.md`, `reliability-design.md`, `observability-design.md`, `logical-components.md`
- `construction/u1-import-slice/infrastructure-design/` : `infrastructure-specification.md`, `monitoring-design.md`, `cicd-pipeline.md`
- `inception/contract-design/contract-summary.md` (contracts C1 to C4), `inception/requirements-analysis/requirements.md`, `inception/units-generation/unit-of-work.md`

## Design facts that matter for the code

- Python 3.12 target; this machine has 3.13.2 and no pytest installed. Create `.venv` and install with pip. Pin exact versions in `pyproject.toml`. Black and Ruff must pass. Package layout: `src/model_import/` (installed with `pip install -e ".[dev]"`), `infra/`, `tests/{unit,integration,e2e,infra}`.
- Libraries: boto3, httpx (Hugging Face client, streaming, timeouts connect 10 s and read 60 s, certificate verification on, never send a token and ignore HF_TOKEN-like environment variables). Dev: pytest, pytest-cov, moto (s3, sqs, ses), respx, black, ruff, pip-audit, aws-cdk-lib, constructs.
- Hugging Face API: file list `GET https://huggingface.co/api/models/{owner}/{name}/tree/{revision}?recursive=true` (JSON list of entries with `type`, `path`, `size`, and for LFS files `lfs: {oid (sha256), size}`); download `GET https://huggingface.co/{owner}/{name}/resolve/{revision}/{path}`. Keep all of this inside `hf_client.py`.
- The five building blocks map to modules: ImportCoordinator = `coordinator.py` + `event.py`; ModelDownloader = `downloader.py` + `hf_client.py` + `retry.py`; LeakScanner = `scanner.py`; FileCustodian = `custodian.py` + `store.py`; FailureNotifier = `notifier.py`. `worker.py` is the queue loop. `settings.py` reads the environment. `logs.py` is the allow-list JSON logger (fields list in `observability-design.md`).
- Rule IDs: put the rule ID (for example `BR3.2`) in the docstring of the function that implements it and in the name or docstring of at least one test, because a test must list every rule ID BR1.1 to BR5.6 and check that a test names it.
- Never put a secret, token, or credential in code, logs, findings, or emails. Planted fake secrets in tests must be built in the test from two string parts so the file itself has no complete secret-shaped string.
- Retry waits and the watchdog must use an injectable clock/sleep so tests never sleep for real.
- S3 layout: holding bucket has `holding/<importId>/<path>` and `state/<importId>/record.json`, `state/<importId>/outcome.json` (conditional create with `IfNoneMatch="*"`), `state/counters/emails-<YYYY-MM-DD>.json` (conditional update on ETag with `IfMatch`); final bucket has `models/<lowercase name>/<version>/<path>` and `_COMPLETE` marker last. importId is derived from the SQS/EventBridge event id.
- Message life cycle, scale-in protection, watchdog and exit codes: exactly as in `reliability-design.md` (Message Life Cycle) and `scalability-design.md`.
- The outcome values are `stored`, `removed-with-email`, `removed-email-failed`.

## Working rules

- Complete, runnable files; no placeholder stubs. Handle errors at integration boundaries.
- After each step's implementation, write and run that layer's tests before moving on (test-after). Run Black and Ruff on what you wrote.
- Standard test volume: 5 to 8 tests per component, integration tests at key boundaries, one end-to-end test. Never write a test that always passes (`assert True`). Never lower a coverage threshold.
- If a design document is ambiguous or contradicts another, choose the reading that follows `rules.md`, and write the choice in `code-summary.md` under a heading "Choices made while building" at the end of your last step. Do not stop to ask.
- Do not run `aidlc` commands. Do not write to the audit folder. Do not dispatch other agents.
- Report back: the steps you completed, files created, the exact test command output summary (number of tests and coverage per module), and anything not finished.
