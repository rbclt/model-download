# Intent Capture Questions

## Sources

- [desc] Initial description: "create a application for bringing Llama model from Hugging face to Amazon S3 by importing it from Hugging Face to a common aws account and then scan for data leak.If data leak or eval fails then stop the process and clean up the resources and any artifacts downloaded and let the user know about the failure. This process should be triggered by an event."
- [scope] Workflow-selected scope: `mvp`.

## Q1. [Product owner] What problem should this application solve?

This sets the purpose that every later decision is checked against.

A. Give people a safe, repeatable way to get Llama model files from Hugging Face into our shared AWS account, instead of downloading them by hand
B. Keep unchecked model files from entering our AWS environment
C. Both A and B
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q2. [Product owner] Who will use the application, and who feels the pain today?

A. Internal machine learning or data science engineers who need Llama model files in AWS
B. Internal platform or security team who approve and run model imports
C. External customers
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q3. [Product owner] Why is this needed now?

A. A security or compliance rule requires checking open model files before they enter our AWS account
B. Teams are waiting to use Llama models and there is no approved path yet
C. Manual downloads have already caused, or nearly caused, a problem
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q4. [Product owner] What does success look like for the first version? (select all that apply)

A. Every import ends in exactly one of two outcomes: clean model files stored in S3, or everything removed and the user told why it failed
B. No model file reaches the final S3 location without passing the scan
C. A target time for a full import, which you will name under X
D. Not yet defined
X. Other (please specify)

[Answer]: A, B

## Q5. [Product owner] What should this first version leave out? (select all that apply)

The workflow plan is already chosen (mvp). This asks only what the product itself should not do yet.

A. Loading the model into Amazon Bedrock (stop at storing it in S3)
B. Models other than Llama
C. A human approval step before the model is stored
D. Fine-tuning or changing the model
E. Not applicable, nothing is out for now
X. Other (please specify)

[Answer]: A

## Q6. [Product owner] Who decides the scope and priorities for this application, and who only gives input?

A. One owner decides, and the security team gives input
B. The security team decides, and the machine learning team gives input
C. A small group of both teams decides together
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q7. [Security engineer] Your request says "if data leak or eval fails". What does "eval" mean here?

This decides what kind of checks the scan has to include.

A. Checking the model's quality, for example running it against a benchmark
B. Checking for risky code, such as `eval` or `exec` calls hidden in model config, tokenizer, or script files
C. Both A and B
D. Not yet defined
X. Other (please specify)

[Answer]: X. For MVP just the scan

## Q8. [Platform engineer] What kind of event should start an import?

A. A request file uploaded to an S3 bucket
B. An Amazon EventBridge event that carries the Hugging Face model name
C. A message on an Amazon SQS queue
D. A scheduled time
E. Not yet defined
X. Other (please specify)

[Answer]: B

## Q9. [Operations engineer] How should the user be told when an import fails, and how often do you want updates otherwise?

A. An email through Amazon SNS on every failure, and nothing on success
B. A chat message (Slack or Microsoft Teams) on every failure and every success
C. An email on failure and a short summary once a week
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q10. [Security engineer] Follow-up to Q7: you said the first version is "just the scan". Which checks should make an import fail?

Your Q7 answer rules out model quality testing. This asks whether the scan covers risky code as well as data leaks.

A. Data leak checks only (secrets, credentials, and personal data inside the model files)
B. Data leak checks and risky code checks (`eval`, `exec`, or similar calls in model config, tokenizer, or script files)
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
