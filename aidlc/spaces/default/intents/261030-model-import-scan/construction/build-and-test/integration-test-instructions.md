# Integration Test Instructions

How the integration tests of the model import application work and how to run them. The strategy is Standard, so this file covers the key boundary tests and the interaction of the two Units. The application has no performance or security test suite of its own; those targets are checked through the unit, integration, end-to-end, and CDK template tests, and the targets that need a real account or real timing are listed in `build-and-test-summary.md`.

## Sources used

- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/code-generation/code-generation-plan.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/code-generation/unit-test-instructions.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/code-generation/code-summary.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u2-import-rules/code-generation/code-generation-plan.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u2-import-rules/code-generation/unit-test-instructions.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u2-import-rules/code-generation/code-summary.md`
- The tests under `tests/integration/`, `tests/e2e/`, `tests/unit/`, and `tests/infra/`.

## Test framework setup

- **Framework:** pytest 9.2.0 with pytest-cov 7.1.0, configured in `pyproject.toml` (`testpaths = ["tests"]`, `--import-mode=importlib`).
- **Stand-ins (nothing real is called):**
  - Amazon S3, SQS, and SES: moto 5.2.5. Each test gets fresh resources through fixtures.
  - Hugging Face: respx 0.23.1 routes for the file listing and the file downloads.
  - Time: an injected clock (`FakeClock` in `tests/conftest.py`). No test waits for real hours or sleeps for real.
  - AWS credentials: `tests/conftest.py` sets fake credentials for every test, so no test can reach a real account.
- **Setup:** the virtual environment and the `.[dev]` install from `build-instructions.md`. Node.js must be installed because the CDK library runs on it.

## Key boundary tests

| Boundary | What the tests cover | Where |
|----------|----------------------|-------|
| Start event (EventBridge message in the queue) | A start event, a rejected event with every failed field, and a message that is not a start event; an optional `time`; the sender address hidden from text forms | `tests/unit/test_event.py`, `tests/integration/test_worker.py` |
| Queue (SQS) | Message life cycle: delete after the outcome, heartbeat every 5 minutes, visibility 0 on a fatal error, a stop signal, or the 7-hour watchdog, move to the dead-letter queue on the next receive, redelivery of a message whose outcome exists | `tests/integration/test_worker.py` |
| Hugging Face | Listing with sizes and hashes, no token, certificate failure, stall, slow host, bad hash, 404, 429 with Retry-After, interrupted file, deadline checks per chunk | `tests/unit/test_hf_client.py`, `tests/unit/test_retry.py`, `tests/unit/test_downloader.py` |
| Storage (S3) | Holding area writes, copy to the final location, the `_COMPLETE` marker written last, removal by listing with proof, leftovers, the forced-failure cases, the counters and the import record and outcome under `state/` | `tests/unit/test_custodian.py`, `tests/unit/test_store.py` |
| Email (SES) | One email per recipient, content without values, the rejection and already-stored emails, the two daily limits (30 and 20), a refused recipient | `tests/unit/test_notifier.py` |
| Container host (ECS) | Task protection set for 8 hours and released; the task identity read from the metadata endpoint (a stand-in) | `tests/unit/test_worker_support.py` |
| Infrastructure (CloudFormation template) | Role actions, bucket settings, lifecycle rules, queue and rule wiring, scaling, alarms, metric filters, budget | `tests/infra/` |

## Cross-unit interaction (u1-import-slice and u2-import-rules together)

u2-import-rules changes the code that u1-import-slice built. The integration and end-to-end tests run the real worker loop, coordinator, downloader, Hugging Face client, scanner, custodian, notifier, and store together, with the stand-ins above. These are the scenarios that exercise both Units at once:

| Scenario | Test |
|----------|------|
| A clean model goes from the event to `stored`, with its marker, no email, an empty holding area | `tests/e2e/test_end_to_end.py::test_br4_1_a_clean_small_model_goes_from_the_event_to_stored_with_its_marker` |
| A model with a planted secret is removed and both recipients are emailed, with no secret value anywhere | `tests/e2e/test_end_to_end.py::test_br5_1_a_model_with_a_planted_secret_is_removed_and_both_recipients_are_emailed` |
| A bad event downloads nothing, emails both, and ends as `rejected` | `tests/e2e/test_end_to_end.py::test_br6_1_a_rejected_event_downloads_nothing_emails_both_and_ends_as_rejected` and `..._br6_2_a_rejected_event_with_a_bad_sender_address_tells_only_the_team` |
| A second event for a stored version is refused and changes nothing | `tests/e2e/test_end_to_end.py::test_br7_1_a_second_event_for_a_stored_version_is_refused_and_changes_nothing` |
| A download that reaches the 6-hour limit stops and removes everything | `tests/e2e/test_end_to_end.py::test_br8_4_a_download_that_reaches_the_6_hour_limit_stops_and_removes_everything` |
| A failed move leaves nothing in the final location and counts older files | `tests/e2e/test_end_to_end.py::test_br9_2_a_failed_move_leaves_nothing_in_the_final_location_and_counts_older_files` |
| The worker loop with a rejected event, a repeated message, and a stuck import under the 7-hour watchdog | `tests/integration/test_worker.py` (19 tests) |
| The changed tests of u1 (a bad event is now rejected with an email; a failure after the marker no longer removes the model) | Listed in `code-summary.md` of u2-import-rules under "Changed tests of u1" |

Not covered by an automated test, because it needs a real service: the multipart copy of a file above 5 GiB with real S3 (tested with a lowered limit and a stand-in), the conditional writes against real S3 (tested against moto), SES sending and the sandbox status, a real Hugging Face download, and a deployed stack.

## How to run

Run from the project root. The first two commands are the ones recorded in both Units' `unit-test-instructions.md` and were run in this stage (see `test-results.md`).

```text
.venv\Scripts\python -m pytest tests/unit tests/integration tests/e2e tests/infra -q
.venv\Scripts\python -m pytest --cov --cov-fail-under=80
```

The first command runs every test and prints no coverage. The second runs every test with the project coverage settings (`[tool.coverage.run]` in `pyproject.toml`: `src/model_import` and `infra`) and fails below 80 percent.

To run only the integration and end-to-end tests, name their folders. This uses the same tests as above with a narrower selection and was not run on its own in this stage:

```text
.venv\Scripts\python -m pytest tests/integration tests/e2e -q
```

Both full commands took about 3 minutes on 2026-11-02 (169 seconds and 189 seconds). Several tests start a short real thread wait; none waits for real hours.

## Expected coverage

- **Project:** at least 80 percent of lines for `src/model_import` and `infra` together. The threshold is in the command, and it is never lowered to make a run pass. Result on 2026-11-02: 95.88 percent.
- **Code that u2-import-rules changed** (`custodian.py`, `coordinator.py`, `notifier.py`, `event.py`, `worker.py`, `store.py`, `deadline.py`): 90 percent or more for each module, as `unit-test-instructions.md` of u2 sets. Result on 2026-11-02: five modules reached it; `worker.py` (89.77 percent) and `store.py` (88.64 percent) did not. See `build-and-test-summary.md`.
- **Code that u1-import-slice built:** 85 percent or more for its own code. Every module reached it.
- Coverage is line coverage. Branch coverage is not switched on.

## Test data

- Fake models are built inside the tests: small text files, one valid `.safetensors` header, and optional weight files. The clean model is `CLEAN` and the model with a planted secret is `LEAKY` in `tests/e2e/test_end_to_end.py`.
- Planted secrets and sender addresses are fake and built from two parts in the test, so no test file holds a complete secret-shaped string.
- Bad events are built from a valid event with fields removed or damaged.
- No test data is stored outside `tests/`.
- Each test builds its own S3 buckets, queue, and email identity. Tests do not share state.
