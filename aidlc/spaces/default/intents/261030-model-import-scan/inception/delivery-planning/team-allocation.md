# Team Allocation

This document says who builds each Bolt. A **Bolt** is one pass of building over a piece of the work, ending in something that runs and can be checked. A **mob** is a group that works on one thing together; here the mob is a single AI agent, because the team-formation stage was skipped in the `mvp` plan and no human team was set up. The Bolts are listed in `bolt-plan.md`.

## Assignment

| Bolt | Unit of Work | Mob | Notes |
|------|--------------|-----|-------|
| B1 | u1-import-slice | aidlc-developer-agent (AI) | Walking skeleton, built first |
| B2 | u2-import-rules | aidlc-developer-agent (AI) | Starts after B1 passes its checks |

All Bolts are executed by aidlc-developer-agent. With one mob there is no Program Board (the shared view that lines up several teams' Bolts), and the Bolts run one after another, not at the same time. This matches the Construction setting of one Unit at a time.

## People Outside the Mob

| Person | Role | What they decide or supply | When |
|--------|------|----------------------------|------|
| Owner | Decides scope and priorities | Approves each stage; supplies the team mailbox address, the verified email sender address, and the first model | Before the first deployment to staging |
| Security team | Gives input | Advises on what the scan must look for; does not approve the design before deployment | During the scan rules work in B1 |
| Approver for production | Manual approval | Approves a deployment to production after staging | Only if the team deploys to production |

## Assumptions & Open Questions

- [assumption] The owner is the one person who approves each stage, as stated in `team-practices.md`.
- Open question: Who approves a production deployment? The team practice says a manual approval is needed, but no person is named. The `mvp` plan has no deployment stage, so this does not block the first version.
