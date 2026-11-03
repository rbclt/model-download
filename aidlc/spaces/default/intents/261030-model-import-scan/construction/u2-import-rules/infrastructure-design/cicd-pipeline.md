# CI/CD Pipeline: u2-import-rules

This describes what this Unit changes in the delivery pipeline of u1-import-slice. It uses the resources in `infrastructure-specification.md`, the monitoring in `monitoring-design.md`, the designs in `security-design.md`, `reliability-design.md`, `performance-design.md`, `scalability-design.md`, and `observability-design.md`, the components in `logical-components.md`, `components.md`, and `functional-spec.md`, and the boundaries in `contract-summary.md`. The team practice is unchanged: a merged change deploys to staging automatically, and production only after a manual approval. The pipeline itself is built in the CI Pipeline stage after Build and Test; this document fixes what it must do for this Unit, and it keeps the stages, the prerequisites, and the deployment strategy of u1 (`cicd-pipeline.md` of u1-import-slice) unless it says otherwise.

## Blocks the First Deployment

No new prerequisite. The eight prerequisites of u1 (repository, CI platform, Docker, `cdk bootstrap`, verified mail identities and SES status, break-glass roles, SNS confirmation, a small public model) still decide when the first deployment can happen. Nothing in this Unit adds an item to that list.

## Pipeline Stages

The stages are those of u1 (source, install, format and lint, test, dependency check, secret scan, synthesise, image check, readiness check, deploy, smoke check, production approval). The table lists the stages that gain a check because of this Unit.

| Order | Stage | What is added for this Unit | Gate to pass |
|-------|-------|-----------------------------|--------------|
| 3 | Format and lint | None. The new modules are covered by the same `black --check` and `ruff check` | No findings (NFR6.8) |
| 4 | Test | The same command, `python -m pytest --cov --cov-fail-under=80`, now also runs the forced-failure tests of `functional-spec.md`, the tests for the deadline with an injected clock, the rule-coverage test for BR6.1 to BR11.3, and the tests that search for planted values (a sender address, a secret in each field of a bad event) in every log record, record, outcome, marker, and email text | All tests pass, coverage at least 80 percent for the whole application (NFR6.5, NFR6.6, NFR6.7, NFR3.19, NFR3.20) |
| 5 | Dependency check | None. This Unit adds no dependency | No known critical vulnerability |
| 7 | Synthesise | The synthesis test adds: (1) the list of IAM actions and resources of the task role equals the list recorded for u1, and the search for `s3:*` and `Resource: *` still finds none (NFR3.17); (2) alarm (a) reads the new metric `EmailNotReached`, whose filter tests `emailSent = false`, while the filter `OutcomeRemovedEmailFailed` is still a pure count of that outcome, and the count filters include `rejected` and `refused-already-stored` (NFR5.18, NFR5.20, NFR4.12); the filter `DeadlineStopSeconds` matches the application's `REASON_TIME_LIMIT` text; the description of alarm (e) names 30 and 20 and the descriptions of `d-queue` and `d-reminder` name the record to open; (3) alert (b) has no condition on the outcome (NFR5.19); (4) the value filters for `frontDoorSeconds` and `proofSeconds` exist (NFR5.17); (5) the lifecycle rules still expire `holding/` after 2 days and `state/` after 30 days (NFR5.15, NFR4.9) | No such permission and no missing filter or rule |
| 11 | Smoke check | Unchanged flow with the model of prerequisite 8, with one added check that needs no new grant: the check reads the `_COMPLETE` marker in the final bucket, which the smoke-check role of u1 may already read, and checks that it holds the model name, the version, and the file list with sizes that match the files of the model. It does not read `state/` or the log group: the outcome object, the `import_outcome` line, `removalComplete`, and the order of those writes are proved by tests in stage 4 with stand-in storage. The bad-event and duplicate paths are not sent in the smoke check, because each sends an email and the 20 emails a day for rejected and already-stored events should not be used up by merges; those paths are proved by tests in stage 4. (The u1 check that the holding bucket is empty needs list access to it that the role description of u1 does not state; the CI Pipeline stage settles the grants of that role for the checks of u1 and this Unit together.) | The check passes within 90 minutes |

## Deployment Strategy and Rollback

- **A deploy while an import runs.** Unchanged from u1: the pipeline waits for the task count to reach 0 before the deploy, with its own time limit of 8 hours, and a timed-out wait leaves the stage red and alerts.
- **Strategy.** Unchanged: replacement of the service from desired count 0 to 1 with the new image.
- **Rollback and the new objects.** A rollback redeploys the last successful commit and never touches the buckets. This Unit writes objects that the build of u1 does not know: outcome values `rejected` and `refused-already-stored`, the fields `emailSent` and `recipientsNotReached`, the findings in the record, a marker with the model name and version, and the second email counter. The build of u1 only checks that an outcome object exists (a `HEAD`), so a rolled-back u1 build still treats a repeated message as finished. It does not read the new outcome values. A test of this Unit reads an outcome object written by u1 and a record written by u1, so a forward move from u1 to u2 also works.
- **Rollback of a model stored by a bad build.** A model stored by a faulty u2 build has a marker. The owner removes it by hand, following the run book written in Build and Test, if a stored model is found to be wrong.

## Environment Promotion

Unchanged: staging is the only environment of the first version. A production stack uses the same code with a different stack name, deploys only after the manual approval, and uses its own verified SES identity and team mailbox. The limits of 20 and 30 emails a day are constants in the code and the same in every environment.

## Secrets Management in CI/CD

Unchanged: the application holds no secrets, and the pipeline uses the role-based access of the CI platform to the AWS account. The tests that plant secrets and addresses use fake values written in the test code, and the build log prints none of them.

## Assumptions & Open Questions

- [assumption] The CI platform defaults to AWS CodePipeline with AWS CodeBuild, as in u1; the CI Pipeline stage confirms it with the owner.
- [assumption] The list of task-role actions recorded for u1 can be produced from the synthesised template of the u1 build, so the comparison in stage 7 has a baseline. If it cannot, Code Generation records the list from the stack as it is before this Unit's changes.
- [assumption] The smoke check stays on every merge, as in u1.
- Open question: Whether a daily scheduled bad-event smoke check should be added to prove the rejection path against the real services. It is not added in this version, to keep the emails of rejected events for real events.
