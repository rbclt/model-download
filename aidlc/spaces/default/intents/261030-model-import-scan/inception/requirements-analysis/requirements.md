# Requirements

## Intent Analysis

People who need open-weight model files in the shared AWS account have no safe, repeatable way to get them from Hugging Face. The application gives them one path. An event starts an import of a public model, the files are held in Amazon S3 in the common AWS account while they are scanned for data leaks, and every import ends in exactly one of two outcomes: clean files stored in S3, or everything removed and the people responsible told by email (`intent-statement.md`, Problem Statement and Success Metrics).

This document turns the intent statement, the scope document, the team practices, and the answers in `requirements-analysis-questions.md` (Q1 to Q7) into testable requirements. The first version is due at the end of tomorrow, so the requirements stay thin (`scope-document.md`).

## Functional Requirements

### FR1: Start an import from an event

- **FR1.1**: An event that carries the model name and an exact version of that model starts one import. (Q1; `scope-document.md`)
- **FR1.2**: The event also carries the email address of the person who sent it, so the failure email can reach them. (Q6)
- **FR1.3**: If the model name, the exact version, or the sender's email address is missing, or the version is not an exact version, the application rejects the event, downloads nothing, and sends the failure email described in FR5.4. (corner sweep: invalid input)
- **FR1.4**: If an import for the same model name and exact version is already stored, the application refuses the request, downloads nothing, and sends an email saying it is already stored. (Q2)
- **FR1.5**: Only one import runs at a time. An event that arrives while an import is running is not lost: it starts after the running import ends. (`scope-document.md`, Q5 of scope definition; see assumption A2)

### FR2: Download the model

- **FR2.1**: The application downloads every file of the exact version of a public Hugging Face model, without a Hugging Face account or access token. (Feasibility gate feedback; constraint TC-2)
- **FR2.2**: The downloaded files are held in a holding area in Amazon S3 in the common AWS account in the US East (N. Virginia) Region, us-east-1, separate from the final location. (Q5; constraint TC-12)
- **FR2.3**: If the model needs a login, does not exist, or a file cannot be fully downloaded, the import fails (FR5).
- **FR2.4**: A half-downloaded file is never scanned or kept as if it were complete. (corner sweep: half-written file)

### FR3: Scan for data leaks

- **FR3.1**: The application scans the text and settings files of the model (settings, tokenizer files, README, and any other text file) for secrets, credentials, and personal data. (Q3; `intent-statement.md`)
- **FR3.2**: Weight files are not read for content. They are only checked to be the expected file type. (Q3)
- **FR3.3**: Any finding of any kind stops the import and makes it fail. (Q4)
- **FR3.4**: A file that cannot be read or scanned counts as a finding, so the import fails rather than passes. (corner sweep: unreadable file; see assumption A3)
- **FR3.5**: Records of a finding name the file and the place in it and the kind of finding, and never contain the secret or personal value itself. (Rule: never put secrets, tokens, or credentials in code or logs)

### FR4: Keep clean files

- **FR4.1**: When every file passes the scan, the files are moved to the final location in S3 and kept. (`intent-statement.md`, Success Metrics)
- **FR4.2**: No file reaches the final location unless the whole model has passed the scan. The final location shows either the complete model or nothing from this import. (`intent-statement.md`, Success Metrics; corner sweep: multi-step write interrupted)

### FR5: Handle a failure

- **FR5.1**: When the scan finds anything, any step fails, or the import runs past 6 hours, the application stops the import. (`intent-statement.md`; Q7)
- **FR5.2**: After stopping, the application removes all downloaded files and all temporary resources created for the import. (Rule: always remove downloaded files and temporary resources when an import fails)
- **FR5.3**: If the removal itself fails, the application says so in the failure email and names what is left, so the team can remove it by hand. (corner sweep: clean-up fails)
- **FR5.4**: The application sends one failure email to both the person who started the import and a fixed team mailbox. The email gives the model name, the exact version, the reason in plain words (for example a finding in a file, a download problem, or the time limit), and whether the removal was complete. It contains no secret or personal value. (Q6; `intent-statement.md`)
- **FR5.5**: If the failure email cannot be sent, the import still ends as failed with its files removed, and the problem with the email is recorded. (corner sweep: remote service down)

### FR6: Keep a record of imports (could have)

- **FR6.1**: If time allows after FR1 to FR5 work, the application keeps a record of each import: the model name, the exact version, the outcome, and the findings without their values. (Q1 of scope definition; `intent-backlog.md`, item B-6)

## Non-Functional Requirements

- **NFR1 (time limit)**: An import that has not finished 6 hours after it started counts as failed and is handled as FR5. (Q7)
- **NFR2 (size)**: The current version handles smaller models, taken to mean about 8 billion parameters or fewer, roughly 16 GB of files. Larger models, such as the 140 GB case, are not a target of this version. (Gate feedback at Units Generation; Feasibility Q2 option A; see assumption A7)
- **NFR3 (secrets)**: No secret, token, or credential appears in code or logs. (Rule: never put secrets, tokens, or credentials in code or logs)
- **NFR4 (exactly one outcome)**: Every import ends in exactly one outcome, stored or removed with an email, and never both or neither. (`intent-statement.md`, Success Metrics)
- **NFR5 (visibility)**: For each import, the outcome and its reason can be found in the application's logs, without secret or personal values. (assumption A5)
- **NFR6 (quality)**: Tests cover at least 80 percent of lines. (`team-practices.md`, Testing Posture)

## Constraints

- Code is written in Python, and AWS resources are managed with AWS CDK. (`team-practices.md`)
- Code is formatted with Black and checked with Ruff. (`team-practices.md`)
- Files and resources are in the existing common AWS account, in us-east-1. (Q5)
- Only public Hugging Face models that need no account are in scope, and their architecture should be one Amazon Bedrock Custom Model Import supports. (Feasibility gate feedback)
- The first version is due at the end of tomorrow. (`scope-document.md`)
- The company's own security policy is the only compliance rule. (Feasibility Q4)

## Assumptions

- **A1**: "End of tomorrow" is counted from the day the answer was recorded, and the owner confirms the exact time.
- **A2**: An event received while an import runs waits and starts afterwards. Nobody has been asked, and the owner can change it to a refusal.
- **A3**: A file that cannot be scanned should fail closed, because passing an unscanned file would break the rule that nothing unscanned reaches the final location.
- **A4**: Files are encrypted at rest in S3 with AWS's standard encryption, and only the application can read the holding area.
- **A5**: Logs are enough for the first version to show the outcome of an import. A separate record is the could-have FR6.
- **A6**: The "exact version" in the event is a Hugging Face revision identifier. Its format is a design detail.
- **A7**: "Smaller models" means the small size band offered in Feasibility Q2 (about 8 billion parameters or fewer, roughly 16 GB of files). You said the current version is focused on smaller models without naming a size, so the owner confirms the limit.

## Out of Scope

- Loading the model into Amazon Bedrock. (`intent-statement.md`)
- Model quality testing and risky code checks. The scan covers data leaks only. (`intent-statement.md`)
- An email when an import succeeds, a way to check the progress of an import, and a check of the model's architecture against Bedrock's list before downloading. (`scope-document.md`)
- Any user interface. (`team-practices.md`)

## Open Questions

- Which scan tool or rule set will find secrets, credentials, and personal data? This is a design decision for a later stage.
- Which public model will be imported first? It must fit the smaller-model size in NFR2.
- What is the address of the fixed team mailbox?
- Because any finding fails an import (FR3.3), harmless findings such as an email address in a README will also fail it. The owner may want to revisit Q4 once real models have been tried.
