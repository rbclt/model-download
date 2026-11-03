# Requirements Document

## Introduction

This feature defines an ordered, gated pipeline that brings a Llama model from Hugging Face into Amazon Bedrock. The pipeline runs in a shared ("common") AWS account and has six stages:

1. **Intake**: a requester submits a Hugging Face model reference, pinned to an exact revision.
2. **Download**: the model files are downloaded into an isolated quarantine S3 bucket.
3. **Scan**: the files are scanned for malicious code and data leaks, with extra attention on metadata and configuration files.
4. **Verdict**: the scan results are combined into a single pass, review, or fail verdict.
5. **Promotion or rejection**: clean artifacts move to an approved S3 bucket. Failing artifacts stay in quarantine.
6. **Import**: the approved artifacts are imported into Amazon Bedrock with Custom Model Import.

Every stage uses least-privilege access and writes audit records. No stage may start until the previous stage has completed successfully.

Scope notes:
- Amazon Bedrock Custom Model Import reads Hugging Face weights in safetensors format together with `config.json` and the tokenizer files. The pipeline therefore accepts only safetensors weights and rejects pickle-based weight formats.
- Scanning reduces risk but does not prove a model is safe. Model weights can still carry behavioral risks (for example backdoors) that file scanning cannot detect. This residual risk is out of scope for this feature.

## Glossary

- **Pipeline**: The orchestrated workflow that performs intake, download, scan, verdict, promotion or rejection, and Bedrock import.
- **Orchestrator**: The component that runs the Pipeline stages in order and records the state of each Import_Request.
- **Common_Account**: The shared AWS account that hosts the Pipeline, the Quarantine_Bucket, and the Approved_Bucket.
- **Requester**: A person or system that submits an Import_Request.
- **Reviewer**: An authorized person who reviews Findings with Warning severity and approves or rejects an Import_Request.
- **Import_Request**: A record containing the Hugging Face repository ID, the pinned revision, the Requester identity, and the target Bedrock model name.
- **Model_Source_Allowlist**: The configured list of Hugging Face organizations and repository IDs that the Pipeline may download.
- **Pinned_Revision**: A full 40-character Hugging Face commit SHA that identifies one immutable version of a repository.
- **Download_Worker**: The isolated compute component that downloads model files from Hugging Face.
- **Quarantine_Bucket**: The S3 bucket that holds downloaded artifacts before and during scanning. Only the Download_Worker and the Scanner have access to the bucket.
- **Approved_Bucket**: The S3 bucket that holds artifacts that received a Pass verdict or an approved Warning verdict.
- **Artifact_Manifest**: A JSON document that lists every downloaded file with its path, size in bytes, and SHA-256 digest.
- **Scanner**: The set of components that inspect artifacts in the Quarantine_Bucket. The Scanner includes the File_Type_Validator, Safetensors_Validator, Metadata_Scanner, Code_Scanner, Pickle_Scanner, Leak_Scanner, and Malware_Scanner.
- **File_Type_Validator**: The Scanner component that checks each file against the File_Allowlist.
- **File_Allowlist**: The configured list of permitted file names and extensions: `*.safetensors`, `model.safetensors.index.json`, `config.json`, `generation_config.json`, `tokenizer.json`, `tokenizer_config.json`, `tokenizer.model`, `special_tokens_map.json`, `LICENSE*`, `USE_POLICY*`, and `README.md`.
- **Safetensors_Validator**: The Scanner component that validates the structure of each `.safetensors` file.
- **Metadata_Scanner**: The Scanner component that inspects `config.json`, `generation_config.json`, tokenizer files, `model.safetensors.index.json`, the safetensors `__metadata__` header section, and the model card.
- **Code_Scanner**: The Scanner component that statically analyzes Python files, shell scripts, and notebooks.
- **Pickle_Scanner**: The Scanner component that inspects pickle-based files (`.bin`, `.pt`, `.pth`, `.pkl`, `.ckpt`) for dangerous opcodes and imports.
- **Leak_Scanner**: The Scanner component that detects secrets, credentials, personally identifiable information (PII), and embedded network locations in text content.
- **Malware_Scanner**: The Scanner component that runs an antivirus or malware detection service against every file.
- **Finding**: One result produced by a Scanner component, with a file path, rule ID, severity, and description.
- **Severity**: One of Critical, High, Warning, or Info.
- **Scan_Report**: A JSON document that contains all Findings, the Artifact_Manifest digest, the Scanner version, and the Verdict.
- **Verdict**: The Orchestrator decision for an Import_Request: Pass, Review, or Fail.
- **Bedrock_Importer**: The component that creates and monitors an Amazon Bedrock Custom Model Import job.
- **Import_Role**: The IAM service role that Amazon Bedrock assumes to read the Approved_Bucket during a Custom Model Import job.
- **Audit_Log**: The append-only record of Pipeline events, stored in CloudWatch Logs and an S3 bucket with Object Lock enabled.
- **KMS_Key**: The AWS KMS customer managed key that encrypts the Quarantine_Bucket, the Approved_Bucket, the Scan_Report, and the Audit_Log.

