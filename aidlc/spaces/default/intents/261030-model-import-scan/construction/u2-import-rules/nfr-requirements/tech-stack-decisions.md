# Tech Stack Decisions: u2-import-rules

This lists the technology this Unit uses and why. This Unit builds on the code of u1-import-slice and runs in the same application and the same AWS account, so it adds no new service, no new library, and no new permission. It follows the team practices (`team-practices.md`), the requirements (`requirements.md`), and the rules and steps in `rules.md` and `functional-spec.md`, and it uses the boundaries in `contract-summary.md`. The full list of choices for the application is in `tech-stack-decisions.md` of u1-import-slice.

## Decided

| Area | Choice | Why |
|------|--------|-----|
| Language and tools | Python 3.12, AWS CDK v2 in Python, Black, Ruff, pytest and pytest-cov, moto, boto3, pip-audit | Unchanged from u1; team choices (feasibility Q5, Q6; interview Q3) |
| Deadline and clock | The application's own clock, injected into the code so tests can move it; no workflow timer | The 6-hour rule needs check points inside downloads and copies (NFR1.10), which a timer outside the program cannot give; tests must not wait for real hours (NFR6.7) |
| Completion marker | The object `_COMPLETE` in the model version's folder, written last, with JSON content: importId, model name, exact version, and time | The marker is the commit point (NFR4.13) and must tell a redriven message that the stored model is its own work (BR7.1) |
| Outcome and record | The same objects under `state/<importId>/` in the holding bucket that u1 built (record.json and outcome.json, the outcome created with a condition) | No new store; one more value set for the outcome and a few more fields (NFR4.12, NFR5.11) |
| Email counters | The per-day counter object of u1 plus a second counter object for rejected and already-stored emails, both updated with a conditional write | NFR3.18 needs two limits counted apart, and the conditional write is how u1 counts safely |
| Failure, rejection, and already-stored emails | Amazon SES from the verified sender of u1, one email for each recipient | Unchanged from u1 (NFR3.9 of u1) |
| Alerts | Amazon CloudWatch alarms and metric filters to the SNS topic of u1, with two filters widened (NFR5.18, NFR5.19) | The only infrastructure change this Unit asks for |
| Unexpected stop of a run | Alert, expiry after 2 days, and sending the message again, as u1 built it | Owner's decision (Q2 option A); no extra function and no new permission |
| Records retention | 30 days for records and outcomes under `state/`, as u1 built it | Owner's decision (Q1 option A) |

## Open Choices for Later Stages

| Decision | Options | Decided in |
|----------|---------|------------|
| How the deadline reaches the download loop and the copy loop | An injected clock object passed to the downloader and the custodian, checked at each chunk or part | NFR Design |
| How the copy of a file larger than 5 GiB is done in parts, so the deadline can be checked in it | The multipart copy of the storage client, one part at a time | NFR Design |
| The exact result for a marker that cannot be read or parsed, and for a model file named `_COMPLETE` | Refuse as already stored with a log note, or fail the import with a plain reason | NFR Design |
| How the second email counter is kept | A second object `state/counters/emails-rejected-<date>.json`, or one object with two numbers | NFR Design |
| How the widened alerts read the log | Metric filters on the `emailSent` and `removalComplete` fields | Infrastructure Design |
| Which test double stands in for the S3 listing that fails | A moto failure injection or a small wrapper client | Code Generation |

## Assumptions & Open Questions

- [assumption] Nothing in this Unit needs a library that is not already pinned in `pyproject.toml`. If Code Generation finds that it does, that is raised before it is added.
- [assumption] The conditional write used for the email counter is available on the S3 operations that u1 already uses (u1 built it and tested it with moto; the real service is not tried).
- Open question: Which AWS CDK v2 and boto3 versions are pinned? They are already chosen in u1; this Unit keeps them.
