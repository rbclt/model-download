"""Allow-list JSON logger (NFR5.4, NFR5.5, NFR3.8, BR5.4).

One JSON record per line. A record holds only the fields in ``ALLOWED_FIELDS`` (plus the
numeric metric values that a valid ``_aws`` envelope names). Any other field is dropped before
writing, so a secret or a matched value cannot reach a log by accident. File names are cleaned
before use (BR5.4).
"""

from __future__ import annotations

import json
import re
import sys
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, TextIO

ALLOWED_FIELDS = frozenset(
    {
        "time",
        "level",
        "event",
        "importId",
        "modelName",
        "exactVersion",
        "status",
        "step",
        "outcome",
        "reason",
        "removalComplete",
        "fileName",
        "place",
        "kind",
        "bytes",
        "durationSeconds",
        "findingsTotal",
        "matchesTotal",
        "emailsSentToday",
        "downloadSeconds",
        "scanSeconds",
        "keepSeconds",
        "totalSeconds",
        "_aws",
        "error",
        # u2-import-rules (NFR5.16): exactly these eight fields are added. None can hold an
        # address or a value taken from an event.
        "eventId",
        "emailSent",
        "recipientsNotReached",
        "emailProblem",
        "olderFilesRemoved",
        "leftoversTotal",
        "frontDoorSeconds",
        "proofSeconds",
    }
)

MAX_FILE_NAME_LENGTH = 200
MAX_TEXT_LENGTH = 500
METRIC_NAMESPACE = "ModelImport"

# NFR3.19: an event id is shown only when it looks like an id of the event bus. Any other id is
# left out of the record, and the importId (derived from it, always safe) identifies the import.
_SAFE_EVENT_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_ERROR_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_METRIC_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{0,63}")
_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def clean_file_name(name: str) -> str:
    """Replace control characters with a question mark and cut to 200 characters (BR5.4)."""
    return _CONTROL_CHARACTERS.sub("?", name)[:MAX_FILE_NAME_LENGTH]


def is_safe_event_id(value: object) -> bool:
    """NFR3.19: True when ``value`` is an event id that may be shown in a log or an email."""
    return isinstance(value, str) and _SAFE_EVENT_ID.fullmatch(value) is not None


def event_reference(event_id: str, import_id: str) -> str:
    """NFR3.19: the id that may be shown for an event: its bus id when safe, else the importId.

    The importId is derived from the event id and is always safe, so it identifies the import
    when the event id has an unexpected form. One choice for the log, the record, and the email.
    """
    return event_id if is_safe_event_id(event_id) else import_id


def build_metric_envelope(
    metrics: Mapping[str, str],
    *,
    timestamp_ms: int,
    dimensions: Iterable[str] = (),
    namespace: str = METRIC_NAMESPACE,
) -> dict[str, Any]:
    """Build the CloudWatch Embedded Metric Format ``_aws`` field.

    ``metrics`` maps a metric name to its unit (for example ``{"bytesDownloaded": "Bytes"}``).
    The logger writes the matching numeric values as fields of the same record. ``dimensions``
    are names of other allowed fields (for example ``outcome``).
    """
    dimension_names = list(dimensions)
    return {
        "Timestamp": timestamp_ms,
        "CloudWatchMetrics": [
            {
                "Namespace": namespace,
                "Dimensions": [dimension_names],
                "Metrics": [{"Name": name, "Unit": unit} for name, unit in metrics.items()],
            }
        ],
    }


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _sanitize_directive(directive: object) -> dict[str, Any] | None:
    """Rebuild one metric directive from checked parts, or return None when it is not valid."""
    if not isinstance(directive, dict):
        return None
    namespace, dimensions, metrics = (
        directive.get("Namespace"),
        directive.get("Dimensions"),
        directive.get("Metrics"),
    )
    if not isinstance(namespace, str) or not _METRIC_NAME.fullmatch(namespace):
        return None
    if not isinstance(dimensions, list) or not isinstance(metrics, list):
        return None
    clean_dimensions = []
    for group in dimensions:
        if not isinstance(group, list) or not all(
            isinstance(name, str) and name in ALLOWED_FIELDS for name in group
        ):
            return None
        clean_dimensions.append(list(group))
    clean_metrics = []
    for metric in metrics:
        if not isinstance(metric, dict):
            return None
        name, unit = metric.get("Name"), metric.get("Unit")
        if not isinstance(name, str) or not _METRIC_NAME.fullmatch(name):
            return None
        if not isinstance(unit, str) or not _METRIC_NAME.fullmatch(unit):
            return None
        clean_metrics.append({"Name": name, "Unit": unit})
    return {"Namespace": namespace, "Dimensions": clean_dimensions, "Metrics": clean_metrics}


