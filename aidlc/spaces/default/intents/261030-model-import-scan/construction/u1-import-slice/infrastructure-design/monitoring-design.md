# Monitoring Design: u1-import-slice

This is the AWS-specific monitoring that implements `observability-design.md` for the thin end-to-end slice. It uses the resources in `infrastructure-specification.md`, the logical components in `logical-components.md`, the requirements in `observability-requirements.md`, the lifecycle in `functional-spec.md`, and the boundaries in `contract-summary.md`. All alarms notify the SNS topic `model-import-alerts`, which has the team mailbox subscribed.

## Metrics & KPIs

| Metric | Source | Threshold | Why it matters |
|--------|--------|-----------|----------------|
| `ImportOutcome` by outcome (stored, removed-with-email, removed-email-failed) | Embedded-format metric in the worker logs | Each import adds exactly one | Shows success and failure counts and proves one outcome per import (NFR4.1, NFR5.7) |
| `ImportTotalSeconds`, `DownloadSeconds`, `ScanSeconds`, `KeepSeconds` | Embedded-format metrics | Total over 3,600 seconds for a model up to 16 GB is a miss of NFR1.2 | Checks the time targets (NFR1.2 to NFR1.5) |
| `BytesDownloaded` | Embedded-format metric | Average under 20 MB/s is a miss of NFR1.3 | Checks download speed |
| `FindingsTotal` | Embedded-format metric | Any value above 0 means an import failed on findings | Shows how often the scan fails imports |
| `EmailsSent` and `email_limit_reached` | Embedded-format metric and log record | Limit of 50 a day | NFR3.16 |
| Queue depth and age of the oldest message | SQS metrics `ApproximateNumberOfMessagesVisible` and `ApproximateAgeOfOldestMessage` | Age over 8 hours alarms (stuck) | Shows waiting events and a task that cannot start |
| Dead-letter queue depth | SQS metric on both dead-letter queues | One or more | NFR4.11 |
| Running task count | ECS Container Insights metric `RunningTaskCount` (Container Insights is switched on for the cluster) | Not 0 or 1 | One import at a time (NFR2.3) |
| Monthly cost | AWS Budgets | The set limit | NFR5.10 |

## Alerts

| Alert | Condition | Severity | Routes to |
|-------|-----------|----------|-----------|
| (a) Failure email did not go out | Metric filter: `outcome = removed-email-failed`, one or more in 5 minutes | High | Alert topic |
| (b) Removal not complete | Metric filter: `removalComplete = false`, one or more in 5 minutes | High | Alert topic |
| (c) Worker stopped with an error | A rule on the default event bus for the ECS event "task state change": the task stopped in this cluster with a non-zero exit code, or with stop code `TaskFailedToStart` (an image pull error has no exit code); sent to the alert topic. The worker exits with code 0 on a termination signal while idle (a scale-in stop, see `scalability-design.md`), so a normal scale-in raises no alert | High | Alert topic |
| (d) Message in a dead-letter queue | Two alarms. For the event dead-letter queue (filled by a normal send from EventBridge): sum of `NumberOfMessagesSent` at one or more in 1 minute. For the queue dead-letter queue (filled only by the redrive policy, which `NumberOfMessagesSent` does not count): a metric-math alarm on `RATE(ApproximateNumberOfMessagesVisible)` above 0 in 1 minute, so every new arrival alerts even while older messages still sit there; a second alarm on `ApproximateNumberOfMessagesVisible` of one or more for 60 minutes reminds the team to empty the queue | High | Alert topic. The text tells the team to check the outcome object of the import first |
| (e) Email limit reached | Metric filter: `event = email_limit_reached`, one or more in 5 minutes | Medium | Alert topic |
| (f) SES bounce or complaint | SES configuration set event destination to the alert topic | Medium | Alert topic |
| (stuck) Oldest message waits too long | `ApproximateAgeOfOldestMessage` over 10 hours | Medium | Alert topic. Outside the 15-minute target by design. The age includes the time a message waits behind a running import (up to 7 hours), so the limit is 10 hours to avoid a false alarm for a second import that waited behind a long first one |
| (cost) Budget | AWS Budgets at 100 percent of the limit | Medium | Alert topic |

Alerts (a) to (f) use periods of 1 to 5 minutes, so they reach the team mailbox within the 15 minutes of NFR5.8.

## SLIs / SLOs

| SLI | SLO target | Measurement window |
|-----|------------|--------------------|
| Share of imports of models up to 16 GB that end within 1 hour | 90 percent | The first 10 imports, then a rolling 30 days |
| Share of imports that end with exactly one outcome record | 100 percent | Every import |
| Time from a failure to the alert reaching the team mailbox | Under 15 minutes | Every alert |
| Share of failed imports whose removal was complete | 100 percent; any miss raises alert (b) | Every failed import |

## Logs & Tracing

- **Metric path:** the worker writes each JSON record to standard output. The `awslogs` log driver sends it to the log group. The metrics in the table above are made by CloudWatch Logs metric filters on the JSON fields (a filter on `outcome` counts outcomes, and a filter with the metric value taken from a field such as `$.totalSeconds` gives each duration). This replaces the embedded-metric envelope named in `observability-design.md`, because metric filters are a documented path that needs no agent and no extra permission; the task role needs no log or metric permission.
- **Logs:** one JSON record per line from the worker to the log group `/model-import/worker`, 30-day retention. The allow-list logger in `observability-design.md` decides the fields. Container start-up output goes to the same group with the stream prefix `container`.
- **Tracing:** none. One program runs an import end to end, and the `importId` in every record is the correlation id.
- **Dashboards:** none in this version (NFR5.9). A saved CloudWatch Logs Insights query by `importId` is documented in the run book written in Build and Test.

## Assumptions & Open Questions

- [assumption] CloudWatch metric filters accept the structured fields used above, because the logs are JSON.
- [assumption] The SLO targets are starting figures chosen by the architect and are reviewed after the first 10 imports.
- Open question: The alarm names and the exact message texts are set in Code Generation.
