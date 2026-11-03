"""Tests for custodian.py with moto S3: BR4.1, BR4.2, BR5.2, BR5.5, BR7.1, BR8.2, BR8.3, BR9.

Two buckets stand in for the holding bucket and the final bucket. A wrapper around the data
client records the order of calls and forces a failure at a chosen call: a copy, a marker write,
a delete, a listing, a part of a large copy. A second wrapper stands in for the state client
that reads the marker. moto does not accept the small parts these tests need for a multipart
copy, so the four multipart calls of a large copy are replaced by a stand-in
(``stand_in_multipart``) that records them and, at the end, copies the whole small object in one
call.
"""

import io
import json
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from moto import mock_aws

from model_import.custodian import (
    COMPLETE_MARKER,
    MARKER_DELETE_TRIES,
    FileCustodian,
    KeepError,
    MarkerCheckError,
    MarkerState,
    classify_marker_body,
)
from model_import.deadline import Deadline, DeadlineReached
from model_import.logs import JsonLogger
from model_import.models import (
    FileClass,
    ImportRecord,
    ImportStatus,
    KeepResult,
    Leftover,
    LeftoverArea,
    LeftoverKind,
    ModelFile,
    ModelFileSet,
    ScannedFile,
    final_prefix,
    holding_prefix,
)
from model_import.scanner import ScanResult
from model_import.store import ImportStore

HOLDING = "holding-bucket"
FINAL = "final-bucket"
IMPORT_ID = "imp-1"
OTHER_ID = "imp-2"
NAME = "Org/Model"
VERSION = "a" * 40
TARGET = final_prefix(NAME, VERSION)  # models/org/model/<version>/
MARKER = TARGET + COMPLETE_MARKER
PASSED = ScanResult(files=(), findings=())
HOUR = 3600


