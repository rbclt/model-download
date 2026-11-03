# Code Summary: u1-import-slice

Conversation language: English.

Unit u1-import-slice is built: a Python application that imports a public Hugging Face model, scans it, and keeps or removes it, plus the AWS CDK stack that runs it. The code is in the project root (not in `aidlc/`). All 14 plan steps are done. The team writes each layer's code first and then its tests (test-after), as the Testing Contract says.

## What was built

| Path | What it does |
|------|--------------|
| `src/model_import/models.py` | Record types (ImportRequest, ModelFile, ModelFileSet, ScannedFile, ScanFinding, StoredModel, FailureNotice), status and outcome values, the import id from the event id |
| `src/model_import/settings.py` | Reads bucket names, queue URL, sender and team addresses, and the optional SES configuration set from the environment. Never reads a Hugging Face token |
| `src/model_import/logs.py` | JSON logger that writes only allow-listed fields, so no secret or personal value can reach a log |
| `src/model_import/event.py` | Checks the start event (contract C1) and the file paths of a model listing (BR1.1, BR1.3) |
| `src/model_import/retry.py` | Retry rule: 3 retries per file, waits 5, 10 and 20 seconds, a 30-minute budget, Retry-After up to 60 seconds. Clock and sleep are injected |
| `src/model_import/hf_client.py` | The only module that knows the Hugging Face web addresses: file listing with sizes and hashes, streaming download, no token, certificate check, 10 s connect and 60 s read time-outs |
| `src/model_import/downloader.py` | ModelDownloader: up to 4 files in parallel, early 1,000-file and 20 GiB checks, size and hash check, upload to the holding bucket (BR2.1 to BR2.6) |
| `src/model_import/scanner.py` | LeakScanner: file classes, safetensors header check, text scan with the minimum pattern set, scan bounds (BR3.1 to BR3.7) |
| `src/model_import/store.py` | S3 access for the import record, the single outcome (conditional create) and the per-day email counter (conditional update) |
| `src/model_import/custodian.py` | FileCustodian: duplicate check on the marker, server-side copy, `_COMPLETE` marker last, removal that deletes the marker first (BR4.1 to BR4.3, BR5.2) |
| `src/model_import/notifier.py` | FailureNotifier: one email per recipient, 50 a day, no finding values in the text (BR5.3, BR5.4, BR5.6) |
| `src/model_import/coordinator.py` | ImportCoordinator: the state machine, one removal, one outcome (BR5.1, BR5.5) |
| `src/model_import/worker.py` | The queue loop: receive, heartbeat, delete after the outcome, 7-hour watchdog, task protection, exit codes, SIGTERM handling |
| `infra/config.py` | Reads the deployment settings from CDK context or the environment and reports every problem at once |
| `infra/model_import_stack.py` | The CDK stack: event bus and policy, rule, FIFO queue with dead-letter queues, VPC, two buckets, Fargate service with scaling, roles, SES configuration set, SNS topic, log group, metric filters, alarms, budget |
| `app.py`, `cdk.json` | CDK entry point |
| `Dockerfile`, `.dockerignore` | Container image of the worker; the build context holds only `pyproject.toml` and `src/` |
| `pyproject.toml` | Metadata, exact dependency pins, Black, Ruff, pytest and coverage settings |
| `README.md` | Set up, checks, settings, prerequisites, deploy, test event, reading the final bucket, known limits |
| `.gitignore` (appended) | Python and CDK output lines only; no existing line changed |