def _sanitize_envelope(envelope: object) -> dict[str, Any] | None:
    """Return a rebuilt ``_aws`` envelope, or None when the envelope is not valid."""
    if not isinstance(envelope, dict):
        return None
    directives = envelope.get("CloudWatchMetrics")
    timestamp = envelope.get("Timestamp")
    if not _is_number(timestamp) or not isinstance(directives, list):
        return None
    clean = [_sanitize_directive(directive) for directive in directives]
    if not clean or any(directive is None for directive in clean):
        return None
    return {"Timestamp": timestamp, "CloudWatchMetrics": clean}


def _metric_names(envelope: object) -> set[str]:
    """Return the metric names of a valid envelope (empty when it is not valid)."""
    clean = _sanitize_envelope(envelope)
    if clean is None:
        return set()
    return {
        metric["Name"]
        for directive in clean["CloudWatchMetrics"]
        for metric in directive["Metrics"]
    }


def _clean_text(value: str) -> str:
    return _CONTROL_CHARACTERS.sub("?", value)[:MAX_TEXT_LENGTH]


def _clean_value(field: str, value: object) -> tuple[bool, object]:
    """Return (keep, cleaned value). Only plain text, numbers, booleans, and None are kept."""
    if field == "_aws":
        clean = _sanitize_envelope(value)
        return (clean is not None, clean)
    if field == "eventId":  # NFR3.19: only a safe bus id, whatever the type of the value
        return (is_safe_event_id(value), value)
    if value is None or isinstance(value, bool):
        return (True, value)
    if _is_number(value):
        return (True, value)
    if not isinstance(value, str):
        return (False, None)
    if field == "fileName":
        return (True, clean_file_name(value))
    if field == "error":
        return (True, value if _ERROR_CODE.fullmatch(value) else "invalid_error_code")
    return (True, _clean_text(value))


def filter_record(fields: Mapping[str, object]) -> dict[str, object]:
    """Return only the allowed, cleaned fields of ``fields``."""
    metric_names = _metric_names(fields.get("_aws"))
    record: dict[str, object] = {}
    for field, value in fields.items():
        if field in ALLOWED_FIELDS:
            keep, cleaned = _clean_value(field, value)
        elif field in metric_names and _is_number(value):
            keep, cleaned = True, value
        else:
            continue
        if keep:
            record[field] = cleaned
    return record


class JsonLogger:
    """Writes one allow-listed JSON record per line to ``stream``.

    ``bind`` returns a logger that adds fixed fields (for example ``importId``) to every
    record. The clock is injectable so tests never depend on real time.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        context: Mapping[str, object] | None = None,
        _lock: threading.Lock | None = None,
    ) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._clock = clock or (lambda: datetime.now(UTC))
        self._context = dict(context or {})
        self._lock = _lock or threading.Lock()

    def bind(self, **fields: object) -> JsonLogger:
        """Return a logger that adds ``fields`` to every record (checked by the allow-list)."""
        return JsonLogger(
            self._stream,
            clock=self._clock,
            context={**self._context, **fields},
            _lock=self._lock,
        )

    def now(self) -> datetime:
        """Return the logger's current time (used for metric time stamps)."""
        return self._clock()

    def log(self, level: str, event: str, **fields: object) -> None:
        """Write one record. Unknown fields are dropped; this never raises for a bad value."""
        merged = {**self._context, **fields}
        record = filter_record(merged)
        record["time"] = self._clock().astimezone(UTC).isoformat().replace("+00:00", "Z")
        record["level"] = level if level in _LEVELS else "INFO"
        record["event"] = _clean_text(event)
        line = json.dumps(record, separators=(",", ":"), sort_keys=True)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()

    def debug(self, event: str, **fields: object) -> None:
        self.log("DEBUG", event, **fields)

    def info(self, event: str, **fields: object) -> None:
        self.log("INFO", event, **fields)

    def warning(self, event: str, **fields: object) -> None:
        self.log("WARNING", event, **fields)

    def error(self, event: str, **fields: object) -> None:
        self.log("ERROR", event, **fields)