def client_error(code: str = "InternalError", status: int = 500, operation: str = "Call") -> Any:
    return ClientError(
        {"Error": {"Code": code, "Message": "no"}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


class _Paginator:
    """Forwards to the real paginator, or fails the listing of one bucket."""

    def __init__(self, inner: Any, owner: "FaultyS3") -> None:
        self._inner = inner
        self._owner = owner

    def paginate(self, **arguments: Any) -> Any:
        if self._owner.fail_list_bucket == arguments.get("Bucket"):
            raise client_error(operation="List")
        return self._inner.paginate(**arguments)


class FaultyS3:
    """Forwards to a real client; records calls; can fail the calls the tests choose."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[tuple[str, str]] = []
        self.delete_attempts: list[str] = []
        self.multipart: list[tuple[str, Any]] = []
        self.fail_copy_at: int | None = None
        self.fail_delete: set[str] = set()
        self.raise_delete_bucket: str | None = None
        self.fail_list_bucket: str | None = None
        self.marker_write = "ok"  # "ok", "error" (nothing written), "effect-then-error"
        self.after_copy: Callable[[int], None] | None = None
        self.after_part: Callable[[int], None] | None = None
        self.stand_in_multipart = False
        self.fail_part_at: int | None = None
        self._copies = 0
        self._parts = 0
        self._part_source: Any = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def copy(self, source: Any, bucket: str, key: str, **options: Any) -> Any:
        self._copies += 1
        if self._copies == self.fail_copy_at:
            raise client_error(operation="CopyObject")
        self.calls.append(("copy", key))
        result = self._inner.copy(source, bucket, key, **options)
        if self.after_copy is not None:
            self.after_copy(self._copies)
        return result

    def put_object(self, **arguments: Any) -> Any:
        self.calls.append(("put", arguments["Key"]))
        if arguments["Key"].endswith(COMPLETE_MARKER) and self.marker_write != "ok":
            if self.marker_write == "effect-then-error":
                self._inner.put_object(**arguments)
            raise client_error(operation="PutObject")
        return self._inner.put_object(**arguments)

    def delete_objects(self, **arguments: Any) -> Any:
        keys = [item["Key"] for item in arguments["Delete"]["Objects"]]
        self.delete_attempts.extend(keys)
        if self.raise_delete_bucket == arguments["Bucket"]:
            raise client_error(operation="DeleteObjects")
        deletable = [key for key in keys if key not in self.fail_delete]
        self.calls.extend(("delete", key) for key in deletable)
        if deletable:
            self._inner.delete_objects(
                Bucket=arguments["Bucket"],
                Delete={"Objects": [{"Key": key} for key in deletable]},
            )
        refused = [key for key in keys if key in self.fail_delete]
        return {"Errors": [{"Key": k, "Code": "InternalError", "Message": "no"} for k in refused]}

    def get_paginator(self, name: str) -> Any:
        return _Paginator(self._inner.get_paginator(name), self)

    # -- the four multipart calls of a large copy (stand-in, see the module text) -------------

    def create_multipart_upload(self, **arguments: Any) -> Any:
        if not self.stand_in_multipart:
            return self._inner.create_multipart_upload(**arguments)
        self.multipart.append(("create", arguments["Key"]))
        return {"UploadId": "upload-1"}

    def upload_part_copy(self, **arguments: Any) -> Any:
        self._parts += 1
        if self._parts == self.fail_part_at:
            raise client_error(operation="UploadPartCopy")
        self.multipart.append(("part", arguments["CopySourceRange"]))
        self._part_source = arguments["CopySource"]
        if self.after_part is not None:
            self.after_part(self._parts)
        return {"CopyPartResult": {"ETag": f'"etag-{arguments["PartNumber"]}"'}}

    def complete_multipart_upload(self, **arguments: Any) -> Any:
        self.multipart.append(
            ("complete", [p["PartNumber"] for p in arguments["MultipartUpload"]["Parts"]])
        )
        return self._inner.copy_object(
            Bucket=arguments["Bucket"], Key=arguments["Key"], CopySource=self._part_source
        )

    def abort_multipart_upload(self, **arguments: Any) -> Any:
        if not self.stand_in_multipart:
            return self._inner.abort_multipart_upload(**arguments)
        self.multipart.append(("abort", arguments["Key"]))
        return {}


class FaultyState:
    """The state client that reads the marker: records calls; can fail HEAD or GET."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[tuple[str, str]] = []
        self.fail_head: Exception | None = None
        self.fail_get: Exception | None = None

    def head_object(self, **arguments: Any) -> Any:
        self.calls.append(("head", arguments["Key"]))
        if self.fail_head is not None:
            raise self.fail_head
        return self._inner.head_object(**arguments)

    def get_object(self, **arguments: Any) -> Any:
        self.calls.append(("get", arguments["Key"]))
        if self.fail_get is not None:
            raise self.fail_get
        return self._inner.get_object(**arguments)


class SpyDeadline(Deadline):
    """A deadline that counts how often it was checked."""

    def __init__(self, started_at: datetime, clock: Any) -> None:
        super().__init__(started_at, clock)
        self.checks = 0

    def check(self) -> None:
        self.checks += 1
        super().check()


@pytest.fixture
def s3() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=HOLDING)
        client.create_bucket(Bucket=FINAL)
        yield client


@pytest.fixture
def faulty(s3: Any) -> FaultyS3:
    return FaultyS3(s3)


@pytest.fixture
def state(s3: Any) -> FaultyState:
    return FaultyState(s3)


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def log_stream() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def custodian(
    faulty: FaultyS3,
    s3: Any,
    state: FaultyState,
    clock: Any,
    sleeps: list[float],
    log_stream: io.StringIO,
) -> FileCustodian:
    return FileCustodian(
        faulty,
        HOLDING,
        FINAL,
        ImportStore(s3, HOLDING),
        clock=clock,
        logger=JsonLogger(log_stream, clock=clock),
        state_client=state,
        sleep=sleeps.append,
    )


def keys(s3: Any, bucket: str, prefix: str = "") -> list[str]:
    listing = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return sorted(item["Key"] for item in listing.get("Contents", []))


def hold_files(s3: Any, files: dict[str, bytes], import_id: str = IMPORT_ID) -> ModelFileSet:
    for path, data in files.items():
        s3.put_object(Bucket=HOLDING, Key=holding_prefix(import_id) + path, Body=data)
    return ModelFileSet(
        import_id, NAME, VERSION, [ModelFile(p, len(d), len(d)) for p, d in files.items()]
    )


def new_record(clock: Any) -> ImportRecord:
    return ImportRecord(IMPORT_ID, NAME, VERSION, ImportStatus.KEEPING, "keep", clock(), clock())


def log_records(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def marker_body(import_id: str) -> bytes:
    return json.dumps({"importId": import_id}).encode()


FILES = {"a.json": b"{}", "w.safetensors": b"0" * 40, "sub/c.txt": b"hello"}
ALL_TARGET_KEYS = sorted([TARGET + path for path in FILES] + [MARKER])


def leave_marker_that_took_effect(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any
) -> None:
    """The marker write took effect but the answer was an error: keep fails, the marker stays."""
    faulty.marker_write = "effect-then-error"
    with pytest.raises(KeepError, match="completion marker could not be written"):
        custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)
    assert keys(s3, FINAL) == ALL_TARGET_KEYS


# -- keep (u1 behavior that still holds) --------------------------------------------------------


def test_br4_1_keep_copies_every_file_writes_the_marker_last_and_empties_holding(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any
) -> None:
    file_set = hold_files(s3, FILES)
    record = new_record(clock)

    result = custodian.keep(record, file_set, PASSED)

    # Three copies, then the marker, and only then the holding area is emptied.
    assert [op for op, _ in faulty.calls] == ["copy"] * 3 + ["put"] + ["delete"] * 3
    assert faulty.calls[3] == ("put", MARKER)  # the marker is written last
    assert keys(s3, FINAL) == ALL_TARGET_KEYS
    marker = json.loads(s3.get_object(Bucket=FINAL, Key=MARKER)["Body"].read())
    assert marker["importId"] == IMPORT_ID
    assert {item["path"]: item["sizeBytes"] for item in marker["files"]} == {
        path: len(data) for path, data in FILES.items()
    }
    assert marker["completedAt"] == "2026-10-30T12:00:00Z"
    assert keys(s3, HOLDING, holding_prefix(IMPORT_ID)) == []
    # u2 (BR9.6): keep returns a KeepResult; the stored model is inside it.
    assert isinstance(result, KeepResult)
    assert result.stored.stored_model_key == TARGET
    assert (result.marker_written, result.holding_emptied, result.leftovers) == (True, True, ())
    saved = ImportStore(s3, HOLDING).read_record(IMPORT_ID)
    assert saved is not None
    assert (saved.files_copied, saved.last_key_copied) == (3, TARGET + "w.safetensors")


def test_br9_1_the_marker_keeps_its_shape_and_adds_the_model_name_and_exact_version(
    s3: Any, custodian: FileCustodian, clock: Any
) -> None:
    custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)

    marker = json.loads(s3.get_object(Bucket=FINAL, Key=MARKER)["Body"].read())

    assert set(marker) == {"importId", "modelName", "exactVersion", "files", "completedAt"}
    assert (marker["modelName"], marker["exactVersion"]) == (NAME, VERSION)
    assert "@" not in json.dumps(marker)  # nothing about the sender (NFR3.21)


