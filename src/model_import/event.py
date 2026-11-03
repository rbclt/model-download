"""The queue message, the start event, and the name checks (BR1.1, BR1.3, BR6.1, BR6.4).

The queue message body is the EventBridge event of contract C1::

    {"id": "...", "source": "model.import", "detail-type": "ModelImportRequested",
     "time": "...", "detail": {"modelName": "owner/name", "exactVersion": "<40 hex>",
     "senderEmail": "..."}}

``classify_message`` is the first parse of a message (``logical-components.md``, Result of the
First Parse). It returns one of three results and nothing touches S3 or the network before it:

* ``StartEvent``: every check passed.
* ``RejectedEvent``: the envelope is a start event, but one or more of the three fields of the
  detail failed. It carries the names of all failed fields and, for each field that passed, its
  value. A value that failed is never kept (BR6.3).
* ``NotAStartEvent``: the body cannot be read, or the envelope lacks the event id, the source,
  the detail type, or the detail. It names the envelope field that failed (BR6.4).

The sender address and the event time are optional to read: ``time`` may be missing, and the
address is hidden from the text form of every event object (NFR3.20). The u1 function
``parse_start_event`` is gone: nothing but its own tests used it once the worker called
``classify_message``.

The path rules of BR1.3 for the names of files in a model listing live here too, so every
name that becomes part of a storage path is checked by this one module.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from model_import.logs import event_reference
from model_import.models import EVENT_FIELDS, ImportRequest, ImportStatus, derive_import_id
from model_import.settings import is_valid_address

EVENT_SOURCE = "model.import"
EVENT_DETAIL_TYPE = "ModelImportRequested"
MAX_PATH_LENGTH = 1024
MAX_EVENT_ID_LENGTH = 256
MAX_BODY_BYTES = 256 * 1024
MAX_EVENT_TIME_LENGTH = 64
RESERVED_FILE_NAME = "_COMPLETE"  # NFR3.25: the name of the completion marker

_NAME_PART = re.compile(r"[A-Za-z0-9._-]+")
_EXACT_VERSION = re.compile(r"[0-9a-f]{40}")


class InvalidEventError(ValueError):
    """BR1.1: the event failed the check. ``field`` names the field and never shows its value."""

    def __init__(self, field: str) -> None:
        super().__init__(f"the event field {field} is missing or not valid")
        self.field = field


class PathCheckError(ValueError):
    """BR1.3: a file path of the model listing failed the check. ``reason`` is plain words."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class StartEvent:
    """A start event that passed the check (BR1.1)."""

    event_id: str
    import_id: str
    model_name: str
    exact_version: str
    sender_email: str = field(repr=False)  # NFR3.20: never in a text form of the object
    event_time: datetime | None = None

    def to_import_request(
        self, started_at: datetime, *, deadline_at: datetime | None = None
    ) -> ImportRequest:
        """BR1.1, BR1.2: create the ImportRequest at the start, in status received.

        The front door then moves it to downloading, removing, or straight to done.
        """
        return ImportRequest(
            import_id=self.import_id,
            model_name=self.model_name,
            exact_version=self.exact_version,
            sender_email=self.sender_email,
            started_at=started_at,
            status=ImportStatus.RECEIVED,
            event_id=event_reference(self.event_id, self.import_id),
            deadline_at=deadline_at,
        )


@dataclass(frozen=True)
class RejectedEvent:
    """BR6.1: a start event whose content failed the check of BR1.1 and BR1.3.

    ``rejected_fields`` names every field that failed, in the order modelName, exactVersion,
    senderEmail; ``missing_fields`` is the part of it that was absent or empty (the others are
    "not in the expected form"). A value is kept only for a field that passed, and the sender
    address only when it is one well-formed address (BR6.2).
    """

    event_id: str
    import_id: str
    rejected_fields: tuple[str, ...]
    missing_fields: tuple[str, ...] = ()
    event_time: datetime | None = None
    model_name: str | None = None
    exact_version: str | None = None
    sender_email: str | None = field(default=None, repr=False)  # NFR3.20

    def __post_init__(self) -> None:
        if not self.rejected_fields or any(
            name not in EVENT_FIELDS for name in self.rejected_fields
        ):
            raise ValueError("a rejected event names one or more of the three event fields")
        if any(name not in self.rejected_fields for name in self.missing_fields):
            raise ValueError("a missing field must be one of the rejected fields")

    def to_import_request(
        self, started_at: datetime, *, deadline_at: datetime | None = None
    ) -> ImportRequest:
        """The ImportRequest of a rejected event: it starts at ``received`` and ends there."""
        return ImportRequest(
            import_id=self.import_id,
            model_name=self.model_name,
            exact_version=self.exact_version,
            sender_email=self.sender_email,
            started_at=started_at,
            status=ImportStatus.RECEIVED,
            event_id=event_reference(self.event_id, self.import_id),
            deadline_at=deadline_at,
            rejected_fields=self.rejected_fields,
        )


@dataclass(frozen=True)
class NotAStartEvent:
    """BR6.4: a message that is not a start event. ``field_name`` names the envelope field."""

    field_name: str

    @property
    def reason(self) -> str:
        """Plain words for the log; the same sentence u1 logged for a bad event."""
        return str(InvalidEventError(self.field_name))


