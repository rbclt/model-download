# Observability Requirements: u2-import-rules

This describes what the finished application must record so people can tell what happened to every start event, once this Unit is added. It details inception NFR5 (visibility) and NFR6 (quality) in `requirements.md` and uses the rules in `rules.md` (BR10.1 to BR10.5, BR11.1 to BR11.3), the lifecycle in `functional-spec.md`, and the contracts in `contract-summary.md`. The logging, metrics, and alerts of u1-import-slice (NFR5.1 to NFR5.10) stay, except where a requirement below changes one. The retention of records comes from Q1 in `nfr-requirements-questions.md`. Each requirement carries the ID of the inception NFR it details.

## Logging

| ID | Requirement | Source |
|----|-------------|--------|
| NFR5.11 | For every start event, one log line with the event name `import.outcome` is written just before the outcome object is created (BR10.2). It holds importId, outcome, reason in plain words, removalComplete, emailSent, findingsTotal, olderFilesRemoved, and the durations of the steps that ran, and no other field. It covers rejected and already-stored events too. After a stop and a send again, the same line can appear twice with the same content. This refines NFR5.2 of u1. | NFR5 |
| NFR5.12 | When the outcome object already exists at creation, one line `outcome.already_set` with the importId is written and nothing else (BR10.1, BR10.2). | NFR5 |
| NFR5.13 | When an email is not accepted, one line `email.not_sent` is written with the kind of problem (service-error or daily-limit), the role that was not reached (sender or team), and the importId. It never holds an address (BR10.3, NFR3.20). | NFR5, NFR3 |
| NFR5.14 | When a removal leaves leftovers, the full list is logged, each with kind, area, and cleaned location. The email shows at most 20 (BR9.4). | NFR5 |
| NFR5.15 | The records and outcomes under `state/` are kept 30 days, the same as the logs (NFR5.6 of u1). The expiry rule built in u1 stays unchanged (owner's decision, Q1 option A). | NFR5, Q1 |
| NFR5.16 | The log allow-list of u1 gets exactly the fields of NFR5.11, NFR5.12, and NFR5.13 and no others. A field outside the allow-list is dropped. | NFR5, NFR3 |
| NFR5.17 | The duration of the front door (NFR1.11) and of the proof listing (NFR1.13) are logged for every import, so the time targets can be checked. | NFR5, NFR1 |

## Metrics and Alerts

| ID | Requirement | Source |
|----|-------------|--------|
| NFR5.18 | Alert (a) of NFR5.8 fires for any `import.outcome` line with emailSent false, so it also covers a rejected or already-stored event that the email did not reach. Until then it fired only for the outcome removed-email-failed. | NFR5 |
| NFR5.19 | Alert (b) of NFR5.8 fires for any `import.outcome` line with removalComplete false, whatever the outcome, so it also covers the outcome stored with a holding area that could not be fully deleted (BR9.6) and a failed import that left the marker in place (NFR4.16). | NFR5 |
| NFR5.20 | For every start event the outcome is logged in a way that lets the team count rejected and refused-already-stored events next to stored and removed ones, for example by a metric filter on the outcome field. No alert is raised for their number alone in this version. | NFR5 |
| NFR5.21 | Every alert of NFR5.8 reaches the team mailbox within 15 minutes, unchanged from u1. | NFR5 |

## How Each Requirement Is Verified

| IDs | Verified by |
|-----|-------------|
| NFR5.11 to NFR5.14, NFR5.16, NFR5.17 | Unit tests that run each outcome with stand-ins, read the log records, check the fields and values, and plant a secret and an address and search every record for them |
| NFR5.15 | A deployment check of the 30-day expiry of `state/` in the synthesised infrastructure |
| NFR5.18, NFR5.19, NFR5.20 | Deployment checks of the metric filters and alarms in the synthesised infrastructure; tests that produce signals (a) and (b) for a rejected event and for a stored model with removalComplete false |
| NFR6.5 to NFR6.8 | The Construction verification command, the Black and Ruff checks, and the rule-coverage test described below |

## Quality Targets for the Build

| ID | Requirement | Source |
|----|-------------|--------|
| NFR6.5 | Tests still cover at least 80 percent of the lines of the whole application after this Unit is added. The coverage check runs in the project check recorded for the Unit (`.venv\Scripts\python -m pytest --cov --cov-fail-under=80`). | NFR6 |
| NFR6.6 | Each business rule of this Unit (BR6.1 to BR11.3 in `rules.md`) has at least one test that names its ID. The rule-coverage test of u1 lists the new IDs too. | NFR6 |
| NFR6.7 | Every case of the forced-failure proof in `functional-spec.md` has its own test. Tests use injected clocks and stand-ins, never real waiting and never a real AWS account. | NFR6 |
| NFR6.8 | Code is formatted with Black and passes Ruff with no errors, and the tests of u1 still pass unchanged except where a changed u1 rule requires a changed test. Each such test change is named in the code summary. | NFR6 |

## Assumptions & Open Questions

- [assumption] Alerts keep going to the team mailbox through the Amazon SNS topic of u1.
- [assumption] The metric filters of u1 read the log field names. Infrastructure Design checks that the filters for signals (a) and (b) can be widened as NFR5.18 and NFR5.19 ask.
- Open question: Is an alert on a burst of rejected events wanted later? None is added in this version (NFR5.20).