## Requirements

### Requirement 1: Intake and Source Validation

**User Story:** As a security engineer, I want every import to start from a validated, pinned request, so that only approved and reproducible model versions enter the Pipeline.

#### Acceptance Criteria

1. WHEN a Requester submits an Import_Request, THE Orchestrator SHALL record the repository ID, the Pinned_Revision, the authenticated Requester identity, the target Bedrock model name, and a UTC timestamp with at least 1-second precision.
2. IF the repository ID in an Import_Request is not in the Model_Source_Allowlist, THEN THE Orchestrator SHALL reject the Import_Request without starting a download, return an error to the Requester indicating the repository is not allowlisted, and record the rejection reason, the repository ID, the Requester identity, and a UTC timestamp in the Audit_Log.
3. IF the revision in an Import_Request is not a 40-character hexadecimal commit SHA (characters 0-9 and a-f, case-insensitive), THEN THE Orchestrator SHALL reject the Import_Request without starting a download, return an error that identifies the invalid revision value, and record the rejection reason in the Audit_Log. A branch name, tag name, or abbreviated SHA SHALL be treated as invalid.
4. IF the Hugging Face repository is gated and the Common_Account has no recorded acceptance of the Llama license for the repository, THEN THE Orchestrator SHALL reject the Import_Request without starting a download, return an error to the Requester indicating that license acceptance is missing, and record the rejection reason in the Audit_Log.
5. WHEN an Import_Request passes validation, THE Orchestrator SHALL assign an Import_Request ID that is unique across all Import_Requests, set the state to `INTAKE_ACCEPTED`, and return the Import_Request ID to the Requester.
6. WHEN an Import_Request is submitted with a repository ID and Pinned_Revision that match an existing Import_Request in state `IMPORTED`, THE Orchestrator SHALL return the existing Import_Request ID, indicate to the Requester that the model is already imported, and not start a new download.
7. IF an Import_Request has a missing repository ID, Pinned_Revision, or target Bedrock model name, or the target Bedrock model name is not 1 to 63 characters, THEN THE Orchestrator SHALL reject the Import_Request without assigning an Import_Request ID, return an error identifying each missing or invalid field, and record the rejection reason in the Audit_Log.
8. IF the Orchestrator cannot determine within 30 seconds whether the Hugging Face repository is gated or whether the Pinned_Revision exists in the repository, THEN THE Orchestrator SHALL reject the Import_Request without starting a download, return an error indicating that source verification was unavailable, and record the rejection reason in the Audit_Log.

### Requirement 2: Isolated Download into Quarantine

**User Story:** As a security engineer, I want downloads to happen in an isolated environment and land only in a quarantine bucket, so that untrusted files cannot reach any other system before they are scanned.

#### Acceptance Criteria

