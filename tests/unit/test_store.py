"""Tests for store.py with moto S3: record, single outcome, daily counter (NFR4.1, NFR3.16)."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import boto3
import pytest
from moto import mock_aws

from model_import.models import (
    ImportRecord,
    ImportStatus,
    Outcome,
    OutcomeRecord,
)
from model_import.store import (
    FAILURE_EMAIL_LIMIT,
    NOTICE_COUNTER,
    NOTICE_EMAIL_LIMIT,
    EmailReservation,
    ImportStore,
    StoreError,
)

BUCKET = "holding-test"
NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
DAY = date(2026, 10, 30)


@pytest.fixture
def s3() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def store(s3: Any) -> ImportStore:
    return ImportStore(s3, BUCKET)


def make_outcome(outcome: Outcome = Outcome.STORED, import_id: str = "imp-1") -> OutcomeRecord:
    return OutcomeRecord(import_id, outcome, True, NOW)


def test_nfr4_8_record_is_written_and_read_back(store: ImportStore, s3: Any) -> None:
    record = ImportRecord("imp-1", "org/model", "a" * 40, ImportStatus.SCANNING, "scan", NOW, NOW)

    store.write_record(record)

    assert store.read_record("imp-1") == record
    assert s3.head_object(Bucket=BUCKET, Key="state/imp-1/record.json")["ContentLength"] > 0


def test_nfr4_8_record_is_rewritten_before_each_step(store: ImportStore) -> None:
    record = ImportRecord("imp-1", "org/model", "a" * 40, ImportStatus.KEEPING, "keep", NOW, NOW)
    store.write_record(record)
    record.files_copied, record.last_key_copied = 2, "models/org/model/x/b.json"

    store.write_record(record)

    stored = store.read_record("imp-1")
    assert stored is not None
    assert (stored.files_copied, stored.last_key_copied) == (2, "models/org/model/x/b.json")


def test_a_missing_record_and_outcome_read_as_none(store: ImportStore) -> None:
    assert store.read_record("imp-none") is None
    assert store.read_outcome("imp-none") is None
    assert not store.has_outcome("imp-none")


def test_nfr4_1_outcome_is_created_once_and_the_first_one_stays(store: ImportStore) -> None:
    first = make_outcome(Outcome.STORED)
    second = make_outcome(Outcome.REMOVED_WITH_EMAIL)

    created_first = store.create_outcome(first)
    created_second = store.create_outcome(second)

    assert created_first is True
    assert created_second is False
    assert store.read_outcome("imp-1") == first
    assert store.has_outcome("imp-1")


def test_nfr3_16_counter_counts_each_attempt_of_the_day(store: ImportStore) -> None:
    results = [store.reserve_email_attempt(DAY) for _ in range(3)]

    assert [item.count for item in results] == [1, 2, 3]
    assert all(item.allowed for item in results)
    assert store.read_email_count(DAY) == 3


def test_nfr3_16_nfr3_18_the_31st_failure_attempt_of_a_day_is_not_allowed_and_not_counted(
    store: ImportStore,
) -> None:
    # u2-import-rules (NFR3.18) amends NFR3.16: the main counter now stops at 30, not 50.
    for _ in range(30):
        assert store.reserve_email_attempt(DAY).allowed

    refused = store.reserve_email_attempt(DAY)

    assert refused.allowed is False
    assert refused.count == 30
    assert store.read_email_count(DAY) == 30


def test_nfr3_16_counters_are_kept_per_day(store: ImportStore) -> None:
    store.reserve_email_attempt(DAY)
    store.reserve_email_attempt(DAY)

    assert store.reserve_email_attempt(date(2026, 10, 31)).count == 1
    assert store.read_email_count(DAY) == 2


class RacingS3:
    """Wraps a client: just before our first ETag update, another writer updates the counter."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._raced = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def put_object(self, **kwargs: Any) -> Any:
        if "IfMatch" in kwargs and not self._raced:
            self._raced = True
            ImportStore(self._inner, BUCKET).reserve_email_attempt(DAY)  # the other writer wins
        return self._inner.put_object(**kwargs)


def test_nfr3_16_a_lost_race_is_read_again_so_no_update_is_overwritten(s3: Any) -> None:
    ImportStore(s3, BUCKET).reserve_email_attempt(DAY)  # counter exists, count 1
    racing_store = ImportStore(RacingS3(s3), BUCKET)

    result = racing_store.reserve_email_attempt(DAY)

    assert result == EmailReservation(allowed=True, count=3)
    assert ImportStore(s3, BUCKET).read_email_count(DAY) == 3


