# Scope Definition Questions

Earlier stages already settled the main capabilities: an event starts an import, the model files are downloaded into S3, they are scanned for data leaks, everything is removed and the user is emailed if the scan fails, and loading into Amazon Bedrock is out of the first version. These questions cover what is still open.

## Q1. [Product owner] Which extras, if any, would you add to the first version if time allows? (select all that apply)

The first version has a few days of work (feasibility answer Q7), so anything chosen here is "could have": it goes in only if the must-have capabilities are done.

A. Keep a record of each import and its scan result so it can be looked at later
B. Send an email when an import succeeds, as well as when it fails
C. A way to check the progress of an import that is running
D. Check the model's architecture against the list Amazon Bedrock supports before downloading anything
E. None, the first version should include only the must-have capabilities
X. Other (please specify)

[Answer]: A

## Q2. [Product owner] In what order do you want the capabilities built?

A. Risk first: prove the hardest part (downloading and scanning a model of about 140 GB) before anything else
B. Value first: build the capability users will notice most, then the rest
C. Follow the flow of an import: event, download, scan, clean-up, failure email
D. Not yet defined
X. Other (please specify)

[Answer]: A

## Q3. [Product owner] Is any capability tied to a hard deadline?

A. No, there is no hard deadline beyond the few days already set aside
B. Yes, the whole first version has a fixed date (tell me the date under Other)
C. Yes, a single capability has a fixed date (tell me which one and the date under Other)
D. Not yet defined
X. Other (please specify)

[Answer]: B

## Q4. [Product owner] Who is allowed to start an import?

A. Any machine learning or data science engineer on the team
B. Only a named group, such as the platform team
C. Anyone who can send the start event, with no extra restriction in the first version
D. Not yet defined
X. Other (please specify)

[Answer]: C

## Q5. [Platform engineer] Should the first version handle one import at a time, or several at once?

A. One import at a time is enough
B. Several imports at once, so two people can import different models together
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Q6. [Product owner] You said the whole first version has a fixed date (Q3). When is it due?

Q3 asked you to give the date under Other, and no date has been recorded yet.

A. Within the next 3 working days
B. Within the next 5 working days
C. Within the next 10 working days
D. A specific calendar date (tell me the date under Other)
E. Not yet defined
X. Other (please specify)

[Answer]: X. Within tomorrow end of the day

## Q7. [Product owner] The deadline for the whole first version is now the end of tomorrow, but earlier you set aside a few days of work (feasibility answer Q7). Which one governs?

These two do not fit together, so the scope depends on your pick.

A. The deadline governs: the first version is due at the end of tomorrow, and the scope shrinks to fit
B. The few days stand, and the deadline is only for something that can be shown working, not the finished first version
C. Not yet defined
X. Other (please specify)

[Answer]: A

## Consolidated Summary Confirmation

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
