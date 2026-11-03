# Infrastructure Specification: u2-import-rules

This lists what this Unit changes in, or confirms about, the AWS resources of the stack of u1-import-slice. It maps the NFR Design of this Unit onto AWS: the logical components in `logical-components.md`, the designs in `performance-design.md`, `security-design.md`, `scalability-design.md`, `reliability-design.md`, and `observability-design.md`, the building blocks in `components.md`, the steps in `functional-spec.md`, and the boundaries in `contract-summary.md`. The infrastructure of u1 is described in full in `infrastructure-specification.md` of u1-import-slice and is not repeated. All resources belong to the one CDK stack in Python (`staging`), so this Unit adds code to the same stack and owns no resource of its own. No question to the owner was needed: this Unit adds no AWS service, no bucket, no queue, no role, and no permission. Its stack changes are the pattern of one metric filter (alert (a)), more count and value metric filters on the log (listed in `monitoring-design.md`), and the text of one alarm, plus constants in the application code.

## Deployment

| Facet | Choice | Rationale |
|-------|--------|-----------|
| Compute model | Unchanged: the Amazon ECS service on AWS Fargate (desired count 0 to 1, 4 vCPU, 8 GiB, 30 GiB disk). The new code ships in the same container image | Nothing in this Unit needs more compute: the deadline checks, the marker read, and the listing are small calls. The two S3 clients and the SES client are created in the program (see `performance-design.md`, Client Settings) |
| Networking | Unchanged: the public subnets, no NAT gateway, no inbound traffic, the S3 gateway endpoint | The new calls go to S3 and SES as before |
| Storage | Unchanged: the holding bucket (`holding/` and `state/`) and the final bucket (`models/`). The second email counter is one more object under `state/counters/` in the holding bucket | No new bucket (NFR2.11) |
| Environments | Unchanged: one stack named `staging` | Practices: merge deploys to staging; production after manual approval, added later |
| IaC approach | AWS CDK v2 in Python, the same stack class, the same deployment settings. No new setting is added: the limits 20, 30, 200, and 20 are constants in the application code (`scalability-design.md`) | Keeps the deployment unchanged and the owner's list of settings short |
| Resource sizing | Unchanged | NFR1.x and NFR2.x are met by the existing size (`performance-design.md`) |
| Cost estimate (staging) | No change from u1. Rejected and already-stored events make no download and no copy, so each costs a few cents of Fargate time at most and a few S3 requests. The emails are limited to 50 a day by u1 and now to 20 plus 30. The extra S3 listings and marker reads add requests worth far less than a dollar a month | NFR2.8, NFR2.9 |
| Tags | Unchanged: `app = model-import-scan`, `env = staging` | Cost allocation |

## Infrastructure Services

Only the services that change, or whose settings this Unit relies on and confirms, are listed.

