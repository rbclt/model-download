# Phase Check: Construction to Operation

**Verdict: PASS WITH ONE EXPLAINED EXCEPTION.** The person decides at the CI Pipeline gate whether the exception is accepted.

Checked at the CI Pipeline stage on 2026-11-02. The plan skips the Operation stages (`aidlc-state.md`), so this transition ends the workflow.

## What was read

- `construction/build-and-test/cross-unit-traceability.md` (written from `aidlc engine sensor-traceability --stage build-and-test`)
- `construction/u1-import-slice/code-generation/traceability.json`
- `construction/u2-import-rules/code-generation/traceability.json`
- `construction/build-and-test/build-and-test-summary.md` and `test-results.md`
- `construction/ci-pipeline/ci-config.md` and `quality-gates.md`

## Both Units are built and tested

| Unit | `traceability.json` | OK | N/A | Deferred | GAP / ORPHAN |
|------|---------------------|---:|----:|---------:|-------------:|
| u1-import-slice | present | 85 | 9 | 1 | 0 |
| u2-import-rules | present | 91 | 34 | 0 | 0 |

Both Units were approved at their checkpoints. The full suite of 540 tests passed at 95.88 percent line coverage, against the floor of 80 percent (Build and Test, run R6).

## The explained exception

The cross-Unit check reports the group `NFR1` as uncovered. The cause is the one `Deferred` row in the u1 file: `NFR1.1` ("u2-import-rules builds the 6-hour stop"). u2-import-rules built it, and its own file has `NFR1.1` as `OK` (`src/model_import/deadline.py` and the deadline checks in the other modules). The u1 files were approved earlier and are not edited, so the check keeps seeing the `Deferred` row. No code is missing. The cross-Unit gate therefore did **not** pass as the check reports it; it passes when this row is read together with the u2 row.

## Unresolved items that are not traceability findings

These are known and were accepted or left open by the person at earlier gates. They do not change the traceability result.

- Build and Test: 3 targets `Not Met` and 19 `Unverified` (the person chose "Accept failure" at that gate). The list is in `construction/build-and-test/build-and-test-summary.md`.
- Wording points in earlier design documents, recorded in the code summaries of both Units (for example, log event names written with dots in the NFR documents and with underscores in the code).
- Not verified anywhere: the container image build, a pipeline run, deployment to AWS, SES sending, a real Hugging Face download.

## Do the CI gates enforce the build and test commands?

Yes, as designed. Gates G1 to G4 and G7 of `construction/ci-pipeline/quality-gates.md` use the same commands Build and Test recorded, including `python -m pytest --cov --cov-fail-under=80`. No gate has run in a real pipeline, because the pipeline is not written yet.