1. WHEN an Import_Request enters state `INTAKE_ACCEPTED`, THE Download_Worker SHALL download every file of the Pinned_Revision from Hugging Face and write each file under the prefix `quarantine/{import_request_id}/` in the Quarantine_Bucket, retrying a failed file transfer up to 3 times before treating the download as failed.
2. THE Download_Worker SHALL run in a private subnet that accepts no inbound connections and SHALL allow outbound connections only to the Hugging Face domains configured for the Pipeline, the Quarantine_Bucket, and AWS Secrets Manager.
3. WHEN the Download_Worker starts a download, THE Download_Worker SHALL read the Hugging Face access token from AWS Secrets Manager and SHALL NOT write the token to disk, to the Audit_Log, to any other log output, or to the Quarantine_Bucket.
4. THE Download_Worker SHALL write each downloaded file to the Quarantine_Bucket without executing, importing, or loading the file contents.
5. WHEN the download of all files completes, THE Download_Worker SHALL generate an Artifact_Manifest that lists every downloaded file with its size in bytes and its SHA-256 digest, and SHALL store the Artifact_Manifest under the prefix `quarantine/{import_request_id}/`.
6. WHEN the Artifact_Manifest has been stored, THE Orchestrator SHALL set the state of the Import_Request to `DOWNLOADED`.
7. IF the download fails, or does not complete within 4 hours of the Download_Worker starting the download, THEN THE Orchestrator SHALL set the state to `DOWNLOAD_FAILED`, record the failure reason in the Audit_Log, and delete all files under the prefix `quarantine/{import_request_id}/` within 15 minutes.
8. IF the total size in bytes of the downloaded files exceeds the configured maximum download size, THEN THE Download_Worker SHALL stop the download within 60 seconds, and THE Orchestrator SHALL set the state to `DOWNLOAD_FAILED`, record the size limit violation in the Audit_Log, and delete all files under the prefix `quarantine/{import_request_id}/` within 15 minutes.
9. IF the Download_Worker cannot read the Hugging Face access token from AWS Secrets Manager, THEN THE Download_Worker SHALL NOT start the download, and THE Orchestrator SHALL set the state to `DOWNLOAD_FAILED` and record the failure reason in the Audit_Log without including the token value.
10. IF deletion of the files under the prefix `quarantine/{import_request_id}/` fails after a `DOWNLOAD_FAILED` transition, THEN THE Orchestrator SHALL record the deletion failure and the affected object count in the Audit_Log and SHALL retain the state `DOWNLOAD_FAILED`.

### Requirement 3: File Allowlist and Weight Format Enforcement

**User Story:** As a security engineer, I want only expected, non-executable file types to be accepted, so that custom code and pickle payloads are blocked before deeper scanning.

#### Acceptance Criteria

1. WHEN an Import_Request enters state `DOWNLOADED`, THE File_Type_Validator SHALL compare every file in the Artifact_Manifest against the File_Allowlist, using the text after the last `.` in the file name, matched case-insensitively, as the file's extension.
2. IF a file is not in the File_Allowlist and its extension is not listed in criterion 3 or criterion 4, THEN THE File_Type_Validator SHALL record exactly one Finding with Severity High that names the file.
3. IF a file has the extension `.py`, `.sh`, `.bat`, `.ps1`, `.so`, `.dll`, `.exe`, `.ipynb`, or `.jar`, THEN THE File_Type_Validator SHALL record exactly one Finding with Severity Critical that names the file and SHALL NOT record an additional Severity High Finding for the same file under criterion 2.
4. IF a file has the extension `.bin`, `.pt`, `.pth`, `.pkl`, `.pickle`, or `.ckpt`, THEN THE File_Type_Validator SHALL record exactly one Finding with Severity Critical that names the file and SHALL NOT record an additional Severity High Finding for the same file under criterion 2.
5. IF the Artifact_Manifest contains zero files with the extension `.safetensors`, THEN THE File_Type_Validator SHALL record one Finding with Severity Critical that states weights in safetensors format are missing.
6. IF the content type detected from a file's content differs from the content type expected for the file's extension, THEN THE File_Type_Validator SHALL record one Finding with Severity High that names the file, the detected content type, and the content type expected for the extension.
7. IF the SHA-256 digest computed from a file in the Quarantine_Bucket differs from the SHA-256 digest listed for that file in the Artifact_Manifest, THEN THE File_Type_Validator SHALL record one Finding with Severity Critical that names the file.
8. IF a file listed in the Artifact_Manifest is absent from the Quarantine_Bucket or cannot be read, THEN THE File_Type_Validator SHALL record one Finding with Severity Critical that names the file and states that the file could not be validated.
9. IF the Quarantine_Bucket contains a file that is not listed in the Artifact_Manifest, THEN THE File_Type_Validator SHALL record one Finding with Severity Critical that names the file and states that the file is not listed in the Artifact_Manifest.

### Requirement 4: Safetensors Structure Validation

**User Story:** As a security engineer, I want weight files to be structurally validated without being deserialized by code, so that malformed or disguised files are detected.

#### Acceptance Criteria

