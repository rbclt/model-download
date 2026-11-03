"""Entities, enums, and stored record shapes of u1-import-slice (``entities.md``).

The model holds no secret and no matched value: a ``ScanFinding`` has a file name, a place, and
a kind only (BR3.5). Where an entity of ``entities.md`` has a fact that follows from other
fields (``complete``, ``downloadComplete``), it is a property here, so it can never disagree
with them.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from model_import.logs import clean_file_name, is_safe_event_id

MAX_FINDINGS_PER_FILE = 100  # BR3.7
MAX_EMAIL_FINDINGS = 20  # BR5.6
MAX_RECORD_FINDINGS = 200  # BR11.2: findings listed in the ImportRecord
MAX_LEFTOVERS_LISTED = 20  # BR9.4: leftovers listed in the failure email
MAX_REASON_LENGTH = 500
HOLDING_PREFIX = "holding"
STATE_PREFIX = "state"
MODELS_PREFIX = "models"

_IMPORT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class ImportStatus(StrEnum):
    """Where an import is in its life cycle (``functional-spec.md``).

    ``received`` is the start of the state diagram, before any step: the front door of u2 makes
    its decision from there (to ``downloading``, to ``removing``, or straight to ``done``). It is
    never written to an ``ImportRecord``. Code from u1 starts an ``ImportRequest`` in
    ``downloading`` and does not use it.
    """

    RECEIVED = "received"
    DOWNLOADING = "downloading"
    SCANNING = "scanning"
    KEEPING = "keeping"
    REMOVING = "removing"
    DONE = "done"


class Outcome(StrEnum):
    """The single outcome of an import (NFR4.1, BR4.3, BR5.3, BR5.5)."""

    STORED = "stored"
    REMOVED_WITH_EMAIL = "removed-with-email"
    REMOVED_EMAIL_FAILED = "removed-email-failed"
    REJECTED = "rejected"  # BR6.1
    REFUSED_ALREADY_STORED = "refused-already-stored"  # BR7.1


# BR10.4: the roles an email goes to. The outcome names roles, never addresses (BR10.3).
ROLE_SENDER = "sender"
ROLE_TEAM = "team"
EMAIL_ROLES = frozenset({ROLE_SENDER, ROLE_TEAM})
# BR6.3: the only field names a rejected event can name.
EVENT_FIELDS = ("modelName", "exactVersion", "senderEmail")


class FileClass(StrEnum):
    """The class the scan gives a file (BR3.1)."""

    WEIGHT = "weight"
    SCANNED_TEXT = "scanned-text"
    IGNORED = "ignored"
    DISALLOWED_WEIGHT = "disallowed-weight"


class FindingKind(StrEnum):
    """What a finding is about (BR3.3, BR3.4, BR3.2, BR3.1)."""

    ACCESS_KEY = "access-key"
    PRIVATE_KEY = "private-key"
    TOKEN = "token"  # noqa: S105 - the name of a finding kind, not a credential
    PASSWORD = "password"  # noqa: S105 - the name of a finding kind, not a credential
    EMAIL_ADDRESS = "email-address"
    PHONE_NUMBER = "phone-number"
    GOVERNMENT_ID = "government-id"
    STREET_ADDRESS = "street-address"
    UNREADABLE_FILE = "unreadable-file"
    DISALLOWED_WEIGHT_TYPE = "disallowed-weight-type"
    BAD_WEIGHT_HEADER = "bad-weight-header"


# The only moves an import may make before it is done. ``done`` is entered through
# ``ImportRequest.finish`` so the outcome is set at the same moment.
ALLOWED_TRANSITIONS: dict[ImportStatus, frozenset[ImportStatus]] = {
    # (start) -> done goes through ``finish`` (rejected, refused-already-stored, own stored).
    ImportStatus.RECEIVED: frozenset({ImportStatus.DOWNLOADING, ImportStatus.REMOVING}),
    ImportStatus.DOWNLOADING: frozenset({ImportStatus.SCANNING, ImportStatus.REMOVING}),
    ImportStatus.SCANNING: frozenset({ImportStatus.KEEPING, ImportStatus.REMOVING}),
    ImportStatus.KEEPING: frozenset({ImportStatus.REMOVING}),
    ImportStatus.REMOVING: frozenset(),
    ImportStatus.DONE: frozenset(),
}


class InvalidTransitionError(RuntimeError):
    """A status change that ``functional-spec.md`` does not allow."""


class OutcomeAlreadySetError(RuntimeError):
    """The outcome of an import is set once and never changed (NFR4.1)."""


def derive_import_id(event_id: str) -> str:
    """BR1.2: derive the importId from the event id.

    The same event id always gives the same importId, so a message that is received again maps
    to the same ``state/<importId>/`` objects. A hash keeps any character of the event id out
    of a storage path.
    """
    if not event_id.strip():
        raise ValueError("the event id is empty")
    return "imp-" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:32]


def is_valid_import_id(import_id: str) -> bool:
    """Return True when ``import_id`` is safe to use in a storage key."""
    return _IMPORT_ID.fullmatch(import_id) is not None


def holding_prefix(import_id: str) -> str:
    """BR2.2: the holding area of one import, ``holding/<importId>/``."""
    return f"{HOLDING_PREFIX}/{import_id}/"


def final_prefix(model_name: str, exact_version: str) -> str:
    """BR1.3, BR4.1: ``models/<model name in lowercase>/<exact version>/``."""
    return f"{MODELS_PREFIX}/{model_name.lower()}/{exact_version}/"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must carry a time zone")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_time(text: str) -> datetime:
    return _utc(datetime.fromisoformat(text.replace("Z", "+00:00")))


def _plain_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    return " ".join(reason.split())[:MAX_REASON_LENGTH]


def _check_names(names: Sequence[str], allowed: Sequence[str] | frozenset[str], what: str) -> None:
    """Only names from a fixed set may be stored, so a value from an event never gets in."""
    if any(name not in allowed for name in names):
        raise ValueError(f"unknown {what}")


@dataclass
class ImportRequest:
    """BR1.2: one import, from the accepted start event to its single outcome."""

    import_id: str
    model_name: str | None  # None only for a rejected event whose value failed the check
    exact_version: str | None  # the same
    sender_email: str | None = field(repr=False)  # NFR3.20: never in a text form of the object
    started_at: datetime
    status: ImportStatus = ImportStatus.DOWNLOADING
    outcome: Outcome | None = None
    failure_reason: str | None = None
    event_id: str | None = None
    deadline_at: datetime | None = None  # BR8.1: set for an event that passed the content check
    rejected_fields: tuple[str, ...] = ()  # BR6.3: field names only, never values
    email_sent: bool | None = None  # BR10.4: empty for the outcome stored
    recipients_not_reached: tuple[str, ...] = ()  # BR10.3: roles, never addresses

    def __post_init__(self) -> None:
        if not is_valid_import_id(self.import_id):
            raise ValueError("the importId is not safe to use in a storage key")
        _utc(self.started_at)
        if self.deadline_at is not None:
            _utc(self.deadline_at)
        _check_names(self.rejected_fields, EVENT_FIELDS, "rejected field")
        _check_names(self.recipients_not_reached, EMAIL_ROLES, "email role")

    def advance(self, to: ImportStatus) -> None:
        """Move to ``to`` along an allowed transition (BR5.1); ``done`` uses ``finish``."""
        if to not in ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTransitionError(f"cannot move from {self.status} to {to}")
        self.status = to

    def finish(
        self,
        outcome: Outcome,
        failure_reason: str | None = None,
        *,
        email_sent: bool | None = None,
        recipients_not_reached: Sequence[str] = (),
        stored_during_removal: bool = False,
    ) -> None:
        """Set the outcome once and move to ``done`` (NFR4.1, BR4.3, BR5.3, BR5.5, BR10.1).

        * ``stored`` is valid from ``keeping`` (a model kept) or from ``received`` (the model was
          stored by an earlier attempt of this same event, BR7.1). It carries no reason and no
          email result. It is valid from ``removing`` only with ``stored_during_removal``: the
          last guard of BR9.6, when the removal found that the completion marker was written. The
          coordinator never enters ``removing`` after the marker; this only repairs a wrong call.
        * ``rejected`` and ``refused-already-stored`` are valid only from ``received``: nothing
          was downloaded. ``rejected`` needs the names of the failed fields.
        * The two removed outcomes are valid only from ``removing``.
        """
        if self.outcome is not None:
            raise OutcomeAlreadySetError("the outcome is already set")
        self._check_can_finish(outcome, failure_reason, email_sent, stored_during_removal)
        roles = tuple(recipients_not_reached)
        _check_names(roles, EMAIL_ROLES, "email role")
        self.outcome = outcome
        self.failure_reason = _plain_reason(failure_reason)
        self.email_sent = email_sent
        self.recipients_not_reached = roles
        self.status = ImportStatus.DONE

    def _check_can_finish(
        self,
        outcome: Outcome,
        failure_reason: str | None,
        email_sent: bool | None,
        stored_during_removal: bool = False,
    ) -> None:
        if outcome is Outcome.STORED:
            allowed = {ImportStatus.KEEPING, ImportStatus.RECEIVED}
            if stored_during_removal:
                allowed.add(ImportStatus.REMOVING)
            if self.status not in allowed:
                raise InvalidTransitionError("stored needs status keeping or received")
            if failure_reason is not None or email_sent is not None:
                raise InvalidTransitionError("stored carries no reason and no email result")
        elif outcome in {Outcome.REJECTED, Outcome.REFUSED_ALREADY_STORED}:
            if self.status is not ImportStatus.RECEIVED:
                raise InvalidTransitionError(f"{outcome} needs status received")
        elif self.status is not ImportStatus.REMOVING:
            raise InvalidTransitionError(f"{outcome} needs status removing")
        if (outcome is Outcome.REJECTED) != bool(self.rejected_fields):
            raise ValueError("rejectedFields is set exactly when the outcome is rejected")


@dataclass
class ModelFile:
    """BR2.5: one file of the model version, as listed and as received."""

    path: str
    expected_size_bytes: int
    received_size_bytes: int = 0

    def __post_init__(self) -> None:
        if self.expected_size_bytes < 0 or self.received_size_bytes < 0:
            raise ValueError("file sizes cannot be negative")

    @property
    def complete(self) -> bool:
        """True only when the received size equals the listed size (BR2.5)."""
        return self.received_size_bytes == self.expected_size_bytes


@dataclass
class ModelFileSet:
    """All files of one downloaded model version, held while they are scanned."""

    import_id: str
    model_name: str
    exact_version: str
    files: list[ModelFile] = field(default_factory=list)

    @property
    def holding_area_path(self) -> str:
        return holding_prefix(self.import_id)

    @property
    def total_size_bytes(self) -> int:
        return sum(item.expected_size_bytes for item in self.files)

    @property
    def download_complete(self) -> bool:
        """True only when there is at least one file and every file is complete (BR2.5)."""
        return bool(self.files) and all(item.complete for item in self.files)


@dataclass(frozen=True)
class ScannedFile:
    """The scan result for one file."""

    import_id: str
    path: str
    file_class: FileClass
    findings_recorded: int = 0
    matches_total: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.findings_recorded <= MAX_FINDINGS_PER_FILE:
            raise ValueError("at most 100 findings are recorded for one file")
        if self.matches_total < self.findings_recorded:
            raise ValueError("matchesTotal cannot be less than the findings recorded")


@dataclass(frozen=True)
class ScanFinding:
    """BR3.5: one thing the scan found. It has no attribute for the matched value."""

    finding_id: str
    import_id: str
    file_name: str
    place: str
    kind: FindingKind


@dataclass(frozen=True)
class StoredModel:
    """A model version that passed the scan and is kept in the final location."""

    model_name: str
    exact_version: str
    stored_at: datetime

    @property
    def stored_model_key(self) -> str:
        return final_prefix(self.model_name, self.exact_version)


class NoticeKind(StrEnum):
    """The three kinds of email (``entities.md``, FailureNotice)."""

    IMPORT_FAILED = "import-failed"
    EVENT_REJECTED = "event-rejected"
    ALREADY_STORED = "already-stored"


@dataclass
class FailureNotice:
    """BR5.3, BR5.6, BR6.3, BR9.4, BR9.5: the one email about a start event that did not store.

    ``recipients`` are addresses used for sending only; they are never stored or logged.
    """

    notice_id: str
    import_id: str
    recipients: tuple[str, ...]
    reason: str
    removal_complete: bool
    findings_listed: tuple[ScanFinding, ...] = ()
    findings_total: int = 0
    sent_at: datetime | None = None
    kind: NoticeKind = NoticeKind.IMPORT_FAILED
    fields_named: tuple[str, ...] = ()  # BR6.3: only for event-rejected; names, never values
    leftovers_listed: tuple[Leftover, ...] = ()  # BR9.4: at most 20
    leftovers_total: int = 0
    older_files_removed: int = 0  # BR9.5

    def __post_init__(self) -> None:
        if len(self.findings_listed) > MAX_EMAIL_FINDINGS:
            raise ValueError("an email lists at most 20 findings")
        if self.findings_total < len(self.findings_listed):
            raise ValueError("findingsTotal cannot be less than the findings listed")
        _check_names(self.fields_named, EVENT_FIELDS, "rejected field")
        if len(self.leftovers_listed) > MAX_LEFTOVERS_LISTED:
            raise ValueError("an email lists at most 20 leftovers")
        if self.leftovers_total < len(self.leftovers_listed) or self.older_files_removed < 0:
            raise ValueError("leftoversTotal cannot be less than the leftovers listed")


MAX_SUMMARY_PLACE_LENGTH = 100


@dataclass(frozen=True)
class FindingSummary:
    """BR11.2: one finding as the ImportRecord keeps it: cleaned file name, place, and kind.

    It has no attribute for the matched value, any part of it, or the text around it (BR3.5).
    """

    file_name: str
    place: str
    kind: FindingKind

    @classmethod
    def from_finding(cls, finding: ScanFinding) -> FindingSummary:
        return cls(
            file_name=clean_file_name(finding.file_name),
            place=" ".join(finding.place.split())[:MAX_SUMMARY_PLACE_LENGTH],
            kind=finding.kind,
        )

    def to_dict(self) -> dict[str, str]:
        return {"fileName": self.file_name, "place": self.place, "kind": self.kind.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FindingSummary:
        return cls(
            file_name=str(data["fileName"]),
            place=str(data["place"]),
            kind=FindingKind(data["kind"]),
        )


class LeftoverKind(StrEnum):
    """What a Leftover is (``entities.md``)."""

    OBJECT = "object"
    INCOMPLETE_UPLOAD = "incomplete-upload"
    TEMPORARY_RESOURCE = "temporary-resource"


class LeftoverArea(StrEnum):
    """Where a Leftover is (``entities.md``)."""

    HOLDING_AREA = "holding-area"
    FINAL_LOCATION = "final-location"
    LOCAL_DISK = "local-disk"


@dataclass(frozen=True)
class Leftover:
    """BR9.3: one thing the removal could not delete, or that the check afterwards still found.

    ``location`` is a storage key or a resource name, cleaned by the rule of BR5.4. It is never
    a value taken from a file.
    """

    kind: LeftoverKind
    area: LeftoverArea
    location: str

    def __post_init__(self) -> None:
        if not self.location:
            raise ValueError("a leftover needs a location")
        object.__setattr__(self, "location", clean_file_name(self.location))


@dataclass(frozen=True)
class RemovalResult:
    """BR9.3: what the removal achieved and what it could not remove.

    ``removal_complete`` follows from the other fields, so it can never disagree with them: it is
    true only when there is no Leftover. ``model_stored`` is True for one case only: the removal
    was asked for an import whose completion marker was written. Nothing is deleted then, and
    the result must not say "removal complete"; the coordinator treats the import as stored
    (BR9.6, ``reliability-design.md`` NFR4.14).
    """

    import_id: str
    leftovers: tuple[Leftover, ...] = ()
    older_files_removed: int = 0  # BR9.5: not leftovers
    model_stored: bool = False

    def __post_init__(self) -> None:
        if self.older_files_removed < 0:
            raise ValueError("olderFilesRemoved cannot be negative")

    @property
    def removal_complete(self) -> bool:
        return not self.leftovers and not self.model_stored

    @property
    def leftovers_total(self) -> int:
        return len(self.leftovers)


@dataclass(frozen=True)
class KeepResult:
    """BR9.6: what the keep step returns once the completion marker is written.

    ``keep`` raises before the marker and never after it, so a result always means the model is
    stored. ``holding_emptied`` is False when the holding folder could not be fully deleted: the
    outcome is still stored, with ``removalComplete`` false, and ``leftovers`` lists what stayed.
    """

    stored: StoredModel
    marker_written: bool = True
    holding_emptied: bool = True
    leftovers: tuple[Leftover, ...] = ()


@dataclass
class ImportRecord:
    """NFR4.8, BR11.1: ``state/<importId>/record.json``, rewritten before each step.

    It is history only (BR11.3). It holds no matched value, no part of one, no text around one,
    and no email address. ``model_name`` and ``exact_version`` are empty for a rejected event
    whose value failed the check. ``event_id`` is the id of the event bus when it has the safe
    form; any other id is replaced by the importId (NFR3.19).
    """

    import_id: str
    model_name: str | None
    exact_version: str | None
    status: ImportStatus
    step: str
    started_at: datetime
    updated_at: datetime
    files_copied: int = 0
    last_key_copied: str | None = None
    event_id: str | None = None
    outcome: Outcome | None = None
    failure_reason: str | None = None
    findings: list[FindingSummary] = field(default_factory=list)
    findings_total: int = 0

    def __post_init__(self) -> None:
        if self.event_id is not None and not is_safe_event_id(self.event_id):
            self.event_id = self.import_id
        self._check_findings(self.findings, self.findings_total)

    def set_findings(self, findings: Sequence[FindingSummary], total: int) -> None:
        """BR11.2: keep at most 200 findings and the total of all findings of the scan."""
        self._check_findings(findings, total)
        self.findings = list(findings)
        self.findings_total = total

    @staticmethod
    def _check_findings(findings: Sequence[FindingSummary], total: int) -> None:
        if len(findings) > MAX_RECORD_FINDINGS:
            raise ValueError("a record lists at most 200 findings")
        if total < len(findings):
            raise ValueError("findingsTotal cannot be less than the findings listed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "importId": self.import_id,
            "eventId": self.event_id,
            "modelName": self.model_name,
            "exactVersion": self.exact_version,
            "status": self.status.value,
            "step": self.step,
            "startedAt": _iso(self.started_at),
            "updatedAt": _iso(self.updated_at),
            "filesCopied": self.files_copied,
            "lastKeyCopied": self.last_key_copied,
            "outcome": self.outcome.value if self.outcome is not None else None,
            "failureReason": _plain_reason(self.failure_reason),
            "findings": [item.to_dict() for item in self.findings],
            "findingsTotal": self.findings_total,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImportRecord:
        """Read a record. A record written by u1 has none of the new fields; they default."""
        outcome = data.get("outcome")
        return cls(
            import_id=str(data["importId"]),
            model_name=_optional_text(data["modelName"]),
            exact_version=_optional_text(data["exactVersion"]),
            status=ImportStatus(data["status"]),
            step=str(data["step"]),
            started_at=_parse_time(data["startedAt"]),
            updated_at=_parse_time(data["updatedAt"]),
            files_copied=int(data["filesCopied"]),
            last_key_copied=data.get("lastKeyCopied"),
            event_id=_optional_text(data.get("eventId")),
            outcome=Outcome(outcome) if outcome is not None else None,
            failure_reason=data.get("failureReason"),
            findings=[FindingSummary.from_dict(item) for item in data.get("findings") or []],
            findings_total=int(data.get("findingsTotal") or 0),
        )


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True)
class OutcomeRecord:
    """NFR4.1, BR10.3: ``state/<importId>/outcome.json``, created once and never changed.

    ``email_sent`` is empty for the outcome stored, which sends no email. ``recipients_not_reached``
    holds roles (``sender``, ``team``), never addresses.
    """

    import_id: str
    outcome: Outcome
    removal_complete: bool
    finished_at: datetime
    failure_reason: str | None = None
    email_sent: bool | None = None
    recipients_not_reached: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _check_names(self.recipients_not_reached, EMAIL_ROLES, "email role")

    def to_dict(self) -> dict[str, Any]:
        return {
            "importId": self.import_id,
            "outcome": self.outcome.value,
            "removalComplete": self.removal_complete,
            "finishedAt": _iso(self.finished_at),
            "failureReason": _plain_reason(self.failure_reason),
            "emailSent": self.email_sent,
            "recipientsNotReached": list(self.recipients_not_reached),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutcomeRecord:
        """Read an outcome. An outcome written by u1 has no email fields; they default."""
        sent = data.get("emailSent")
        return cls(
            import_id=str(data["importId"]),
            outcome=Outcome(data["outcome"]),
            removal_complete=bool(data["removalComplete"]),
            finished_at=_parse_time(data["finishedAt"]),
            failure_reason=data.get("failureReason"),
            email_sent=None if sent is None else bool(sent),
            recipients_not_reached=tuple(
                str(item) for item in data.get("recipientsNotReached") or []
            ),
        )