def test_br4_1_a_marker_means_already_stored_and_nothing_there_is_deleted(
    s3: Any, custodian: FileCustodian, clock: Any
) -> None:
    s3.put_object(Bucket=FINAL, Key=TARGET + "old.safetensors", Body=b"old")
    s3.put_object(Bucket=FINAL, Key=MARKER, Body=b"{}")
    file_set = hold_files(s3, FILES)

    with pytest.raises(KeepError, match="already in the final location"):
        custodian.keep(new_record(clock), file_set, PASSED)
    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert result.removal_complete is True
    assert keys(s3, FINAL) == [MARKER, TARGET + "old.safetensors"]  # the earlier model is intact
    assert keys(s3, HOLDING, holding_prefix(IMPORT_ID)) == []


def test_reliability_unfinished_files_without_a_marker_are_overwritten_not_blocking(
    s3: Any, custodian: FileCustodian, clock: Any
) -> None:
    s3.put_object(Bucket=FINAL, Key=TARGET + "a.json", Body=b"left by a crashed run")
    s3.put_object(Bucket=FINAL, Key=TARGET + "stray.txt", Body=b"stray")

    custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)

    assert s3.get_object(Bucket=FINAL, Key=TARGET + "a.json")["Body"].read() == b"{}"
    assert TARGET + "stray.txt" in keys(s3, FINAL)  # keep never deletes what it did not copy
    assert MARKER in keys(s3, FINAL)


@pytest.mark.parametrize("case", ["scan-failed", "file-named-marker"])
def test_br4_2_nothing_reaches_the_final_location_unless_the_whole_model_passed(
    s3: Any, custodian: FileCustodian, clock: Any, case: str
) -> None:
    if case == "scan-failed":
        file_set = hold_files(s3, FILES)
        scan = ScanResult(
            files=(ScannedFile(IMPORT_ID, "a.json", FileClass.SCANNED_TEXT, 1, 1),), findings=()
        )
    else:
        file_set = hold_files(s3, {**FILES, COMPLETE_MARKER: b"x"})
        scan = PASSED

    with pytest.raises(KeepError):
        custodian.keep(new_record(clock), file_set, scan)

    assert keys(s3, FINAL) == []


def test_br5_2_a_copy_that_fails_halfway_is_removed_with_the_holding_area(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any
) -> None:
    faulty.fail_copy_at = 2
    file_set = hold_files(s3, FILES)
    record = new_record(clock)

    with pytest.raises(KeepError, match="could not be copied"):
        custodian.keep(record, file_set, PASSED)

    assert record.files_copied == 1  # the record counts the files copied so far
    assert len(keys(s3, FINAL)) == 1
    result = custodian.remove(IMPORT_ID, NAME, VERSION)
    assert result.removal_complete is True
    assert keys(s3, FINAL) == []
    assert keys(s3, HOLDING, holding_prefix(IMPORT_ID)) == []


