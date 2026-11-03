# Code Generation Plan: u1-import-slice

## Summary

- Builds: the Python application for u1-import-slice: five building blocks, the queue worker, and an AWS CDK stack with its container image.
- Touches: new folders `src/model_import/`, `infra/`, and `tests/` at the project root, plus `pyproject.toml`, `Dockerfile`, `app.py`, `cdk.json`, and a README for running it; nothing already in the project is changed.
- Tests: about 60 automated tests (unit tests for each building block, integration tests with stand-ins for S3, SQS, SES and Hugging Face, one end-to-end test, and CDK template tests), checked at 80 percent line coverage.

The plan follows the design in `functional-spec.md`, `rules.md`, `entities.md`, `contract-summary.md`, `performance-design.md`, `security-design.md`, `infrastructure-specification.md`, `unit-of-work.md`, and `requirements.md`. The team writes the code of each layer first and then its tests (test-after), as the Testing Contract below says. Code goes to the project root, not to the record folder.

## Testing Contract

```json
{
  "version": 1,
  "methodology": "test-after",
  "source": "team",
  "ordering": "We implement each testable layer first, then write and run the tests for that layer.",
  "scope": "mvp",
  "test_strategy": "standard",
  "project_type": "greenfield",
  "applicable_notes": [
    {
      "layer": "org",
      "text": "We treat tests as a first-class deliverable in every Bolt. The specific\nmethodology (TDD, BDD, ATDD, or classic test-after) is affirmed at\npractices-discovery and recorded in `team.md` under this heading with explicit\n`Methodology` and `Ordering` fields; Code Generation resolves those fields\nindependently from coverage, tooling, and scope notes.\n\nWhen no posture has been affirmed, our default per scope is:\n- **Methodology**: test-after\n- **Ordering**: implement each applicable testable layer, then write and run\n  that layer's tests.\n- `mvp`, `enterprise`, `feature`, `infra`, `classic` add an 80% line-coverage\n  floor and CI execution before merge.\n- `bugfix`, `security-patch` add a targeted regression for the specific\n  bug/vulnerability and require the existing suite to remain green.\n- `express` uses the Minimal strategy: requirement-driven unit tests (one per\n  requirement, with a happy-path floor per component); existing tests remain\n  green.\n- `poc`, `refactor`, `workshop` add no extra new-test floor and require the\n  existing suite to remain green.\n\nThe active `Test Strategy` still applies in every scope and determines test\nvolume/types. Scope floors are additive; they never reduce or replace the\nselected strategy.\n\nBuild and Test verifies defined coverage floors and affirmed quality targets;\nthey may not be weakened to make a step pass.\n\nAffirm a stricter posture in `team.md` if the team commits to one."
    },
    {
      "layer": "team",
      "text": "- **Methodology**: test-after\n- **Methodology evidence**: The team chose to write the code first and then write tests for it (interview Q4).\n- **Ordering**: We implement each testable layer first, then write and run the tests for that layer.\n- **Coverage floor**: Tests cover at least 80 percent of lines (interview Q5). The team chose this knowingly and accepts that it may be hard to reach by the end-of-tomorrow deadline.\n- **Test language and tooling**: Python (confirmed, feasibility Q5). The test framework is not chosen yet."
    }
  ],
  "obligations": {
    "strategy": "standard",
    "strategy_volume": [
      "Five to eight tests per component.",
      "Unit tests plus integration tests for key boundaries.",
      "Add E2E, performance, or security tests when requirements demand them."
    ],
    "scope_floor": [
      "Meet an 80% line-coverage floor.",
      "Run the selected tests in CI before merge."
    ],
    "combination_rule": "Apply every selected-strategy obligation and every scope-floor obligation; neither replaces the other, and a targeted scope regression may add the narrowest necessary test type beyond the strategy default."
  },
  "plan_profile": {
    "methodology": "test-after",
    "runner_step": "Bootstrap the minimal test runner/configuration and record the exact unit-scoped command.",
    "runner_ready_before_first_test": true,
    "testable_layers": [
      "Data model / database behavior",
      "Repository / data access",
      "Business logic",
      "API / endpoint",
      "Frontend behavior"
    ],
    "steps": [
      "Project structure and production configuration skeleton.",
      "Bootstrap the minimal test runner/configuration and record the exact unit-scoped command.",
      "Data model / database behavior - implement.",
      "Data model / database behavior - write and run its tests after implementation.",
      "Repository / data access - implement.",
      "Repository / data access - write and run its tests after implementation.",
      "Business logic - implement.",
      "Business logic - write and run its tests after implementation.",
      "API / endpoint - implement.",
      "API / endpoint - write and run its tests after implementation.",
      "Frontend behavior - implement.",
      "Frontend behavior - write and run its tests after implementation.",
      "Environment/build configuration.",
      "Documentation and traceability."
    ]
  },
  "input_sha256": "sha256:e750d717fc48af7682406d4df21b48e7d57011c589283971c8804bb89b1d2a63",
  "contract_sha256": "sha256:5a6a1157d8f334fa6b8f695861a1801548ad3ef7724c8317424e42947794adbc"
}
```