1. WHEN a `.safetensors` file is scanned, THE Safetensors_Validator SHALL read the 8-byte little-endian header length and then read only the number of bytes given by that header length as the JSON header, without reading tensor data and without loading tensor data into a Python object graph.
2. IF the file is smaller than 8 bytes, or the declared header length is 0, or the declared header length plus 8 exceeds the file size, or the declared header length exceeds the configured maximum header size (default 100,000,000 bytes), THEN THE Safetensors_Validator SHALL record a Finding with Severity Critical identifying the file and the violated condition, SHALL NOT read the header, and SHALL continue scanning the remaining files.
3. IF the header bytes are not valid UTF-8, or are not valid JSON, or the parsed JSON top-level value is not an object, THEN THE Safetensors_Validator SHALL record a Finding with Severity Critical identifying the file and SHALL skip the tensor offset checks for that file.
4. IF any tensor entry has a `data_offsets` value that is not a pair of non-negative integers [begin, end] with begin less than or equal to end, or has an `end` greater than the data section size (file size minus 8 minus header length), or has a range that overlaps the range of another tensor entry, THEN THE Safetensors_Validator SHALL record a Finding with Severity Critical identifying the file and the tensor name.
5. WHEN the header contains a `__metadata__` section, THE Safetensors_Validator SHALL pass every key and value string in that section to the Metadata_Scanner and the Leak_Scanner.
6. WHEN `model.safetensors.index.json` is present in the Artifact_Manifest, THE Safetensors_Validator SHALL verify that every shard file name listed in `weight_map` exists in the Artifact_Manifest, and SHALL record one Finding with Severity High per missing shard identifying the shard file name.
7. IF the `__metadata__` section contains any value that is not a string, THEN THE Safetensors_Validator SHALL record a Finding with Severity Warning identifying the file and the offending key, and SHALL continue validating the remaining entries.
8. IF `model.safetensors.index.json` is not valid JSON, or has no `weight_map` object, or lists a shard file name that contains a path separator or a parent-directory reference (`..`), THEN THE Safetensors_Validator SHALL record a Finding with Severity High identifying the index file and the violated condition, and SHALL NOT resolve the offending shard name against the file system.

### Requirement 5: Metadata and Configuration Scanning

**User Story:** As a security engineer, I want metadata and configuration files inspected for dangerous settings and hidden payloads, so that code-execution hooks and tampered settings are caught.

#### Acceptance Criteria

1. WHEN `config.json`, `tokenizer_config.json`, `tokenizer.json`, `special_tokens_map.json`, or `generation_config.json` (each a "metadata JSON file") is scanned, THE Metadata_Scanner SHALL parse the file as strict JSON (RFC 8259, with no comments, trailing commas, NaN, Infinity, or duplicate keys within the same object) and record a Finding with Severity Critical that names the file and the line and column of the first error if parsing fails.
2. IF `config.json` contains a top-level `auto_map` key, THEN THE Metadata_Scanner SHALL record a Finding with Severity Critical that names the file and lists every module reference found in the `auto_map` values.
3. IF a metadata JSON file contains the key `trust_remote_code` at any nesting depth with the boolean value true or the string value "true" (case-insensitive), THEN THE Metadata_Scanner SHALL record a Finding with Severity Critical that names the file and the key path.
4. IF the `architectures` value in `config.json` is absent, is not an array of strings, or contains an entry that is not in the configured list of Llama architectures supported by Amazon Bedrock Custom Model Import, THEN THE Metadata_Scanner SHALL record a Finding with Severity High that names the file and each unsupported or malformed entry.
5. IF `config.json` contains a top-level key that is not in the configured set of known Llama configuration keys, THEN THE Metadata_Scanner SHALL record a Finding with Severity Warning that names the file and the key, with one Finding per unknown key.
6. WHEN `tokenizer_config.json`, `tokenizer.json`, or `special_tokens_map.json` is scanned, THE Metadata_Scanner SHALL record a Finding with Severity High that names the file and key path for each string value that contains a template construct (Jinja statement or expression delimiters `{% %}` or `{{ }}`) not in the configured allow-list of standard chat template constructs.
7. WHEN `tokenizer_config.json`, `tokenizer.json`, or `special_tokens_map.json` is scanned, THE Metadata_Scanner SHALL record a Finding with Severity High that names the file and key path for each string value that contains a URL (a string beginning with `http://`, `https://`, or `ftp://`), a command from the configured shell command list, or a match for the configured Python expression patterns.
8. WHEN `generation_config.json` is scanned, THE Metadata_Scanner SHALL record a Finding with Severity Warning that names the key for each top-level key that is not in the configured set of recognized Hugging Face generation parameters.
9. WHEN a metadata JSON file contains a string value longer than the configured maximum string length (default 10,000 characters, configurable from 1 to 1,000,000), THE Metadata_Scanner SHALL record a Finding with Severity Warning that names the file and the key path.
10. WHEN a metadata JSON file contains a string value of at least 16 characters that is valid Base64 or hexadecimal, or that is Base64 or hexadecimal text wrapping gzip, zlib, or bzip2 compressed data, and the decoded content (after up to 3 nested decoding layers) begins with an ELF, PE (`MZ`), or Mach-O signature, begins with `#!`, or matches the configured script patterns, THE Metadata_Scanner SHALL record a Finding with Severity Critical that names the file and the key path.
11. WHEN the model card (`README.md`) is scanned, THE Metadata_Scanner SHALL record a Finding with Severity Warning that names the file and line number for each URL beginning with `http://`, `https://`, or `ftp://`, each HTML `script` or `iframe` element (tag names matched case-insensitively), and each line inside a fenced code block in which `curl` or `wget` output is piped (`|`) to `sh`, `bash`, `zsh`, or `python`.
12. IF a metadata file or the model card cannot be read, or its size exceeds the configured maximum metadata file size (default 50 MiB), THEN THE Metadata_Scanner SHALL record a Finding with Severity Warning that names the file and the reason, skip that file, and continue scanning the remaining metadata files.
13. IF decoding a string value under criterion 10 would produce more than the configured maximum decoded size (default 10 MiB) or more than 3 nested decoding layers, THEN THE Metadata_Scanner SHALL stop decoding that value, record a Finding with Severity High that names the file and the key path, and continue scanning the remaining values.

