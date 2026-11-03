# Infrastructure Design Questions: u1-import-slice

Infrastructure Design maps the design onto AWS resources. Much is already fixed by earlier answers and is not asked again: AWS CDK in Python, us-east-1, the common AWS account, one Fargate worker with at most one task, an SQS queue in front, S3 for the files, Amazon SES for the email, CloudWatch for logs and alarms, and a merged change deploying to staging automatically with production behind a manual approval. These three questions cover what is still open.

## Q1. [Product owner] Which environments does the first version create?

A. One stack, named staging, in the common AWS account. A production stack is added later with the same code and a manual approval (recommended for a first version due soon)
B. A staging stack and a production stack, both in the common AWS account
C. Staging and production in separate AWS accounts
X. Other (please specify)

[Answer]: A

## Q2. [Platform engineer] How are the holding area and the final location stored?

A. Two S3 buckets, one for the holding area and one for the final location. Each has its own bucket policy, lifecycle rules, and permissions, which keeps the rules simple (recommended)
B. One S3 bucket with the two prefixes `holding/` and `models/`
X. Other (please specify)

[Answer]: A

## Q3. [Platform engineer] Where does the worker container run on the network?

The worker needs to reach Hugging Face on the internet and the AWS services. It accepts no incoming connections.

A. A new small VPC created by the stack with public subnets and no NAT gateway. The task gets a public address, a security group allows no inbound traffic, and outbound is open. The cheapest option, with no fixed monthly network cost (recommended)
B. An existing VPC and subnets that you name. You supply their ids before the first deployment
C. A new VPC with private subnets and a NAT gateway: no public address on the task, at roughly 35 US dollars a month plus data charges
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

- Environments (Q1): one stack named staging in the common AWS account. A production stack is added later with the same code and a manual approval.
- Storage (Q2): two S3 buckets, one for the holding area and one for the final location, each with its own bucket policy and lifecycle rules.
- Network (Q3): a new small VPC with public subnets and no NAT gateway. The worker task gets a public address and a security group that allows no inbound traffic.
- Worker size (my design choice, not asked): 4 vCPU, 8 GiB memory, 30 GiB local disk.

**Confirm** — Does this all look correct before I generate the artifacts?

Looks correct — Generate the artifacts from these answers
Request changes — Revise one or more answers before generation

[Answer]: Looks correct
