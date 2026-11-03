# Scalability Design: u2-import-rules

This describes how this Unit meets the requirements in `scalability-requirements.md`. It uses the components in `logical-components.md`, the rules in `rules.md` (BR6.1, BR7.1, BR9.4, BR10.3, BR11.2), the steps in `functional-spec.md`, and the technology in `tech-stack-decisions.md`. The other requirement documents are `performance-requirements.md`, `security-requirements.md`, `reliability-requirements.md`, and `observability-requirements.md`. The scaling design of u1-import-slice (a FIFO queue with one message group in front of a worker limited to one task, scaling between 0 and 1) is not changed.

## Design Decisions

| Requirement | Design |
|-------------|--------|
| NFR2.8 (a burst of bad events) | Rejected and already-stored events are messages of the same queue. The worker takes them one at a time in order, like imports. Each ends by deleting its message, so the next one starts at once. The worker stays up while the queue has messages and leaves after 2 empty minutes (u1). A burst of 100 such events costs about 100 times the time of NFR1.12 and no download |
| NFR2.9 (20 of 50 emails) | The per-day counter of u1 becomes two counters, both kept as objects in `state/counters/` and updated with the same conditional write on the ETag. `emails-<UTC date>.json` keeps counting the emails of imports that ran and now stops at 30. `notices-<UTC date>.json` counts the emails of rejected and already-stored events and stops at 20. An attempt past a limit is not sent; the log records `email_limit_reached` with the kind of counter. The day changes at midnight UTC, as u1 built it |
| NFR2.10 (record size) | The record does not store the list of copied keys: it stores the number of files copied and the last key copied (u1's amendment of NFR4.8). Findings are stored as cleaned file name (at most 200 characters), place (at most about 40), and kind (about 20), so about 300 bytes each. 200 findings are about 60 KB, and the rest of the record is a few KB, under the 100 KB target. A test builds a record with 200 findings and 1,000 files copied and checks its size |
| NFR2.11 (object count) | Each start event writes the record and the outcome. The counters are two objects a day. The existing 30-day expiry of `state/` keeps the total under 500 objects for 5 events a day |
| NFR2.12 (20 leftovers) | The custodian returns the full list of leftovers. The notifier takes the first 20 in the order area then location and the total. The full list is logged one record per leftover, up to 200 records, then one record with the total |

## Scaling Triggers and Capacity

No new trigger. The worker scales from 0 to 1 on a waiting message, as in u1. A deployment setting is not added for the new limits: the numbers 20, 30, 200, and 20 are constants in the code, named and tested, so they can be changed in one place.

## Changes to Code Built in u1

- `store.py`: `reserve_email_attempt` takes the name of the counter and the limit. `DAILY_EMAIL_LIMIT` (50) becomes two constants (30 and 20).
- `notifier.py`: every send names its counter: the `notices` counter for the rejection email and the already-stored email, the main counter for the failure email.
- `models.py`: the record gets `findings` and `findingsTotal`, with the size guards above.

## Assumptions & Open Questions

- [assumption] The two counters are two objects, not one object with two numbers, so each keeps the conditional write that u1 already tested.
- [assumption] The leftover log records are capped at 200 so a pathological removal cannot flood the log. The total is always logged.
- Open question: Should the 20 and 30 be settings? This version keeps them as constants (the owner can ask for settings later).