### Requirement 6: Malicious Code Detection

**User Story:** As a security engineer, I want any code or code-like content analyzed statically, so that malicious scripts are detected without being executed.

#### Acceptance Criteria

1. WHEN a file with extension `.py`, `.sh`, or `.ipynb` is added to the Quarantine_Bucket, THE Code_Scanner SHALL analyze the file by static analysis and complete the analysis within 60 seconds, analyzing every code cell of a `.ipynb` file.
2. WHEN the Code_Scanner finds a call to `eval`, `exec`, `compile`, `os.system`, `pickle.loads`, or any function or attribute of the `subprocess`, `socket`, `requests`, `urllib`, `http.client`, or `ctypes` modules, including calls made through an import alias, THE Code_Scanner SHALL record one Finding with Severity High for each occurrence that names the file, line number, and called function. For a `.sh` file, THE Code_Scanner SHALL apply this criterion to the `eval` and `exec` commands only.
3. WHEN the Code_Scanner finds a string produced by Base64 decoding, hex decoding, or character-code concatenation that is passed to `eval`, `exec`, `compile`, `os.system`, or `subprocess`, THE Code_Scanner SHALL record one Finding with Severity Critical for each occurrence that names the file, line number, and execution function.
4. WHEN the Code_Scanner finds a reference to the paths `.aws/credentials` or `.aws/config`, an environment variable whose name contains `KEY`, `TOKEN`, or `SECRET` (case-insensitive), or the instance metadata endpoint `169.254.169.254`, THE Code_Scanner SHALL record one Finding with Severity Critical for each occurrence that names the file, line number, and the matched reference.
5. IF the Code_Scanner cannot parse a file because of a syntax error, an unreadable character encoding, or a malformed notebook structure, THEN THE Code_Scanner SHALL record a Finding with Severity High that names the file and states that the file could not be analyzed, and SHALL leave the file unmodified in the Quarantine_Bucket.
6. THE Code_Scanner SHALL NOT execute, import, or evaluate any content of a file in the Quarantine_Bucket during analysis.
7. IF a file exceeds 5 MB or its analysis exceeds 60 seconds, THEN THE Code_Scanner SHALL stop analyzing that file, record a Finding with Severity High that names the file and states that the file could not be fully analyzed because of the size or time limit, and SHALL leave the file unmodified in the Quarantine_Bucket.
8. WHEN the Code_Scanner completes analysis of a file and records no Finding for it, THE Code_Scanner SHALL report the file as analyzed with zero Findings.
### Requirement 7: Pickle Payload Detection

**User Story:** As a security engineer, I want any pickle-based file inspected at the opcode level, so that deserialization attacks are identified even if the file passes the allowlist step.

#### Acceptance Criteria

