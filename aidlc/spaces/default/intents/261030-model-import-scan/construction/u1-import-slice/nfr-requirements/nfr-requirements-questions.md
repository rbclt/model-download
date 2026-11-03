# NFR Requirements Questions: u1-import-slice

Non-functional requirements are the measurable qualities of the application, such as how long an import takes, who may send an event, and how long the logs are kept. Several are already fixed and are not asked again: an import must end within 6 hours (NFR1), the current version targets models of about 16 GB (NFR2), nothing secret goes in code or logs (NFR3), every import ends in one outcome (NFR4), and the outcome and reason are in the logs (NFR5), and tests cover at least 80 percent of lines (NFR6). Imports run one at a time, and everything runs in the common AWS account in us-east-1. These questions set the numbers and choices that are still open.

## Q1. [Product owner] How many imports do you expect?

This sets the size of the design. A few imports a week need no special tuning.

A. A few a week, at most 5 a day (recommended)
B. Dozens a day
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q2. [Product owner] How long should a typical import of a smaller model (about 16 GB) take, from the event to the outcome?

The hard limit stays at 6 hours (NFR1). This is the target the design aims for, and the number the tests and the logs are compared with.

A. Within 1 hour (recommended)
B. Within 3 hours
C. No target beyond the 6-hour limit
X. Other (please specify)

[Answer]: A

## Q3. [Security engineer] Who may send the start event?

Anyone who can put an event on the event bus can make the application download a model and email the sender address in the event (Contract Design review finding R-07).

A. Only identities in the common AWS account that are given permission to put events on the bus; nobody outside the account (recommended)
B. Only a named list of roles, which the owner supplies before the first deployment
C. Also identities from other AWS accounts
X. Other (please specify)

[Answer]: A

## Q4. [Security engineer] Must the sender's email address belong to the company?

A. Any well-formed email address is accepted (recommended for the first version; the permission in Q3 already limits who can send an event)
B. Only addresses that end with the company's email domain; the owner supplies the domain
X. Other (please specify)

[Answer]: A

## Q5. [Security engineer] How are the files in S3 encrypted?

Both options encrypt the files at rest, block all public access, and allow only the application to read the holding area (requirements assumption A4).

A. S3-managed keys (SSE-S3): simple, no extra key to manage (recommended)
B. A customer-managed key in AWS Key Management Service: you control and audit the key, and the key needs its own permissions and cost
X. Other (please specify)

[Answer]: A

## Q6. [Operations engineer] How long are the logs of an import kept?

A. 30 days (recommended)
B. 90 days
C. 1 year
X. Other (please specify)

[Answer]: A

## Q7. [Platform engineer] Which Python version should the application use?

A. Python 3.12: supported by all AWS services used here (recommended)
B. Python 3.13: the version installed on this machine
X. Other (please specify)

[Answer]: A

## Follow-up Q8. [Platform engineer] How is the failure email sent?

The team practices say Amazon SNS sends the failure email. SNS emails only people who subscribed to a topic and clicked a confirmation link, so it cannot reach the sender address that arrives in each event. Amazon SES can email any address directly, but it needs a verified sender address in the common AWS account, and while the account is in the SES sandbox every recipient must also be verified.

A. Amazon SES: one email to the sender address in the event and to the team mailbox (recommended)
B. Amazon SNS: the team mailbox is subscribed once; the sender gets the email only if they subscribe and confirm
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

- Failure email (Q8, asked after the summary below was confirmed): Amazon SES sends it to the sender address in the event and to the team mailbox. This replaces the SNS wording in the team practices.
- Volume (Q1): a few imports a week, at most 5 a day.
- Typical import time (Q2): within 1 hour for a smaller model (about 16 GB). The hard limit stays at 6 hours.
- Who may send the start event (Q3): only identities in the common AWS account that are given permission to put events on the bus. Nobody outside the account.
- Sender email address (Q4): any well-formed email address is accepted.
- Encryption (Q5): S3-managed keys. Public access is blocked and only the application reads the holding area.
- Log retention (Q6): 30 days.
- Python version (Q7): Python 3.12.

**Confirm** — Does this all look correct before I generate the artifacts?

Looks correct — Generate the artifacts from these answers
Request changes — Revise one or more answers before generation

[Answer]: Looks correct