def test_br5_2_only_this_imports_unfinished_uploads_are_aborted(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any
) -> None:
    faulty.fail_copy_at = 1
    with pytest.raises(KeepError):
        custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)
    mine_final = TARGET + "a.json"  # the key this import began to write
    uploads = {
        "mine-holding": (HOLDING, holding_prefix(IMPORT_ID) + "big.bin"),
        "mine-final": (FINAL, mine_final),
        "other-holding": (HOLDING, holding_prefix("imp-2") + "big.bin"),
        "other-final": (FINAL, "models/other/model/" + "b" * 40 + "/x"),
    }
    for bucket, key in uploads.values():
        s3.create_multipart_upload(Bucket=bucket, Key=key)

    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    left = {
        (bucket, item["Key"])
        for bucket in (HOLDING, FINAL)
        for item in s3.list_multipart_uploads(Bucket=bucket).get("Uploads", [])
    }
    assert left == {uploads["other-holding"], uploads["other-final"]}
    assert result.removal_complete is True


# -- the marker after a write that took effect (u1 removal tests, changed for BR9.6) -----------


def test_br5_2_removal_deletes_the_marker_first_then_the_files(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any
) -> None:
    # u1 removed a stored model here. u2 (BR9.6, NFR4.14) never removes a model whose marker
    # write returned success, so the marker is made by a write that took effect but failed.
    leave_marker_that_took_effect(s3, faulty, custodian, clock)
    faulty.calls.clear()

    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    deleted = [key for op, key in faulty.calls if op == "delete"]
    assert deleted[0] == MARKER
    assert sorted(deleted[1:4]) == sorted(TARGET + path for path in FILES)
    assert result.removal_complete is True
    assert keys(s3, FINAL) == []


def test_nfr4_3_removal_run_twice_gives_the_same_end_state(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any
) -> None:
    leave_marker_that_took_effect(s3, faulty, custodian, clock)
    ImportStore(s3, HOLDING).write_record(new_record(clock))

    first = custodian.remove(IMPORT_ID, NAME, VERSION)
    second = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert first.removal_complete is True
    assert second.removal_complete is True
    assert (first.older_files_removed, second.older_files_removed) == (0, 0)
    assert keys(s3, FINAL) == []
    assert keys(s3, HOLDING) == [f"state/{IMPORT_ID}/record.json"]  # state is not holding data


def test_br5_5_a_marker_never_outlives_its_files_and_a_failed_delete_is_not_complete(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any, sleeps: list[float]
) -> None:
    leave_marker_that_took_effect(s3, faulty, custodian, clock)
    faulty.fail_delete = {MARKER}

    first = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert first.removal_complete is False
    assert first.leftovers == (Leftover(LeftoverKind.OBJECT, LeftoverArea.FINAL_LOCATION, MARKER),)
    # BR9.2: three tries, with the waits between them. The marker stays, so the files it lists
    # stay with it: the folder is never left as a model that looks incomplete without a marker.
    assert faulty.delete_attempts.count(MARKER) == MARKER_DELETE_TRIES
    assert sleeps == [1.0, 2.0]
    assert keys(s3, FINAL) == ALL_TARGET_KEYS
    assert keys(s3, HOLDING, holding_prefix(IMPORT_ID)) == []  # the holding area still went

    faulty.fail_delete = {TARGET + "a.json"}
    second = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert second.removal_complete is False
    assert keys(s3, FINAL) == [TARGET + "a.json"]  # marker gone first, the other files gone

    faulty.fail_delete = set()
    third = custodian.remove(IMPORT_ID, NAME, VERSION)
    assert third.removal_complete is True
    assert keys(s3, FINAL) == []


# -- the forced-failure proof (functional-spec.md; BR5.2, BR9.1, BR9.2, BR9.3) ----------------


@pytest.mark.parametrize(
    ("fail_at", "copied"), [(1, 0), (2, 1), (3, 2)], ids=["first", "middle", "last"]
)
def test_br9_2_a_failed_copy_at_the_first_a_middle_and_the_last_file_leaves_nothing(
    fail_at: int,
    copied: int,
    s3: Any,
    faulty: FaultyS3,
    custodian: FileCustodian,
    clock: Any,
) -> None:
    faulty.fail_copy_at = fail_at
    record = new_record(clock)

    with pytest.raises(KeepError):
        custodian.keep(record, hold_files(s3, FILES), PASSED)

    assert MARKER not in keys(s3, FINAL)  # no marker: the folder never looks like a model
    assert record.files_copied == copied
    result = custodian.remove(IMPORT_ID, NAME, VERSION)
    assert result.removal_complete is True and result.leftovers == ()
    assert keys(s3, FINAL) == [] and keys(s3, HOLDING, holding_prefix(IMPORT_ID)) == []


