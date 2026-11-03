# Security Requirements: u1-import-slice

These are the security requirements for the thin end-to-end slice. They detail inception NFR3 (no secret, token, or credential in code or logs) in `requirements.md`, and they use the rules in `rules.md`, the steps in `functional-spec.md`, and the boundaries in `contract-summary.md`. The choices come from Q3 to Q5 and Q8 in `nfr-requirements-questions.md`. The team's security policy is the only compliance rule (feasibility Q4), and the security team gives input but does not approve the design before deployment (`team-practices.md`). Each requirement carries the ID of the inception NFR it details.

## Authentication and Authorization

| ID | Requirement | Source |
|----|-------------|--------|
| NFR3.1 | No secret, token, or credential appears in code, configuration files, logs, findings, or emails. Settings that differ by environment, such as the team mailbox address, are read at run time from deployment settings. | NFR3 |
| NFR3.2 | The download from Hugging Face uses no account, token, or other credential (BR2.1). The application never reads a Hugging Face token even if one is present in its environment. | NFR3 |
| NFR3.3 | Only identities in the common AWS account that are given explicit permission can put a start event on the event bus. The bus refuses events from other accounts and from the public. (Contract Design review finding R-07.) | NFR3, Q3 |
| NFR3.4 | The application runs under one role, shared by all five building blocks, which is allowed to do only this: read, write, list, and delete under the `holding/` prefix and the `models/` prefix of the two S3 locations; send email through Amazon SES only from the one verified sender identity; write logs and metrics. It has no `s3:*` and no `Resource: *` permission. A permission policy cannot tell one import's objects from another's, so "only this import's objects" is enforced in code (BR5.2) and tested, not by the policy. | NFR3 |
| NFR3.13 | The HTTPS connection to Hugging Face verifies the server certificate. When the file listing gives a SHA-256 hash for a file, the received file is compared with it, and a mismatch counts like an incomplete file (BR2.5, BR2.4). This refines BR2.5, which compares sizes only. | NFR3 |
| NFR3.14 | The sender address in the event is rejected if it contains a line break, a comma, a semicolon, or more than one address (BR1.1, BR1.3). | NFR3, Q4 |
| NFR3.15 | The minimum TLS version for access to S3 is 1.2. Versioning is off on both locations, so a deleted object is gone. | NFR3, Q5 |
| NFR3.16 | Before the first real import these are true and checked by the owner: Amazon SES is out of the sandbox (or every recipient address is verified), the sender identity is verified in us-east-1, and the team mailbox address is verified. The notifier sends one separate email to each recipient, so a refusal for the sender address does not stop the email to the team mailbox. The application sends at most 50 emails a day; more raises the alert in NFR5.8. SES bounces and complaints go to the team mailbox. | NFR3, Q8 |

## Data Protection

| ID | Requirement | Source |
|----|-------------|--------|
| NFR3.5 | Files in the holding area and the final location are encrypted at rest with S3-managed keys. Every request to them must use TLS; a request without TLS is refused. | NFR3, Q5 |
| NFR3.6 | Both locations block all public access. | NFR3, Q5 |
| NFR3.7 | Only the application's role reads the holding area and the final location. A bucket policy denies every other principal in the account except the account's administrators for break-glass use. Naming another reader is a recorded change made by the owner. | NFR3, requirements assumption A4 |
| NFR3.8 | A finding, a log line, and an email carry only the model name, the version, the reason, file names, places, and kinds. They never carry a matched value, any part of it, or the text around it. File names are cleaned before use (BR3.5, BR5.4). | NFR3 |
| NFR3.9 | The failure email goes through Amazon SES from a verified sender identity in the common AWS account, to the sender address in the event and the team mailbox. The sender address is any well-formed address (Q4); the permission in NFR3.3 is what limits who can trigger an email. | NFR3, Q4, Q8 |

## Input and Content Handling

| ID | Requirement | Source |
|----|-------------|--------|
| NFR3.10 | Every name that becomes part of a storage path is validated before any write (BR1.3). An event that fails the check starts nothing. | NFR3 |
| NFR3.11 | Downloaded files are only read as bytes or text. They are never run, imported as code, or loaded with a library that can run code from the file. This is why pickle-based weight formats are findings (BR3.1). | NFR3 |
| NFR3.12 | Dependencies are pinned to exact versions and checked for known vulnerabilities in the CI pipeline. A known critical vulnerability in a dependency fails the build. | NFR3 |

## Threat Considerations

| Threat | Mitigation |
|--------|------------|
| Someone triggers imports or emails by sending events | NFR3.3: only permitted identities in the account can send events |
| A model repository holds a secret that is copied into the shared account | The scan (BR3.3, BR3.6) fails the import and the files are removed |
| A model file is a pickle that runs code when loaded | Pickle-based weights are findings (BR3.1); files are never loaded (NFR3.11) |
| A path in the model listing writes outside its folder | Path checks in BR1.3 |
| A finding exposes the secret it found | NFR3.8 and BR3.5 |
| Files in S3 are exposed publicly | NFR3.5 to NFR3.7 |
| A tool stack exposes credentials in logs | NFR3.1 and the log rules in `observability-requirements.md` |
| A permitted identity is misused, or an event loop floods the application | At most one import runs at a time, and a second event is not run in parallel (NFR2.3); a monthly cost alarm (NFR5.10); at most 50 emails a day (NFR3.16) |
| A hostile repository attacks the scanner with huge files or lines | Bounds in NFR1.9 |
| A tampered or damaged file of the right size is kept | Certificate check and hash comparison (NFR3.13) |
| SES is used to send mail to arbitrary people | Only permitted identities can send events (NFR3.3); header-safe address check (NFR3.14); daily limit (NFR3.16) |

## How Each Requirement Is Verified

| IDs | Verified by |
|-----|-------------|
| NFR3.1, NFR3.8 | Tests that plant a secret and check that no log, finding, or email holds it; a repository scan for credentials in CI |
| NFR3.2, NFR3.11, NFR3.13 | Unit tests with a stand-in host: no token is sent, no file is loaded as code, a bad hash is a retry then a failure |
| NFR3.3 | A deployment check of the event bus policy; a test event from a role without permission is refused |
| NFR3.4, NFR3.5, NFR3.6, NFR3.7, NFR3.15 | A deployment check of the generated policies and bucket settings; the synthesised infrastructure is searched for `s3:*` and `Resource: *` |
| NFR3.9, NFR3.14, NFR3.16 | Unit tests with a fake email service; the owner's check before the first real import |
| NFR3.10, NFR3.12 | Unit tests of the path checks; the dependency vulnerability check in the CI pipeline (pip-audit) |

## Assumptions & Open Questions

- [assumption] Files left unscanned by the owner's choice (the ignored class, BR3.1) are accepted risk, so this document does not add a requirement to scan them.
- [assumption] The shared role in NFR3.4 is chosen for the first version because it works with every compute option left to NFR Design. A separate role per building block is a later hardening step.
- Open question: Who may read the final location after an import (for example the later step that loads the model into Amazon Bedrock)? NFR3.7 gives the application's role and no one else access; the owner names the readers before the model is used.
- Open question: Is the common AWS account out of the SES sandbox? NFR3.16 makes this a check the owner completes before the first real import (dependency D2 in `external-dependency-map.md`).
