# Business Rules: u1-import-slice

These are the rules the thin end-to-end slice follows. Each rule is numbered `BRx.y` and traces to a requirement (`requirements.md`) and to the acceptance criteria of the stories this Unit implements (`stories.md`, `unit-of-work-story-map.md`). The answers in `functional-design-questions.md` (Q1 to Q6) fix the scan rules. Rules for a bad event, a duplicate, a waiting event, the 6-hour limit, the proof that nothing is left in the final location after a failed move, the list of leftovers, the record of a failed email, and the single-outcome guarantee belong to u2-import-rules.

```yaml
rules:
  - id: BR1.1
    statement: A valid start event starts exactly one import. An event that fails the check starts nothing.
    category: policy
    applies_to: ImportCoordinator
    trigger: A start event arrives.
    logic: IF the event has a model name, an exact version, and a sender email address, and BR1.3 passes for them, THEN create an ImportRequest with status downloading and ask ModelDownloader to start. ELSE start nothing, download nothing, and write one log line that names which field failed and never shows its value.
    violation: In u1 an event that fails the check is dropped with the log line. The rejection email and the other rejection rules are added by u2-import-rules.
    source: FR1.1, FR1.2

  - id: BR1.2
    statement: Each import has its own identity and holds its outcome.
    category: constraint
    applies_to: ImportRequest
    trigger: An import is accepted.
    logic: THEN generate a unique importId, record startedAt, and keep the outcome empty until the import ends. The importId names the holding area and every record of this import.
    violation: An import without an importId is never started.
    source: FR1.1, NFR4

  - id: BR1.3
    statement: Every name that becomes part of a storage path is checked before anything is written.
    category: validation
    applies_to: ImportCoordinator, ModelDownloader
    trigger: An event is checked (BR1.1), and again when the file list of the model is received (BR2.1).
    logic: >
      The model name has the form owner/name. Each of the two parts uses only letters, digits, dot, underscore, and hyphen, and is not only dots.
      The exact version is exactly 40 characters of 0 to 9 and a to f.
      Each file path from the model listing has no leading slash, no backslash, no control character, no empty segment, no segment of "." or "..", and at most 1024 characters.
      Two file paths that are the same, or the same when letters are compared without case, fail the import.
      The final location uses the model name written in lowercase, so Org/Model and org/model are the same model.
    violation: A model name or version that fails is handled by BR1.1. A file path that fails fails the import through BR5.1 before any file is written.
    source: FR1.1, FR2.2, FR4.2

  - id: BR2.1
    statement: The application downloads every file of the exact version of a public model, without an account or token.
    category: policy
    applies_to: ModelDownloader
    trigger: An ImportRequest is in status downloading.
    logic: THEN list the files of the repository at the exact commit hash with the size of each, check them (BR1.3, BR2.6), and download each file over a plain public connection. No Hugging Face account, token, or other credential is used.
    violation: A file that cannot be downloaded fails the import (BR2.3, BR2.4).
    source: FR2.1

  - id: BR2.2
    statement: Downloaded files are written only to the holding area for this import.
    category: constraint
    applies_to: ModelDownloader
    trigger: A file is downloaded.
    logic: THEN write it under holding/<importId>/ in the common AWS account in us-east-1. Nothing is written to the final location during a download.
    violation: A write outside the holding area is a defect and fails the import.
    source: FR2.2

  - id: BR2.3
    statement: A problem that will not pass on its own fails the import at once.
    category: validation
    applies_to: ModelDownloader
    trigger: The listing or a download answers with a permanent problem.
    logic: >
      Permanent problems: the model needs a login or is private or gated (answers 401 or 403), the model or the commit hash is not found (answer 404 or a "revision not found" answer), any other 4xx answer except 408 and 429, and a listing that gives no size for a file.
      THEN fail the import with no retry and give the reason in plain words.
    violation: The import fails (BR5.1).
    source: FR2.3

  - id: BR2.4
    statement: A temporary download problem is retried up to 3 times per file, then fails the import.
    category: policy
    applies_to: ModelDownloader
    trigger: A file download ends with a temporary problem or an incomplete file (BR2.5).
    logic: >
      Temporary problems: a dropped or reset connection, a timeout, and the answers 408, 429, 500, 502, 503, and 504.
      THEN wait a short while, growing between tries, and download that file again from its start, up to 3 retries for that file. The count is per file, not per import. IF the third retry also fails THEN fail the import.
    violation: The import fails (BR5.1).
    source: FR2.3, FR2.4

  - id: BR2.5
    statement: A file counts as complete only when its received size equals the size in the listing, and an incomplete file is never scanned or kept.
    category: constraint
    applies_to: ModelDownloader
    trigger: A file download ends.
    logic: IF receivedSizeBytes equals expectedSizeBytes THEN mark the file complete ELSE it is a temporary problem (BR2.4). THEN set downloadComplete only when every file is complete. The scan starts only when downloadComplete is true.
    violation: A half-downloaded file never reaches the scan or the final location.
    source: FR2.4

  - id: BR2.6
    statement: A model with no files, or with no weight file, fails the import.
    category: validation
    applies_to: ModelDownloader
    trigger: The file list of the model is received.
    logic: IF the list has no file, or has no file ending in .safetensors, THEN fail the import before any download, with the plain reason "the model has no files" or "the model has no .safetensors weight file".
    violation: The import fails (BR5.1).
    source: FR2.3, FR4.1

  - id: BR3.1
    statement: Every downloaded file gets one class from the last part of its path, case ignored.
    category: policy
    applies_to: LeakScanner
    trigger: The download is complete.
    logic: >
      The class comes from the last segment of the file path, written in lowercase. Its ending is the text after the last dot. A name that starts with a dot and has no other dot (for example .env) has no ending. Only the last ending counts, so model.safetensors.index.json ends in .json.
      weight: ending .safetensors.
      disallowed-weight: ending .bin, .pt, .pth, .pkl, .pickle, .ckpt, .h5, .onnx, .gguf, .msgpack, or .npz.
      scanned-text: ending .json, .jsonl, .txt, .md, .yaml, .yml, .py, .jinja, .toml, .cfg, .ini, .csv, or .tiktoken, or no ending (for example LICENSE or .env).
      ignored: every other file, for example tokenizer.model, .pem, .key, .sh, or .zip. This is the owner's decision in Q2 and Q6: these files are kept unscanned and unchecked.
    violation: A disallowed-weight file is a finding of kind disallowed-weight-type (BR3.6).
    source: FR3.1, FR3.2

  - id: BR3.2
    statement: A weight file is not read for content. Only its header is checked to show it is the expected type.
    category: validation
    applies_to: LeakScanner
    trigger: A file has class weight.
    logic: >
      Read the first 8 bytes as an unsigned number N in little-endian order, then read the next N bytes. The file must be at least 8 bytes long and at least 8 + N bytes long. N must be more than 0 and at most 100 million. The N bytes must be valid UTF-8 text and parse as one JSON object. The rest of the file is not read.
      IF any of these fail THEN record a finding of kind bad-weight-header.
      The text inside the header (its metadata strings) is not scanned for leaks. This is deliberate: the header is read only to show the file type.
    violation: A finding is recorded (BR3.6).
    source: FR3.2

  - id: BR3.3
    statement: Text and settings files are scanned for secrets, credentials, and personal data.
    category: policy
    applies_to: LeakScanner
    trigger: A file has class scanned-text.
    logic: >
      Read the file as text, in parts of 1 MiB so a large file is not loaded whole, and look for these kinds. Each match is a finding of its kind. This is the smallest set of patterns for the first version; Code Generation may add patterns and may not remove one.
      access-key: an AWS access key ID (AKIA or ASIA followed by 16 letters or digits).
      private-key: a line that starts a private key block (-----BEGIN ... PRIVATE KEY-----).
      token: a token with a known prefix (hf_, ghp_, github_pat_, xoxb-, xoxp-, sk-) followed by at least 20 letters or digits, and a three-part JSON web token (three groups joined by dots, the first starting with eyJ).
      password: a name containing password, passwd, pwd, secret, or api_key, followed by = or : and a non-empty value.
      email-address: the usual local-part@domain form.
      phone-number: a number with a leading + and 8 to 15 digits, or the usual 10-digit North American form.
      government-id: a number in the form 123-45-6789.
      street-address: a house number followed by a street name and a street word (Street, St, Avenue, Ave, Road, Rd, Lane, Ln, Drive, Dr, Boulevard, Blvd).
    violation: A finding is recorded (BR3.6).
    source: FR3.1

  - id: BR3.4
    statement: A file that cannot be read or checked counts as a finding.
    category: validation
    applies_to: LeakScanner
    trigger: A scanned-text file is not valid UTF-8 (with or without a byte-order mark) or UTF-16 with a byte-order mark, or any file cannot be opened or read.
    logic: THEN record a finding of kind unreadable-file for that file. The scan never passes a file it could not check.
    violation: A finding is recorded (BR3.6).
    source: FR3.4

  - id: BR3.5
    statement: A finding names the file, the place, and the kind, and never holds the value.
    category: constraint
    applies_to: LeakScanner
    trigger: A finding is recorded.
    logic: THEN store the file name, the place (for example line 12, columns 5 to 30), and the kind. The matched value, any part of it, and the text around it are not stored, logged, or sent.
    violation: A value found in a finding, a log, or an email is a defect.
    source: FR3.5, NFR3

  - id: BR3.6
    statement: The scan covers every file, and any finding fails the import.
    category: policy
    applies_to: LeakScanner
    trigger: The scan runs.
    logic: THEN check every file and collect all findings. IF there is one or more finding THEN the scan result is failed. The ImportCoordinator then stops the import and does not start the keep step. IF the scan itself breaks (a crash or a storage read error that is not about one file) THEN it is a failed step (BR5.1).
    violation: Not applicable.
    source: FR3.3

  - id: BR3.7
    statement: At most 100 findings are recorded for one file.
    category: constraint
    applies_to: LeakScanner
    trigger: A file has more than 100 matches.
    logic: THEN record the first 100 in the order they appear, count the rest, and keep scanning the other files. The total count of all matches is kept with the scan result.
    violation: Not applicable.
    source: FR3.3, FR3.5

  - id: BR4.1
    statement: When every file has passed the scan, all files are moved to the final location and kept.
    category: policy
    applies_to: FileCustodian
    trigger: The scan result is passed.
    logic: >
      In this order. First, IF the final location models/<modelName in lowercase>/<exactVersion>/ already holds any object THEN fail the import with the plain reason "the model version is already in the final location" and delete nothing there. Second, copy every file from holding/<importId>/ to that location and note each object copied. Third, when all are copied, create the StoredModel and remove the holding area.
      A failure in the second or third step is a keep failure and fails the import (BR5.1).
    violation: The import fails (BR5.1). The removal then deletes what this import copied (BR5.2). The refusal of a duplicate before the download and the proof by forced failures that nothing remains are added by u2-import-rules.
    source: FR4.1

  - id: BR4.2
    statement: Nothing is moved to the final location unless the whole model has passed the scan.
    category: constraint
    applies_to: FileCustodian
    trigger: Any step before the keep step.
    logic: THEN the final location is not written. The keep step starts only after the scan result is passed.
    violation: A write to the final location from a scan that did not pass is a defect.
    source: FR4.2

  - id: BR4.3
    statement: A kept model ends the import with the outcome stored.
    category: policy
    applies_to: ImportCoordinator
    trigger: The keep step ends without an error.
    logic: THEN set outcome to stored and status to done. No email is sent.
    violation: Not applicable.
    source: FR4.1, NFR4

  - id: BR5.1
    statement: A finding or a failed step stops the import and starts the removal.
    category: policy
    applies_to: ImportCoordinator
    trigger: The scan result is failed, or the download, the scan, or the keep step reports an error.
    logic: THEN stop the import, set status to removing, remove everything (BR5.2), and send the failure email (BR5.3). The first reason found is the reason; the findings, if any, are listed with it. An error during the removal or during the email never starts the removal again (BR5.5).
    violation: Not applicable.
    source: FR5.1

  - id: BR5.2
    statement: On a failure, all downloaded files and all temporary resources created for the import are removed.
    category: policy
    applies_to: FileCustodian
    trigger: The import is in status removing.
    logic: THEN delete every object under holding/<importId>/, every object this import copied into the final location (BR4.1), every incomplete upload of this import, and any temporary resource created for this import. Never delete an object this import did not create. Then check that nothing of this import is left. Record whether the removal was complete.
    violation: A removal that is not complete is reported in the email as removalComplete = false.
    source: FR5.2

  - id: BR5.3
    statement: One failure email goes to the sender and the team mailbox, with the model, the version, the reason, and whether the removal was complete.
    category: policy
    applies_to: FailureNotifier
    trigger: The removal has ended.
    logic: THEN send one email to the sender's address and the team mailbox address with the model name, the exact version, the reason in plain words, whether the removal was complete, and the findings listed as in BR5.6. Then set outcome to removed-with-email and status to done.
    violation: If the email cannot be sent, see BR5.5.
    source: FR5.4

  - id: BR5.4
    statement: No secret and no personal value appears in an email or in a log, and file names are cleaned first.
    category: constraint
    applies_to: FailureNotifier
    trigger: An email or a log line is written.
    logic: THEN it carries only the model name, the version, the reason, file names, places, and kinds. It never carries a matched value, a token, or a credential. Before a file name goes into an email or a log, control characters are replaced by a question mark and the name is cut to 200 characters.
    violation: Any such value is a defect.
    source: FR5.4, NFR3

  - id: BR5.5
    statement: An error while removing or while sending the email never starts a second removal and never leaves the import open.
    category: constraint
    applies_to: ImportCoordinator
    trigger: A delete fails during the removal, or the email cannot be sent.
    logic: >
      IF a delete fails THEN set removalComplete to false, go on with the other deletes, and go on to the email.
      IF the email cannot be sent THEN write the problem to the log, leave sentAt empty, set outcome to removed-email-failed, and set status to done. In u1 this is an additional outcome value; u2-import-rules records the failed email in full.
      The import always reaches status done after the removal step.
    violation: Not applicable.
    source: FR5.1, FR5.5, NFR4

  - id: BR5.6
    statement: The email lists at most 20 findings, and the logs hold the full list.
    category: policy
    applies_to: FailureNotifier
    trigger: The email is written after a scan with findings.
    logic: THEN write every finding to the logs of the import. List the first 20 in the email, in the order file path then place, and add a line "N findings in total" when there are more than 20.
    violation: Not applicable.
    source: FR5.4, FR3.5
```

