"""Tests for event.py: BR1.1 (the start event check), BR1.2, BR1.3 (names), NFR3.14."""

import json
from datetime import UTC, datetime

import pytest

from model_import.event import (
    InvalidEventError,
    NotAStartEvent,
    PathCheckError,
    RejectedEvent,
    StartEvent,
    check_file_path,
    check_file_paths,
    classify_message,
    is_valid_exact_version,
    is_valid_model_name,
    is_valid_sender_email,
)
from model_import.models import ImportStatus, Outcome, derive_import_id

VERSION = "0123456789abcdef0123456789abcdef01234567"


def start_event_of(body: object) -> StartEvent:
    """The start event of a body that must be one (the u1 parser is gone; this replaces it)."""
    result = classify_message(body)  # type: ignore[arg-type]  # a test of bad input types
    assert isinstance(result, StartEvent)
    return result


def first_failed_field(body: object) -> str:
    """The first field a body fails on: the envelope field, else the first rejected field."""
    result = classify_message(body)  # type: ignore[arg-type]  # a test of bad input types
    assert not isinstance(result, StartEvent)
    return result.field_name if isinstance(result, NotAStartEvent) else result.rejected_fields[0]


def make_event(**detail_overrides: object) -> dict[str, object]:
    detail: dict[str, object] = {
        "modelName": "Org/Model-1.0",
        "exactVersion": VERSION,
        "senderEmail": "sender@example.com",
    }
    detail.update(detail_overrides)
    return {
        "id": "11111111-2222-3333-4444-555555555555",
        "source": "model.import",
        "detail-type": "ModelImportRequested",
        "detail": detail,
    }


def test_br1_1_br1_2_a_valid_event_starts_one_import_with_its_own_identity() -> None:
    event = start_event_of(json.dumps(make_event()))

    request = event.to_import_request(datetime(2026, 1, 1, tzinfo=UTC))

    assert event.model_name == "Org/Model-1.0"
    assert event.exact_version == VERSION
    # u2 (BR7.1): an event starts at "received"; the front door then decides where it goes.
    assert request.status is ImportStatus.RECEIVED
    assert request.sender_email == "sender@example.com"
    assert request.outcome is None

    # BR1.2: the import id comes from the event id and is stable.
    again = start_event_of(json.dumps(make_event()).encode())
    other_body = make_event()
    other_body["id"] = "another-event-id"
    assert event.import_id == again.import_id
    assert start_event_of(other_body).import_id != event.import_id


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ("{not json", "body"),
        ("[1, 2]", "body"),
        ({**make_event(), "id": ""}, "id"),
        ({**make_event(), "source": "other.source"}, "source"),
        ({**make_event(), "detail-type": "Other"}, "detail-type"),
        ({**make_event(), "detail": "text"}, "detail"),
        (make_event(modelName="no-slash"), "modelName"),
        (make_event(exactVersion="main"), "exactVersion"),
        (make_event(senderEmail=None), "senderEmail"),
    ],
)
def test_br1_1_an_event_that_fails_the_check_starts_nothing_and_names_the_field(
    body: object, field: str
) -> None:
    assert first_failed_field(body) == field


def test_br1_1_the_result_never_shows_the_value_of_the_field() -> None:
    value = "bad value with secret text"
    result = classify_message(make_event(modelName=value))

    assert isinstance(result, RejectedEvent)
    assert value not in repr(result) and "secret" not in repr(result)
    assert value not in str(InvalidEventError("modelName"))


def test_br1_3_model_names_are_owner_slash_name_of_safe_characters() -> None:
    for name in ["org/model", "Org/Model-1.0_x", "a.b/c.d", "o/.hidden", "123/456"]:
        assert is_valid_model_name(name), name
    for name in [
        "model",
        "a/b/c",
        "../model",
        "org/..",
        "./x",
        "org/mo del",
        "org/mo\ndel",
        "/model",
        "a/",
        "",
    ]:
        assert not is_valid_model_name(name), name


@pytest.mark.parametrize("version", ["main", "v1.0", VERSION[:39], VERSION.upper(), VERSION + "0"])
def test_br1_3_the_version_must_be_exactly_40_lowercase_hex_characters(version: str) -> None:
    assert not is_valid_exact_version(version)
    assert is_valid_exact_version(VERSION)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/abs/file.json",
        "dir\\file.json",
        "dir//file.json",
        "dir/",
        "./file.json",
        "dir/../file.json",
        "dir/file\x00.json",
        "dir/file\n.json",
        "a" * 1025,
    ],
)
def test_br1_3_unsafe_file_paths_fail(path: str) -> None:
    with pytest.raises(PathCheckError):
        check_file_path(path)