1. WHEN a file with a `.pkl`, `.pickle`, `.bin`, or `.pt` extension is added to the Quarantine_Bucket, THE Pickle_Scanner SHALL inspect the file's opcode stream without unpickling the file and without executing any code contained in it, and SHALL finish inspecting each file within 300 seconds.
2. WHEN the opcode stream contains a `GLOBAL`, `STACK_GLOBAL`, or `REDUCE` operation whose resolved module and callable pair does not exactly match an entry in the configured safe-callable list, THE Pickle_Scanner SHALL record one Finding with Severity Critical for each distinct module and callable pair in the file, and each Finding SHALL state the module, the callable, the opcode name, and the file path.
3. WHEN a `.bin` or `.pt` file is a ZIP archive, THE Pickle_Scanner SHALL inspect every archive entry named `data.pkl`, at any directory depth, under the rules in criteria 1 and 2, and each Finding for such an entry SHALL also state the entry's path within the archive.
4. IF the Pickle_Scanner cannot parse a file or archive entry because the opcode stream is malformed or truncated, the ZIP structure is invalid, an entry cannot be read, or the 300-second limit in criterion 1 is exceeded, THEN THE Pickle_Scanner SHALL record a Finding with Severity High that states the file could not be analyzed and gives the reason, SHALL leave the file unchanged in the Quarantine_Bucket, and SHALL continue inspecting the remaining files.
5. IF a `STACK_GLOBAL` or `REDUCE` operation references a module or callable that cannot be resolved from the opcode stream without executing code, THEN THE Pickle_Scanner SHALL record a Finding with Severity Critical that states the opcode name, the file path, and that the module and callable are unresolved.

### Requirement 8: Data Leak Detection

**User Story:** As a security engineer, I want all text content scanned for secrets, PII, and embedded network locations, so that sensitive data is not carried into the Bedrock environment.

#### Acceptance Criteria

1. WHEN the Leak_Scanner scans a text file or a metadata string, THE Leak_Scanner SHALL check the content against rules for AWS access keys, private keys, Hugging Face tokens, GitHub tokens, JWTs, and generic high-entropy credential strings, where a generic high-entropy credential string is a contiguous run of at least 20 characters from the set A-Z, a-z, 0-9, +, /, =, _, and - with a Shannon entropy of at least 4.5 bits per character.
2. WHEN the Leak_Scanner detects a secret, THE Leak_Scanner SHALL record one Finding with Severity Critical for each match, and each Finding SHALL name the file, the location, and the rule ID, where the location is the 1-based line number for a file and the metadata key for a safetensors `__metadata__` string.
3. WHEN the Leak_Scanner detects a secret, THE Leak_Scanner SHALL store only a redacted form of the matched value that reveals at most the first 4 characters, SHALL fully mask any matched value shorter than 8 characters, and SHALL NOT include the unredacted value in any Finding or scan output.
4. WHEN the Leak_Scanner detects an email address, a phone number in E.164 or 10-digit North American format, or a US Social Security number in NNN-NN-NNNN format, THE Leak_Scanner SHALL record one Finding with Severity Warning for each match, and each Finding SHALL name the file, the location, the rule ID, and a redacted form of the value that reveals at most the first 4 characters.
5. WHEN the Leak_Scanner detects a private IP address, an internal hostname, or a URL whose domain is not in the configured trusted-domain list, THE Leak_Scanner SHALL record one Finding with Severity Warning for each match that names the value and location, where a private IP address is an IPv4 address in 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8, or 169.254.0.0/16, an internal hostname is a hostname ending in .internal, .local, .corp, .lan, or .intranet, and a domain is in the trusted-domain list when it equals a list entry or is a subdomain of a list entry.
6. THE Leak_Scanner SHALL scan the text content of every file in the Artifact_Manifest whose content type, as recorded in the Artifact_Manifest, is text, JSON, or Markdown, and SHALL scan every string value in the safetensors `__metadata__` section.
7. IF the Leak_Scanner cannot scan a file or metadata section because it cannot be read, cannot be decoded as UTF-8 text, is larger than 100 MB, or has malformed `__metadata__` content, THEN THE Leak_Scanner SHALL record a Finding with Severity Warning that names the file and the reason the content was not scanned, SHALL retain all Findings already recorded, and SHALL continue scanning the remaining files.

### Requirement 9: Malware Detection

**User Story:** As a security engineer, I want every artifact checked by a malware detection service, so that known malicious binaries are caught in addition to the custom rules.

#### Acceptance Criteria

