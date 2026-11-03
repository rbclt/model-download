# Reliability Requirements: u2-import-rules

This describes how the finished application behaves when something goes wrong, once this Unit is added. It details inception NFR4 (exactly one outcome) in `requirements.md` and uses the rules in `rules.md` (BR7.1, BR8.1 to BR8.4, BR9.1 to BR9.6, BR10.1 to BR10.5), the lifecycle and failure table in `functional-spec.md`, and the failure behaviour in `contract-summary.md`. The reliability requirements of u1-import-slice (NFR4.1 to NFR4.11) stay, except where a requirement below says it changes one. Each requirement carries the ID of the inception NFR it details.

| ID | Requirement | Target | Source |
|----|-------------|--------|--------|
| NFR4.12 | Every start event that the application finishes handling ends in exactly one of five outcomes: stored, removed-with-email, removed-email-failed, rejected, or refused-already-stored (BR10.1). The outcome object is created once, with a condition, and never changed. This extends NFR4.1 of u1, which named three outcomes. | One outcome per start event | NFR4 |
| NFR4.13 | The completion marker is the one commit point of a stored model: it is the last object written to the final location, it names the importId, and no folder without it counts as a model (BR9.1). | Marker written last | NFR4 |
| NFR4.14 | Once the marker is written, nothing that follows can remove the model, delete the marker, or change the outcome from stored. An error in the clean-up of the holding area after the marker is logged, and the outcome is stored with removalComplete false (BR9.6). | No loss after the marker | NFR4 |
| NFR4.15 | After a failed import, the removal is proved by a listing of the holding folder of the import, the model version's folder, the incomplete uploads, and the local disk. Every entry still found, and every delete that failed, is reported as a leftover, and removalComplete is true only when there are none (BR9.3). | Proof by listing for every failed import | NFR4 |
| NFR4.16 | If the marker of a failed import cannot be deleted after 3 tries, the files stay with the marker, and the marker is reported as the first leftover with a warning that the final location may hold a complete copy (BR9.2). This is the one case where a failed import leaves files in the final location, and it needs two failures at once. | 3 tries; reported | NFR4 |
| NFR4.17 | A message that is received again is not run twice. If its outcome exists, nothing is done. If the final location holds a marker with its own importId, it finishes as stored with no email. If the marker holds another importId, it is refused as already stored (BR7.1, BR10.1). | No second import, no second email for the same outcome | NFR4 |
| NFR4.18 | An email that cannot be sent never stops the outcome. The problem is logged as `email.not_sent` and recorded in the outcome as `emailSent` false with the roles not reached. For a failed import the outcome is removed-email-failed; for a rejected or already-stored event the outcome keeps its name (BR10.3, BR10.4). | Outcome always recorded | NFR4 |
| NFR4.19 | An unexpected stop of a running import is handled as u1 built it, and the automatic clean-up that NFR4.2 of u1 expected from this Unit is not built (owner's decision, Q2 option A). The message goes to the dead-letter queue, the team is alerted within 15 minutes (NFR5.8, signals c and d), the leftover files expire within 2 days (NFR4.9), and a person can send the message again, which starts afresh and finds its own work (BR8.1, BR7.1, BR10.1). This replaces the last sentence of NFR4.2 of u1. | Alert within 15 minutes; files expire in 2 days | NFR4 |
| NFR4.20 | The new removal steps are safe to run twice with the same result: deleting a marker, a folder, or an upload that is already gone is not an error (extends NFR4.3 of u1 to BR9.2 and BR9.3). | Safe to repeat | NFR4 |
| NFR4.21 | The deadline never cancels work that has produced a complete model: no deadline check applies after the last check just before the marker write (BR8.2, BR8.3, BR9.6). | No model lost to the deadline | NFR4 |
| NFR4.22 | The record of an import is written before each step as in u1, and for a rejected or already-stored event once at the end, after the outcome exists, so it never shows done without an outcome (BR11.1). The outcome object is the authority, and a missing record does not change an outcome. | Record never done without outcome | NFR4 |

## How Each Requirement Is Verified

| IDs | Verified by |
|-----|-------------|
| NFR4.12, NFR4.18 | Unit tests that force each of the five outcomes, a failed email, and a second creation of the outcome |
| NFR4.13, NFR4.14, NFR4.15, NFR4.16, NFR4.20, NFR4.21 | The forced-failure proof of `functional-spec.md`: failure at the first, a middle, and the last file copy, at the marker write, with the marker delete failing, with a delete failing, with the listing failing, with the deadline reached in a copy, and with the holding area delete failing after the marker; each case checks the final location and removalComplete; the removal is run twice |
| NFR4.17 | Tests that receive the same message twice after an outcome, after only a marker with its own importId, and with a marker of another importId |
| NFR4.19 | A deployment check that the dead-letter queue and the alerts of NFR5.8 exist (built in u1); a test that stops a run after the marker and sends the message again |
| NFR4.22 | A test that stops after the outcome of a rejected event and checks the outcome stands with no record |

## Availability

No availability target is set, as in u1. Imports are triggered by people a few times a week, and a failed or missed import is shown by an email or by the absence of one and can be sent again.

## Recovery

- A failed import is recovered by sending the same event again after the cause is fixed. The second run finds the final location without a marker, because the failed run removed what it created (BR9.2), and starts afresh.
- An import whose removal was incomplete is cleaned by hand using the list of leftovers in the email (BR9.4). The log holds the full list.
- A run that stopped unexpectedly is recovered by a person who sends its message from the dead-letter queue again (NFR4.19).

## Assumptions & Open Questions

- [assumption] The case in NFR4.16 needs a marker write that reports an error but took effect, followed by a marker delete that fails three times. It is rare, and it is reported rather than hidden.
- [assumption] After a stop between the email and the outcome, a second email for the same import is possible when the message is sent again. The owner accepts this for the first version (the Functional Design records the same assumption).
- Open question: Does the owner want the automatic clean-up for an unexpected stop later? NFR4.19 leaves it out of this version on the owner's decision.