def is_valid_model_name(name: object) -> bool:
    """BR1.3: ``owner/name``, each part of letters, digits, dot, underscore, hyphen.

    A part made only of dots is refused, so ``.`` and ``..`` never reach a path.
    """
    if not isinstance(name, str):
        return False
    parts = name.split("/")
    if len(parts) != 2:
        return False
    return all(_NAME_PART.fullmatch(part) is not None and set(part) != {"."} for part in parts)


def is_valid_exact_version(version: object) -> bool:
    """BR1.3: exactly 40 characters of 0 to 9 and a to f."""
    return isinstance(version, str) and _EXACT_VERSION.fullmatch(version) is not None


def is_valid_sender_email(address: object) -> bool:
    """NFR3.14: one header-safe address (no line break, comma, semicolon, or second address)."""
    return isinstance(address, str) and is_valid_address(address)


def _is_control(character: str) -> bool:
    return unicodedata.category(character) in {"Cc", "Zl", "Zp"}


def check_file_path(path: str) -> None:
    """BR1.3: check one file path of a model listing; raise ``PathCheckError`` if it fails."""
    if not path:
        raise PathCheckError("a file path in the model is empty")
    if len(path) > MAX_PATH_LENGTH:
        raise PathCheckError("a file path in the model is longer than 1024 characters")
    if path.startswith("/"):
        raise PathCheckError("a file path in the model starts with a slash")
    if "\\" in path:
        raise PathCheckError("a file path in the model holds a backslash")
    if any(_is_control(character) for character in path):
        raise PathCheckError("a file path in the model holds a control character")
    for segment in path.split("/"):
        if segment == "":
            raise PathCheckError("a file path in the model has an empty part")
        if segment in {".", ".."}:
            raise PathCheckError("a file path in the model holds a dot part")
    # NFR3.25: only a file at the root of the version folder can collide with the marker key.
    if path.casefold() == RESERVED_FILE_NAME.casefold():
        raise PathCheckError(f"a file of the model is named {RESERVED_FILE_NAME}")


def check_file_paths(paths: Iterable[str]) -> None:
    """BR1.3: check every path, and refuse two paths that are equal when case is ignored."""
    seen: set[str] = set()
    for path in paths:
        check_file_path(path)
        key = path.casefold()
        if key in seen:
            raise PathCheckError("two file paths in the model are the same when case is ignored")
        seen.add(key)


def _load_body(body: str | bytes | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(body, Mapping):
        return body
    if len(body) > MAX_BODY_BYTES:
        raise InvalidEventError("body")
    try:
        loaded = json.loads(body)
    except (ValueError, RecursionError) as error:
        raise InvalidEventError("body") from error
    if not isinstance(loaded, dict):
        raise InvalidEventError("body")
    return loaded


def _parse_event_time(value: object) -> datetime | None:
    """The ``time`` of the envelope, read and written again by the code (NFR3.19).

    ``time`` is optional (BR6.4 as corrected by NFR Design): a missing, oversized, or unparsable
    value, or one without a time zone, gives ``None`` and the email says the time is not
    available. The text of the event is never kept.
    """
    if not isinstance(value, str) or not 0 < len(value) <= MAX_EVENT_TIME_LENGTH:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def classify_message(
    body: str | bytes | Mapping[str, Any],
) -> StartEvent | RejectedEvent | NotAStartEvent:
    """BR1.1, BR1.3, BR6.1, BR6.4, NFR3.10, NFR3.14: the first parse of a queue message.

    Never raises for a bad message. The envelope is checked first; a failure there is
    ``NotAStartEvent`` naming the field. When the envelope is a start event, all three fields of
    the detail are checked (not only the first that fails) and every failed field is named. Unknown
    fields are ignored (contract C1). Nothing here reads a secret, and no value that failed a
    check is placed in a result.
    """
    try:
        envelope = _load_body(body)
    except InvalidEventError as error:
        return NotAStartEvent(error.field)
    event_id = envelope.get("id")
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > MAX_EVENT_ID_LENGTH:
        return NotAStartEvent("id")
    if envelope.get("source") != EVENT_SOURCE:
        return NotAStartEvent("source")
    if envelope.get("detail-type") != EVENT_DETAIL_TYPE:
        return NotAStartEvent("detail-type")
    detail = envelope.get("detail")
    if not isinstance(detail, Mapping):
        return NotAStartEvent("detail")

    model_name = detail.get("modelName")
    exact_version = detail.get("exactVersion")
    sender_email = detail.get("senderEmail")
    checks = (
        ("modelName", model_name, is_valid_model_name(model_name)),
        ("exactVersion", exact_version, is_valid_exact_version(exact_version)),
        ("senderEmail", sender_email, is_valid_sender_email(sender_email)),
    )
    failed = tuple(name for name, _, passed in checks if not passed)
    import_id = derive_import_id(event_id)
    event_time = _parse_event_time(envelope.get("time"))
    if not failed:
        return StartEvent(
            event_id=event_id,
            import_id=import_id,
            model_name=str(model_name),
            exact_version=str(exact_version),
            sender_email=str(sender_email),
            event_time=event_time,
        )
    return RejectedEvent(
        event_id=event_id,
        import_id=import_id,
        rejected_fields=failed,
        missing_fields=tuple(
            name for name, value, passed in checks if not passed and _is_missing(value)
        ),
        event_time=event_time,
        model_name=str(model_name) if "modelName" not in failed else None,
        exact_version=str(exact_version) if "exactVersion" not in failed else None,
        sender_email=str(sender_email) if "senderEmail" not in failed else None,
    )
