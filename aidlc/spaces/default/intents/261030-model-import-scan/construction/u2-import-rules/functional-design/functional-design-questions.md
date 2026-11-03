# Functional Design Questions: u2-import-rules

Functional Design describes what the application does for this Unit, as rules a developer can build without guessing. The Unit is u2-import-rules (`unit-of-work.md`). It finishes the rules around the working slice that u1-import-slice built. Most of its behaviour is already settled in `requirements.md`, `stories.md`, and `contract-summary.md`. Some of it also changed while u1 was designed and built: the wait for a running import is now a queue that u1 already has, the stored-model marker (`_COMPLETE`) and the import record and outcome objects under `state/<importId>/` already exist, and a failed email already raises an alert. The questions below cover only the points that are still open and need your judgement.

## Q1. [Product owner] What are the outcome names for a rejected event and for a model that is already stored?

Every import must end in exactly one outcome (AC5.1.4). u1 has three outcome values today: `stored`, `removed-with-email`, and `removed-email-failed`. The stories add two cases that end before any download: an event that is missing a field or is malformed (AC1.1.2), and a model version that is already stored (AC1.1.3). Both send an email. The contract left open whether they share one outcome name (contract C4, open question).

A. Two new outcomes: `rejected` for a bad event and `refused-already-stored` for a duplicate. The team can count and alert on each one separately (recommended)
B. One new outcome, `refused`, for both cases; the reason in the record tells them apart
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q2. [Product owner] When does the 6-hour limit start counting?

An import that has not finished 6 hours after it started is stopped and treated as failed (NFR1, AC5.1.2). Events wait in a queue when another import is running, so "started" can mean two things. With about 5 imports a day, each normally under 1 hour, the wait is usually short, but it can be longer if an import runs long.

A. From the moment the application takes the event off the queue and begins the import; time spent waiting in the queue does not count (recommended)
B. From the moment the event was sent; time spent waiting in the queue counts, so a waiting event can fail without ever starting
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q3. [Developer] What should a failed import do with older unfinished files that sit where the model would be kept?

The kept model is marked complete by a `_COMPLETE` file written last. A model folder without that file is unfinished. If an earlier import crashed halfway through the move, it can leave such files behind. A new import of the same model version may overwrite them. Now suppose that new import also fails during the move. The rule from u1 says the removal never deletes an object this import did not create (rule BR5.2), but the final location must show either the complete model or nothing from this import (AC4.1.2).

A. Remove every file under that model version's folder when it has no `_COMPLETE` file, because an unfinished folder is never a model anyone should use; the failure email says how many were removed (recommended)
B. Remove only the files this import copied; older unfinished files stay and are named as leftovers in the failure email
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q4. [Product owner] What does the email for a rejected event show of what the sender typed?

When an event is rejected, the email says why (AC1.1.2). The values in a bad event come from whoever sent it, so they can be wrong or contain anything. The model name and version that pass the format check are safe to repeat. A field that fails is never repeated.

A. Name each field that is missing or malformed, and never repeat any value from the event; the email gives only the event's id and time so the team can find it in the logs (recommended)
B. Name each field that is missing or malformed, and also show the model name and version when they pass the format check, so the sender can match the email to their request
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q5. [Product owner] Should the first version keep a record of every import, including rejected events?

The story for a record of each import (US6.1) is a could-have, and you chose it as the one extra for the first version. u1 already writes an import record and a single outcome object to the holding bucket under `state/<importId>/` for every import that starts. What is missing is the list of findings (file, place, kind; never a value) in that record, and a record for events that end before the import starts (a rejected event, an already-stored model).

A. Build it: add the findings list without values to the record, and write a record and outcome for rejected and already-stored events as well. This is a small change on what u1 built (recommended)
B. Leave it out: the logs hold the outcome and reason of every import (AC6.1.3), and the records stay as u1 wrote them
C. Not yet defined
X. Other (please specify)

[Answer]: A