def test_br1_3_safe_paths_pass_and_two_paths_equal_without_case_fail() -> None:
    check_file_paths(["config.json", "sub/dir/model.safetensors", "a" * 1024])

    with pytest.raises(PathCheckError, match="same"):
        check_file_paths(["Model.safetensors", "sub/x.json", "model.SAFETENSORS"])
    with pytest.raises(PathCheckError, match="same"):
        check_file_paths(["x.json", "x.json"])


@pytest.mark.parametrize(
    "address",
    [
        "a@example.com\nBcc: x@example.com",
        "a@example.com,b@example.com",
        "a@example.com;b@example.com",
        "a@example.com b@example.com",
        "a@b@example.com",
        "no-at-sign",
        "a@example.com\r",
        "x" * 250 + "@example.com",
    ],
)
def test_nfr3_14_a_sender_address_that_is_not_one_header_safe_address_is_refused(
    address: str,
) -> None:
    assert not is_valid_sender_email(address)
    assert first_failed_field(make_event(senderEmail=address)) == "senderEmail"


# -- u2-import-rules: the message classifier (BR6.1, BR6.3, BR6.4, NFR3.19, NFR3.20) ------------

PLANTED_SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"  # two parts: this file holds no whole secret
PLANTED_ADDRESS = "planted.person" + "@example.invalid"


def test_br6_1_a_message_with_good_content_is_a_start_event_with_its_time() -> None:
    body = make_event()
    body["time"] = "2026-10-30T12:34:56Z"

    result = classify_message(json.dumps(body))

    assert isinstance(result, StartEvent)
    assert result.event_time == datetime(2026, 10, 30, 12, 34, 56, tzinfo=UTC)
    assert result.import_id == derive_import_id(body["id"])  # type: ignore[arg-type]
    request = result.to_import_request(datetime(2026, 1, 1, tzinfo=UTC))
    assert request.event_id == body["id"]  # a safe bus id is shown as it is


def test_br6_3_a_bad_event_names_every_failed_field_and_keeps_no_failed_value() -> None:
    body = make_event(modelName="no-slash", exactVersion="main", senderEmail=PLANTED_ADDRESS)
    body["detail"]["senderEmail"] = "a@b@example.invalid"  # type: ignore[index]

    result = classify_message(body)

    assert isinstance(result, RejectedEvent)
    assert result.rejected_fields == ("modelName", "exactVersion", "senderEmail")
    assert (result.model_name, result.exact_version, result.sender_email) == (None, None, None)
    assert result.missing_fields == ()  # all three are present, only in the wrong form


def test_br6_1_br6_3_a_field_that_passed_is_kept_and_missing_is_told_from_malformed() -> None:
    detail = make_event()["detail"]
    del detail["senderEmail"]  # type: ignore[attr-defined]  # missing
    body = {**make_event(), "detail": {**detail, "exactVersion": "main"}}  # type: ignore[dict-item]

    result = classify_message(body)

    assert isinstance(result, RejectedEvent)
    assert result.rejected_fields == ("exactVersion", "senderEmail")
    assert result.missing_fields == ("senderEmail",)
    assert result.model_name == "Org/Model-1.0"  # it passed, so it may be kept
    assert result.exact_version is None
    request = result.to_import_request(datetime(2026, 1, 1, tzinfo=UTC))
    assert request.status is ImportStatus.RECEIVED
    assert request.rejected_fields == ("exactVersion", "senderEmail")
    request.finish(Outcome.REJECTED, "the request was rejected")  # valid from the start
    assert request.status is ImportStatus.DONE


def test_br6_2_a_rejected_event_keeps_the_sender_only_when_it_is_well_formed() -> None:
    good_sender = classify_message(make_event(modelName="bad"))
    bad_sender = classify_message(make_event(modelName="bad", senderEmail="a,b@example.com"))

    assert isinstance(good_sender, RejectedEvent) and isinstance(bad_sender, RejectedEvent)
    assert good_sender.sender_email == "sender@example.com"
    assert bad_sender.sender_email is None