This unit has no frontend, so the frontend layer steps do not apply. The data model layer is the record types and the import record kept in S3. The repository layer is the S3 and queue access. The business logic is the five building blocks and the coordinator. The "API / endpoint" layer is the worker loop and the start-event boundary (there is no HTTP endpoint).

## Layout

| Path | Contents |
|------|----------|
| `pyproject.toml` | Project metadata, pinned dependencies, Black, Ruff, pytest, and coverage settings |
| `src/model_import/` | `models.py`, `settings.py`, `logs.py`, `event.py`, `retry.py`, `hf_client.py`, `downloader.py`, `scanner.py`, `store.py`, `custodian.py`, `notifier.py`, `coordinator.py`, `worker.py` |
| `infra/` | `model_import_stack.py`, the CDK stack with all resources of `infrastructure-specification.md` |
| `app.py`, `cdk.json` | CDK entry point |
| `Dockerfile` | Container image for the worker, base image pinned by digest |
| `tests/` | `unit/`, `integration/`, `e2e/`, `infra/` |
| `README.md` | How to set up, run the tests, and deploy (new file; the project has none) |

## Steps

- [x] **Step 1: Project structure and production configuration skeleton.** Create `pyproject.toml` (Python 3.12, exact pins), the `src/model_import` package, `settings.py` (reads bucket names, queue URL, sender and team addresses from the environment; never reads a Hugging Face token), and `logs.py` (allow-list JSON logger). Stories: all. Rules: BR5.4, NFR3.1, NFR5.4, NFR5.5.
- [x] **Step 2: Bootstrap the test runner.** Install pytest, pytest-cov, moto, respx, Black, Ruff; configure coverage for `src/model_import` and `infra`; record the exact unit-scoped command in `unit-test-instructions.md`.
- [x] **Step 3: Data model, implement.** `models.py` with the entities of `entities.md` (ImportRequest, ModelFile, ModelFileSet, ScannedFile, ScanFinding, StoredModel, FailureNotice) and the outcome and status values; `store.py` with the import record at `state/<importId>/record.json`, the outcome at `state/<importId>/outcome.json` written with a conditional create, and the per-day email counter. Stories: US1.1, US5.1. Rules: BR1.2, NFR4.1, NFR4.8.
- [x] **Step 4: Data model, write and run its tests.** Tests for the record, the single outcome, and the counter (about 8 tests).
- [x] **Step 5: Repository and access layer, implement.** `hf_client.py` (listing with sizes and hashes, streaming download, no token, certificate check, 10 s connect and 60 s read time-outs), `retry.py` (3 retries per file, waits 5, 10, 20 s, 30-minute budget, Retry-After up to 60 s), and the S3 and SQS access inside `store.py` and `custodian.py`. Stories: US2.1, US4.1. Rules: BR2.1 to BR2.5, NFR3.2, NFR3.13, NFR4.4, NFR4.10.
- [x] **Step 6: Repository and access layer, write and run its tests.** Tests with a stand-in Hugging Face host (listing, stall, slow, bad hash, 404, 429 with Retry-After, interrupted file) and moto S3/SQS (about 10 tests).
- [x] **Step 7: Business logic, implement.** `event.py` (the check of BR1.1, BR1.3, NFR3.14), `downloader.py` (BR2.1 to BR2.6, parallel download of up to 4 files, early 1,000-file and 20 GB checks), `scanner.py` (BR3.1 to BR3.7: file classes, safetensors header check, text scan with the minimum pattern set, bounds of NFR1.9), `custodian.py` (BR4.1 to BR4.3, BR5.2: duplicate check on the marker, copy, marker last, removal that deletes the marker first), `notifier.py` (BR5.3, BR5.4, BR5.6, NFR3.16: one send per recipient, 50-a-day limit), and `coordinator.py` (the state machine of `functional-spec.md`, BR5.1, BR5.5, a single outcome). Stories: US1.1, US2.1, US3.1, US4.1, US5.1, US5.2.
- [x] **Step 8: Business logic, write and run its tests.** Unit tests for each block, with at least one test naming each rule ID BR1.1 to BR5.6 (about 35 tests).
- [x] **Step 9: API / endpoint layer (worker loop and start-event boundary), implement.** `worker.py`: receive one message, heartbeat every 5 minutes, delete the message after the outcome, visibility 0 on a fatal error or the 7-hour watchdog (exit code non-zero), clean exit on SIGTERM while idle, task protection set and renewed before each receive and released after 2 empty minutes, and bad events deleted at once. Rules: BR1.1, NFR1.8, NFR4.2.
- [x] **Step 10: API / endpoint layer, write and run its tests.** Integration tests of the worker loop with stand-ins, and one end-to-end test from event to outcome for a clean model and for a model with a planted secret (about 8 tests).
- [x] **Step 11: Deployment artifacts (IaC), implement.** `infra/model_import_stack.py` with the event bus and policy, the rule, the FIFO queue and its dead-letter queues, the VPC, the two buckets with their policies and lifecycle rules, the Fargate service with scaling, the roles, SES configuration set, SNS topic, log group with metric filters, alarms and the budget; `app.py`, `cdk.json`; the `Dockerfile`. Stories: all. Requirements: NFR2, NFR3, NFR4, NFR5.
- [x] **Step 12: Deployment artifacts, write and run their tests.** CDK template tests: no `s3:*` and no `Resource: *` in the task role, bucket settings, lifecycle rules, queue wiring, scaling limits (about 8 tests).
- [x] **Step 13: Environment and build configuration.** `README.md`, the `.gitignore` additions needed for Python and CDK output (the existing `.gitignore` is the person's; only lines needed for this code are appended, and the plan names them), and the quality checks (Black, Ruff, coverage).
- [x] **Step 14: Documentation and traceability.** `code-summary.md`, `source-manifest.json`, and `traceability.json`.

## Story-to-Code Traceability

| Story | Plan steps |
|-------|-----------|
| US1.1 (AC1.1.1) | 1, 3, 4, 7, 8, 9, 10 |
| US2.1 (AC2.1.1 to AC2.1.3) | 5, 6, 7, 8 |
| US3.1 (all) | 7, 8 |
| US4.1 (AC4.1.1, AC4.1.3) | 5, 7, 8 |
| US5.1 (AC5.1.1) | 3, 7, 8, 9, 10 |
| US5.2 (AC5.2.1, AC5.2.2) | 7, 8 |
| Infrastructure for all | 11, 12 |

## Assumptions & Open Questions

- [assumption] Hugging Face's public API gives the file list with sizes and hashes at `/api/models/<owner>/<name>/tree/<revision>?recursive=true` and the files at `/<owner>/<name>/resolve/<revision>/<path>`. The code isolates this in `hf_client.py` so a change affects one file.
- [assumption] The existing `.gitignore` and `AGENTS.md` stay as they are, except for the Python and CDK lines named in Step 13.
- [assumption] The CDK stack is synthesised and tested without an AWS account. It is not deployed in this stage.
- Open question: Docker is not confirmed on this machine, so the image build is checked only in the pipeline (see `cicd-pipeline.md`).