## Rules Summary

| Group | Rules | What they cover |
|-------|-------|-----------------|
| 1 Start | BR1.1 to BR1.3 | A valid event starts one import with its own identity; names are checked before any write |
| 2 Download | BR2.1 to BR2.6 | Public download into the holding area, permanent and temporary problems, whole files only, no empty model |
| 3 Scan | BR3.1 to BR3.7 | File classes, weight header check, text scan, unreadable files, findings without values, a cap per file |
| 4 Keep | BR4.1 to BR4.3 | Move after a pass only, outcome stored |
| 5 Fail | BR5.1 to BR5.6 | Stop, remove everything, one email without secrets, errors in the end game |

## Decisions Taken

- Files in the ignored class (for example tokenizer.model, .pem, .key) are kept unscanned and unchecked (owner's choice in Q2 and Q6).
- Any finding of any kind fails the import (requirements FR3.3, owner's choice in intent capture).
- A model with no .safetensors file fails the import (BR2.6). This follows from Q1, where only .safetensors files are weight files, and from Amazon Bedrock Custom Model Import reading that format. The owner can reverse it at the checkpoint.

## Assumptions & Open Questions

- [assumption] The "short wait" before a retry (BR2.4) starts at a few seconds and grows between tries. The exact times are chosen in NFR Design.
- [assumption] The weight-like endings in BR3.1 are a judgement of what Q1 means by "weight-like". The list can be extended without changing the rule.
- [assumption] The pattern set in BR3.3 is the smallest useful set. A tester can plant one example of each kind for AC3.1.1.
- [assumption] The new outcome value removed-email-failed (BR5.5) is an additive change to the shared record in contract C4 (`contract-summary.md`), which its ownership rules allow.
- Open question: Contract review finding R-07: anyone who can put an event on the bus can make the application email any address. Who may publish the event is decided in NFR Requirements and Infrastructure Design (permissions on the event bus). Whether senderEmail must be inside the company domain is a question for the owner and does not block Code Generation.
- Open question: A README with an email address will fail the import under BR3.3 and BR3.6, because any finding fails it. The owner may want to revisit this after real tries (requirements, Open Questions).
