# Feasibility Assessment

## Summary

The first version looks feasible within the few days the team has set aside, if it stays as small as the intent statement describes: import a public open-weight model from Hugging Face into Amazon S3 in the common AWS account, scan it for data leaks, and either keep it or remove everything and tell the user. At the approval gate you narrowed the models in scope to public ones that need no Hugging Face account and whose architecture Amazon Bedrock supports, and Llama is no longer a focus. That change removes the Hugging Face account as a blocker. Two conditions remain, listed under "Conditions for Proceeding".

## Inputs

- `intent-statement.md` from Intent Capture: the problem, the users, the success measures, and the first-version boundary. Following your gate feedback, its text now says "open-weight model" in place of "Llama".
- Feasibility answers Q1 to Q8 in `feasibility-questions.md`, all confirmed. Your gate feedback replaces the Q3 answer (no Hugging Face account yet) with a narrower scope: only public models that need no account. The questions file is left as you confirmed it.
- Your gate feedback: "Let's not worry about Llama and move forward with a model that doesn't need hugging face account and compatible with architectures supported within Bedrock".
- The Amazon Bedrock Custom Model Import documentation, section "Supported architectures" (https://docs.aws.amazon.com/bedrock/latest/userguide/model-customization-import-model.html), for the list of architectures and size limits.

## Model Scope

A model is in scope when both are true:

- It is public on Hugging Face, so it can be downloaded without an account or access token.
- Its architecture is one that Amazon Bedrock Custom Model Import supports. The documented list is Mistral, Mixtral, Flan, Llama (2, 3, 3.1, 3.2, 3.3 and Mllama), GPTBigCode, Qwen (Qwen2, Qwen2.5, Qwen2-VL, Qwen2.5-VL and Qwen3), and GPT-OSS.

Bedrock also documents these limits for imported models, which this application keeps in mind even though the first version stops at S3:

- Weights must be under 200 GB for text models and under 100 GB for multimodal models.
- The maximum context length must be under 128K.
- Embedding models are not supported.
- GPT-OSS models are supported only in the US East (N. Virginia) region.
- Bedrock supports transformers version 4.51.3.

A medium model of about 140 GB (Feasibility Q2) is inside the 200 GB text limit. Which exact model to use first is not yet decided.

## Technical Viability

| Area | Viability | Basis |
|------|-----------|-------|
| Using the common AWS account | High | The account already exists and can be used as is (Feasibility Q1). |
| Moving a medium model (about 140 GB) into S3 | Medium | The largest expected model is about 70 billion parameters, roughly 140 GB of files (Feasibility Q2). A download of that size takes roughly 20 minutes at a sustained 1 Gbps and roughly 3 hours at 100 Mbps. These times are an estimate (hypothesis) and depend on the real network speed. |
| Scanning 140 GB for secrets, credentials, and personal data | Medium | The scan is the only check in the first version (intent statement). Its run time on this much data is unknown (hypothesis). Short-lived compute options usually cap run time and temporary disk. For example, AWS Lambda allows up to 15 minutes and 10 GB of temporary disk, which is too small for this job. To be confirmed in design. |
| Starting an import from an event | High | An event that carries the model name starts an import (intent statement). Event-driven starts are a standard pattern on AWS. |
| Removing everything on failure | Medium | The downloaded files and any resources created for the import must be removed, including after a crash or a timeout. This needs a clean-up path that still runs when the import itself fails. |
| Telling the user about a failure | High | An email on every failure, and nothing on success (intent statement). |
| Access to Hugging Face | High | Only public models are in scope, so no account or access token is needed. Whether anonymous downloads of very large files are limited or slowed by Hugging Face is not yet known (hypothesis). |
| Picking a model Bedrock can later import | Medium | Several architecture families are on Bedrock's supported list, and some families publish public models. Each model's size, license, and whether it needs an account has to be checked on its Hugging Face page before use (hypothesis). |

## Risk Analysis

- **Time pressure:** The first version has to fit in a few days (Feasibility Q7). Any growth beyond the scan, such as loading into Amazon Bedrock or a human approval step, would not fit.
- **Scan coverage:** The first version checks only for data leaks (intent statement). A model file can also carry other risks that this scan will not find. Whether weights themselves can hide harmful behavior is not something a file scan can rule out (hypothesis, to be confirmed with the security team).
- **Large data and long runs:** At 140 GB, a failed run wastes a lot of time and storage, and a half-finished run leaves large files behind. Clean-up has to be reliable.
- **Model license:** A public model still comes with license terms. Whether those terms allow copying the files into the company account is not yet known (assumption).
- **Model fit for Bedrock:** A model on Hugging Face can look right and still fall outside Bedrock's limits, such as size, context length, or architecture details. The first version does not load into Bedrock, so a mismatch would only show up later.
- **Public source changes:** A public model repository can be changed or removed by its owner. Importing a fixed version of the files reduces this risk.

## Conditions for Proceeding

- The first version stays limited to import, data leak scan, clean-up, and the failure email, as the intent statement sets out.
- Before the first import, someone picks a specific public model whose architecture is on Bedrock's supported list and whose files are under Bedrock's size limit, and checks its license.
- The design stage confirms that the compute used for the import and scan can handle about 140 GB and a run of several hours.

## Assumptions & Open Questions

- [assumption] The estimated download times (about 20 minutes to about 3 hours) assume sustained speeds between 1 Gbps and 100 Mbps. The real figure is not known.
- [assumption] Public Hugging Face model files can be downloaded without an account, within whatever limits Hugging Face applies to anonymous downloads.
- [assumption] The company's own security policy is the only compliance rule (Feasibility Q4). Nobody has checked it for rules on where model files may be stored or for how long.
- [assumption] The license of the chosen model allows the team to store a copy of the files in its own AWS account.
- Open question: Which public model will be imported first? Families on Bedrock's list that publish public models, such as Qwen, Mistral, or GPT-OSS, are candidates, and each size has to be checked for gating and license on its own Hugging Face page.
- Open question: Which tool or rule set will be used to find secrets, credentials, and personal data? This is a design decision for a later stage.
