"""Tests for logs.py: the allow-list JSON logger (NFR5.4, NFR5.5, BR5.4)."""

import io
import json
from datetime import UTC, datetime

import pytest

from model_import.logs import (
    ALLOWED_FIELDS,
    JsonLogger,
    build_metric_envelope,
    clean_file_name,
    event_reference,
    is_safe_event_id,
)

FIXED_TIME = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


def make_logger() -> tuple[JsonLogger, io.StringIO]:
    stream = io.StringIO()
    return JsonLogger(stream, clock=lambda: FIXED_TIME), stream


def read_records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_writes_one_json_record_per_line() -> None:
    logger, stream = make_logger()

    logger.info("import_started", importId="imp-1", status="downloading")
    logger.warning("slow", importId="imp-1")

    records = read_records(stream)
    assert len(records) == 2
    assert records[0] == {
        "time": "2026-10-30T12:00:00Z",
        "level": "INFO",
        "event": "import_started",
        "importId": "imp-1",
        "status": "downloading",
    }
    assert records[1]["level"] == "WARNING"


def test_fields_outside_the_allow_list_are_dropped_nfr5_5() -> None:
    logger, stream = make_logger()

    logger.info("scan_done", importId="imp-1", password="hunter2", matchedValue="AKIA-example")

    (record,) = read_records(stream)
    assert set(record) <= ALLOWED_FIELDS
    assert "hunter2" not in stream.getvalue()
    assert "AKIA-example" not in stream.getvalue()


def test_values_that_are_not_plain_are_dropped() -> None:
    logger, stream = make_logger()

    logger.info("scan_done", importId="imp-1", reason={"nested": "x"}, place=["line 1"])

    (record,) = read_records(stream)
    assert "reason" not in record
    assert "place" not in record
    assert record["importId"] == "imp-1"


def test_file_name_is_cleaned_before_logging_br5_4() -> None:
    logger, stream = make_logger()

    logger.info("finding", fileName="bad\nname\x1b[0m" + "x" * 300)

    (record,) = read_records(stream)
    name = str(record["fileName"])
    assert "\n" not in name and "\x1b" not in name
    assert len(name) == 200
    assert name.startswith("bad?name?")


def test_clean_file_name_replaces_control_characters_and_cuts_br5_4() -> None:
    assert clean_file_name("a\tb\u2028c") == "a?b?c"
    assert len(clean_file_name("z" * 1000)) == 200


def test_error_field_keeps_only_short_codes() -> None:
    logger, stream = make_logger()

    logger.error("failed", error="HF_404")
    logger.error("failed", error="Traceback: secret=abc at line 3")

    first, second = read_records(stream)
    assert first["error"] == "HF_404"
    assert second["error"] == "invalid_error_code"


U1_ALLOWED_FIELDS = frozenset(
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
    }
)
U2_NEW_FIELDS = frozenset(
    {
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


def test_nfr5_16_the_allow_list_is_the_old_list_plus_exactly_the_eight_new_fields() -> None:
    assert ALLOWED_FIELDS == U1_ALLOWED_FIELDS | U2_NEW_FIELDS
    assert len(U2_NEW_FIELDS) == 8
    assert not {"senderEmail", "sender", "address", "email", "recipients"} & ALLOWED_FIELDS


def test_nfr5_16_the_new_fields_are_written_and_an_address_field_is_still_dropped() -> None:
    logger, stream = make_logger()

    logger.info(
        "import_outcome",
        eventId="evt-1:a",
        emailSent=False,
        recipientsNotReached="sender,team",
        emailProblem="service-error",
        olderFilesRemoved=3,
        leftoversTotal=0,
        frontDoorSeconds=0.5,
        proofSeconds=1.25,
        senderEmail="person@example.com",
    )

    (record,) = read_records(stream)
    assert record["eventId"] == "evt-1:a"
    assert record["emailSent"] is False
    assert record["recipientsNotReached"] == "sender,team"
    assert (record["olderFilesRemoved"], record["leftoversTotal"]) == (3, 0)
    assert (record["frontDoorSeconds"], record["proofSeconds"]) == (0.5, 1.25)
    assert "senderEmail" not in record
    assert "person@example.com" not in stream.getvalue()


@pytest.mark.parametrize(
    "value", ["", "a b", "evt\n1", "x" * 129, "secret=hunter2!", "person@example.com", 7, None]
)
def test_nfr3_19_an_event_id_that_is_not_a_safe_bus_id_is_left_out_of_the_record(
    value: object,
) -> None:
    logger, stream = make_logger()

    logger.info("event_rejected", importId="imp-1", eventId=value)

    (record,) = read_records(stream)
    assert "eventId" not in record
    assert record["importId"] == "imp-1"
    assert not isinstance(value, str) or not value or value not in stream.getvalue()


def test_nfr3_19_a_uuid_style_event_id_is_a_safe_bus_id() -> None:
    assert is_safe_event_id("3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c")
    assert is_safe_event_id("a" * 128)
    assert not is_safe_event_id("a" * 129)


def test_bound_context_is_added_to_every_record() -> None:
    logger, stream = make_logger()
    bound = logger.bind(importId="imp-9", modelName="org/model")

    bound.info("step", step="download")

    (record,) = read_records(stream)
    assert record["importId"] == "imp-9"
    assert record["modelName"] == "org/model"
    assert record["step"] == "download"


def test_metric_envelope_and_named_values_are_written() -> None:
    logger, stream = make_logger()
    envelope = build_metric_envelope(
        {"bytesDownloaded": "Bytes"}, timestamp_ms=1, dimensions=["outcome"]
    )

    logger.info("import_outcome", outcome="stored", _aws=envelope, bytesDownloaded=1234)

    (record,) = read_records(stream)
    assert record["bytesDownloaded"] == 1234
    assert record["_aws"]["CloudWatchMetrics"][0]["Metrics"] == [
        {"Name": "bytesDownloaded", "Unit": "Bytes"}
    ]


def test_unnamed_metric_values_and_broken_envelopes_are_dropped() -> None:
    logger, stream = make_logger()

    logger.info("x", bytesDownloaded=5, _aws={"Timestamp": 1, "CloudWatchMetrics": "bad"})

    (record,) = read_records(stream)
    assert "bytesDownloaded" not in record
    assert "_aws" not in record


def test_nfr3_19_the_event_reference_is_the_bus_id_when_safe_else_the_import_id() -> None:
    assert event_reference("11111111-2222-3333-4444-555555555555", "imp-1") == (
        "11111111-2222-3333-4444-555555555555"
    )
    for unsafe in ("evt 1", "a@b.c", "x" * 129, "line\nbreak", ""):
        assert event_reference(unsafe, "imp-1") == "imp-1"