@pytest.mark.parametrize(
    ("body", "field"),
    [
        (b"\xff\xfe not text", "body"),
        ("{not json", "body"),
        ("[1, 2]", "body"),
        ("[" * 100_000, "body"),
        ({**make_event(), "id": ""}, "id"),
        ({**make_event(), "id": "x" * 257}, "id"),
        ({**make_event(), "id": 7}, "id"),
        ({**make_event(), "source": "other.source"}, "source"),
        ({**make_event(), "detail-type": "Other"}, "detail-type"),
        ({**make_event(), "detail": "text"}, "detail"),
        ({k: v for k, v in make_event().items() if k != "detail"}, "detail"),
    ],
)
def test_br6_4_a_message_that_is_not_a_start_event_names_the_envelope_field(
    body: object, field: str
) -> None:
    result = classify_message(body)  # type: ignore[arg-type]

    assert result == NotAStartEvent(field)
    assert result.reason == f"the event field {field} is missing or not valid"


@pytest.mark.parametrize(
    "time_value", [None, "", "yesterday", "2026-13-45T00:00:00Z", 12345, "x" * 99]
)
def test_br6_4_the_time_is_optional_and_a_bad_one_is_not_available_not_an_error(
    time_value: object,
) -> None:
    body = make_event()
    if time_value is not None:
        body["time"] = time_value

    for candidate in (body, {**body, "detail": {**body["detail"], "modelName": "bad"}}):  # type: ignore[dict-item]
        result = classify_message(candidate)
        assert isinstance(result, StartEvent | RejectedEvent)
        assert result.event_time is None


def test_br6_4_a_time_without_a_zone_is_not_trusted() -> None:
    body = make_event()
    body["time"] = "2026-10-30T12:34:56"

    result = classify_message(body)

    assert isinstance(result, StartEvent) and result.event_time is None


def test_nfr3_20_the_sender_address_is_hidden_from_every_text_form_of_an_event() -> None:
    start = classify_message(make_event(senderEmail=PLANTED_ADDRESS))
    rejected = classify_message(make_event(senderEmail=PLANTED_ADDRESS, modelName="bad"))

    assert isinstance(start, StartEvent) and isinstance(rejected, RejectedEvent)
    for event in (start, rejected):
        assert PLANTED_ADDRESS not in repr(event) and PLANTED_ADDRESS not in str(event)
        assert event.sender_email == PLANTED_ADDRESS  # still there for the one send that needs it


def test_nfr3_19_no_value_that_failed_is_in_the_rejected_event_in_any_form() -> None:
    body = make_event(modelName="bad/" + PLANTED_SECRET + "/x", exactVersion=PLANTED_SECRET)
    body["id"] = "evt 1 " + PLANTED_SECRET  # unsafe form: shown as the importId instead
    body["time"] = PLANTED_SECRET
    body["detail"]["note"] = PLANTED_SECRET  # type: ignore[index]  # an unknown field is ignored

    result = classify_message(body)

    assert isinstance(result, RejectedEvent)
    assert (result.model_name, result.exact_version) == (None, None)  # failed values are dropped
    request = result.to_import_request(datetime(2026, 1, 1, tzinfo=UTC))
    assert request.event_id == result.import_id  # NFR3.19: the importId stands for an unsafe id
    assert PLANTED_SECRET not in repr(request)
    assert result.event_time is None  # the text of an unparsable time is not kept


@pytest.mark.parametrize("name", ["_COMPLETE", "_complete", "_Complete", "_cOmPlEtE"])
def test_br9_1_a_model_file_named_like_the_marker_fails_the_path_check_without_case(
    name: str,
) -> None:
    with pytest.raises(PathCheckError) as caught:
        check_file_path(name)
    with pytest.raises(PathCheckError):
        check_file_paths(["config.json", name])

    assert caught.value.reason == "a file of the model is named _COMPLETE"


def test_br9_1_only_the_root_file_collides_with_the_marker_key() -> None:
    check_file_paths(["sub/_COMPLETE", "_COMPLETE.txt", "x/_complete", "COMPLETE"])


@pytest.mark.parametrize(
    ("changes", "first"),
    [
        ({"modelName": "bad"}, "modelName"),
        ({"exactVersion": "main", "senderEmail": None}, "exactVersion"),
        ({"senderEmail": None}, "senderEmail"),
    ],
)
def test_br1_1_the_failed_fields_are_named_in_the_order_model_version_sender(
    changes: dict[str, object], first: str
) -> None:
    result = classify_message(make_event(**changes))

    assert isinstance(result, RejectedEvent)
    assert result.rejected_fields[0] == first
    assert list(result.rejected_fields) == [
        name for name in ("modelName", "exactVersion", "senderEmail") if name in changes
    ]
