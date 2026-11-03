"""Durable import state in the holding bucket (NFR4.1, NFR4.8, NFR3.16).

Layout (all keys in the holding bucket):

* ``state/<importId>/record.json``: the import record, rewritten before each step (NFR4.8).
* ``state/<importId>/outcome.json``: the single outcome, created with ``IfNoneMatch="*"`` so a
  second write cannot change it (NFR4.1).
* ``state/counters/emails-<YYYY-MM-DD>.json``: the per-day counter of failure emails (limit 30),
  and ``state/counters/notices-<YYYY-MM-DD>.json``: the per-day counter of rejection and
  already-stored emails (limit 20). Each is updated with a conditional write on the object's
  ETag (``IfMatch``) so two updates cannot overwrite each other (NFR3.16, NFR3.18). The day is
  the UTC day.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from botocore.exceptions import ClientError

from model_import.models import (
    STATE_PREFIX,
    ImportRecord,
    OutcomeRecord,
    is_valid_import_id,
)

FAILURE_EMAIL_LIMIT = 30  # NFR3.18: failure emails of imports that ran, a day (amends NFR3.16)
NOTICE_EMAIL_LIMIT = 20  # NFR3.18: rejection and already-stored emails, a day
FAILURE_COUNTER = "emails"  # state/counters/emails-<date>.json (the counter of u1)
NOTICE_COUNTER = "notices"  # state/counters/notices-<date>.json
COUNTERS = frozenset({FAILURE_COUNTER, NOTICE_COUNTER})
_MISSING_CODES = frozenset({"NoSuchKey", "404", "NotFound"})
_PRECONDITION_CODES = frozenset({"PreconditionFailed", "412"})


class StoreError(RuntimeError):
    """The state store could not read or write an object. The message holds no object content."""


@dataclass(frozen=True)
class EmailReservation:
    """The result of asking to send one more email today.

    ``count`` is the number of attempts counted for the day after this request. When
    ``allowed`` is False the limit was already reached and nothing was counted.
    """

    allowed: bool
    count: int


def _error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", "unknown"))


def _status(error: ClientError) -> int:
    return int(error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))


def _is_missing(error: ClientError) -> bool:
    return _error_code(error) in _MISSING_CODES or _status(error) == 404


def _is_precondition_failed(error: ClientError) -> bool:
    return _error_code(error) in _PRECONDITION_CODES or _status(error) == 412


def _dump(data: dict[str, Any]) -> bytes:
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")


class ImportStore:
    """Reads and writes the state objects of imports in one bucket."""

    def __init__(self, s3_client: Any, bucket: str) -> None:
        self._s3 = s3_client
        self._bucket = bucket

    @staticmethod
    def record_key(import_id: str) -> str:
        return f"{STATE_PREFIX}/{_checked_id(import_id)}/record.json"

    @staticmethod
    def outcome_key(import_id: str) -> str:
        return f"{STATE_PREFIX}/{_checked_id(import_id)}/outcome.json"

    @staticmethod
    def counter_key(day: date, counter: str = FAILURE_COUNTER) -> str:
        """NFR3.18: ``state/counters/<counter>-<UTC date>.json``; the name is one of two."""
        if counter not in COUNTERS:
            raise ValueError("unknown email counter")
        return f"{STATE_PREFIX}/counters/{counter}-{day.isoformat()}.json"

    # -- import record (NFR4.8) -------------------------------------------------------------

    def write_record(self, record: ImportRecord) -> None:
        """NFR4.8: rewrite the import record. It is written before each step."""
        self._put(self.record_key(record.import_id), _dump(record.to_dict()))

    def read_record(self, import_id: str) -> ImportRecord | None:
        data = self._get_json(self.record_key(import_id))
        if data is None:
            return None
        try:
            return ImportRecord.from_dict(data)
        except (KeyError, ValueError, TypeError) as error:
            raise StoreError("the import record cannot be read") from error

    # -- single outcome (NFR4.1) ------------------------------------------------------------

    def create_outcome(self, outcome: OutcomeRecord) -> bool:
        """NFR4.1: create the outcome object once.

        Returns True when this call created it and False when it already existed. An existing
        outcome is never changed.
        """
        try:
            self._s3.put_object(
                Bucket=self._bucket,
                Key=self.outcome_key(outcome.import_id),
                Body=_dump(outcome.to_dict()),
                ContentType="application/json",
                IfNoneMatch="*",
            )
        except ClientError as error:
            if _is_precondition_failed(error):
                return False
            raise StoreError(f"the outcome cannot be written ({_error_code(error)})") from error
        return True

    def read_outcome(self, import_id: str) -> OutcomeRecord | None:
        data = self._get_json(self.outcome_key(import_id))
        if data is None:
            return None
        try:
            return OutcomeRecord.from_dict(data)
        except (KeyError, ValueError, TypeError) as error:
            raise StoreError("the outcome cannot be read") from error

    def has_outcome(self, import_id: str) -> bool:
        """True when the import already finished (the outcome object exists)."""
        try:
            self._s3.head_object(Bucket=self._bucket, Key=self.outcome_key(import_id))
        except ClientError as error:
            if _is_missing(error):
                return False
            raise StoreError(f"the outcome cannot be checked ({_error_code(error)})") from error
        return True

    # -- per-day email counters (NFR3.16, NFR3.18) ------------------------------------------

    def read_email_count(self, day: date, counter: str = FAILURE_COUNTER) -> int:
        """Return the number of email attempts counted for ``day`` (0 when none)."""
        return self._read_counter(day, counter)[0]

    def reserve_email_attempt(
        self,
        day: date,
        limit: int = FAILURE_EMAIL_LIMIT,
        max_tries: int = 10,
        *,
        counter: str = FAILURE_COUNTER,
    ) -> EmailReservation:
        """NFR3.16, NFR3.18: count one email attempt for ``day`` unless ``limit`` is reached.

        Two counters exist, each its own object: ``emails`` (the failure email of an import that
        ran, limit 30) and ``notices`` (the rejection and the already-stored email, limit 20).
        The caller names the counter and the limit at each send, so an email cannot be counted in
        the wrong one by accident.

        The update is a conditional write on the ETag of the counter object (a conditional
        create when the object is new). When another update wins the race, the count is read
        again and the update is tried again, up to ``max_tries`` times.
        """
        for _ in range(max_tries):
            current, etag = self._read_counter(day, counter)
            if current >= limit:
                return EmailReservation(allowed=False, count=current)
            body = _dump({"date": day.isoformat(), "count": current + 1})
            condition: dict[str, str] = {"IfNoneMatch": "*"} if etag is None else {"IfMatch": etag}
            try:
                self._s3.put_object(
                    Bucket=self._bucket,
                    Key=self.counter_key(day, counter),
                    Body=body,
                    ContentType="application/json",
                    **condition,
                )
            except ClientError as error:
                if _is_precondition_failed(error):
                    continue
                raise StoreError(f"the counter cannot be written ({_error_code(error)})") from error
            return EmailReservation(allowed=True, count=current + 1)
        raise StoreError("the email counter stayed busy")

    # -- helpers ----------------------------------------------------------------------------

    def _read_counter(self, day: date, counter: str) -> tuple[int, str | None]:
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=self.counter_key(day, counter))
        except ClientError as error:
            if _is_missing(error):
                return 0, None
            raise StoreError(f"the counter cannot be read ({_error_code(error)})") from error
        try:
            count = int(json.loads(response["Body"].read())["count"])
        except (KeyError, ValueError, TypeError) as error:
            raise StoreError("the email counter cannot be read") from error
        if count < 0:
            raise StoreError("the email counter cannot be read")
        return count, str(response["ETag"])

    def _put(self, key: str, body: bytes) -> None:
        try:
            self._s3.put_object(
                Bucket=self._bucket, Key=key, Body=body, ContentType="application/json"
            )
        except ClientError as error:
            raise StoreError(f"an object cannot be written ({_error_code(error)})") from error

    def _get_json(self, key: str) -> dict[str, Any] | None:
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _is_missing(error):
                return None
            raise StoreError(f"an object cannot be read ({_error_code(error)})") from error
        try:
            data = json.loads(response["Body"].read())
        except ValueError as error:
            raise StoreError("an object does not hold valid data") from error
        if not isinstance(data, dict):
            raise StoreError("an object does not hold valid data")
        return data


def _checked_id(import_id: str) -> str:
    if not is_valid_import_id(import_id):
        raise ValueError("the importId is not safe to use in a storage key")
    return import_id