def test_br9_1_a_failed_marker_write_leaves_no_marker_and_the_removal_clears_the_files(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any
) -> None:
    faulty.marker_write = "error"

    with pytest.raises(KeepError, match="completion marker could not be written"):
        custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)

    assert len(keys(s3, FINAL)) == 3 and MARKER not in keys(s3, FINAL)
    assert keys(s3, HOLDING, holding_prefix(IMPORT_ID)) != []  # not emptied before the marker
    result = custodian.remove(IMPORT_ID, NAME, VERSION)
    assert result.removal_complete is True
    assert keys(s3, FINAL) == [] and keys(s3, HOLDING, holding_prefix(IMPORT_ID)) == []


def test_br9_3_a_file_whose_delete_fails_is_a_leftover_and_the_removal_is_not_complete(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any
) -> None:
    faulty.fail_copy_at = 3
    with pytest.raises(KeepError):
        custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)
    faulty.fail_delete = {TARGET + "a.json"}

    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert result.removal_complete is False
    assert result.leftovers == (
        Leftover(LeftoverKind.OBJECT, LeftoverArea.FINAL_LOCATION, TARGET + "a.json"),
    )
    assert result.leftovers_total == 1
    assert keys(s3, FINAL) == [TARGET + "a.json"]  # the proof listing found it, once


def test_br9_3_a_failing_listing_is_reported_as_a_leftover_and_the_known_keys_still_go(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any, log_stream: io.StringIO
) -> None:
    faulty.fail_copy_at = 3
    with pytest.raises(KeepError):
        custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)
    faulty.fail_list_bucket = FINAL

    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert result.removal_complete is False
    assert {item.kind for item in result.leftovers} == {LeftoverKind.TEMPORARY_RESOURCE}
    assert {item.area for item in result.leftovers} == {LeftoverArea.FINAL_LOCATION}
    assert keys(s3, FINAL) == []  # the keys this import wrote were deleted without the listing
    leftover_records = [r for r in log_records(log_stream) if r["event"] == "leftover"]
    assert leftover_records and all(r["place"] == "final-location" for r in leftover_records)


def test_br9_3_a_failing_listing_of_the_holding_area_is_a_leftover(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any
) -> None:
    faulty.fail_copy_at = 1
    with pytest.raises(KeepError):
        custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)
    faulty.fail_list_bucket = HOLDING

    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert result.removal_complete is False
    assert all(item.area is LeftoverArea.HOLDING_AREA for item in result.leftovers)


def test_br9_5_older_unfinished_files_are_removed_with_a_failed_move_and_counted(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any
) -> None:
    s3.put_object(Bucket=FINAL, Key=TARGET + "stray.txt", Body=b"left by a crashed run")
    faulty.fail_copy_at = 2
    with pytest.raises(KeepError):
        custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)

    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert result.removal_complete is True
    assert result.older_files_removed == 1  # stray.txt: not a leftover, but counted
    assert keys(s3, FINAL) == []


def test_br9_5_nothing_in_the_final_location_is_touched_when_the_keep_step_never_began(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian
) -> None:
    # A download failure or a deadline stop before the keep step: older files are left alone.
    s3.put_object(Bucket=FINAL, Key=TARGET + "stray.txt", Body=b"left by a crashed run")
    s3.put_object(Bucket=FINAL, Key=TARGET + "a.json", Body=b"older")
    hold_files(s3, FILES)

    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert result.removal_complete is True and result.older_files_removed == 0
    assert keys(s3, FINAL) == [TARGET + "a.json", TARGET + "stray.txt"]
    assert keys(s3, HOLDING, holding_prefix(IMPORT_ID)) == []
    assert all(key.startswith(holding_prefix(IMPORT_ID)) for key in faulty.delete_attempts)


# -- the marker reader (BR7.1) ---------------------------------------------------------------


def test_br7_1_the_reader_says_missing_when_there_is_no_marker_and_makes_one_call(
    custodian: FileCustodian, state: FaultyState
) -> None:
    assert custodian.read_marker(NAME, VERSION, IMPORT_ID) is MarkerState.MISSING
    assert state.calls == [("head", MARKER)]  # no GET when there is nothing to read


