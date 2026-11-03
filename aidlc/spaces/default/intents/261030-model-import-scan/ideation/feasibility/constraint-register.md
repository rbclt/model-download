# Constraint Register

Constraints come from `intent-statement.md` (Intent Capture), from the confirmed answers in `feasibility-questions.md`, from your feedback at the approval gate, and from the Amazon Bedrock Custom Model Import documentation (https://docs.aws.amazon.com/bedrock/latest/userguide/model-customization-import-model.html).

## Technical Constraints

| ID | Constraint | Source |
|----|------------|--------|
| TC-1 | The application must handle model files of about 140 GB (medium models, about 70 billion parameters). | Feasibility Q2 |
| TC-2 | Only public Hugging Face models that need no account or access token are in scope. Llama is not a focus. | Gate feedback |
| TC-3 | The model's architecture must be one that Amazon Bedrock Custom Model Import supports: Mistral, Mixtral, Flan, Llama, GPTBigCode, Qwen, or GPT-OSS. | Gate feedback; Bedrock documentation |
| TC-4 | Weights must be under 200 GB for text models and under 100 GB for multimodal models. | Bedrock documentation |
| TC-5 | The model's maximum context length must be under 128K, and embedding models are not supported. | Bedrock documentation |
| TC-6 | GPT-OSS models are supported by Bedrock only in the US East (N. Virginia) region. | Bedrock documentation |
| TC-7 | An event that carries the model name starts an import. | intent-statement.md (Initial Scope Signal) |
| TC-8 | The only check in the first version is the data leak scan: secrets, credentials, and personal data inside the model files. | intent-statement.md (Initial Scope Signal) |
| TC-9 | The first version ends at storing the model in Amazon S3. Loading the model into Amazon Bedrock is out. | intent-statement.md (Initial Scope Signal) |
| TC-10 | The code is written in Python. | Feasibility Q5 |
| TC-11 | AWS resources are created and managed with AWS CDK. | Feasibility Q6 |
| TC-12 | Files and resources are stored in the existing common AWS account. | Feasibility Q1 |

## Organizational Constraints

| ID | Constraint | Source |
|----|------------|--------|
| OC-1 | The first version has to be done in a few days of work, kept as small as possible. | Feasibility Q7 |
| OC-2 | One owner decides scope and priorities, and the security team gives input. | intent-statement.md (Initial Scope Signal) |
| OC-3 | No organizational blockers are known today. | Feasibility Q8 |
| OC-4 | No Hugging Face account is needed, because only public models are in scope. The team has no account today. | Feasibility Q3; gate feedback |

## Regulatory Constraints

| ID | Constraint | Source |
|----|------------|--------|
| RC-1 | No regulation or audit standard applies beyond the company's own security policy. | Feasibility Q4 |
| RC-2 | A security or compliance rule requires checking open model files before they enter the AWS account. This is the reason the scan exists. | intent-statement.md (Initiative Trigger) |

## Process Constraints

| ID | Constraint | Source |
|----|------------|--------|
| PC-1 | Every import ends in exactly one of two outcomes: clean files stored in S3, or everything removed and the user told why it failed. | intent-statement.md (Success Metrics) |
| PC-2 | No model file reaches the final S3 location without passing the scan. | intent-statement.md (Success Metrics) |
| PC-3 | On a failed scan, the process stops, and the downloaded files and any resources created for the import are removed. | intent-statement.md (Problem Statement) |
| PC-4 | The user gets an email on every failure and nothing on success. | intent-statement.md (Success Metrics, Initial Scope Signal) |

## Assumptions & Open Questions

- [assumption] The compliance answer (RC-1) covers where the files are stored and how long. The company policy has not been read against this application.
- [assumption] Bedrock's limits (TC-3 to TC-6) are as documented today and may change.
- Open question: Do TC-10 and TC-11 (Python and AWS CDK) bind later stages, or are they preferences the design may revisit?
- Open question: Which specific public model will be used first?
