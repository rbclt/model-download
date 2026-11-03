# Model import and scan

This application imports a public Hugging Face model, scans it for leaked secrets and personal
data, and keeps it or removes it.

1. An event on the `model-import` Amazon EventBridge bus names a model and an exact version.
2. The worker downloads every file of that version into a holding S3 bucket.
3. It scans the files. Text and settings files are searched for secrets and personal data. Weight
   files are not read for content; their file type is checked.
4. A clean model is moved to the final S3 bucket and a `_COMPLETE` marker is written last.
5. If anything fails, or the scan finds anything, everything downloaded is removed and an email
   goes to the team mailbox and to the sender.

Each event ends in exactly one of five outcomes: `stored`, `removed-with-email`,
`removed-email-failed`, `rejected`, or `refused-already-stored`. See "The five outcomes" below.
It runs as one task at a time on AWS Fargate and is deployed with AWS CDK in Python.

Nothing here has been run against a real AWS account, and the Docker image has not been built.
See "What has not been verified".

## The start event (contract C1)

```json
{
  "source": "model.import",
  "detail-type": "ModelImportRequested",
  "detail": {
    "modelName": "owner/name",
    "exactVersion": "<40 characters of 0-9 and a-f>",
    "senderEmail": "person@example.com"
  }
}
```

- `modelName` is `owner/name`. Letters, digits, dot, underscore and hyphen only.
- `exactVersion` is the full 40-character commit hash, not a branch or tag.
- `senderEmail` is one plain address.
- A start event that fails these checks is rejected. Nothing is downloaded. One email goes to the
  team mailbox, and one to the sender when `senderEmail` is present and well-formed. The email
  names each failed field (`modelName`, `exactVersion`, `senderEmail`) as "missing" or "not in the
  expected form", and gives the event id and the event time. It never repeats a value from the
  event. The outcome is `rejected`.
- A message that is not a start event at all (wrong `source` or `detail-type`, a body that cannot
  be read, or no event id or `detail`) gets no email and no outcome. It is logged as
  `event_rejected` with the name of the field that failed, and the message is deleted from the
  queue.
- A model version that is already stored is refused before any download. See
  `refused-already-stored` below.

## Set up

Python 3.12 or newer is needed.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

On macOS or Linux, activate with `source .venv/bin/activate`.

## Check the code

```powershell
python -m pytest --cov --cov-fail-under=80
black --check src infra tests app.py
ruff check src infra tests app.py
```

The pytest command is the project check. It fails below 80 percent line coverage. The tests need
no AWS account and no network: S3, SQS, SES and Hugging Face are stand-ins. The full run takes
several minutes. Run `pip-audit` to check the pinned dependencies.

## Settings the owner supplies

Pass each as CDK context (`-c name=value`) or as an environment variable. A context value wins.

| Setting | Environment variable | Notes |
|---------|----------------------|-------|
| `teamAddress` | `MODEL_IMPORT_TEAM_ADDRESS` | Team mailbox. Gets every failure email. Required |
| `senderAddress` | `MODEL_IMPORT_SENDER_ADDRESS` | SES sender address. Must be verified in SES. Required |
| `breakGlassRoleArns` | `MODEL_IMPORT_BREAK_GLASS_ROLE_ARNS` | Comma-separated administrator role ARNs that can still inspect the buckets. Required, at least one |
| `budgetLimitUsd` | `MODEL_IMPORT_BUDGET_LIMIT_USD` | Monthly budget limit in US dollars. Default 100 |
| `stackName` | `MODEL_IMPORT_STACK_NAME` | Default `staging` |
| `smokeCheckRoleArn` | `MODEL_IMPORT_SMOKE_CHECK_ROLE_ARN` | Optional. Role used by the pipeline smoke check |

A missing or invalid setting stops `cdk synth` and names every problem at once.

## Before the first deployment

| # | Prerequisite | Who |
|---|--------------|-----|
| 1 | A Git repository on a hosting service, with a main branch | Owner |
| 2 | A CI platform (default: AWS CodePipeline with AWS CodeBuild) | Owner |
| 3 | Docker (or a compatible tool) on the machine that builds the image | Owner |
| 4 | `cdk bootstrap` of the common AWS account in us-east-1 | Owner |
| 5 | Team mailbox and SES sender identity verified; SES out of the sandbox, or recipients verified | Owner |
| 6 | Break-glass administrator role ARNs | Owner |
| 7 | Confirm the SNS alert subscription by clicking the link in the email (after the first deployment) | Owner |
| 8 | A small public model for the smoke check | Owner |

