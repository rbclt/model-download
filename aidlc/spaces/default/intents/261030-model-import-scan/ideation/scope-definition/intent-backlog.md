# Intent Backlog

Each item is a proto-Unit: a piece of work that later stages turn into units of work. Items are listed in build order. The order follows your choice of risk first (Scope Q2). Priorities use MoSCoW. The sources are `intent-statement.md`, `feasibility-assessment.md`, `constraint-register.md`, and the confirmed answers in `scope-definition-questions.md`.

## Backlog

| Order | ID | Item | Priority | Depends on | Why this priority |
|-------|----|------|----------|------------|-------------------|
| 1 | B-1 | Download the files of a public Hugging Face model into S3, including a medium model of about 140 GB | Must | None | The hardest and riskiest part (risks R-1 and R-8 in the RAID log). Proving it first shows whether the deadline is realistic. |
| 2 | B-2 | Scan the downloaded files for data leaks (secrets, credentials, personal data) | Must | B-1 | The reason the application exists (intent statement, Initiative Trigger). |
| 3 | B-3 | Remove the downloaded files and all resources created for the import when the scan fails or any step fails | Must | B-1, B-2 | Every import must end in one of two outcomes (intent statement, Success Metrics). |
| 4 | B-4 | Email the user about every failure | Must | B-3 | The user must be told why an import failed (intent statement). |
| 5 | B-5 | Start an import from an event that carries the model name | Must | B-1 | The process must be triggered by an event (original request). It is built last because it only connects the other parts. |
| 6 | B-6 | Keep a record of each import and its scan result | Could | B-2, B-3 | Chosen as the one extra (Scope Q1). It goes in only if B-1 to B-5 are done. |

## Won't Have This Time

| Item | Reason |
|------|--------|
| Load the model into Amazon Bedrock | Out of the first version (intent statement). |
| Model quality testing and risky code checks | The first version scans for data leaks only (intent statement). |
| Email on success | Not chosen as an extra (Scope Q1). |
| A way to check the progress of a running import | Not chosen as an extra (Scope Q1). |
| A check of the model's architecture against Bedrock's list before downloading | Not chosen as an extra (Scope Q1). |

## Prioritization Notes

- Five of the six items are Must Have, which is more than the usual limit of about 60 percent. All five are needed for the flow in the original request, so none can move down. The scope is kept small by building each one in its thinnest form.
- The riskiest item (B-1) is first, so a problem with a 140 GB download shows up early.

## Assumptions & Open Questions

- [assumption] The items are sized as thin as possible. No effort estimates have been made yet.
- Open question: Does the owner accept that five of six items are Must Have, given the deadline?
