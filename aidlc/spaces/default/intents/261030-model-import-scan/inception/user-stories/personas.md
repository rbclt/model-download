# Personas

## Model Importer (primary)

- **Role:** A machine learning or data science engineer on the team who needs the files of an open-weight model in the shared AWS account. (`requirements.md`, Intent Analysis; story plan Q1)
- **Goals:**
  - Get the files of a public model, at an exact version, into S3 in the common account without downloading them by hand.
  - Know that the files were scanned for data leaks before anyone uses them.
  - Hear quickly and clearly when an import did not work, and why.
- **Pain points today:**
  - Files are downloaded by hand with no safe, repeatable path into the shared account.
  - Nothing checks the files before they land in the account.
- **Context:**
  - Starts an import by sending an event with the model name, an exact version, and their own email address.
  - Works with smaller models in the current version, so an import runs for minutes to a few hours, not days.
  - Is not expected to clean up after a failed import. The application does that.

## Recipients Who Are Not Personas

- **The team mailbox and the security team** receive the failure email (`requirements.md`, FR5.4). They do not start imports in the first version, so no stories are written for them. Their needs show up as acceptance criteria on the failure email story.

## Persona Priority

1. Model Importer. The only persona in the first version.
