# Cross-Unit Traceability

Stage: Build and Test (`build-and-test`), scope `mvp`. Run date: 2026-11-02. Units covered: `u1-import-slice` and `u2-import-rules`.

Source: the output of `aidlc engine sensor-traceability --stage build-and-test`, run after the Code Generation of both Units was approved. It reads the `traceability.json` of every Unit stage and reports, for every requirement and acceptance criterion, whether it is built, marked not applicable with a reason, or deferred.

Upstream artifacts used (both Units):

- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/code-generation/code-generation-plan.md`, `unit-test-instructions.md`, `code-summary.md`
- `aidlc/spaces/default/intents/261030-model-import-scan/construction/u2-import-rules/code-generation/code-generation-plan.md`, `unit-test-instructions.md`, `code-summary.md`

## Verdict

**FAIL, with one explained exception.** 1 of 28 top-level groups is reported uncovered by the check: `NFR1`. Everything else is covered.

The reported gap is a bookkeeping gap, not a missing feature. The u1 files mark `NFR1.1` (the 6-hour stop) as `Deferred` with the target "u2-import-rules builds the 6-hour stop". u2-import-rules built it (`src/model_import/deadline.py`, plus the deadline checks in `coordinator.py`, `hf_client.py`, `downloader.py`, `retry.py`, `scanner.py` and `custodian.py`), and its own `traceability.json` has `NFR1.1` as `OK`. The u1 files were approved earlier and are not edited, so the check keeps seeing the u1 `Deferred` row as "not built". Whether the 6-hour stop works is checked by the Target Verification Matrix in `build-and-test-summary.md` (target `NFR1.1`, verdict `Met`).

## Requirements (functional)

All 22 functional requirements are covered through their acceptance criteria.

| Requirement | Covered through |
|-------------|-----------------|
| FR1.1 to FR1.5 | AC1.1.1 to AC1.1.4 |
| FR2.1 to FR2.4 | AC2.1.1 to AC2.1.4 |
| FR3.1 to FR3.5 | AC3.1.1 to AC3.1.5 |
| FR4.1, FR4.2 | AC4.1.1 to AC4.1.3 |
| FR5.1 to FR5.3 | AC5.1.1 to AC5.1.5 |
| FR5.4, FR5.5 | AC5.2.1 to AC5.2.3 |
| FR6.1 | AC6.1.1 to AC6.1.3 |

## Acceptance criteria: where each is built

| ID | Owning Unit | Built in |
|----|-------------|----------|
| AC1.1.1 | u1-import-slice | `src/model_import/event.py` |
| AC1.1.2 | u2-import-rules | `src/model_import/coordinator.py` |
| AC1.1.3 | u2-import-rules | `src/model_import/coordinator.py` |
| AC1.1.4 | u1-import-slice | `infra/model_import_stack.py` |
| AC2.1.1 | u1-import-slice | `src/model_import/downloader.py` |
| AC2.1.2 | u1-import-slice | `tests/unit/test_hf_client.py` |
| AC2.1.3 | u1-import-slice | `tests/unit/test_retry.py` |
| AC2.1.4 | u2-import-rules | `src/model_import/deadline.py` |
| AC3.1.1 to AC3.1.5 | u1-import-slice | `src/model_import/scanner.py` |
| AC4.1.1 | u1-import-slice | `src/model_import/custodian.py` |
| AC4.1.2 | u2-import-rules | `src/model_import/custodian.py` |
| AC4.1.3 | u1-import-slice | `src/model_import/custodian.py` |
| AC5.1.1 | u1-import-slice | `src/model_import/coordinator.py` |
| AC5.1.2 | u2-import-rules | `src/model_import/deadline.py` |
| AC5.1.3 | u2-import-rules | `src/model_import/custodian.py` |
| AC5.1.4 | u2-import-rules | `src/model_import/coordinator.py` |
| AC5.1.5 | u2-import-rules | `src/model_import/coordinator.py` |
| AC5.2.1 | u1-import-slice | `src/model_import/notifier.py` |
| AC5.2.2 | u1-import-slice | `src/model_import/notifier.py` |
| AC5.2.3 | u2-import-rules | `src/model_import/coordinator.py` |
| AC6.1.1 | u2-import-rules | `src/model_import/coordinator.py` |
| AC6.1.2 | u2-import-rules | `src/model_import/models.py` |
| AC6.1.3 | u2-import-rules | `src/model_import/coordinator.py` |

AC2.1.2 and AC2.1.3 are recorded in the u1 files against test files (`tests/unit/test_hf_client.py`, `tests/unit/test_retry.py`), not against source files; this is how u1 recorded them.

## Quality requirements (detailed NFR groups)

| Group | Covered | Notes |
|-------|---------|-------|
| NFR1 (performance) | **No (reported)** | u1 `NFR1.1` is `Deferred` to u2, which built it. See the verdict above. u2 marks u1's `NFR1.2`, `NFR1.6` and `NFR1.9` as `N/A` (named for reference, unchanged) |
| NFR2 (scalability) | Yes | u1 `NFR2.1` to `NFR2.6`; u2 `NFR2.7` to `NFR2.12`; u2 marks u1's `NFR2.1` and `NFR2.3` as `N/A` (reference only) |
| NFR3 (security) | Yes | u1 `NFR3.1` to `NFR3.16`; u2 `NFR3.16` to `NFR3.26`; u2 marks `NFR3.1`, `NFR3.3`, `NFR3.4`, `NFR3.5`, `NFR3.7`, `NFR3.8` as `N/A` (reference only) |
| NFR4 (reliability) | Yes | u1 `NFR4.1` to `NFR4.11`; u2 `NFR4.12` to `NFR4.22`; u1's `NFR4.2` is replaced in full by u2 (`N/A` in u2 with that reason) |
| NFR5 (observability) | Yes | u1 `NFR5.5`, `NFR5.7`, `NFR5.8`, `NFR5.10`; u2 `NFR5.11` to `NFR5.21` |
| NFR6 (quality) | Yes | u2 `NFR6.5` to `NFR6.8` |

Being traced to code is not the same as being verified. Which targets are `Met`, `Not Met` or `Unverified` is decided by the Target Verification Matrix in `build-and-test-summary.md`: 85 `Met`, 3 `Not Met`, 19 `Unverified`, 1 `N/A`.

## Uncovered elements to surface at the approval gate

1. `NFR1`: reported uncovered only because of the u1 `Deferred` row for `NFR1.1`, which u2 closed (above). No code is missing.
