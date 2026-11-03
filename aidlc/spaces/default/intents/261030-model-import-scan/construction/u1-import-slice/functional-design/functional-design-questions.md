# Functional Design Questions: u1-import-slice

Functional Design describes what the application does for this Unit, as rules a developer can build without guessing. The Unit is u1-import-slice, the thin end-to-end slice (`unit-of-work.md`). Most behaviour is already settled in `requirements.md`, `stories.md`, and `contract-summary.md`. These questions cover the points that are still open or that need your judgement. The scan rules are the largest gap: the requirements review left open which files count as weight files and which kinds of data the scan looks for.

## Q1. [Security engineer] Which weight files does the application accept?

A weight file holds the model's numbers. The scan does not read it for content and only checks that it is the expected type (FR3.2). Amazon Bedrock Custom Model Import reads weights in the safetensors format. Older formats such as .bin, .pt, and .pkl are Python pickle files, which can run code when they are opened.

A. Only .safetensors files are weight files; a .bin, .pt, .pkl, or other weight-like file counts as a finding and fails the import (recommended)
B. Both .safetensors and .bin files are weight files
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q2. [Security engineer] How is every other file scanned?

A model repository also holds settings files (config.json), tokenizer files, a README, and sometimes code or a chat template. Some tokenizer files are binary, such as tokenizer.model.

A. Scan every file that is not a weight file as text; a short list of known binary tokenizer files (tokenizer.model) is only checked to be the expected type; any other file that cannot be read as text counts as a finding (recommended)
B. Scan only files with known text endings (.json, .txt, .md, .yaml, .py, .jinja); ignore all other files
C. Not yet defined
X. Other (please specify)

[Answer]: B

## Q3. [Security engineer] Which kinds of data must the scan look for?

A. Secrets and credentials (access keys, tokens, passwords, private keys) and personal data (email addresses, phone numbers, government ID numbers, street addresses) (recommended)
B. Secrets and credentials only
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q4. [Product owner] When the scan finds something, should it keep looking?

A. Scan every file and report all findings, up to 20 listed in the email and the full list in the logs; the import still fails as soon as the scan ends (recommended)
B. Stop at the first finding; faster, but the email names only one finding
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q5. [Product owner] What should happen if someone asks for a model much larger than the smaller-model target of about 16 GB?

A. No size check; the import is attempted and the 6-hour limit decides (recommended, the simplest rule)
B. Refuse before downloading anything when the files add up to more than 20 GB, with an email saying the model is too large
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Follow-up Q6. [Security engineer] Q2 option B ignores files with other endings. Which files does that leave unscanned?

Under option B a file whose ending is not in the list is neither scanned nor checked, and it is kept with the model if the import passes. That includes tokenizer.model, files with no ending such as LICENSE, and text files with endings such as .toml, .cfg, .ini, .csv, and .tiktoken. The requirements say the scan covers the settings, tokenizer files, README, "and any other text file" (FR3.1), and that a file that cannot be scanned counts as a finding (FR3.4). Option A below follows the answer exactly as given; option B widens the list so fewer text files escape the scan.

A. Keep Q2 option B exactly: scan only .json, .txt, .md, .yaml, .py, and .jinja files; every other file is kept unscanned and unchecked (accepts that a secret in a file with another ending would pass)
B. Widen the list to .json, .jsonl, .txt, .md, .yaml, .yml, .py, .jinja, .toml, .cfg, .ini, .csv, and .tiktoken, plus files with no ending such as LICENSE; every other file is kept unscanned and unchecked (recommended)
C. Not yet defined
X. Other (please specify)

[Answer]: B

## Consolidated Summary Confirmation

- Weight files (Q1): only .safetensors files are weight files. They are checked for type and not read for content. A .bin, .pt, .pkl, or other weight-like file is a finding.
- Other files (Q2 and follow-up Q6): the scan reads files ending in .json, .jsonl, .txt, .md, .yaml, .yml, .py, .jinja, .toml, .cfg, .ini, .csv, or .tiktoken, and files with no ending such as LICENSE. Every other file (for example tokenizer.model) is kept unscanned and unchecked.
- Kinds of data (Q3): secrets and credentials (access keys, tokens, passwords, private keys) and personal data (email addresses, phone numbers, government ID numbers, street addresses).
- Findings (Q4): the scan covers every file and reports all findings, up to 20 listed in the email and the full list in the logs. The import still fails when the scan ends.
- Size (Q5): no size check. A larger model is attempted and the 6-hour limit decides.

**Confirm** — Does this all look correct before I generate the artifacts?

Looks correct — Generate the artifacts from these answers
Request changes — Revise one or more answers before generation

[Answer]: Looks correct
