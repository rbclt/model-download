# Practices Discovery Questions

This is a new project with no code and no history yet, so the suggested answers come from the framework's defaults, and the answers you give become the team's practices. Python and AWS CDK are already confirmed from the feasibility stage.

## Q1. [Engineering lead] How should the team use branches for this project?

I could not find git on this machine, so this also tells me whether to expect version control.

A. Work directly on one main branch and commit small changes often (the suggested default, good for a small, fast project)
B. Use short-lived branches and merge each one after a review
C. No version control is used yet
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q2. [Engineering lead] Build a thin end-to-end slice first? A walking skeleton is a minimal version that runs the whole way through, built first to prove the pieces connect before the real features go in.

Your build order already puts the download and scan first (scope answer Q2), so this decides whether the first piece should also connect to the start event and the failure email.

A. Yes, build a thin end-to-end slice first
B. No, build the pieces in the order of the backlog and connect them at the end
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q3. [Engineering lead] How should Python code be formatted and checked?

A. Black to format and Ruff to check (the suggested default for Python)
B. Ruff only
C. The team has its own settings (tell me under Other)
D. No formatter or checker for the first version
E. Not yet defined
X. Other (please specify)

[Answer]: A

## Q4. [Quality engineer] When should tests be written?

A. Write the code first, then write tests for it (the suggested default)
B. Write each test before the code it checks
C. Describe the behavior in plain scenarios first, then write code and tests to match
D. A mix of these (tell me which under Other)
E. Not yet defined
X. Other (please specify)

[Answer]: A

## Q5. [Quality engineer] What minimum share of the code must tests cover?

The suggested default for this kind of project is 80 percent of lines, but the end-of-tomorrow deadline may not leave time for that.

A. 80 percent of lines, as suggested
B. A lower floor, such as 50 percent (tell me the number under Other)
C. No percentage; tests only for the main behaviors: the scan, the clean-up, and the failure email
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q6. [Release engineer] How should changes reach AWS?

A. Deploy automatically to a staging environment when a change is merged, and deploy to production only after a manual approval (the suggested default)
B. Deploy by hand with AWS CDK from a developer's machine
C. Only one environment exists, and changes are deployed straight to it
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q7. [Security engineer] Are there hard rules every change must follow? (select all that apply)

A. Never put secrets, tokens, or credentials in code or logs
B. Always remove downloaded files and temporary resources when an import fails
C. Always scan every file before it reaches the final S3 location
D. None beyond what is already written down
X. Other (please specify)

[Answer]: A, B

## Consolidated Summary Confirmation

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