def test_br7_1_the_reader_tells_an_own_marker_from_the_marker_of_another_import(
    s3: Any, custodian: FileCustodian, state: FaultyState
) -> None:
    s3.put_object(Bucket=FINAL, Key=MARKER, Body=marker_body(IMPORT_ID))

    assert custodian.read_marker(NAME, VERSION, IMPORT_ID) is MarkerState.OWN
    assert custodian.read_marker(NAME, VERSION, OTHER_ID) is MarkerState.OTHER
    assert state.calls[:2] == [("head", MARKER), ("get", MARKER)]  # the state client, not the data


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b"{}",
        b'{"importId": "../x"}',
        b'{"importId": 7}',
        b" " * (1024 * 1024 + 1),
    ],
    ids=["text", "list", "no-id", "unsafe-id", "number-id", "too-large"],
)
def test_br7_1_a_marker_that_is_damaged_is_unreadable_and_logged(
    body: bytes, s3: Any, custodian: FileCustodian, log_stream: io.StringIO
) -> None:
    s3.put_object(Bucket=FINAL, Key=MARKER, Body=body)

    assert custodian.read_marker(NAME, VERSION, IMPORT_ID) is MarkerState.UNREADABLE
    assert classify_marker_body(body, IMPORT_ID) is MarkerState.UNREADABLE
    assert [r["event"] for r in log_records(log_stream)] == ["marker_unreadable"]


@pytest.mark.parametrize("failing_call", ["head", "get", "connection"])
def test_br7_1_a_failed_check_is_raised_and_is_never_read_as_unreadable_or_missing(
    failing_call: str, s3: Any, custodian: FileCustodian, state: FaultyState
) -> None:
    s3.put_object(Bucket=FINAL, Key=MARKER, Body=marker_body(IMPORT_ID))
    if failing_call == "head":
        state.fail_head = client_error("SlowDown", 503, "HeadObject")
    elif failing_call == "get":
        state.fail_get = client_error("InternalError", 500, "GetObject")
    else:
        state.fail_head = EndpointConnectionError(endpoint_url="https://s3.example")

    with pytest.raises(MarkerCheckError) as raised:
        custodian.read_marker(NAME, VERSION, IMPORT_ID)

    assert isinstance(raised.value, KeepError)  # a failed step for the coordinator
    assert "could not be checked" in raised.value.reason


def test_br7_1_a_marker_removed_between_the_head_and_the_get_reads_as_missing(
    s3: Any, custodian: FileCustodian, state: FaultyState
) -> None:
    s3.put_object(Bucket=FINAL, Key=MARKER, Body=marker_body(IMPORT_ID))
    state.fail_get = client_error("NoSuchKey", 404, "GetObject")

    assert custodian.read_marker(NAME, VERSION, IMPORT_ID) is MarkerState.MISSING


def test_br7_1_without_a_state_client_the_reader_uses_the_data_client(s3: Any, clock: Any) -> None:
    s3.put_object(Bucket=FINAL, Key=MARKER, Body=marker_body(IMPORT_ID))
    plain = FileCustodian(s3, HOLDING, FINAL, ImportStore(s3, HOLDING), clock=clock)

    assert plain.read_marker(NAME, VERSION, IMPORT_ID) is MarkerState.OWN


def test_br9_2_a_failed_check_of_the_marker_during_removal_leaves_the_final_location_alone(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, state: FaultyState, clock: Any
) -> None:
    leave_marker_that_took_effect(s3, faulty, custodian, clock)
    state.fail_head = client_error("SlowDown", 503, "HeadObject")

    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert result.removal_complete is False
    assert result.leftovers == (Leftover(LeftoverKind.OBJECT, LeftoverArea.FINAL_LOCATION, MARKER),)
    assert keys(s3, FINAL) == ALL_TARGET_KEYS  # nothing was deleted without knowing the marker


def test_br9_2_a_marker_that_cannot_be_read_during_removal_is_left_and_reported(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, state: FaultyState, clock: Any
) -> None:
    leave_marker_that_took_effect(s3, faulty, custodian, clock)
    state.fail_get = client_error("InternalError", 500, "GetObject")

    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert result.removal_complete is False and result.leftovers_total == 1
    assert keys(s3, FINAL) == ALL_TARGET_KEYS


def test_br9_2_a_damaged_marker_is_not_deleted_and_the_files_beside_it_stay(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any
) -> None:
    faulty.fail_copy_at = 2
    with pytest.raises(KeepError):
        custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)
    s3.put_object(Bucket=FINAL, Key=MARKER, Body=b"damaged by someone else")

    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert result.removal_complete is False
    assert MARKER in keys(s3, FINAL)
    assert MARKER not in faulty.delete_attempts
    assert keys(s3, HOLDING, holding_prefix(IMPORT_ID)) == []


