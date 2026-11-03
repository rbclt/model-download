# Stakeholder Map

## Stakeholders

| Stakeholder | Interest | Source |
|-------------|----------|--------|
| Internal machine learning or data science engineers | Want Llama model files in the shared AWS account through a safe, repeatable path, in place of downloading them by hand. | [Q1][Q2] |
| Security team | A security or compliance rule requires open model files to be checked before they enter the AWS account. | [Q3] |
| Single decision owner | Decides the scope and priorities of the application. | [Q6] |

## Decision-Makers and Influencers

| Role | Who | Source |
|------|-----|--------|
| Decision-maker | One owner decides scope and priorities. | [Q6] |
| Influencer | The security team gives input. | [Q6] |

## Communication Requirements

- The user whose import failed receives an email through Amazon SNS on every failure. [Q9]
- No message is sent on success, so there is no regular reporting cadence in the first version. [Q9]

## Assumptions & Open Questions

None.
