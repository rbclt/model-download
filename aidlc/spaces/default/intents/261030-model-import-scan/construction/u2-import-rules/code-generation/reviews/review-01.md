## Review

**Verdict:** READY
**Reviewer:** aidlc-architecture-reviewer-agent
**Date:** 2026-11-02
**Iteration:** 1
**Verdict Dictated:** no

### Findings

| ID | Severity | Location | Finding | Required action | Status |
|---|---|---|---|---|---|
| R-01 | Minor | src/model_import/custodian.py > `_remove_final` (MarkerCheckError branch) and src/model_import/notifier.py > `MARKER_NOTE` | When the marker cannot be checked during a removal (the HEAD or GET fails with something other than "not found"), the removal leaves the final location alone and reports the marker key as a Leftover. The failure email then says "the final location may hold a complete copy of this version although the import failed". In this case the marker may not exist and the files this import copied may be only some of the model. They are not deleted, and the final bucket never expires them. The design (security-design.md, item 3) chose "leave alone and report", so the build follows it, but the email text can mislead the team. | Give this case its own note, for example "the marker could not be checked, so files of this import may remain in the final location", or add a line to the README recovery steps that says the team must look at the folder by hand when the only Leftover is the marker key. | New |
| R-02 | Minor | README.md > "When an import stops unexpectedly", step 2 | Step 2 says to remove the files by hand "or send the event again (see step 4)". Step 4 is about the 2-day expiry of the holding bucket. The redrive and the new run are described in step 5. | Change the reference to step 5. | New |

### Validation Tool Results

| Tool | Result | Interpretation |
|---|---|---|
| `.venv\Scripts\python -m black --check src infra tests app.py` | 44 files would be left unchanged | Matches the code summary |
| `.venv\Scripts\python -m ruff check src infra tests app.py` | All checks passed | Matches the code summary |
| `.venv\Scripts\python -m pytest --cov --cov-fail-under=80 -q` | 540 passed in 281.26 s; total coverage 95.88 percent (floor 80 percent reached) | Matches the code summary, including `store.py` at 89 percent and `worker.py` at 90 percent |

### Summary

I ran the three checks and read the custodian, coordinator, worker, event, notifier, deadline, downloader/retry/hf_client check points, the stack changes, the stack tests, and the key custodian and coordinator tests. The build matches the approved plan and design: the marker is the one commit point, `keep` and `finish_own_marker` never raise after it, `remove` for a marker-written import deletes nothing and returns `model_stored`, and the marker is read first and deleted (3 tries) only when it holds this importId. Nothing in the final location is touched before the keep step starts. The front door order, the five outcomes, the single outcome object with `outcome_already_set`, the deadline made when the worker takes the message off the queue (no check after the marker), the allow-list log fields, the privacy tests with planted values, the unchanged task-role permissions (compared with a recorded list, no `s3:*`, no `Resource: *`) and the added metric filters and alarm texts all agree with the documents. The code summary and README state what was not verified (Docker image, `cdk synth` through the CLI, deployment, SES, a real Hugging Face download, the real multipart copy) and the test and coverage figures it quotes are true. The two remaining points are Minor wording issues. I could not compare the changed u1 tests or `pyproject.toml` with their u1 versions because Git is not installed; I found no lowered threshold or setting in the current files.