def test_br9_2_a_complete_copy_of_another_import_is_never_removed(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any, log_stream: io.StringIO
) -> None:
    faulty.fail_copy_at = 2
    with pytest.raises(KeepError):
        custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)
    s3.put_object(Bucket=FINAL, Key=MARKER, Body=marker_body(OTHER_ID))
    s3.put_object(Bucket=FINAL, Key=TARGET + "theirs.safetensors", Body=b"complete model")

    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert result.removal_complete is True  # nothing of this import is left to remove there
    assert MARKER in keys(s3, FINAL) and TARGET + "theirs.safetensors" in keys(s3, FINAL)
    assert "complete_copy_left_alone" in [r["event"] for r in log_records(log_stream)]


# -- after the marker (BR9.6, NFR4.14) --------------------------------------------------------


def test_br9_6_the_holding_area_that_cannot_be_deleted_after_the_marker_does_not_fail_the_keep(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any, log_stream: io.StringIO
) -> None:
    faulty.raise_delete_bucket = HOLDING

    result = custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)

    assert result.marker_written is True and result.holding_emptied is False
    assert {item.area for item in result.leftovers} == {LeftoverArea.HOLDING_AREA}
    assert len(result.leftovers) == len(FILES)  # each file once, not twice
    assert keys(s3, FINAL) == ALL_TARGET_KEYS  # the model stays
    events = [r["event"] for r in log_records(log_stream)]
    assert "holding_not_emptied" in events and events.count("leftover") == len(FILES)


def test_br9_6_an_unexpected_error_after_the_marker_is_caught_and_never_removes_the_model(
    s3: Any,
    custodian: FileCustodian,
    clock: Any,
    monkeypatch: pytest.MonkeyPatch,
    log_stream: io.StringIO,
) -> None:
    def explode(*_: Any, **__: Any) -> Any:
        raise RuntimeError("secret=should-not-be-logged")

    monkeypatch.setattr(custodian, "_clear_prefix", explode)

    result = custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)

    assert result.holding_emptied is False
    assert [item.kind for item in result.leftovers] == [LeftoverKind.TEMPORARY_RESOURCE]
    assert keys(s3, FINAL) == ALL_TARGET_KEYS
    assert "should-not-be-logged" not in log_stream.getvalue()  # only the error name is logged
    assert custodian.remove(IMPORT_ID, NAME, VERSION).model_stored is True


def test_nfr4_14_a_removal_asked_for_a_stored_import_deletes_nothing_and_says_stored(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian, clock: Any
) -> None:
    custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)
    faulty.delete_attempts.clear()

    result = custodian.remove(IMPORT_ID, NAME, VERSION)

    assert result.model_stored is True
    assert result.removal_complete is False  # never "removal complete" for a stored model
    assert faulty.delete_attempts == []
    assert keys(s3, FINAL) == ALL_TARGET_KEYS


def test_br7_1_br9_6_finishing_an_own_marker_deletes_only_the_holding_folder(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian
) -> None:
    s3.put_object(Bucket=FINAL, Key=MARKER, Body=marker_body(IMPORT_ID))
    s3.put_object(Bucket=FINAL, Key=TARGET + "a.json", Body=b"{}")
    hold_files(s3, FILES)
    hold_files(s3, {"x.json": b"{}"}, import_id=OTHER_ID)

    result = custodian.finish_own_marker(IMPORT_ID, NAME, VERSION)

    assert isinstance(result, KeepResult) and result.marker_written and result.holding_emptied
    assert keys(s3, HOLDING, holding_prefix(IMPORT_ID)) == []
    assert keys(s3, HOLDING, holding_prefix(OTHER_ID)) != []  # another import's files stay
    assert keys(s3, FINAL) == [MARKER, TARGET + "a.json"]
    assert all(key.startswith(holding_prefix(IMPORT_ID)) for key in faulty.delete_attempts)
    assert custodian.remove(IMPORT_ID, NAME, VERSION).model_stored is True


def test_br9_6_finishing_an_own_marker_never_raises_when_the_delete_fails(
    s3: Any, faulty: FaultyS3, custodian: FileCustodian
) -> None:
    s3.put_object(Bucket=FINAL, Key=MARKER, Body=marker_body(IMPORT_ID))
    hold_files(s3, FILES)
    faulty.raise_delete_bucket = HOLDING

    result = custodian.finish_own_marker(IMPORT_ID, NAME, VERSION)

    assert result.holding_emptied is False and result.leftovers


