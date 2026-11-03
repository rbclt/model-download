# RAID Log

Risks, Assumptions, Issues, and Dependencies for the first version. Sources are `intent-statement.md` (Intent Capture), the confirmed answers in `feasibility-questions.md`, your feedback at the approval gate, and the Amazon Bedrock Custom Model Import documentation.

## Risks

| ID | Risk | Likelihood | Impact | Mitigation idea |
|----|------|------------|--------|-----------------|
| R-1 | The import and scan of a 140 GB model take longer than short-lived compute allows. | High | High | Choose compute in the design stage that can run for hours and holds 140 GB. |
| R-2 | A failure leaves large files or resources behind, which costs money and keeps unscanned files around. | Medium | High | Make clean-up a step that runs after any failure, and check it with a test that forces a failure. |
| R-3 | The data leak scan misses a risk because it only checks for secrets, credentials, and personal data. | Medium | Medium | Say clearly in the user-facing text that the first version checks for data leaks only. Plan further checks for a later version. |
| R-4 | A public model repository is changed or removed by its owner between runs. | Low | Medium | Import a fixed version of the files and record which version was scanned. |
| R-5 | The few days of work are not enough. | Medium | Medium | Hold the scope to import, scan, clean-up, and the failure email. |
| R-6 | The chosen model's license does not allow storing a copy in the company account. | Low | High | Check the license of the chosen model before its first import. |
| R-7 | The chosen model falls outside Bedrock's documented limits (architecture, size, or context length) and cannot be imported later. | Medium | Medium | Check the model against the Bedrock list and limits before its first import. |
| R-8 | Hugging Face limits or slows anonymous downloads of very large files. | Medium | Medium | Measure a real download early in the build and adjust the plan. |

## Assumptions

| ID | Assumption |
|----|------------|
| A-1 | Download speeds are between 100 Mbps and 1 Gbps, which gives roughly 3 hours to 20 minutes for 140 GB. |
| A-2 | The company's own security policy is the only compliance rule and contains nothing that blocks storing open-weight model files. |
| A-3 | The license of the chosen public model allows a copy in the team's own AWS account. |
| A-4 | The existing common AWS account has the permissions and quotas this application needs. |
| A-5 | Public model files can be downloaded without an account, within Hugging Face's limits for anonymous downloads. |

## Issues

| ID | Issue | Owner |
|----|-------|-------|
| I-1 | The intent statement named Llama, while you moved away from Llama toward any public model Bedrock supports. Intent Capture was approved before this change. At your request the intent statement now says "open-weight model". The original request text and the Intent Capture answers still contain the word, because they are kept as you wrote them. | Resolved in the intent statement. |
| I-2 | The Q3 answer in the feasibility questions (no Hugging Face account yet) no longer matches the scope. Only public models are in scope, which need no account. | Replaced by gate feedback. |

## Dependencies

| ID | Dependency | Needed for |
|----|------------|------------|
| D-1 | A specific public Hugging Face model with a Bedrock-supported architecture, chosen before the first import. | The first import. |
| D-2 | The existing common AWS account (Feasibility Q1). | Storing files and creating resources. |
| D-3 | The security team's input on scope and priorities. | Scope decisions (intent statement). |
| D-4 | A way to receive the start event and send the failure email, set up in the common account. | Starting imports and telling the user about failures. |

## Assumptions & Open Questions

- Open question: Who chooses the first model, and who checks its license?
- Open question: Which of R-1 to R-8 does the owner want to accept, and which need a plan before the design stage?
