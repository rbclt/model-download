# Evidence

## Inspected

- Project type: greenfield. The folder has no code and no git history, so there is no branching, CI, deployment or code-style evidence to read. Collaborators is off, so the lead worked alone and no support-agent contributions exist.
- `aidlc/spaces/default/memory/org.md`: the five matching sections (Way of Working, Walking Skeleton, Testing Posture, Deployment, Code Style) used as suggested answers for the interview.
- `aidlc/spaces/default/memory/team.md`: holds only template comments, so there is no earlier affirmed baseline.
- `aidlc/spaces/default/memory/phases/inception.md`: phase rules (testable requirements, traceability).
- `ideation/feasibility/feasibility-questions.md`: Q4 (only the company security policy), Q5 (Python), Q6 (AWS CDK), Q1 (common account exists), Q7 (a few days, kept small), Q8 (no blockers).
- `ideation/scope-definition/scope-document.md`: deadline end of tomorrow, one import at a time, riskiest part first, scan covers data leaks only.
- `ideation/intent-capture/intent-statement.md`: backend, event-driven, EventBridge start event, SNS failure email, one decision owner.
- `practices-discovery-questions.md`: completed interview with the consolidated summary confirmed as "Looks correct".

## Inferred

- Snake_case naming follows Python convention. The team did not state it.
- A thin end-to-end slice fits the scope document's "build the riskiest part first" sequencing. The team confirmed it in Q2.
- The timestamp line says `at commit none` because no git commit exists.

## Interview Decisions

- Q1 Way of Working: A. Work directly on one main branch and commit small changes often.
- Q2 Walking Skeleton: A. Yes, build a thin end-to-end slice first.
- Q3 Code Style: A. Black to format and Ruff to check Python code.
- Q4 Testing method: A. Write the code first, then write tests for it (Methodology: test-after).
- Q5 Coverage: A. At least 80 percent of lines. The team chose this knowingly. It may be hard to reach by the end-of-tomorrow deadline.
- Q6 Deployment: A. Deploy automatically to staging when a change is merged, and to production only after a manual approval.
- Q7 Hard rules: A and B. Recorded as `NEVER put secrets, tokens, or credentials in code or logs` and `ALWAYS remove downloaded files and temporary resources when an import fails`. Option C (scan every file before the final S3 location) was not chosen, so it is not a rule here.

## Unresolved Uncertainty

- Git was not found on the machine, and no repository exists. The chosen single-main-branch practice needs one, so the team has to set it up before the first commit.
- The 80 percent line-coverage floor may not be reachable by the end-of-tomorrow deadline. It was chosen knowingly. Whether to relax it later is the team's call.
- The test framework is not chosen.
- No staging environment or pipeline exists yet in the folder. Automatic deploy on merge to staging, with manual approval for production, depends on building them.
- Whether the first end-to-end test uses a smaller public model than the 140 GB case is still open (carried from the scope document).
