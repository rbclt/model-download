# NFR Requirements Questions: u2-import-rules

NFR Requirements turns the quality targets into numbers a developer can build and test. Most of them are already fixed. The inception NFRs in `requirements.md` set the 6-hour limit (NFR1), the smaller-model size (NFR2), no secret in code or logs (NFR3), exactly one outcome (NFR4), the outcome in the logs (NFR5), and 80 percent test coverage (NFR6). u1-import-slice already set the volume, the security settings, the logs, the alerts, and the tech stack for the whole application, and u2-import-rules runs inside the same application and the same AWS account, so it reuses them. The Functional Design of this Unit (`functional-spec.md`, `rules.md`) already fixed the deadline check points, the 10-minute target after the deadline, the limits of 20 leftovers and 200 findings, and the size of the records. Two points remain that need your judgement.

## Q1. [Product owner] How long should the records of imports be kept?

The record of each import (what was imported, the outcome, the reason, and the findings without their values) is the history that user story US6.1 asks for. u1 stores each record in the holding bucket under `state/` and, as built, deletes it after 30 days, the same time the logs are kept (u1 NFR5.6). The records are small: about 5 imports a day is under 2,000 records a year, and they hold no secret or personal value. This Unit now writes a record for rejected and already-stored events too.

A. Keep 30 days, the same as the logs; nothing changes from what u1 built (recommended)
B. Keep 1 year; one setting changes in the infrastructure, and the logs still go after 30 days
C. Keep until someone removes them by hand
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q2. [Platform engineer] What should happen when a running import stops unexpectedly?

An import can stop without finishing: the program crashes, the server running it is replaced, or the 7-hour backstop of u1 ends it. u1's NFR4.2 said the immediate clean-up and failure email for such a run would be built in this Unit, together with the 6-hour rule. The approved Functional Design of this Unit defined something smaller (rules BR10.1 and BR7.1): the stopped import's message goes to a dead-letter queue (a holding queue for messages that failed), the team is alerted, the files left in the holding area expire by themselves within 2 days, and a person can send the message again, which then finds its own earlier work and finishes. The sender gets no email in that case until the import is run again. Stops like this should be rare, since the 6-hour rule ends a slow import in a controlled way long before the 7-hour backstop.

A. Keep it as designed: the team is alerted, the files expire within 2 days, and a person sends the message again; nothing more is built (recommended; the first version is due soon and this is a rare case)
B. Build an automatic clean-up: a small extra function reacts to a message in the dead-letter queue, removes that import's files, and sends the failure email; this adds a new part with its own permissions and tests, and the Functional Design would need a new rule for it
C. Not yet defined
X. Other (please specify)

[Answer]: A