Rows 1 to 3 are needed for the pipeline to run at all. See
`aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/infrastructure-design/cicd-pipeline.md`.

## Build and deploy

```powershell
npm install -g aws-cdk
cdk bootstrap aws://<account-id>/us-east-1
cdk synth  -c teamAddress=team@example.com -c senderAddress=sender@example.com -c breakGlassRoleArns=arn:aws:iam::<account-id>:role/<admin-role>
cdk deploy -c teamAddress=team@example.com -c senderAddress=sender@example.com -c breakGlassRoleArns=arn:aws:iam::<account-id>:role/<admin-role>
```

Replace the placeholders with your own values. The image is built from the project root with the
`Dockerfile`. Its base image defaults to a tag. In the pipeline, pass a reviewed image digest
with `--build-arg BASE_IMAGE=python:3.12-slim@sha256:<digest>`.

The names of the buckets are shown as stack outputs (`HoldingBucketName`, `FinalBucketName`).
The names of the event bus, queues, log group and alert topic are fixed, so the stack allows one
environment per AWS account and Region.

## Send a test event

Use a small public model. Replace the model, the hash and the address. The hash below is an
example only; use the real commit hash of the version you want.

```powershell
aws events put-events --region us-east-1 --entries file://event.json
```

with `event.json`:

```json
[
  {
    "EventBusName": "model-import",
    "Source": "model.import",
    "DetailType": "ModelImportRequested",
    "Detail": "{\"modelName\":\"owner/name\",\"exactVersion\":\"0123456789abcdef0123456789abcdef01234567\",\"senderEmail\":\"person@example.com\"}"
  }
]
```

The first task starts about 3 minutes after the event reaches the queue.

## The five outcomes

Every start event that the worker finishes handling ends in exactly one outcome. The outcome is
written once, to `state/<importId>/outcome.json`, and never changed. One log line named
`import_outcome` is written just before it, with the outcome and the reason in plain words.

| Outcome | What happened | What it means for you |
|---------|---------------|-----------------------|
| `stored` | The scan found nothing, every file was copied, and the `_COMPLETE` marker was written. | The model is in the final bucket. No email is sent. |
| `removed-with-email` | The import failed (a download problem, a finding, the time limit, a failed copy, or another failed step). Everything it created was removed, and every email was accepted by the email service. | The sender and the team mailbox were told why. Nothing of the import should be left. |
| `removed-email-failed` | The import failed and was removed, but at least one of the two emails (sender, team) was not accepted: the email service refused it, or a daily limit was reached. | The files are removed, but someone may not know. Read `emailSent` and `recipientsNotReached` in the outcome object. Alert (a) fires. |
| `rejected` | The start event had a missing or malformed model name, version, or sender address. Nothing was downloaded. | The team mailbox (and the sender, if the address was well-formed) got an email naming the failed fields. Send a corrected event. |
| `refused-already-stored` | The model version already has a `_COMPLETE` marker from another import, or a marker that cannot be read. Nothing was downloaded. | The sender and the team mailbox were told where the model is. No new import is needed. |

Notes:

- A failed import is `removed-with-email` only when every email was accepted. If any one was not,
  it is `removed-email-failed`.
- A `rejected` or `refused-already-stored` event keeps its outcome name even when its email was not
  accepted. The outcome object then has `emailSent: false`.
- `stored` can carry `removalComplete: false`. That happens when the model was kept but the
  holding folder of the import could not be fully deleted afterwards. The model stays stored, no
  email is sent, and alert (b) fires so the team removes the leftovers by hand.
- Once the `_COMPLETE` marker is written, nothing that follows can remove the model or change its
  outcome from `stored`.
- If a failed import cannot delete everything, the email lists up to 20 leftovers (kind, area, and
  location) with the total, and says the team should remove them by hand. If the removal also
  deleted older unfinished files of an earlier import of the same version from the final location,
  the email says how many. The log has one `leftover` line for each leftover, up to 200, and the
  total in `leftoversTotal` on the `import_outcome` line.

## Email limits

The limits are fixed in the code and count each email to one recipient as one. The day is the UTC
day.