## How to run it

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest --cov --cov-fail-under=80
black --check src infra tests app.py
ruff check src infra tests app.py
```

Deploy with `cdk deploy -c teamAddress=... -c senderAddress=... -c breakGlassRoleArns=...` (see `README.md`). The worker command is `model-import-worker`.

## Test results

Run on this machine (Python 3.13.2, Windows) at the end of Step 13:

| Check | Result |
|-------|--------|
| `python -m pytest --cov --cov-fail-under=80` | 306 passed in 76.75 s; total coverage 94.5 percent (floor 80). Last run, after the review fixes (the 301 tests of Step 13 plus 5 new ones) |
| `black --check src infra tests app.py` | 41 files would be left unchanged |
| `ruff check src infra tests app.py` | All checks passed |
| `pip-audit --local` | 12 findings, all in `pip` 24.3.1 (the installer in the virtual environment, not a project dependency); none in the pinned project dependencies |

Coverage per module:

| Module | Statements | Missed | Cover |
|--------|-----------:|-------:|------:|
| `infra/config.py` | 69 | 0 | 100% |
| `infra/model_import_stack.py` | 212 | 2 | 99% |
| `model_import/coordinator.py` | 182 | 8 | 96% |
| `model_import/custodian.py` | 194 | 28 | 86% |
| `model_import/downloader.py` | 95 | 3 | 97% |
| `model_import/event.py` | 105 | 2 | 98% |
| `model_import/hf_client.py` | 176 | 7 | 96% |
| `model_import/logs.py` | 120 | 10 | 92% |
| `model_import/models.py` | 191 | 3 | 98% |
| `model_import/notifier.py` | 94 | 0 | 100% |
| `model_import/retry.py` | 93 | 2 | 98% |
| `model_import/scanner.py` | 254 | 9 | 96% |
| `model_import/settings.py` | 42 | 0 | 100% |
| `model_import/store.py` | 126 | 15 | 88% |
| `model_import/worker.py` | 300 | 34 | 89% |
| Total | 2,254 | 123 | 94.54% |

Tests by area: unit tests in `tests/unit/` (one file per module, plus `test_rule_coverage.py`, which lists every rule ID BR1.1 to BR5.6 and checks that a test names it), integration tests of the worker loop in `tests/integration/test_worker.py`, two end-to-end tests in `tests/e2e/test_end_to_end.py` (a clean model and a model with a planted secret), and CDK template tests in `tests/infra/`.

## Choices made while building

Design points that the documents left open or that two documents read differently. Each follows `rules.md` where it speaks.

1. **Sizes.** 20 GB is counted as 20 GiB so it fits the 30 GiB disk. The 200 MB limit for a text file is counted as 200 MiB.
2. **File class edge cases.** The ending is the text after the last dot, case ignored. A name that starts with a dot and has no other dot (for example `.env`) has no ending, and so does a name that ends in a dot; a file with no ending is scanned as text. Only the last ending counts, so `model.safetensors.index.json` is json. An ending that is in no known class falls into the ignored class, which is kept unscanned (the owner's choice, `rules.md` Decisions Taken).
3. **Read errors versus failed storage.** A file that cannot be read during the scan is a finding (AC3.1.4). A failure of S3 itself (a failed read from the holding bucket, a failed put) is a failed step, not a finding, so the email names the step and not a file.
4. **Pattern details.** The patterns beyond the list in `rules.md` (exact shapes, context words, minimum lengths) are a judgement. They are the smallest useful set, and a test plants one example of each kind.
5. **Scan bound.** The 5-minute scan time limit applies to each text file (NFR1.9), checked on an injected clock. A file that passes it counts as a finding with the place "scan time limit of 5 minutes passed".
6. **Downloader stop.** The downloads share one stop flag. The first file that fails for good sets it. The flag is checked only before a file starts, so files that have not started do not start, but downloads already running continue until they end (BR2.4). The partial files are not used, and the local folder of the import is deleted.
7. **Numbered local file names.** Files are written locally as `file-<n>` under a folder named by the import id, not under their model paths, so a model path can never reach the local disk as a path.
8. **Event check.** The event check also checks `source` and `detail-type`, not only `detail`, so a message that is not a start event is dead-lettered.
9. **Parse order.** The event is parsed before the outcome lookup, so a bad event never touches S3.
10. **Stop right after a receive.** A stop request that arrives right after a message is received makes the worker set the message's visibility to 0 and leave. The queue's dead-letter rule has a maximum receive count of 1, so the message goes to the dead-letter queue and the team is alerted, not retried.
11. **Idle exit.** The worker exits with code 0 after 2 empty minutes. The ECS service may start it again; scale-in is by the alarms.
12. **Message id.** The SQS message id is not logged because it is not in the log allow-list; the import id is.
13. **Work directory.** The work directory is `model-import-work` inside the system temp folder. The task's 30 GiB disk must be that location, because the stack sets 30 GiB of task storage and the worker writes nowhere else. The Dockerfile sets no other folder.
14. **Task protection.** Scale-in protection is set and renewed through the ECS task metadata endpoint (`ECS_CONTAINER_METADATA_URI_V4`), before each receive, and released after the idle exit.
15. **SES configuration set.** The SES configuration set setting (`MODEL_IMPORT_SES_CONFIGURATION_SET`) was added to `settings.py` and `worker.py`, because the stack creates the set and the sender must name it (NFR3.9). It is optional so tests and local runs work without it.
16. **Image in tests.** The container image is a prop in the stack tests, so they never build an image. `app.py` builds it from the project root.
17. **Base image digest.** `BASE_IMAGE` in the Dockerfile defaults to a tag. The pipeline must replace it with a reviewed digest; a digest cannot be read offline.
18. **Alert topic.** The alert topic receives events through a custom EventBridge target, because the stack must control the message text and keep model names out of it.
19. **Fixed names.** The bus, queues, service, log group, configuration set and topic have fixed names, so the stack allows one environment per AWS account and Region.
20. **Not tried against AWS.** The budget filter and the stopped-task rule were written from the documentation and checked only in the synthesised template.

## Not verified against a real account

- No deployment was made. Everything in the stack is checked only in the synthesised template.
- The budget filter (cost allocation) and the rule for stopped tasks have not been tried against AWS.
- The Hugging Face address shapes and the response fields (`type`, `path`, `size`, `lfs.oid`) come from the public documentation and a stand-in host; no real model was downloaded. A 140 GB model is beyond NFR2.1 and was never tried.
- Scale-out timing (about 3 minutes), the 7-hour watchdog and task protection during a real long import are tested only with injected clocks and stand-ins.
- SES sending, sandbox status and the verified sender identity are not tested. Moto stands in for SES.
- The ECS task metadata endpoint is a stand-in in tests.
- The Docker image was not built here (Docker is not confirmed on this machine). The image check is left to the pipeline.
- `cdk synth` through the CLI was not run; the template tests build the stack in Python.
- The tests ran on Python 3.13.2. The target is Python 3.12 and the image uses 3.12.
- Git is not installed on this machine, so nothing was committed.

## Story criteria covered

Acceptance criteria (from `stories.md`) with the module that covers them. The 6-hour limit, the "already stored" email, the list of leftovers, and the record of a failed email are in u2-import-rules, as `rules.md` says. This unit has the 7-hour watchdog that ends a stuck task.

| Criterion | Covered by |
|-----------|-----------|
| AC1.1.1 one import starts for a valid event | `event.py`, `coordinator.py`, `worker.py` |
| AC1.1.2 a bad event is rejected with an email | N/A here: delivered by u2-import-rules (u1 drops a bad event with one log line, `event.py`, `worker.py`) |
| AC1.1.3 a version already stored is refused early | N/A here: delivered by u2-import-rules (u1 fails at the keep step on the marker, `custodian.py`) |
| AC1.1.4 a second event waits | FIFO queue with one group in `model_import_stack.py`, `worker.py` (one message at a time) |
| AC2.1.1 every file downloaded into the holding area, no token | `hf_client.py`, `downloader.py`, `store.py` |
| AC2.1.2 a model that needs a login or does not exist fails | `hf_client.py`, `downloader.py`, `coordinator.py` |
| AC2.1.3 an interrupted file is not scanned or kept | `downloader.py`, `retry.py`, `coordinator.py` |
| AC3.1.1 a planted secret or personal value fails the import | `scanner.py` |
| AC3.1.2 weight files are not read for content | `scanner.py` |
| AC3.1.3 any finding stops the import | `scanner.py`, `coordinator.py` |
| AC3.1.4 a file that cannot be read is a finding | `scanner.py` |
| AC3.1.5 a finding names file, place and kind, never the value | `scanner.py`, `models.py`, `logs.py` |
| AC4.1.1 a clean model is moved and kept | `custodian.py`, `coordinator.py` |
| AC4.1.3 a failed scan leaves nothing in the final location | `custodian.py`, `coordinator.py` |
| AC4.1.2 a failed move leaves nothing in the final location | N/A here: delivered by u2-import-rules (u1 removes what it copied and deletes the marker first, `custodian.py`) |
| AC5.1.1 on failure all downloads and temporary resources are removed | `custodian.py`, `coordinator.py`, `worker.py` |
| AC5.1.4 one outcome per import | N/A here: delivered by u2-import-rules |
| AC5.1.5 outcome and reason in the logs, without secrets | N/A here: delivered by u2-import-rules |
| AC5.2.1 one email to sender and team with model, version, reason, removal state | `notifier.py`, `coordinator.py` |
| AC5.2.2 no secret or personal value in the email | `notifier.py` |

Non-functional requirements are covered by the same modules and by `tests/infra/` (bucket and role settings, scaling limits, alarms, budget).
