"""FailureNotifier: the emails about a start event that did not end in a stored model.

Three kinds of email, each built from typed inputs only (NFR3.19):

* the failure email of an import that ran (BR5.3, BR5.4, BR5.6, BR9.4, BR9.5) goes to the sender
  and the team mailbox, in the format of contract C3;
* the rejection email (BR6.2, BR6.3) goes to the team mailbox, and to the sender when the
  sender's address is present and well-formed. It names the fields that failed and the event id
  (or the importId when the event id is not safe to show), never a value from the event;
* the already-stored email (BR7.2) goes to the sender and the team mailbox.

Rules that hold for all three:

* One separate SES message goes to each recipient, so a refusal for one address never stops the
  other (NFR3.9). The same address twice gets one message. The message is sent from the verified
  sender identity of the application.
* The email lists at most 20 findings (BR5.6) and at most 20 leftovers (BR9.4), with the totals.
  It carries file names, places, and kinds only, never a matched value (BR5.4). File names and
  leftover locations are cleaned first.
* Every attempt is counted in a durable per-day counter of ``store.py`` (NFR3.16, NFR3.18). The
  failure email counts in the main counter (limit 30); the rejection and already-stored emails
  count in the ``notices`` counter (limit 20), so a burst of bad events cannot use up the emails
  of failed imports. An attempt past a limit is not sent and writes one log record
  ``email_limit_reached``. When the counter cannot be read or written, nothing is sent.
* An email that cannot be sent is reported to the caller as a result per recipient, with the
  role (``sender`` or ``team``) and the kind of problem (``service-error`` or ``daily-limit``),
  never an address (BR10.3, BR10.4). The caller decides the outcome.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from model_import.event import RejectedEvent
from model_import.logs import JsonLogger, clean_file_name, event_reference, is_safe_event_id
from model_import.models import (
    EVENT_FIELDS,
    MAX_EMAIL_FINDINGS,
    MAX_LEFTOVERS_LISTED,
    MAX_REASON_LENGTH,
    ROLE_SENDER,
    ROLE_TEAM,
    FailureNotice,
    ImportRequest,
    Leftover,
    LeftoverArea,
    LeftoverKind,
    NoticeKind,
    Outcome,
    ScanFinding,
    final_prefix,
)
from model_import.store import (
    FAILURE_COUNTER,
    FAILURE_EMAIL_LIMIT,
    NOTICE_COUNTER,
    NOTICE_EMAIL_LIMIT,
    ImportStore,
    StoreError,
)

SUBJECT_VERSION_LENGTH = 12  # contract C3: the first 12 characters of exactVersion
PROBLEM_SERVICE_ERROR = "service-error"  # BR10.3: the email service did not accept the email
PROBLEM_DAILY_LIMIT = "daily-limit"  # BR10.3: the limit of the day was reached
REASON_ALREADY_STORED = "this model version is already stored"  # BR7.2
MARKER_NOTE = (
    "the final location may hold a complete copy of this version although the import failed"
)
TIME_NOT_AVAILABLE = "not available"
_LINE_PLACE = re.compile(r"line (\d+), columns (\d+) to (\d+)")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_MISSING = "missing"
_BAD_FORM = "not in the expected form"


@dataclass(frozen=True)
class RecipientResult:
    """What happened to the email of one address. ``roles`` are the roles it stands for.

    Normally one role. When the sender's address equals the team mailbox, one email stands for
    both roles. ``problem`` is set when the email was not accepted. No address is kept here.
    """

    roles: tuple[str, ...]
    sent: bool
    problem: str | None = None


@dataclass(frozen=True)
class NotificationResult:
    """What happened to the email. ``sent`` and ``not_sent`` count recipients (addresses)."""

    notice: FailureNotice
    sent: int
    not_sent: int
    recipients: tuple[RecipientResult, ...] = ()

    @property
    def any_sent(self) -> bool:
        """True when at least one recipient was sent the email."""
        return self.sent > 0

    @property
    def email_sent(self) -> bool:
        """BR10.4: true only when every email that had to go out was accepted."""
        return self.sent > 0 and self.not_sent == 0

    @property
    def not_reached(self) -> tuple[tuple[str, str], ...]:
        """BR10.3: the (role, problem) pairs of the emails that were not accepted."""
        return tuple(
            (role, item.problem or PROBLEM_SERVICE_ERROR)
            for item in self.recipients
            if not item.sent
            for role in item.roles
        )

    @property
    def roles_not_reached(self) -> tuple[str, ...]:
        """BR10.3: the roles whose email was not accepted, in the order sender, team."""
        roles = {role for role, _ in self.not_reached}
        return tuple(role for role in (ROLE_SENDER, ROLE_TEAM) if role in roles)


def build_subject(
    model_name: str, exact_version: str, outcome: Outcome = Outcome.REMOVED_WITH_EMAIL
) -> str:
    """Contract C3: ``Model import <outcome>: <modelName> <first 12 characters of version>``."""
    return f"Model import {outcome.value}: {model_name} {exact_version[:SUBJECT_VERSION_LENGTH]}"


def build_rejection_subject(reference: str) -> str:
    """BR6.3: ``Model import rejected: event <eventId>`` (the importId when the id is unsafe)."""
    return f"Model import rejected: event {reference}"


def _plain(text: str) -> str:
    return " ".join(_CONTROL_CHARACTERS.sub(" ", text).split())[:MAX_REASON_LENGTH]


def _iso(value: datetime | None) -> str:
    if value is None:
        return TIME_NOT_AVAILABLE
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _finding_order(finding: ScanFinding) -> tuple[str, int, int, str, str]:
    """BR5.6: file path, then line, then column, compared as numbers.

    A place that is not a line (for example ``whole file``) sorts before the lines of its file.
    """
    match = _LINE_PLACE.fullmatch(finding.place)
    line, column = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    return (finding.file_name, line, column, finding.place, finding.kind.value)


def order_findings(findings: Sequence[ScanFinding]) -> list[ScanFinding]:
    """BR5.6, BR11.2: all findings in the order file path, line, column."""
    return sorted(findings, key=_finding_order)


def select_findings(findings: Sequence[ScanFinding]) -> tuple[ScanFinding, ...]:
    """BR5.6: the first 20 findings in the order file path, line, column."""
    return tuple(order_findings(findings)[:MAX_EMAIL_FINDINGS])


def _leftover_order(item: Leftover) -> tuple[str, str, str]:
    return (item.area.value, item.location, item.kind.value)


def select_leftovers(leftovers: Sequence[Leftover]) -> tuple[Leftover, ...]:
    """BR9.4: the first 20 leftovers in the order area, then location."""
    return tuple(sorted(leftovers, key=_leftover_order)[:MAX_LEFTOVERS_LISTED])


def _is_marker_leftover(item: Leftover) -> bool:
    return (
        item.kind is LeftoverKind.OBJECT
        and item.area is LeftoverArea.FINAL_LOCATION
        and item.location.endswith("/_COMPLETE")
    )


def _older_files_line(count: int) -> str:
    if count == 1:
        return (
            "1 older unfinished file of an earlier import of this version "
            "was also removed from the final location"
        )
    return (
        f"{count} older unfinished files of an earlier import of this version "
        "were also removed from the final location"
    )


def build_body(notice: FailureNotice, model_name: str, exact_version: str) -> str:
    """Contract C3 body fields, then the findings, the leftovers, and the older files.

    BR5.4, BR5.6, BR9.4, BR9.5. No matched value and no address appears.
    """
    lines = [
        f"modelName: {model_name}",
        f"exactVersion: {exact_version}",
        f"reason: {_plain(notice.reason)}",
        f"removalComplete: {'yes' if notice.removal_complete else 'no'}",
    ]
    if notice.findings_listed:
        lines += ["", "findings:"]
        for number, finding in enumerate(notice.findings_listed, start=1):
            name = clean_file_name(finding.file_name)
            lines.append(f"{number}. {name}: {_plain(finding.place)} ({finding.kind.value})")
        lines.append(f"{notice.findings_total} findings in total")
    if notice.leftovers_listed:
        lines += ["", "leftovers (the team should remove these by hand):"]
        for number, item in enumerate(notice.leftovers_listed, start=1):
            lines.append(f"{number}. {item.kind.value}, {item.area.value}: {item.location}")
        if notice.leftovers_total > len(notice.leftovers_listed):
            lines.append(f"{notice.leftovers_total} leftovers in total")
        if any(_is_marker_leftover(item) for item in notice.leftovers_listed):
            lines.append(f"note: {MARKER_NOTE}")
    if notice.older_files_removed > 0:
        lines += ["", _older_files_line(notice.older_files_removed)]
    return "\n".join(lines) + "\n"


def build_rejection_body(
    reference: str,
    reference_label: str,
    event_time: datetime | None,
    failed: Sequence[str],
    missing: Sequence[str],
) -> str:
    """BR6.3: the request was rejected; the failed fields by name; the event id and time.

    The only inputs are the reference (a safe id), a time the code wrote again, and field names
    from a fixed set, so no value from the event can reach the text.
    """
    names = [name for name in EVENT_FIELDS if name in failed]
    lines = [
        "The request was rejected and nothing was downloaded.",
        "",
        "fields that failed:",
    ]
    lines += [f"- {name}: {_MISSING if name in missing else _BAD_FORM}" for name in names]
    lines += ["", f"{reference_label}: {reference}", f"eventTime: {_iso(event_time)}"]
    return "\n".join(lines) + "\n"


def build_already_stored_body(model_name: str, exact_version: str, reason: str) -> str:
    """BR7.2: the model, the version, the reason, and the final location. No removal is claimed."""
    return (
        "\n".join(
            [
                f"modelName: {model_name}",
                f"exactVersion: {exact_version}",
                f"reason: {_plain(reason)}",
                f"finalLocation: {final_prefix(model_name, exact_version)}",
            ]
        )
        + "\n"
    )


class FailureNotifier:
    """BR5.3, BR6.2, BR7.2: sends the emails about a start event, one message per recipient."""

    def __init__(
        self,
        ses_client: Any,
        store: ImportStore,
        from_address: str,
        team_address: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        logger: JsonLogger | None = None,
        configuration_set: str | None = None,
    ) -> None:
        self._ses = ses_client
        self._store = store
        self._from = from_address
        self._team = team_address
        self._clock = clock
        self._log = logger
        self._configuration_set = configuration_set

    # -- the failure email of an import that ran (BR5.3) ------------------------------------

    def send(
        self,
        request: ImportRequest,
        reason: str,
        removal_complete: bool,
        findings: Sequence[ScanFinding] = (),
        findings_total: int = 0,
        *,
        leftovers: Sequence[Leftover] = (),
        older_files_removed: int = 0,
    ) -> NotificationResult:
        """BR5.3, BR5.6, BR9.4, BR9.5: send the failure email to the sender and the team.

        ``findings_total`` is the sum of ``matchesTotal`` of the scan (all matches, including
        those beyond the 100 recorded for one file). ``leftovers`` is the full list of the
        removal; the email lists the first 20 and the total. Never raises for a send problem: the
        result says, for each recipient, whether the email was sent and why not.
        """
        if request.model_name is None or request.exact_version is None:
            raise ValueError("a failure email needs a model name and a version")
        listed = select_findings(findings)
        notice = FailureNotice(
            notice_id=f"{request.import_id}-notice",
            import_id=request.import_id,
            recipients=self._recipients(request.sender_email),
            reason=_plain(reason),
            removal_complete=removal_complete,
            findings_listed=listed,
            findings_total=max(findings_total, len(findings)),
            leftovers_listed=select_leftovers(leftovers),
            leftovers_total=len(leftovers),
            older_files_removed=older_files_removed,
        )
        subject = build_subject(request.model_name, request.exact_version)
        body = build_body(notice, request.model_name, request.exact_version)
        return self._deliver(
            notice,
            self._targets(request.sender_email, include_sender=True),
            subject,
            body,
            counter=FAILURE_COUNTER,
            limit=FAILURE_EMAIL_LIMIT,
            limit_kind="failure",
        )

    # -- the rejection email (BR6.2, BR6.3) -------------------------------------------------

    def send_rejection(self, event: RejectedEvent) -> NotificationResult:
        """BR6.2, BR6.3: tell the team, and the sender when the address is well-formed.

        The inputs are the event id, the event time, and the names of the failed fields. A value
        from the event is never repeated, not even a model name that passed the check.
        """
        reference = event_reference(event.event_id, event.import_id)
        label = "eventId" if is_safe_event_id(event.event_id) else "importId"
        sender = event.sender_email  # None unless it is one well-formed address (BR6.2)
        notice = FailureNotice(
            notice_id=f"{event.import_id}-notice",
            import_id=event.import_id,
            recipients=self._recipients(sender),
            reason="the request was rejected",
            removal_complete=True,
            kind=NoticeKind.EVENT_REJECTED,
            fields_named=tuple(event.rejected_fields),
        )
        body = build_rejection_body(
            reference, label, event.event_time, event.rejected_fields, event.missing_fields
        )
        return self._deliver(
            notice,
            self._targets(sender, include_sender=sender is not None),
            build_rejection_subject(reference),
            body,
            counter=NOTICE_COUNTER,
            limit=NOTICE_EMAIL_LIMIT,
            limit_kind="notice",
        )

    # -- the already-stored email (BR7.2) ---------------------------------------------------

    def send_already_stored(
        self, request: ImportRequest, reason: str = REASON_ALREADY_STORED
    ) -> NotificationResult:
        """BR7.2: tell the sender and the team that the model version is already stored."""
        if request.model_name is None or request.exact_version is None:
            raise ValueError("an already-stored email needs a model name and a version")
        notice = FailureNotice(
            notice_id=f"{request.import_id}-notice",
            import_id=request.import_id,
            recipients=self._recipients(request.sender_email),
            reason=_plain(reason),
            removal_complete=True,
            kind=NoticeKind.ALREADY_STORED,
        )
        subject = build_subject(
            request.model_name, request.exact_version, Outcome.REFUSED_ALREADY_STORED
        )
        body = build_already_stored_body(request.model_name, request.exact_version, reason)
        return self._deliver(
            notice,
            self._targets(request.sender_email, include_sender=True),
            subject,
            body,
            counter=NOTICE_COUNTER,
            limit=NOTICE_EMAIL_LIMIT,
            limit_kind="notice",
        )

    # -- sending ----------------------------------------------------------------------------

    def _recipients(self, sender_email: str | None) -> tuple[str, ...]:
        """The sender (when there is one), then the team mailbox; one message per address."""
        return tuple(address for address, _ in self._targets(sender_email, include_sender=True))

    def _targets(
        self, sender_email: str | None, *, include_sender: bool
    ) -> list[tuple[str, tuple[str, ...]]]:
        """(address, roles) pairs; an address that stands for both roles is listed once."""
        targets: list[tuple[str, tuple[str, ...]]] = []
        if include_sender and sender_email:
            targets.append((sender_email, (ROLE_SENDER,)))
        for index, (address, roles) in enumerate(targets):
            if address.casefold() == self._team.casefold():
                targets[index] = (address, (*roles, ROLE_TEAM))
                return targets
        targets.append((self._team, (ROLE_TEAM,)))
        return targets

    def _deliver(
        self,
        notice: FailureNotice,
        targets: list[tuple[str, tuple[str, ...]]],
        subject: str,
        body: str,
        *,
        counter: str,
        limit: int,
        limit_kind: str,
    ) -> NotificationResult:
        results: list[RecipientResult] = []
        for address, roles in targets:
            problem = self._send_one(
                notice.import_id,
                address,
                subject,
                body,
                counter=counter,
                limit=limit,
                limit_kind=limit_kind,
            )
            results.append(RecipientResult(roles, problem is None, problem))
        sent = sum(1 for item in results if item.sent)
        if sent:
            notice.sent_at = self._clock()
        return NotificationResult(
            notice=notice, sent=sent, not_sent=len(results) - sent, recipients=tuple(results)
        )

    def _send_one(
        self,
        import_id: str,
        address: str,
        subject: str,
        body: str,
        *,
        counter: str,
        limit: int,
        limit_kind: str,
    ) -> str | None:
        """NFR3.9, NFR3.16, NFR3.18: count the attempt, then send one message.

        Returns None when the email was sent, else the kind of problem (never an address).
        """
        day = self._clock().astimezone(UTC).date()
        try:
            reservation = self._store.reserve_email_attempt(day, limit, counter=counter)
        except StoreError:
            self._warn(
                "email_send_failed",
                import_id,
                reason="the daily email counter failed",
                error="StoreError",
            )
            return PROBLEM_SERVICE_ERROR
        if not reservation.allowed:
            self._warn(
                "email_limit_reached",
                import_id,
                reason=f"the limit of {limit} emails a day is reached",
                kind=limit_kind,
                emailsSentToday=reservation.count,
            )
            return PROBLEM_DAILY_LIMIT
        arguments: dict[str, Any] = {
            "Source": self._from,
            "Destination": {"ToAddresses": [address]},
            "Message": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        }
        if self._configuration_set:
            arguments["ConfigurationSetName"] = self._configuration_set
        try:
            self._ses.send_email(**arguments)
        except (ClientError, BotoCoreError) as error:
            code = (
                str(error.response.get("Error", {}).get("Code", "unknown"))
                if isinstance(error, ClientError)
                else type(error).__name__
            )
            self._warn(
                "email_send_failed",
                import_id,
                reason="the email service did not send the email",
                error=code,
            )
            return PROBLEM_SERVICE_ERROR
        if self._log is not None:
            self._log.info(
                "email_sent", importId=import_id, step="email", emailsSentToday=reservation.count
            )
        return None

    def _warn(self, event: str, import_id: str, **fields: object) -> None:
        if self._log is not None:
            self._log.warning(event, importId=import_id, step="email", **fields)