| Emails | Limit a day |
|--------|-------------|
| Failure emails of imports that ran | 30 |
| Rejection emails and already-stored emails | 20 |

The two counts are kept apart, so a burst of bad events cannot use up the emails about failed
imports. Together they stay within 50 a day. An email past its limit is not sent. The event still
ends with its outcome and a log line, `email_limit_reached` (its field `kind` is `failure` or
`notice`), and alert (e) fires. The outcome object records `emailSent: false` and the role that was
not reached (`sender` or `team`), never an address. The application adds no retry of its own for an
email.

## The 6-hour limit

Each import has a deadline of 6 hours. The clock starts when the worker takes the event off the
queue. Time spent waiting in the queue does not count.

- The deadline is checked before the import moves to a new step, before each file is downloaded,
  scanned, or copied, before each download retry (a retry wait is cut short so it never passes
  the deadline), inside a download at every chunk, and inside the copy of a file larger than 5 GiB
  at every part.
- When the deadline is reached at one of these points, the import fails with the reason "the time
  limit of 6 hours passed". Everything is removed and the failure email goes out
  (`removed-with-email`, or `removed-email-failed`).
- Some work cannot be interrupted: one copy call of a file up to 5 GiB, the scan of one text file
  (which has its own limit of 5 minutes), and the removal and the email. The aim is that the
  import has stopped its work no later than 10 minutes after the deadline. This is a design target.
  It has been tested only with an injected clock.
- The deadline is never checked after the `_COMPLETE` marker is written, so a model that is
  complete is never undone by the clock.
- A message that a person redrives from the dead-letter queue starts a new clock.
- The 7-hour watchdog of the worker stays as a backstop one hour later.

## Reading a record and an outcome

Both are JSON objects in the holding bucket, under `state/<importId>/`. They are history only:
nothing reads them to decide what an import does next, and a removal never deletes them. They
expire after 30 days.

The `importId` is `imp-` followed by the first 32 hex characters of the SHA-256 of the event id
(the id the event bus gave the event):

```powershell
python -c "import hashlib,sys; print('imp-' + hashlib.sha256(sys.argv[1].encode('utf-8')).hexdigest()[:32])" <eventId>
```

Every log line of an import carries `importId`, and `eventId` too when the event id has the usual
form (letters, digits, dot, colon, underscore and hyphen, up to 128 characters). The rejection email
gives the event id. The logs are in the log group `/model-import/worker`.

`state/<importId>/record.json` is rewritten before each step of an import:

| Field | Meaning |
|-------|---------|
| `importId`, `eventId` | The import, and the event id when it has the usual form |
| `modelName`, `exactVersion` | The model, only when each value passed the check. A rejected event may have neither (`null`) |
| `status`, `step` | Where the import is: `downloading`, `scanning`, `keeping`, `removing`, or `done`, and the step in plain words |
| `startedAt`, `updatedAt` | When the worker took the event, and the last write |
| `filesCopied`, `lastKeyCopied` | Progress of the copy to the final bucket |
| `outcome`, `failureReason` | Set only after the outcome object exists, so a record never shows `done` without an outcome |
| `findings` | At most 200 findings, in the order file path then place. Each has `fileName`, `place`, and `kind`. There is no matched value, no part of one, and no text around it |
| `findingsTotal` | The count of all findings of the scan, including those not listed. The full list is in the logs |

A rejected or already-stored event has no record until its end. Its record is written once, with
status `done` and its outcome, after the outcome object exists. If a stop happens in between, the
outcome stands and the record is missing; the `import_outcome` log line holds the same facts.

`state/<importId>/outcome.json`:

| Field | Meaning |
|-------|---------|
| `importId`, `outcome` | The import and one of the five outcomes |
| `removalComplete` | `true` when nothing was left behind (and when nothing had been created). `false` when the check after the removal still found something, or a delete failed |
| `finishedAt` | When the outcome was set |
| `failureReason` | Plain words. Not present for `stored` |
| `emailSent` | `true` when every email owed was accepted, `false` when one or more was not. `null` for `stored`, which sends none |
| `recipientsNotReached` | The roles whose email was not accepted: `sender`, `team`. Never an address |

If the outcome object exists, the import is finished. A message that is received again after that
does nothing.

## When an import stops unexpectedly