1. WHEN an Import_Request transitions to state `DOWNLOADED`, THE Malware_Scanner SHALL submit every object under the quarantine prefix to the malware detection service and obtain a result of clean, threat, or error for each object.
2. WHEN the malware detection service reports one or more threats for an object, THE Malware_Scanner SHALL record one Finding with Severity Critical for each reported threat, and each Finding SHALL name the object and the threat name.
3. IF the malware detection service does not respond within 60 seconds or returns an error for an object on each of 3 attempts, THEN THE Malware_Scanner SHALL record a Finding with Severity High that names the object, retain the results already recorded for other objects, and continue scanning the remaining objects.
4. WHEN the scan of an object completes with a result of clean, threat, or error, THE Malware_Scanner SHALL record in the Scan_Report the malware definition version that was in use for that scan.
5. IF the malware definition version cannot be determined for a scan, THEN THE Malware_Scanner SHALL record the version as unknown in the Scan_Report and retain the scan result for that object.
6. IF the quarantine prefix contains zero objects when scanning starts, THEN THE Malware_Scanner SHALL record in the Scan_Report that 0 objects were scanned and SHALL NOT record any Finding.

### Requirement 10: Scan Report and Verdict

**User Story:** As a reviewer, I want one consolidated report and a clear verdict, so that the promotion decision is consistent and explainable.

#### Acceptance Criteria

1. WHEN every Scanner component for an Import_Request has either completed successfully or been recorded as failed under criterion 5, THE Orchestrator SHALL generate, within 5 seconds, a single Scan_Report in JSON that contains every Finding (an empty list if there are none), the SHA-256 digest of the Artifact_Manifest, the Scanner version, the malware definition version, and exactly one Verdict with the value Pass, Review, or Fail.
2. IF the Scan_Report contains at least one Finding with Severity Critical or High, THEN THE Orchestrator SHALL set the Verdict to Fail.
3. IF the Scan_Report contains no Finding with Severity Critical or High and at least one Finding with Severity Warning, and no Scanner component has failed, THEN THE Orchestrator SHALL set the Verdict to Review.
4. IF the Scan_Report contains no Finding with Severity Critical, High, or Warning, and no Scanner component has failed, THEN THE Orchestrator SHALL set the Verdict to Pass.
5. IF any Scanner component returns an error or does not finish within 900 seconds of starting, THEN THE Orchestrator SHALL set the Verdict to Fail regardless of the Severity of any other Finding, and SHALL record in the Scan_Report the name of each failing component and its failure reason (error or timeout).
6. WHEN the Orchestrator generates a Scan_Report, THE Orchestrator SHALL store it encrypted with the KMS_Key under `reports/{import_request_id}/scan-report.json`.
7. WHEN the Orchestrator generates a Scan_Report, THE Orchestrator SHALL serialize it to UTF-8 JSON that the Scan_Report parser can read back into a Scan_Report object with identical field values, identical Finding order, and identical Verdict.
8. FOR ALL valid Scan_Report objects, serializing, parsing, and serializing again SHALL produce a JSON document that is equal to the first serialized document when compared by field names and values, ignoring whitespace and object key order (round-trip property).
9. IF a Scan_Report JSON document does not conform to the Scan_Report schema, THEN THE Scan_Report parser SHALL return no Scan_Report object and SHALL return an error that names the path of the first invalid field in document order and states whether the field is missing, has the wrong type, or has a value outside the allowed values.
10. IF a Scan_Report JSON document is not well-formed JSON, THEN THE Scan_Report parser SHALL return no Scan_Report object and SHALL return an error indicating a syntax error and the character position where parsing stopped.
11. IF storing the Scan_Report fails after 3 attempts, THEN THE Orchestrator SHALL report a storage error that identifies the Import_Request, SHALL leave no partially written or unencrypted Scan_Report at `reports/{import_request_id}/scan-report.json`, and SHALL NOT mark the Import_Request as promotable.

### Requirement 11: Review of Warning Findings

**User Story:** As a reviewer, I want to approve or reject models that only have low-severity Findings, so that human judgment is applied before promotion.

#### Acceptance Criteria

