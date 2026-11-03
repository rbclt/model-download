# User Stories Questions

## Story Plan

- **Persona approach:** Write stories for the people who touch an import: the engineer who starts it, the people who receive the failure email, and optionally the team that runs the application.
- **Story format:** "As a [persona], I want [goal], so that [benefit]", following INVEST (Independent, Negotiable, Valuable, Estimable, Small, Testable). Acceptance criteria use Given, When, Then.
- **Prioritization:** Each story gets a MoSCoW priority taken from the intent backlog (`intent-backlog.md`): items B-1 to B-5 are Must Have and B-6 is Could Have.
- **Breakdown options:** by workflow step, by persona, or by requirement group.
- **Traceability:** Every requirement in `requirements.md` is traced to one or more stories.

## Q1. [Product owner] Which people should the stories be written for? (select all that apply)

A. The machine learning or data science engineer who starts an import
B. A member of the security team, who receives the failure email and gives input on scope
C. A platform engineer, who runs the application and removes leftovers when a removal fails
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q2. [Product owner] How should the stories be split up?

A. By step of an import: start, download, scan, keep the files, handle a failure, send the email, keep a record (follows the intent backlog)
B. By person: all stories of one person together
C. By requirement group: one group of stories for each of FR1 to FR6
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q3. [Quality engineer] How detailed should each story be?

Each story needs a few acceptance criteria. The team's testing practice (test-after, at least 80 percent of lines covered) means every criterion becomes a test.

A. One story per backlog item, with 3 to 6 acceptance criteria that include the failure path
B. Smaller stories, one for each rule and each failure path, with 2 to 3 criteria each
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
