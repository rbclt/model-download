# Feasibility Questions

These questions treat the application as a way to import any open-weight model from Hugging Face. Llama is one example, not a limit.

## Q1. [Platform engineer] What is the state of the "common" AWS account where the model files will be stored?

This decides whether account setup is part of the work.

A. The common account already exists and we can use it as is
B. The common account exists, but needs new permissions or setup first
C. The common account does not exist yet and has to be created
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q2. [Platform engineer] What is the largest open-weight model you expect to import?

Model files range from a few gigabytes to hundreds of gigabytes, which changes how long an import takes and what it costs.

A. Small models (about 8 billion parameters or fewer, roughly 16 GB of files)
B. Medium models (about 70 billion parameters, roughly 140 GB of files)
C. Very large models (hundreds of billions of parameters, roughly 800 GB of files or more)
D. Sizes will vary, and we cannot name a limit yet
E. Not yet defined
X. Other (please specify)

[Answer]: B

## Q3. [Security engineer] Does your team already have access to download gated models on Hugging Face?

Many open-weight models (for example Llama and Gemma) are gated: a Hugging Face account has to accept the model's license before it can download the files. Other models are public and need no account.

A. Yes, we have a Hugging Face account and an access token, and we accept model licenses as needed
B. We have a Hugging Face account, but no access token yet
C. We have no Hugging Face account yet
D. We only expect to import public models that need no account
E. Not yet defined
X. Other (please specify)

[Answer]: C

## Q4. [Compliance officer] Which regulatory or compliance requirements apply to the model files and the scan results? (select all that apply)

A. None beyond the company's own security policy
B. Data must stay in a specific AWS region (data residency)
C. An audit standard such as SOC 2 or ISO 27001 applies to this account
D. A record of every import and scan result must be kept for audit
E. Not yet defined
X. Other (please specify)

[Answer]: A

## Q5. [Engineering lead] Which programming language does the team prefer for building this application?

A. Python
B. TypeScript or JavaScript
C. Java
D. No preference
E. Not yet defined
X. Other (please specify)

[Answer]: A

## Q6. [Engineering lead] How does the team create and manage AWS resources today?

A. AWS CDK
B. AWS CloudFormation
C. Terraform
D. By hand in the AWS console
E. Not yet defined
X. Other (please specify)

[Answer]: A

## Q7. [Product owner] What budget and timeline limits apply to the first version?

A. A few days of work, kept as small as possible
B. A few weeks of work
C. No fixed limit, but cloud costs must stay low
D. No fixed limit
E. Not yet defined
X. Other (please specify)

[Answer]: A

## Q8. [Product owner] Are there organizational blockers that could delay this work?

A. None known
B. The security team must approve the design before anything is deployed
C. A change freeze or competing project limits when we can deploy
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