1. WHEN the Verdict is Review, THE Orchestrator SHALL set the state to `AWAITING_REVIEW` and, within 60 seconds of setting that state, notify every member of the Reviewer group with the Import_Request ID and a link to the Scan_Report.
2. WHEN a Reviewer who is a member of the Reviewer group approves an Import_Request in state `AWAITING_REVIEW` with a justification text of 1 to 2,000 characters, THE Orchestrator SHALL record the Reviewer identity, the decision, the justification text, and a UTC timestamp in the Audit_Log and SHALL set the state to `APPROVED`.
3. WHEN a Reviewer who is a member of the Reviewer group rejects an Import_Request in state `AWAITING_REVIEW` with a justification text of 1 to 2,000 characters, THE Orchestrator SHALL record the Reviewer identity, the decision, the justification text, and a UTC timestamp in the Audit_Log and SHALL set the state to `REJECTED`.
4. IF the Reviewer identity is the same as the Requester identity, THEN THE Orchestrator SHALL refuse the decision, return an error to the Reviewer indicating that self-review is not permitted, leave the state of the Import_Request unchanged, and record the refusal with the Reviewer identity, the Import_Request ID, and a UTC timestamp in the Audit_Log.
5. IF no Reviewer decision is recorded within 14 days (336 hours) after the Import_Request entered state `AWAITING_REVIEW`, THEN THE Orchestrator SHALL set the state to `REJECTED` and record the timeout, the Import_Request ID, and a UTC timestamp in the Audit_Log.
6. IF a Reviewer submits an approval for an Import_Request whose Verdict is Fail, THEN THE Orchestrator SHALL refuse the approval, return an error to the Reviewer indicating that approval is not permitted for a Fail Verdict, leave the state of the Import_Request unchanged, and record the refused attempt with the Reviewer identity and a UTC timestamp in the Audit_Log.
7. IF a Reviewer submits a decision for an Import_Request that is not in state `AWAITING_REVIEW`, THEN THE Orchestrator SHALL refuse the decision, return an error to the Reviewer indicating the current state of the Import_Request, leave the state unchanged, and record the refused attempt with the Reviewer identity and a UTC timestamp in the Audit_Log.
8. IF a Reviewer decision is submitted with a justification text that is empty or longer than 2,000 characters, THEN THE Orchestrator SHALL refuse the decision, return an error to the Reviewer indicating the allowed justification length, and leave the state of the Import_Request as `AWAITING_REVIEW`.
9. IF a decision is submitted by an identity that is not a member of the Reviewer group, THEN THE Orchestrator SHALL refuse the decision, return an error to the submitter indicating insufficient permission, leave the state of the Import_Request unchanged, and record the refused attempt with the submitter identity and a UTC timestamp in the Audit_Log.

### Requirement 12: Promotion of Clean Artifacts

**User Story:** As a platform owner, I want only verified artifacts copied to an approved location, so that Bedrock never reads from the quarantine area.

#### Acceptance Criteria

1. WHEN the Verdict becomes Pass or the state changes to `APPROVED`, THE Orchestrator SHALL verify, before copying the first file, that the SHA-256 digest of every file named in the File_Allowlist and stored in quarantine equals the digest recorded for that file in the Artifact_Manifest.
2. WHEN the digest verification of every file in criterion 1 succeeds, THE Orchestrator SHALL copy only the files named in the File_Allowlist to the prefix `approved/{import_request_id}/` in the Approved_Bucket, and SHALL NOT copy any file that is absent from the File_Allowlist.
3. WHEN the copy of every file completes, THE Orchestrator SHALL verify that the SHA-256 digest of each copied file in the Approved_Bucket equals the digest recorded for that file in the Artifact_Manifest, and WHEN every copied file matches, THE Orchestrator SHALL set the state to `PROMOTED`.
4. IF a digest verification in criterion 1 or criterion 3 fails for any file, THEN THE Orchestrator SHALL delete every file already copied under `approved/{import_request_id}/` in the Approved_Bucket, set the state to `PROMOTION_FAILED`, leave the quarantined files unchanged, and record in the Audit_Log the import_request_id, the name of each mismatching file, and whether the failure occurred before or after the copy.
5. THE Approved_Bucket SHALL deny every request to create, overwrite, or delete objects from every principal other than the promotion role of the Orchestrator.
6. IF the copy of any file fails, or the promotion does not complete within 900 seconds of the start of the digest verification in criterion 1, THEN THE Orchestrator SHALL stop copying, delete every file already copied under `approved/{import_request_id}/` in the Approved_Bucket, set the state to `PROMOTION_FAILED`, leave the quarantined files unchanged, and record in the Audit_Log the import_request_id, the name of the file that failed or the timeout, and any file that could not be deleted.
