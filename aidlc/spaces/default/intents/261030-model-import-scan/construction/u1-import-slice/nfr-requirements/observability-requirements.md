# Observability Requirements: u1-import-slice

This describes what the thin end-to-end slice must record so people can tell what happened to every import. It details inception NFR5 (visibility) and NFR6 (quality) in `requirements.md` and uses the rules in `rules.md`, the lifecycle in `functional-spec.md`, and the contracts in `contract-summary.md`. The retention period comes from Q6 in `nfr-requirements-questions.md`. Each requirement carries the ID of the inception NFR it details.

## Logging

| ID | Requirement | Source |
|----|-------------|--------|
| NFR5.1 | Every status change of an import is logged with the importId, the model name, the exact version, and the time. | NFR5 |
| NFR5.2 | The outcome and the reason of every import are logged exactly once, in one line that can be found by importId. | NFR5, NFR4 |
| NFR5.3 | The full list of findings is logged, each with file name, place, and kind. When a file has more than 100 matches, the total count for that file is logged as well (BR3.7, BR5.6). | NFR5 |
| NFR5.4 | Logs are written as one structured record per line (JSON) so they can be searched by importId, status, and outcome. | NFR5 |
| NFR5.5 | No log line carries a secret, a token, a credential, or a matched value (NFR3.1, NFR3.8). Hugging Face request details that could carry a credential are not logged. | NFR5, NFR3 |
| NFR5.6 | Logs are kept for 30 days. | NFR5, Q6 |

## Metrics and Alerts

| ID | Requirement | Source |
|----|-------------|--------|
| NFR5.7 | For every import, these values are logged so they can be counted: outcome, total duration, bytes downloaded, download duration, scan duration, keep duration, and number of findings. | NFR5, NFR1.2 |
| NFR5.8 | The team mailbox is alerted within 15 minutes through an Amazon SNS topic whenever one of these signals fires: (a) a log record with outcome removed-email-failed; (b) a log record with removalComplete false; (c) the task or workflow that runs an import stops with an error or at the 7-hour backstop; (d) a message arrives in the dead-letter queue of NFR4.11; (e) the limit of 50 emails a day is reached; (f) an SES bounce or complaint. These are the cases where the failure email may not have reached anyone. | NFR5, NFR4.2 |
| NFR5.9 | No dashboard is required in this version. The logs and the failure email are the way to see what happened. | NFR5 |
| NFR5.10 | A monthly cost alarm sends an alert to the team mailbox when the cost of the application passes a limit the owner sets. Until the owner sets one, the default is 100 US dollars a month. | NFR5, NFR2.2 |

## How Each Requirement Is Verified

| IDs | Verified by |
|-----|-------------|
| NFR5.1 to NFR5.5 | Unit tests that run an import with stand-ins and read the log records; a test that plants a secret and searches every record for it |
| NFR5.6 | A deployment check of the retention setting |
| NFR5.7 | A test that checks every logged value is present and numeric |
| NFR5.8 | Deployment checks that each alarm exists and points at the SNS topic; tests that produce signals (a), (b), and (e) with stand-ins |
| NFR5.10 | A deployment check that the budget alarm exists |
| NFR6.1 to NFR6.4 | The Construction verification command (`python -m pytest --cov --cov-fail-under=80`), the Black and Ruff checks in the CI pipeline, and a test that lists every rule ID and finds a test naming it |

## Quality Targets for the Build

| ID | Requirement | Source |
|----|-------------|--------|
| NFR6.1 | Tests cover at least 80 percent of the lines of the application code. The coverage check runs in the project check recorded for each Unit. | NFR6 |
| NFR6.2 | Code is formatted with Black and passes Ruff with no errors. | NFR6 |
| NFR6.3 | Tests include unit tests for each building block, integration tests with fake stand-ins for S3, Hugging Face, and email, and one end-to-end test of the whole flow from event to outcome, as in the Delivery Planning verification command. | NFR6 |
| NFR6.4 | Each business rule (BR1.1 to BR5.6 in `rules.md`) has at least one test that names its ID. | NFR6 |

## Assumptions & Open Questions

- [assumption] Alerts go to the team mailbox through an Amazon SNS topic with the mailbox subscribed once. The failure email itself goes through Amazon SES (Q8), which supersedes the SNS wording in `team-practices.md` for that email only.
- Open question: Is a CloudWatch dashboard wanted later? None is built in this version (NFR5.9).