If the worker stops in the middle of an import (the task is stopped, the 7-hour watchdog ends it,
or a fatal error occurs), the import is not cleaned up by itself. The queue allows one receive, so
the message moves to the dead-letter queue `import-dlq.fifo` and alerts `d-queue` (new message)
and `d-reminder` (still waiting after 60 minutes) fire. A person recovers it:

1. Find the `importId` (see above) and read `state/<importId>/record.json` in the holding bucket.
   Look at `status` and `step`. If `state/<importId>/outcome.json` exists, the import had already
   finished and there is nothing to recover.
2. If the step is `keeping` or `removing`, look in the final bucket under
   `models/<lowercase owner/name>/<exactVersion>/` for files. A version folder **without** a
   `_COMPLETE` marker is not a stored model. Files in the final bucket never expire, so remove
   those files by hand, or send the event again (see step 4).
3. If the folder has a `_COMPLETE` marker whose `importId` is the one you are recovering, the
   model was stored and only the clean-up was missing. Redriving the message finishes it as
   `stored`, with no email.
4. Check the 2-day expiry of the holding bucket. Files under `holding/<importId>/` expire 2 days
   after they were written, and unfinished multipart uploads are aborted after 1 day. A stop
   before the keep step leaves files only there. The records under `state/` expire after 30 days.
5. Fix the cause, then redrive the message: in the Amazon SQS console open `import-dlq.fifo` and
   choose "Start DLQ redrive" to send it back to `import-queue.fifo`. The dead-letter queue keeps
   messages for 14 days. A redriven message starts afresh with a new 6-hour clock. If the final
   location holds a marker with its own `importId`, it finishes as `stored`. If there is no marker,
   the import runs again from the start, and may overwrite files that an earlier attempt left. A
   second failure email for the same import is possible if the first attempt had already sent one.

A failed import that ended with an email is recovered by sending the same start event again after
the cause is fixed. If the email says the removal was not complete, remove the listed leftovers by
hand. If it says the final location may hold a complete copy although the import failed, look at
the marker: the version counts as stored while the marker exists, and a later event for it is
refused as already stored.

## Reading the final bucket

A model is complete only when its marker exists. The marker is written last.

- Models are stored at `models/<lowercase owner/name>/<exactVersion>/<path>`.
- A reader must check that `models/<lowercase owner/name>/<exactVersion>/_COMPLETE` exists, then read
  only the files it lists.
- The marker is JSON: `importId`, `modelName`, `exactVersion`, `files` (each with `path` and
  `sizeBytes`), and `completedAt`. It holds no sender address.
- Without the marker, treat the version as not stored. Files without a marker come from an import
  that was removed, is still running, or stopped unexpectedly (see "When an import stops
  unexpectedly"). They do not expire.
- A version folder with no marker does not count as stored: a new event for that version is not
  refused, and the import may overwrite those files.

## Known limits

- Model names with a single name part (no `owner/`) are rejected.
- Any finding fails the whole import. There is no way to accept a finding.
- Files with other endings than the known text, settings and weight types are kept, but not
  scanned. This is the owner's choice.
- One import runs at a time. Other events wait in the queue. A second event for the same model
  version that waits behind the first is refused once the first has stored the model.
- An unexpected stop is not cleaned up automatically. A person recovers it (see "When an import
  stops unexpectedly").
- The email limits (30 and 20 a day) and the 6-hour limit are constants in the code. They are not
  settings.

## What has not been verified

- Nothing has been run against a real AWS account. No stack was deployed, and no event was sent.
- The Docker image has not been built. Docker is not confirmed on this machine, so the image build
  is left to the pipeline.
- `cdk synth` through the CDK command line was not run. The template tests build the stack in
  Python.
- SES sending, the sandbox status, and the verified sender identity were not tried. The tests use a
  stand-in for SES.
- No real Hugging Face download was made. The tests use a stand-in host.
- The copy of a file above 5 GiB in parts was tested only with a stand-in, because the S3 stand-in
  rejects the small parts that the test needs.
- The tests ran on Python 3.13.2. The target is Python 3.12.

The details for each Unit are in `code-summary.md` under
`aidlc/spaces/default/intents/261030-model-import-scan/construction/u1-import-slice/code-generation/`
and `.../u2-import-rules/code-generation/`.
#   m o d e l - d o w n l o a d  
 