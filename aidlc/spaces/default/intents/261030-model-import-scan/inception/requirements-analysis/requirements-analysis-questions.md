# Requirements Analysis Questions

The earlier stages already settled what the application does: an event starts an import, a public model is downloaded into S3, the files are scanned for data leaks, and on any failure everything is removed and the user gets an email. These questions cover the details that the requirements need and that only you can answer.

## Q1. [Product owner] What information does the start event carry?

Public model repositories can change over time (risk R-4 in the RAID log), so pinning an exact version makes every import reproducible.

A. Only the model name (the Hugging Face repository name); the latest version is always imported
B. The model name and an exact version of that model, so the same files are imported every time
C. The model name, an exact version, and the S3 location where the files should be stored
D. Not yet defined
X. Other (please specify)

[Answer]: B

## Q2. [Product owner] What should happen when someone asks to import a model and version that is already stored?

This can happen when the same event is sent twice.

A. Do nothing and finish quietly, because the files are already there
B. Run the import again and replace the stored files only if the new import passes the scan
C. Refuse the request and send an email saying it is already stored
D. Not yet defined
X. Other (please specify)

[Answer]: C

## Q3. [Security engineer] Which files should the data leak scan read?

A model of about 140 GB is mostly large weight files. Those are binary data, not text, so a scan for secrets and personal data finds almost nothing in them and takes far longer than reading the text files.

A. Only the text and settings files (settings, tokenizer files, README, and any other text); weight files are only checked to be the expected file type
B. Every file, including the large weight files, even though it takes much longer
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q4. [Security engineer] How strict should the scan be about what it finds?

Your request says a data leak stops the import. Some findings are clear leaks, such as a password or key. Others are less clear, such as an email address in the README.

A. Any finding stops the import, of any kind
B. Secrets and credentials stop the import; personal data such as an email address is written in the failure record but does not stop the import
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q5. [Platform engineer] In which AWS Region should the files be stored?

A. US East (N. Virginia), us-east-1
B. A different Region (tell me which one under Other)
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q6. [Operations engineer] Who receives the failure email?

The start event has to carry the sender's email address if the sender is to be told.

A. The person who started the import
B. A fixed team mailbox
C. Both the person who started the import and a fixed team mailbox
D. Not yet defined
X. Other (please specify)

[Answer]: C

## Q7. [Operations engineer] How long may one import run before it counts as failed?

A stuck import would otherwise hold large files and resources open without anyone noticing. The download alone is estimated at about 20 minutes to about 3 hours for 140 GB (feasibility assessment).

A. 6 hours; after that, the import is treated as a failure, everything is removed, and the user is emailed
B. 12 hours, with the same handling
C. 24 hours, with the same handling
D. No time limit
E. Not yet defined
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

Does this all look correct before I generate the requirements artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
