# Infrastructure Specification: u1-import-slice

This maps the NFR Design of the thin end-to-end slice onto AWS resources. It uses the logical components in `logical-components.md`, the designs in `performance-design.md`, `security-design.md`, `scalability-design.md`, `reliability-design.md`, and `observability-design.md`, the building blocks in `components.md`, the steps in `functional-spec.md`, and the boundaries in `contract-summary.md`. The answers are Q1 to Q3 in `infrastructure-design-questions.md`. All resources belong to one AWS CDK stack in Python, so u2-import-rules adds code to the same stack and no resource is owned by a second Unit. Complete CDK code and policy documents are written in Code Generation.

## Deployment

| Facet | Choice | Rationale |
|-------|--------|-----------|
| Compute model | One Amazon ECS service on AWS Fargate, Linux on x86_64, desired count 0 to 1; one task = 4 vCPU, 8 GiB memory, 30 GiB local disk; container image built from the project by CDK | Containers run up to 6 hours (Q1 A of NFR Design); x86_64 avoids cross-building the image on a Windows machine; 8 GiB is above the 4 GiB floor of NFR1.9 and gives headroom for 4 parallel files |
| Networking | A new VPC with 2 public subnets in two Availability Zones, an internet gateway, no NAT gateway; the task gets a public address; its security group allows no inbound traffic and all outbound traffic; a free S3 gateway endpoint keeps S3 traffic inside AWS | Q3 A; the worker needs the public internet for Hugging Face; no fixed monthly network cost |
| Storage | Two S3 buckets: holding (`holding/` and `state/`) and final location (`models/`); S3-managed encryption; Block Public Access on; versioning off; the bucket policies and lifecycle rules in the services table | Q2 A; NFR3.5 to NFR3.7, NFR4.9 |
| Environments | One stack named `staging` in the common AWS account in us-east-1. A production stack is the same code with a different stack name and a manual approval, added later | Q1 A; practices: merge deploys to staging automatically, production after manual approval |
| IaC approach | AWS CDK v2 in Python; one stack class; settings passed as CDK context or environment: team mailbox address, SES sender address, break-glass administrator role ARNs (required, deployment fails if empty), monthly budget limit (default 100 US dollars) | Team choice; NFR3.7, NFR5.10 |
| Resource sizing | See the worker row above and the queue and alarm settings in the services table | NFR1.x, NFR2.x |
| Cost estimate (staging) | Worker about 0.20 US dollars per hour of import (4 vCPU and 8 GiB on Fargate), so 5 one-hour imports a day is about 1 US dollar a day, at most about 30 a month. S3 storage about 0.40 US dollars a month for each 16 GB model kept. Queues, alarms, logs, and SES are a few dollars a month at this volume. No fixed network cost | NFR2.2, NFR5.10; the default budget alarm of 100 US dollars sits well above this |
| Tags | Every resource carries `app = model-import-scan` and `env = staging` for cost tracking | Cost allocation |

## Infrastructure Services

