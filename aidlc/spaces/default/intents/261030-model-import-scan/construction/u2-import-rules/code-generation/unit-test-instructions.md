# Unit Test Instructions: u2-import-rules

How to run the tests of this Unit. The strategy is Standard: 5 to 8 tests for each changed building block, plus integration tests at the key boundaries and end-to-end tests for the new paths (`code-generation-plan.md`). The team writes each layer first and then its tests (test-after). This Unit changes code of u1-import-slice, so it also runs the tests of u1 that still apply, and it changes a few of them (a bad event is now rejected with an email; a failure after the marker no longer removes the model). Each such change is named in the code summary with the rule that required it.

## Setup

The environment was set up by u1 and is reused.

1. Python 3.12 is the target. This machine has Python 3.13.2; the code avoids anything that differs between the two, and the pipeline runs 3.12.
2. The virtual environment `.venv` and the pinned test tools (pytest, pytest-cov, moto, respx, Black, Ruff, and `aws-cdk-lib` for the template tests) are installed as in u1:

```text
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

3. This Unit adds no dependency. If one is needed, the build stops and reports it.

## Commands for this Unit

Run the tests of this Unit and the tests it changes (the first run, before the coverage check). The new and changed tests of this Unit are under the same folders as u1's; the run below covers the whole suite, because the Unit changes shared code and a narrower filter would miss a break in u1's tests:

```text
.venv\Scripts\python -m pytest tests/unit tests/integration tests/e2e tests/infra -q
```

Run the same tests with a coverage report for the changed code (no threshold, so a low number never stops the run):

```text
.venv\Scripts\python -m pytest tests/unit tests/integration tests/e2e tests/infra -q --cov=src/model_import --cov=infra --cov-report=term-missing
```

Format and lint checks:

```text
.venv\Scripts\python -m black --check src infra tests app.py
.venv\Scripts\python -m ruff check src infra tests app.py
```

The project check recorded for every Unit is `.venv\Scripts\python -m pytest --cov --cov-fail-under=80`, run from the project root. It checks the 80 percent line-coverage floor on the whole suite.

## Expected Coverage

- The floor is 80 percent of lines for the whole project (NFR6.1, NFR6.5). Build and Test checks it on the full suite. The threshold is set in `pyproject.toml` and is never lowered to make a run pass.
- Target for the code this Unit changes (`custodian.py`, `coordinator.py`, `notifier.py`, `event.py`, `worker.py`, `store.py`, `deadline.py`): 90 percent or more. u1 ended at 94.5 percent overall, and the total must not fall below the floor.

## What Is Tested

| Area | Kind | Notes |
|------|------|-------|
| Record, outcome, and counters | Unit | Five outcomes; new fields; size of a record with 200 findings and 1,000 files; two counters with their limits on the UTC day |
| Deadline | Unit | Injected clock; `check` at, before, and after the deadline; retry wait cut short |
| Message classifier and event check | Unit | Start event, rejected event with every failed field, not a start event; optional `time`; `_COMPLETE` path check without case; the address hidden from the text form |
| Marker reader | Unit | `own`, `other`, `missing`, `unreadable`, and a failing `HEAD` or `GET` raised as a failed check |
| Keep and the marker | Unit and integration | Marker last; a failure after the marker never removes the model; the holding folder delete failing after the marker |
| Removal and proof | Unit and integration | The forced-failure proof of `functional-spec.md`; Leftovers; `olderFilesRemoved`; nothing touched before the keep step; the removal run twice |
| Notifier | Unit | Rejection email naming fields and no values; already-stored email; per-role result; the 20 and 30 limits |
| Coordinator | Unit | Front door order; redriven message with its own marker; `run.stored` guards; end-of-import order; five outcomes; one log line; `outcome_already_set` |
| Worker loop | Integration | Bad event, duplicate, repeated message, time-limit stop, a message that is not a start event |
| Whole flow | End-to-end | Rejected event, refused duplicate, deadline stop, failed move leaves nothing in the final location |
| Privacy | Unit | A planted sender address and a planted secret in every field of a bad event are searched for in every log record, record, outcome, marker, and email text |
| CDK stack | Template tests | Role action list unchanged; alert (a) and (b) filters; five outcome count filters; the deadline-stop filter and its constant; lifecycle rules; alarm descriptions |
| Rule coverage | Unit | A test lists every rule ID BR1.1 to BR5.6 and BR6.1 to BR11.3 and finds a test that names it |

## Mocking and Stand-ins

- AWS services: moto, with a fresh set of resources for every test through fixtures. Where a call must fail, a thin wrapper client around the moto client raises the chosen error at the chosen call. No test calls a real AWS account.
- Hugging Face: respx routes, as in u1.
- Time: every deadline and wait uses an injected clock; no test waits for real hours or sleeps.
- Secrets and addresses in tests: planted values are fake and built in the test (split in two parts where a scanner might flag them).

## Test Data

- Fake models are built in the test, as in u1. Bad events are built from a valid event with one or more fields removed or damaged.
- No test data is stored outside `tests/`.

## Assumptions & Open Questions

- [assumption] moto supports every S3 call the code uses, including the conditional put used by the counters and the outcome. A call it does not support is replaced by a small stand-in object for that call, and the test says so.
- Open question: Whether a real end-to-end run against the services is made by hand once the deployment settings exist (open since u1). The automated suite never does.
