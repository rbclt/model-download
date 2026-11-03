# Test Results

Build and Test run of 2026-11-02 for `u1-import-slice` and `u2-import-rules`. Machine: Windows, Python 3.13.2 in `.venv`, Node.js v24.19.0, npm 11.17.0. Docker, the CDK command line (`cdk`), and Git are not installed. All commands ran from the project root `c:\Roshin\Kiro\Projects\model-download-scan`.

## Sources used

- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/code-generation/code-generation-plan.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/code-generation/unit-test-instructions.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/code-generation/code-summary.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u2-import-rules/code-generation/code-generation-plan.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u2-import-rules/code-generation/unit-test-instructions.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u2-import-rules/code-generation/code-summary.md`
- The commands come from `build-instructions.md` and `integration-test-instructions.md` in this folder.

## Build status

Success for every command that could run here. Two build steps could not run: the container image build (no Docker) and `cdk synth` through the CDK command line (no `cdk`). No in-stage fix was needed or made.

## Commands run

| Run | Command | Exit code | Key output |
|-----|---------|----------:|------------|
| R1 | `.venv\Scripts\python -m pip check` | 0 | `No broken requirements found.` |
| R2 | `.venv\Scripts\python -m black --check src infra tests app.py` | 0 | `44 files would be left unchanged.` |
| R3 | `.venv\Scripts\python -m ruff check src infra tests app.py` | 0 | `All checks passed!` |
| R4 | `$env:MODEL_IMPORT_TEAM_ADDRESS='team@example.com'; $env:MODEL_IMPORT_SENDER_ADDRESS='sender@example.com'; $env:MODEL_IMPORT_BREAK_GLASS_ROLE_ARNS='arn:aws:iam::123456789012:role/admin'; .venv\Scripts\python app.py` | 0 | No output. No `cdk.out` folder was created in the project (`Test-Path cdk.out` was `False`) |
| R5 | `.venv\Scripts\python -m pytest tests/unit tests/integration tests/e2e tests/infra -q` (the unit-test command recorded in both Units' `unit-test-instructions.md`; identical, run once) | 0 | `540 passed in 168.95s (0:02:48)` |
| R6 | `.venv\Scripts\python -m pytest --cov --cov-fail-under=80` (the project-wide coverage command) | 0 | `Required test coverage of 80% reached. Total coverage: 95.88%` and `540 passed in 188.70s (0:03:08)` |
| R7 | `.venv\Scripts\python -m pip_audit --local` | 1 | `Found 12 known vulnerabilities in 1 package`; all rows are for `pip` 24.3.1; the project package `model-import-scan` is skipped ("Dependency not found on PyPI") |
| R8 | A probe for `docker`, `cdk`, `node`, `npm`, `git` on the path | 0 | `docker : NOT FOUND`, `cdk : NOT FOUND`, `git : NOT FOUND`, `node : C:\Program Files\nodejs\node.exe`, `npm : C:\Program Files\nodejs\npm.ps1` |
| R9 | A `python -c` timing of the project's `LeakScanner` over 100 MiB of text served from memory | 0 | `bytes 104857600 seconds 55.8 findings 0` |
| R10 | A `python -c` run of one clean import with the project's end-to-end fixtures (moto and respx stand-ins, fake clock), printing its `status_changed`, `import_outcome`, and `file_downloaded` log lines | 0 | Two `file_downloaded` lines with `bytes` (23 and 14); two `status_changed` lines (`scanning`, `keeping`) with `importId`, `modelName`, `exactVersion`, `eventId`, `time`; one `import_outcome` line with `outcome` `stored`, `totalSeconds`, `downloadSeconds`, `scanSeconds`, `keepSeconds`, `frontDoorSeconds`, `findingsTotal`, `removalComplete` true. `exit 0` |

R5 is the unit-test command and R6 is the coverage command; both run every test, so the coverage run is the Evidence for the coverage floor. A unit-scoped run is not needed: both Units' recorded unit-test command already selects the whole suite.

About R7: it is a check I added from `README.md` ("Run `pip-audit` to check the pinned dependencies"); the two Units' instructions list it only as a tool. Its non-zero exit comes from `pip` itself, the installer inside the virtual environment, which is not in `pyproject.toml`. The pinned project packages have no finding. It is not counted as a failed build command. `pip` was not updated.

About R9 and R10: they ran the project's own code through `python -c` and wrote no file. R10 created an empty folder `.pytest_cache/probe-work` as its work directory, which was removed afterwards.

## Test totals

| Total | Passed | Failed | Skipped | Errors |
|------:|-------:|-------:|--------:|-------:|
| 540 | 540 | 0 | 0 | 0 |

R5 and R6 gave the same totals. By folder: unit 444, integration 19, end to end 7, CDK template 70.

By file (from the R6 run):

| File | Tests |
|------|------:|
| `tests/e2e/test_end_to_end.py` | 7 |
| `tests/infra/test_config.py` | 6 |
| `tests/infra/test_stack_import_rules.py` | 21 |
| `tests/infra/test_stack_monitoring.py` | 15 |
| `tests/infra/test_stack_resources.py` | 13 |
| `tests/infra/test_stack_security.py` | 15 |
| `tests/integration/test_worker.py` | 19 |
| `tests/unit/test_coordinator.py` | 52 |
| `tests/unit/test_custodian.py` | 49 |
| `tests/unit/test_deadline.py` | 6 |
| `tests/unit/test_downloader.py` | 19 |
| `tests/unit/test_event.py` | 68 |
| `tests/unit/test_hf_client.py` | 25 |
| `tests/unit/test_logs.py` | 21 |
| `tests/unit/test_models.py` | 48 |
| `tests/unit/test_notifier.py` | 22 |
| `tests/unit/test_retry.py` | 24 |
| `tests/unit/test_rule_coverage.py` | 4 |
| `tests/unit/test_scanner.py` | 62 |
| `tests/unit/test_settings.py` | 17 |
| `tests/unit/test_store.py` | 17 |
| `tests/unit/test_worker_support.py` | 10 |

Compared with the Units' own reports: u1 ended at 306 tests and u2 at 540 tests in total; this run found 540.

## Failure details

No test failed. No build command failed. The one non-zero exit was R7 (`pip-audit`), explained above.

The stage's failure predicate is still triggered, by targets, not by commands: 3 targets are `Not Met` and 19 are `Unverified` (see below). No loop-back was started by this lead.

## Coverage report

Line coverage from R6 (`[tool.coverage.run] source = ["src/model_import", "infra"]`; branch coverage is not switched on). The `Percent` column is computed from `Stmts` and `Miss` to two decimals; the tool's own column rounds to a whole number.

| Module | Stmts | Miss | Percent | Missing lines |
|--------|------:|-----:|--------:|---------------|
| `infra/__init__.py` | 0 | 0 | 100.00 | |
| `infra/config.py` | 69 | 0 | 100.00 | |
| `infra/model_import_stack.py` | 228 | 2 | 99.12 | 515-516 |
| `src/model_import/__init__.py` | 1 | 0 | 100.00 | |
| `src/model_import/coordinator.py` | 347 | 9 | 97.41 | 370, 381, 405, 433, 647-648, 656-657, 684 |
| `src/model_import/custodian.py` | 361 | 22 | 93.91 | 230-231, 285-286, 312, 325-327, 345, 382-384, 529-530, 547-548, 605, 612-615, 634, 671 |
| `src/model_import/deadline.py` | 30 | 0 | 100.00 | |
| `src/model_import/downloader.py` | 101 | 3 | 97.03 | 107, 191, 239 |
| `src/model_import/event.py` | 147 | 4 | 97.28 | 123, 125, 162, 220 |
| `src/model_import/hf_client.py` | 179 | 7 | 96.09 | 94, 125, 131, 135, 227-228, 236 |
| `src/model_import/logs.py` | 127 | 10 | 92.13 | 125, 132, 134, 140, 145, 148, 150, 165, 252, 267 |
| `src/model_import/models.py` | 323 | 2 | 99.38 | 187, 370 |
| `src/model_import/notifier.py` | 177 | 2 | 98.87 | 313, 379 |
| `src/model_import/retry.py` | 100 | 2 | 98.00 | 107, 132 |
| `src/model_import/scanner.py` | 257 | 9 | 96.50 | 186, 205-206, 379, 401, 416, 480, 497, 505 |
| `src/model_import/settings.py` | 42 | 0 | 100.00 | |
| `src/model_import/store.py` | 132 | 15 | 88.64 | 61, 110-111, 132, 141-142, 151, 196, 198, 208, 211-212, 214, 231, 237 |
| `src/model_import/worker.py` | 342 | 35 | 89.77 | 168-170, 260, 329-331, 384, 401-402, 459-461, 469-471, 476-477, 493-494, 635-664, 668 |
| **Total** | **2,963** | **122** | **95.88** | |

Checks against the coverage targets:

- Project floor 80 percent: met (95.88).
- u1 target, 85 percent or more for the code u1 built: met (every module at or above 88.64; together 95.84 without `deadline.py`).
- u2 target, 90 percent or more for the changed modules `custodian.py`, `coordinator.py`, `notifier.py`, `event.py`, `worker.py`, `store.py`, `deadline.py`: **not met for `worker.py` (89.77) and `store.py` (88.64)**. Together the seven are at 94.34 percent (1,449 of 1,536 statements). The largest uncovered block of `worker.py` is lines 635-664 and 668, which are 31 lines at the end of the file (the program entry). The lines missing from `store.py` are error branches of reads and writes.

## Target Verification Matrix

The finalized matrix is in `build-and-test-summary.md`, section "Target Verification Matrix", which carries it in full: 108 rows, each with one verdict and no `Pending` left. Counts: `Met` 85, `Not Met` 3, `Unverified` 19, `N/A` 1.

## Unverified and Not Met

For each target: the reason, and what it would take to check it. The Owning Stage is "none scheduled in this plan" unless stated: the plan has no performance-validation or other validation stage after Build and Test.

### Not Met

| Target | Reason | What would settle it |
|--------|--------|----------------------|
| TC-u2-CHANGED-MODULES-90 | `worker.py` is at 89.77 percent and `store.py` at 88.64 percent; the target is 90 percent for each changed module | More tests for the error branches of `store.py` and for the entry code of `worker.py` (lines 635-668), written in Code Generation; or the person accepting a lower target for these two modules. The target was not lowered here |
| ND1-SEC-LIST-PREFIX | The task role may list both whole buckets (`ListWholeBuckets`, `infra/model_import_stack.py` line 449), while the NFR Design limits listing by a prefix condition. Infrastructure Design changed this on purpose | The person accepts the Infrastructure Design change, or the statement gets a prefix condition. The tests record the current statement and would change with it |
| ND1-OBS-STUCK | The stuck-queue alarm threshold is 10 hours (`infra/model_import_stack.py` line 859); the NFR Design says 8 hours. Infrastructure Design set 10 hours because a message can wait up to 7 hours behind a running import | The person accepts 10 hours, or the threshold and its test change to 8 hours (which may give a false alarm behind a long import) |

### Unverified

| Target | Reason | What would settle it |
|--------|--------|----------------------|
| TC-u1-IMAGE-BUILD | Docker is not installed; the `Dockerfile` base image is a tag, with the digest left to the pipeline | A machine with Docker: build the image with `docker build --build-arg BASE_IMAGE=python:3.12-slim@sha256:<digest> .` |
| NFR6.8 | No Git on this machine, so the u1 tests cannot be compared with their earlier version; the u2 code summary says four test files with a new `deadline` argument are not listed row by row | A Git history, or a review of the four test files against the u1 versions |
| NFR1.2 | Needs a real import of a model of about 16 GB | The first real import; the logged `totalSeconds` |
| NFR1.3 | Needs a real download from Hugging Face | The first real import; bytes divided by `downloadSeconds` |
| NFR1.4 | Needs the logged scan duration of a real import. The proxy of R9 (55.8 seconds for 100 MiB from memory) is well under the limit but does not include reading from S3 | The first real import with 100 MB of text files; the logged `scanSeconds` |
| NFR1.5 | Needs a real copy of 16 GB in S3 | The first real import; the logged `keepSeconds` |
| NFR1.10 | The check points are tested; the 10-minute bound after the deadline depends on how long one S3 copy call of up to 5 GiB takes, which nobody measured | A real copy of a file near 5 GiB, and a run that passes the deadline during it |
| NFR1.11 | `frontDoorSeconds` is logged, but no test asserts a value and the fake clock gives 0.0 | A deployed run, or a test with a stand-in that adds latency and asserts the logged value |
| NFR1.12 | No test asserts the time of a rejected or refused event | The same as NFR1.11, for the rejected and refused paths |
| NFR1.13 | No test removes 1,000 stand-in objects, and no duration is asserted | A test with 1,000 objects in the stand-in bucket asserting the removal, the proof, and the email; a real run for the time |
| NFR2.1-STAGED-RUN | Needs a real staged run of a model of 10 GB or more | One staged run before the first release |
| NFR2.7 | No test with 1,000 stand-in objects for the marker and the listing | A test with 1,000 objects and a measured duration |
| ND1-PERF-IO | The part sizes and batch size are constants in the code; no test asserts them and their effect is throughput | Real transfer measurements; or tests that assert the constants |
| NFR2.2 | A volume figure (5 imports a day) with no local check | A period of real use; the cost alarm shows far higher volume |
| NFR2.8 | No test sends a burst of 100 bad events | A test with 100 stand-in bad events checking one outcome each, in order, with the time of each logged |
| NFR3.1-CI-SECRET-SCAN | No scan tool for credentials is chosen or configured in the project. Owning Stage: ci-pipeline, which sets up the CI step | The CI Pipeline stage adds the scan and its first run |
| NFR3.16 | SES sandbox status and verified identities in us-east-1 need the real account; no deployment check script exists in the project | The owner's check before the first real import, or a script added to the pipeline |
| NFR3.24 | No test puts a sibling version folder, or a model whose name starts the same (`models/org/model2/`), beside the folder being removed | Two tests with those objects in the final stand-in bucket checking that the removal leaves them |
| NFR5.3-CAP | The code writes a `findings_capped` line (`src/model_import/coordinator.py` line 685) but no test makes a file reach the cap of 100 and reads that line | A coordinator test with a file of more than 100 matches asserting the line and its `matchesTotal` |

### N/A

| Target | Reason |
|--------|--------|
| TC-CI-OBLIGATION | CI Pipeline, later in this plan, sets up CI to run these tests before merge. Owning Stage: `ci-pipeline` |

## Defects found

None in the source or test code that makes a test fail. Findings, with file and place:

- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u2-import-rules/code-generation/code-summary.md`, section "Test results": it says all changed modules reach 90 percent "except `store.py`". `src/model_import/worker.py` is at 89.77 percent (342 statements, 35 missed); the coverage report rounds it to 90.
- `infra/model_import_stack.py` line 449 (`ListWholeBuckets`) and line 859 (`threshold=10 * 60 * 60`): differ from the NFR Design as described above.
- `Dockerfile` lines 4 to 9: the base image defaults to a tag, not a digest (disclosed in the code summary of u1, choice 17).
- Test gaps, listed under Unverified: NFR2.8, NFR1.13, NFR2.7, NFR3.24, NFR5.3-CAP.
- The virtual environment holds `pip` 24.3.1 with 12 known vulnerabilities (R7).

## In-stage fixes

None. No test configuration, build script, or environment setting was changed. Nothing under `src/`, `infra/`, or `tests/` was edited.