| Service | Role | Configuration | Notes |
|---------|------|---------------|-------|
| Amazon EventBridge custom bus `model-import` | Start events | Resource policy: allow `events:PutEvents` only to principals of this account, deny every principal of another account | NFR3.3; senders also need their own permission |
| EventBridge rule `start-import` | Route start events | Matches source `model.import` and detail type `ModelImportRequested`; target is the import queue with message group id `imports`; retry up to 24 hours and 185 attempts; target dead-letter queue is the event dead-letter queue | NFR4.6, NFR4.11; contract C1 |
| Amazon SQS FIFO queue `import-queue.fifo` | The wait; one at a time | Content-based deduplication on (the EventBridge event, including its unique id, is the body, so a resent event is a new message); visibility timeout 15 minutes, renewed by the worker every 5 minutes; retention 4 days; max receive count 1 to the queue dead-letter queue; SSE-SQS encryption; queue policy allows `sqs:SendMessage` only from the rule's ARN | NFR2.3, NFR4.2. A FIFO queue with a single message group hands out messages one at a time |
| Amazon SQS FIFO queue `import-dlq.fifo` | Queue dead-letter queue | FIFO (required for a FIFO source), retention 14 days, SSE-SQS | NFR4.2, NFR4.11 |
| Amazon SQS standard queue `event-dlq` | Event dead-letter queue | Retention 14 days, SSE-SQS; queue policy allows the rule to send | NFR4.11 |
| CDK asset repository (Amazon ECR) | Holds the container image | The repository created by `cdk bootstrap`; CDK builds and publishes the image into it at deploy time; the image is scanned in the pipeline before the deploy | NFR3.12 |
| Amazon ECS cluster and service `import-worker` | Runs the worker | Container Insights on for the cluster; desired count 0, maximum 1; minimum healthy percent 0 and maximum healthy percent 100, so a deployment never runs a second worker (NFR2.3); deployment circuit breaker on with rollback on (the 30-minute limit on a deployment is set in the pipeline step that runs `cdk deploy`); task role and execution role as below; environment settings: bucket names, queue URL, sender and team addresses; `HF_TOKEN` is not set | NFR2.3, NFR3.2 |
| Application Auto Scaling for the service | Scale 0 to 1 and back | Scale out: a step-scaling policy on an alarm for one or more visible messages for one minute, set to 1 task. Scale in: a step policy on an alarm for no visible and no in-flight messages for 5 minutes (missing data counts as no messages), set to 0 | Scalability design. The alarms are built explicitly (a CloudWatch alarm plus a step-scaling action, not the CDK step-scaling helper alone): the scale-in alarm uses metric math on the visible plus in-flight message counts and treats missing data as breaching. A crashed task with a message in flight keeps that sum above 0, so the service is not scaled in; ECS starts a replacement task, and the message is released or moved to the dead-letter queue within 15 to 20 minutes (see the message life cycle in `reliability-design.md`). The worker sets task protection itself, renewed before each receive, and releases it after the queue has been empty for 2 minutes, which is shorter than the 5-minute scale-in alarm, so an idle task can be stopped |
| Task role | The one role of the worker | Actions exactly as in `security-design.md` row NFR3.4: S3 object and multipart actions on `holding/*` and `state/*` of the holding bucket and on `models/*` of the final bucket; `ListBucket` and `ListBucketMultipartUploads` on the whole of each bucket. This amends NFR3.4, which limited listing by prefix: each bucket holds only this application's data, and a listing right on the whole bucket makes a missing object return "not found" and not "forbidden"; `sqs:ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility` on the import queue; `ecs:UpdateTaskProtection` on tasks of this cluster; `ses:SendEmail` on the identity ARN and on the configuration-set ARN (SES has a from-address condition key but no configuration-set condition key, so the configuration set is listed as a second resource), with the from-address condition; no log permission, because container output goes to CloudWatch Logs through the `awslogs` log driver under the execution role | NFR3.4. No `s3:*`, no `Resource: *`, no metrics permission |
| Task execution role | Pull the image, write container logs | The standard managed execution policy, limited to this repository and log group by CDK | Separate from the task role |
| Holding bucket | `holding/<importId>/` and `state/` | Lifecycle: expire `holding/` after 2 days; expire `state/` after 30 days; abort unfinished multipart uploads after 1 day. Bucket policy: deny non-TLS and TLS below 1.2; deny `GetObject`, `PutObject`, `DeleteObject` to every principal except the task role, the CDK deploy and execution roles, and the break-glass administrator roles, and the dedicated smoke-check role of the pipeline. Removal policy: retain, no auto-delete (the auto-delete helper would be denied by this policy and models must not be lost with a stack) | NFR3.5 to NFR3.7, NFR3.15, NFR4.9 |
| Final bucket | `models/<name>/<version>/` with the `_COMPLETE` marker | No expiry on objects; abort unfinished multipart uploads after 1 day; the same bucket policy as the holding bucket | NFR2.6, NFR3.5 to NFR3.7 |
| Amazon SES | Failure email | Verified sender identity and verified team mailbox in us-east-1; configuration set `model-import` with an event destination for bounces and complaints to the alert topic | NFR3.9, NFR3.16. The identity and the sandbox status are set up by the owner and checked before the first real import (dependency D2) |
| Amazon CloudWatch Logs | Application logs | Log group `/model-import/worker`, retention 30 days | NFR5.6 |
| Amazon SNS topic `model-import-alerts` | Operator alerts | The team mailbox subscribed once; the address is confirmed by the owner by clicking the link in the confirmation email. The topic policy lets the CloudWatch alarms, EventBridge, Amazon SES, and AWS Budgets service principals publish (CDK adds none of these for Budgets and SES) | NFR5.8 |
| AWS Budgets | Cost alarm | Monthly budget with the limit from the deployment setting, default 100 US dollars; alerts the topic at 100 percent. The budget is filtered by the cost allocation tag `app = model-import-scan`; the owner activates that tag once in Billing, and until then the budget measures the whole shared account | NFR5.10 |

## Shared Infrastructure

| Shared Resource | Owner Unit | Consumer Units | Access Boundary |
|-----------------|------------|----------------|-----------------|
| The whole stack (bus, queue, worker, buckets, SES configuration, logs, alerts) | u1-import-slice | u2-import-rules | u2 changes the worker code and adds rules; it adds no new resource except possible alarms. Both units deploy as one stack |

## Settings the Owner Supplies

| Setting | Needed before | Where it comes from |
|---------|---------------|---------------------|
| Team mailbox address and SES sender address | First deployment (D1, D2) | Deployment settings |
| Break-glass administrator role ARNs (at least the owner's) | First deployment | Deployment settings; the deployment fails if empty |
| Monthly budget limit | Optional | Deployment settings; default 100 US dollars |
| SES production access or verified recipients | First real import | Checked by a script before the import (NFR3.16) |
| CDK bootstrap of the account in us-east-1 | First deployment (D4) | One-time `cdk bootstrap` |
| Confirmation of the SNS subscription for the team mailbox | First alert | The owner clicks the link in the email SNS sends |
| Activation of the cost allocation tag `app` | Optional, for the budget filter | Billing console, once |

## Assumptions & Open Questions

- [assumption] x86_64 Fargate tasks in us-east-1 are available at the cost estimated above. The estimate uses on-demand prices and should be checked in the AWS Pricing page before the first deployment.
- [assumption] A FIFO queue with content-based deduplication accepts messages from an EventBridge rule target when the rule sets a message group id. If the deployment shows otherwise, an EventBridge Pipe between the bus and the queue is used and the behaviour above does not change.
- [assumption] The task role can set task protection with a request to the container's task protection endpoint using `ecs:UpdateTaskProtection`; the worker renews protection on every loop before it receives a message.
- Open question: Whether the account already has a default VPC that could be used instead of creating a new one. Q3 A creates a new one, which is independent and cheap.