| Service | Role | Configuration | Notes |
|---------|------|---------------|-------|
| CloudWatch Logs metric filter `EmailNotReached` (new) | Source of alert (a) | Pattern `event = import_outcome` and `emailSent = false`; metric `EmailNotReached` in the namespace `ModelImport`; alarm (a) is repointed to it (one or more in 5 minutes, same topic and name) | NFR5.18. In the stack as built, alarm (a) reads the per-outcome count filter `OutcomeRemovedEmailFailed`. That filter is **not changed**: it stays a pure count of the outcome `removed-email-failed`, so the count by outcome stays right (NFR4.12). Alert (a) gets its own filter instead, so it also fires for a rejected or already-stored event whose email was not accepted, and for an import that was removed with only one of two emails accepted. This corrects the sentence in `observability-design.md` that says the filter "changes from" the outcome pattern. The filter on the boolean needs the field to be written as a JSON boolean, which the logger does once the field is on the allow-list |
| CloudWatch Logs metric filters for outcomes | Count by outcome | The per-outcome count filters get two more values, `rejected` and `refused-already-stored`, each with its own metric | NFR5.20, NFR4.12. No alarm on them |
| CloudWatch Logs value filters | Durations | Two more value filters read the numeric fields `frontDoorSeconds` and `proofSeconds` of the `import_outcome` line, like `downloadSeconds`, `scanSeconds`, and `keepSeconds`. A third, `DeadlineStopSeconds`, reads `totalSeconds` of the lines whose `reason` is the fixed time-limit text | NFR5.17, NFR1.10 |
| CloudWatch Logs metric filter for alert (b) | Removal not complete | Unchanged. It already counts any `import_outcome` line with `removalComplete = false`, whatever the outcome, so it covers the outcome `stored` with a holding folder that stayed | NFR5.19. A template test confirms the pattern has no condition on the outcome |
| CloudWatch alarms `d-queue` and `d-reminder` on the import dead-letter queue | Alert (d) | Unchanged thresholds. The alarm `d-event` (the event dead-letter queue) is not changed. The description of `d-queue` and `d-reminder` changes to: open `state/<importId>/record.json` first; if its step is `keeping`, look for files without a `_COMPLETE` marker under `models/` for that model version | NFR4.19. This is how the team learns of unfinished files in the final location that do not expire |
| CloudWatch alarm (e) for the email limit | Alert (e) | Same condition and metric. The description text, which in u1 says "the limit of 50 failure emails a day is reached", changes to name both limits: 30 emails a day for failure emails and 20 for rejection and already-stored emails. The log record `email_limit_reached` carries the field `kind` (`failure` or `notice`) to say which one was reached | NFR3.18. The synthesis test checks the new text |
| Task role | The one role of the worker | Unchanged. The calls of this Unit (`HeadObject` and `GetObject` of the marker, listings, `DeleteObject`, conditional `PutObject` of the second counter, the multipart copy calls, `AbortMultipartUpload`, and `ListBucketMultipartUploads`) are covered by the object and multipart actions on `holding/*`, `state/*`, and `models/*` and by `ListBucket` and `ListBucketMultipartUploads` on the two buckets, as u1 built them | NFR3.17. A test compares the generated action and resource list with the list recorded for u1 and fails if it grows |
| Holding bucket | `holding/` and `state/` | Unchanged: `holding/` expires after 2 days, `state/` (records, outcomes, both counters) after 30 days, unfinished uploads abort after 1 day | NFR5.15 (Q1 option A), NFR4.9 |
| Final bucket | `models/` | Unchanged: no expiry, unfinished uploads abort after 1 day. **Files of an unfinished model version (no `_COMPLETE`) in this bucket do not expire** | Stated plainly in `reliability-design.md`; S3 cannot expire "files without a marker", and a rule on `models/` would delete stored models, so none is added |
| Amazon SQS queues | The wait and the dead-letter queues | Unchanged: FIFO queue with one message group, maximum receive count 1, FIFO dead-letter queue with 14-day retention | A person redrives a message from the dead-letter queue to the source queue with the SQS console action "Start DLQ redrive"; the run book written in Build and Test describes it (NFR4.19) |
| Amazon SES | Failure, rejection, and already-stored emails | Unchanged identity and configuration set. The three clients' timeouts are set in the program | NFR3.9 |
| SNS topic and AWS Budgets | Alerts and cost | Unchanged | NFR5.10 |

## Shared Infrastructure

| Shared Resource | Owner Unit | Consumer Units | Access Boundary |
|-----------------|------------|----------------|-----------------|
| The whole stack | u1-import-slice | u2-import-rules | u2 changes the worker code, the pattern of one metric filter, adds count and value metric filters, and changes the description text of the dead-letter alarm. It adds no resource other than those filters, and no role or permission. Both units deploy as one stack |

## Settings the Owner Supplies

No new setting. The settings of u1 (team mailbox, SES sender, break-glass administrator roles, budget limit, `cdk bootstrap`, SNS confirmation, cost tag, SES production access) stay as the owner's open list.

## Assumptions & Open Questions

- [assumption] A CloudWatch Logs metric filter can test a JSON boolean (`$.emailSent = false`), as the filter of u1 already does for `removalComplete`.
- [assumption] The SQS console action "Start DLQ redrive" supports FIFO queues in us-east-1 and keeps the message group. If not, the run book gives the equivalent steps by sending the message body again.
- [assumption] Repointing alarm (a) to the new metric `EmailNotReached` starts its history afresh. The old metric `ImportOutcomeRemovedEmailFailed` keeps its history as a count of that outcome.
- Open question: Whether the account already has a default VPC (open since u1). This Unit does not change the network.