def test_an_importid_that_is_not_safe_for_a_key_is_refused(store: ImportStore) -> None:
    with pytest.raises(ValueError):
        store.read_record("../other/state")
    with pytest.raises(ValueError):
        store.create_outcome(make_outcome(import_id="imp/1"))


def test_a_storage_error_is_raised_as_store_error_without_content(s3: Any) -> None:
    broken = ImportStore(s3, "bucket-that-does-not-exist")
    record = ImportRecord("imp-1", "org/model", "a" * 40, ImportStatus.SCANNING, "scan", NOW, NOW)

    with pytest.raises(StoreError, match="NoSuchBucket") as raised:
        broken.write_record(record)

    assert "org/model" not in str(raised.value)


def test_an_unreadable_record_is_a_store_error(store: ImportStore, s3: Any) -> None:
    s3.put_object(Bucket=BUCKET, Key="state/imp-1/record.json", Body=b"not json")

    with pytest.raises(StoreError):
        store.read_record("imp-1")


# -- u2-import-rules: two counters, each with its own name and limit (NFR3.18) -----------------


def test_nfr3_18_the_notice_counter_stops_at_20_and_does_not_use_the_failure_counter(
    store: ImportStore,
) -> None:
    for _ in range(NOTICE_EMAIL_LIMIT):
        assert store.reserve_email_attempt(DAY, NOTICE_EMAIL_LIMIT, counter=NOTICE_COUNTER).allowed

    refused = store.reserve_email_attempt(DAY, NOTICE_EMAIL_LIMIT, counter=NOTICE_COUNTER)

    assert (refused.allowed, refused.count) == (False, 20)
    assert store.read_email_count(DAY, NOTICE_COUNTER) == 20
    assert store.read_email_count(DAY) == 0  # the 30 failure emails are still all there
    assert store.reserve_email_attempt(DAY).allowed


def test_nfr3_18_the_two_limits_are_20_and_30_and_together_at_most_50_a_day() -> None:
    assert (NOTICE_EMAIL_LIMIT, FAILURE_EMAIL_LIMIT) == (20, 30)
    assert NOTICE_EMAIL_LIMIT + FAILURE_EMAIL_LIMIT == 50


def test_nfr3_18_each_counter_is_its_own_object_named_by_counter_and_utc_day(
    store: ImportStore, s3: Any
) -> None:
    store.reserve_email_attempt(DAY)
    store.reserve_email_attempt(DAY, NOTICE_EMAIL_LIMIT, counter=NOTICE_COUNTER)

    assert ImportStore.counter_key(DAY) == "state/counters/emails-2026-10-30.json"
    assert ImportStore.counter_key(DAY, NOTICE_COUNTER) == "state/counters/notices-2026-10-30.json"
    for key in (ImportStore.counter_key(DAY), ImportStore.counter_key(DAY, NOTICE_COUNTER)):
        assert s3.head_object(Bucket=BUCKET, Key=key)["ContentLength"] > 0


def test_nfr3_18_the_notice_counter_starts_again_on_the_next_utc_day(store: ImportStore) -> None:
    for _ in range(NOTICE_EMAIL_LIMIT):
        store.reserve_email_attempt(DAY, NOTICE_EMAIL_LIMIT, counter=NOTICE_COUNTER)
    next_day = date(2026, 10, 31)

    result = store.reserve_email_attempt(next_day, NOTICE_EMAIL_LIMIT, counter=NOTICE_COUNTER)

    assert (result.allowed, result.count) == (True, 1)
    assert store.read_email_count(DAY, NOTICE_COUNTER) == 20


def test_nfr3_18_a_counter_name_outside_the_two_is_refused_so_no_key_can_be_made_from_it(
    store: ImportStore,
) -> None:
    with pytest.raises(ValueError):
        ImportStore.counter_key(DAY, "../outcome")
    with pytest.raises(ValueError):
        store.reserve_email_attempt(DAY, 5, counter="other")


def test_nfr3_18_a_lost_race_on_the_notice_counter_is_read_again(s3: Any) -> None:
    ImportStore(s3, BUCKET).reserve_email_attempt(DAY, NOTICE_EMAIL_LIMIT, counter=NOTICE_COUNTER)

    class RacingNotices(RacingS3):
        def put_object(self, **kwargs: Any) -> Any:
            if "IfMatch" in kwargs and not self._raced:
                self._raced = True
                ImportStore(self._inner, BUCKET).reserve_email_attempt(
                    DAY, NOTICE_EMAIL_LIMIT, counter=NOTICE_COUNTER
                )
            return self._inner.put_object(**kwargs)

    result = ImportStore(RacingNotices(s3), BUCKET).reserve_email_attempt(
        DAY, NOTICE_EMAIL_LIMIT, counter=NOTICE_COUNTER
    )

    assert result == EmailReservation(allowed=True, count=3)
