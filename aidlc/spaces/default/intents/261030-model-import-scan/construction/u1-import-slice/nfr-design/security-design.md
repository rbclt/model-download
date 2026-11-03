# Security Design: u1-import-slice

This describes how the thin end-to-end slice meets the requirements in `security-requirements.md`. It uses the components in `logical-components.md`, the rules in `functional-spec.md`, the technology in `tech-stack-decisions.md`, the targets in `performance-requirements.md`, `scalability-requirements.md`, `reliability-requirements.md`, and `observability-requirements.md`, and the boundaries in `contract-summary.md`. The queue-based design is Q2 B in `nfr-design-questions.md`. The application holds no secrets of its own: it needs no password, key, or token to run.

## Design Decisions

| Requirement | Design |
|-------------|--------|
| NFR3.1 No secrets in code, logs, or emails | The application has no secret to store. Settings that differ by environment (team mailbox address, sender identity, bucket names) are passed to the worker as plain environment settings at deployment. The logger writes only fields from an allow-list (see `observability-design.md`), so an unexpected value cannot reach a log. Secret-pattern checks run over the repository in CI |
| NFR3.2 No Hugging Face credential | The download client is created without a token and the worker ignores any `HF_TOKEN` or similar setting in its environment |
| NFR3.3 Only permitted identities can send events | The event bus is a custom bus with a resource policy that allows `events:PutEvents` only to principals of this account and denies every principal of another account. Identities in the account still need their own permission to put events on the bus. The rule matches only the source and detail type of contract C1 |
| NFR3.4 One role, closed list | The worker's task role has exactly these permissions and no others. **S3, objects:** `GetObject`, `PutObject`, `DeleteObject`, `AbortMultipartUpload`, and `ListMultipartUploadParts` on `holding/*`, `models/*`, and `state/*`; the conditional create of `state/<importId>/outcome.json` uses `PutObject` on `state/*`. **S3, listing:** `ListBucket` and `ListBucketMultipartUploads`, limited by a prefix condition to those three prefixes. **Queue:** `ReceiveMessage`, `DeleteMessage`, and `ChangeMessageVisibility` on the import queue ARN only. **Scale-in protection:** `ecs:UpdateTaskProtection` on the tasks of this cluster only. **Email:** `ses:SendEmail` on the verified identity ARN, with conditions on the from address and on the configuration set. **Logs:** `logs:CreateLogStream` and `logs:PutLogEvents` on the worker's log group only. No metrics permission is needed, because metrics are written through the log format. There is no `s3:*` and no `Resource: *`. Isolation between imports is enforced in code (BR5.2) and tested, including the `state/` prefix |
| NFR3.5 Encryption and TLS | Both S3 locations use S3-managed encryption. A bucket policy denies any request that does not use TLS (`aws:SecureTransport` is false) |
| NFR3.6 No public access | S3 Block Public Access is switched on for the account's buckets of this application |
| NFR3.7 Only the application reads | A bucket policy denies the object data actions (`GetObject`, `PutObject`, `DeleteObject`) to every principal except the worker's task role, the CDK roles that deploy the stack (the deploy role and the CloudFormation execution role), and the administrator roles named in a deployment setting for break-glass use. The setting must name at least the owner's administrator role; the deployment fails if it is empty, so someone can always inspect the buckets and do the hand clean-up the reliability design relies on. The deny does not cover bucket configuration actions, so a stack update or delete is never blocked. Adding a reader is a change to that setting by the owner |
| NFR3.8 No values in findings, logs, emails | The scanner returns file, place, and kind only (BR3.5); file names are cleaned (control characters replaced, cut to 200 characters) before use (BR5.4); the email builder takes a fixed set of fields |
| NFR3.9 SES email | The notifier sends one separate message to each recipient from the verified identity, through a configuration set that sends bounces and complaints to the alert topic |
| NFR3.10 Input validation at the boundary | The program checks the event body against contract C1 and the path rules of BR1.3 before touching S3 or the network |
| NFR3.11 Files are never run | The scanner and downloader open files as bytes or text only. No library that can run code from a file is imported. Pickle-based weight formats are findings (BR3.1) |
| NFR3.12 Dependencies | Exact versions in `pyproject.toml`; `pip-audit` runs in CI and fails the build on a known critical vulnerability |
| NFR3.13 Certificate check and hash | The HTTP client verifies certificates (the default) and never turns verification off; a SHA-256 hash from the listing is compared after each download; a mismatch counts as an incomplete file (BR2.5, BR2.4) |
| NFR3.14 Header-safe sender address | The address must match a strict pattern of a single `local@domain` with no whitespace, comma, semicolon, or control character, at most 254 characters; a failing event starts nothing (BR1.1) |
| NFR3.15 TLS 1.2, versioning off | A bucket policy denies requests with TLS older than 1.2 (`s3:TlsVersion`); versioning is left off |
| NFR3.16 SES prerequisites and limits | A deployment check reads the SES account status and the verified identities and fails the deployment to a production stage if the sender or team mailbox is not verified or the account is in the sandbox; the notifier keeps a durable per-day counter in `state/counters/emails-<date>.json`, updated with a conditional write on the object's ETag so two updates cannot overwrite each other, and counts every attempt to send. When an attempt would be the 51st of the day, it is not sent, one log record with `event = email_limit_reached` is written, and signal (e) alerts the team within minutes. The counter objects expire after 30 days |

## Threats and Controls

| Threat | Control in this design |
|--------|------------------------|
| A flood of events from a permitted identity | The queue serialises imports; the cost alarm and the 50-emails limit bound the damage (see `scalability-design.md`) |
| A path or name escapes its folder | BR1.3 checks in code; the role cannot write outside `holding/*` and `models/*` |
| A secret found in a model is copied | The scan (BR3.3, BR3.6) fails the import and removal runs (BR5.2) |
| A stolen worker role | The role reaches only the two prefixes, the one queue, SES from one identity, and logs |

## Assumptions & Open Questions

- [assumption] The break-glass administrator role names are supplied by the owner as a deployment setting. Until they are supplied, only the worker's role and the deployment role can read the buckets.
- [assumption] The deployment check for NFR3.16 runs in the deployment pipeline of a later stage; in this version it is a script the owner runs before the first real import.
- Open question: A separate role for each building block is a later hardening step (see `security-requirements.md`).