# -- the deadline (BR8.2, BR8.3) ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("advance_after_copy", "copied"),
    [(2, 2), (3, 3)],
    ids=["before-the-third-copy", "before-marker"],
)
def test_br8_2_the_deadline_before_a_copy_or_before_the_marker_stops_the_keep_step(
    advance_after_copy: int,
    copied: int,
    s3: Any,
    faulty: FaultyS3,
    custodian: FileCustodian,
    clock: Any,
) -> None:
    deadline = Deadline(clock(), clock)
    faulty.after_copy = lambda number: (
        clock.advance(6 * HOUR) if number == advance_after_copy else None
    )
    record = new_record(clock)

    with pytest.raises(DeadlineReached):
        custodian.keep(record, hold_files(s3, FILES), PASSED, deadline)

    assert record.files_copied == copied
    assert MARKER not in keys(s3, FINAL)  # BR8.3: no marker once the deadline passed
    result = custodian.remove(IMPORT_ID, NAME, VERSION)
    assert result.removal_complete is True
    assert keys(s3, FINAL) == [] and keys(s3, HOLDING, holding_prefix(IMPORT_ID)) == []


def test_br8_3_the_deadline_is_checked_before_each_file_and_before_the_marker_only(
    s3: Any, custodian: FileCustodian, clock: Any
) -> None:
    deadline = SpyDeadline(clock(), clock)

    custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED, deadline)

    assert deadline.checks == len(FILES) + 1  # three files and the last check before the marker
    clock.advance(7 * HOUR)  # after the marker nothing asks the deadline again
    assert custodian.finish_own_marker(IMPORT_ID, NAME, VERSION).marker_written is True
    assert deadline.checks == len(FILES) + 1


def test_br8_2_a_file_above_the_limit_is_copied_in_parts_with_a_check_before_each(
    s3: Any, faulty: FaultyS3, clock: Any, state: FaultyState
) -> None:
    faulty.stand_in_multipart = True
    custodian = FileCustodian(
        faulty,
        HOLDING,
        FINAL,
        ImportStore(s3, HOLDING),
        clock=clock,
        state_client=state,
        single_copy_limit=10,
        large_copy_part_bytes=16,
    )
    deadline = SpyDeadline(clock(), clock)

    result = custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED, deadline)

    # The 40 byte weight file is above 10 bytes: 3 parts of up to 16 bytes, then the complete call.
    assert faulty.multipart == [
        ("create", TARGET + "w.safetensors"),
        ("part", "bytes=0-15"),
        ("part", "bytes=16-31"),
        ("part", "bytes=32-39"),
        ("complete", [1, 2, 3]),
    ]
    assert deadline.checks == len(FILES) + 1 + 3  # the file checks, one per part, and the marker
    assert result.marker_written is True
    assert s3.head_object(Bucket=FINAL, Key=TARGET + "w.safetensors")["ContentLength"] == 40


def test_br8_2_the_deadline_inside_a_large_copy_aborts_the_upload_and_stops(
    s3: Any, faulty: FaultyS3, clock: Any, state: FaultyState
) -> None:
    faulty.stand_in_multipart = True
    faulty.after_part = lambda number: clock.advance(6 * HOUR) if number == 1 else None
    custodian = FileCustodian(
        faulty,
        HOLDING,
        FINAL,
        ImportStore(s3, HOLDING),
        clock=clock,
        state_client=state,
        single_copy_limit=10,
        large_copy_part_bytes=16,
    )

    with pytest.raises(DeadlineReached):
        custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED, Deadline(clock(), clock))

    assert [op for op, _ in faulty.multipart] == ["create", "part", "abort"]  # no complete call
    assert MARKER not in keys(s3, FINAL) and TARGET + "w.safetensors" not in keys(s3, FINAL)


def test_br8_2_a_failed_part_of_a_large_copy_aborts_the_upload_and_fails_the_keep_step(
    s3: Any, faulty: FaultyS3, clock: Any, state: FaultyState
) -> None:
    faulty.stand_in_multipart = True
    faulty.fail_part_at = 2
    custodian = FileCustodian(
        faulty,
        HOLDING,
        FINAL,
        ImportStore(s3, HOLDING),
        clock=clock,
        state_client=state,
        single_copy_limit=10,
        large_copy_part_bytes=16,
    )

    with pytest.raises(KeepError, match="could not be copied"):
        custodian.keep(new_record(clock), hold_files(s3, FILES), PASSED)

    assert [op for op, _ in faulty.multipart] == ["create", "part", "abort"]


def test_the_copy_sizes_must_be_at_least_one_byte(s3: Any) -> None:
    with pytest.raises(ValueError):
        FileCustodian(s3, HOLDING, FINAL, ImportStore(s3, HOLDING), single_copy_limit=0)
    with pytest.raises(ValueError):
        FileCustodian(s3, HOLDING, FINAL, ImportStore(s3, HOLDING), large_copy_part_bytes=0)
