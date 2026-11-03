"""Tests for models.py: entities, status moves, single outcome (BR1.2, NFR4.1, BR2.5, BR3.5)."""

import dataclasses
import json
from datetime import UTC, datetime

import pytest

from model_import.models import (
    ALLOWED_TRANSITIONS,
    MAX_LEFTOVERS_LISTED,
    MAX_RECORD_FINDINGS,
    FailureNotice,
    FileClass,
    FindingKind,
    FindingSummary,
    ImportRecord,
    ImportRequest,
    ImportStatus,
    InvalidTransitionError,
    KeepResult,
    Leftover,
    LeftoverArea,
    LeftoverKind,
    ModelFile,
    ModelFileSet,
    NoticeKind,
    Outcome,
    OutcomeAlreadySetError,
    OutcomeRecord,
    RemovalResult,
    ScanFinding,
    ScannedFile,
    StoredModel,
    derive_import_id,
    final_prefix,
    holding_prefix,
)

NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
VERSION = "a" * 40


def make_request() -> ImportRequest:
    return ImportRequest(
        import_id=derive_import_id("event-1"),
        model_name="Org/Model",
        exact_version=VERSION,
        sender_email="sender@example.com",
        started_at=NOW,
    )


def make_finding(index: int = 0) -> ScanFinding:
    return ScanFinding(
        finding_id=f"f-{index}",
        import_id="imp-1",
        file_name="config.json",
        place=f"line {index + 1}",
        kind=FindingKind.ACCESS_KEY,
    )


def test_br1_2_import_id_is_derived_from_the_event_id_and_path_safe() -> None:
    first = derive_import_id("1234abcd-../../evil")
    again = derive_import_id("1234abcd-../../evil")
    other = derive_import_id("another-event")

    assert first == again
    assert first != other
    assert first.startswith("imp-") and "/" not in first and ".." not in first
    with pytest.raises(ValueError):
        derive_import_id("   ")


def test_br1_2_new_import_starts_downloading_with_no_outcome() -> None:
    request = make_request()

    assert request.status is ImportStatus.DOWNLOADING
    assert request.outcome is None


def test_import_follows_the_clean_model_path_to_stored_br4_3() -> None:
    request = make_request()

    request.advance(ImportStatus.SCANNING)
    request.advance(ImportStatus.KEEPING)
    request.finish(Outcome.STORED)

    assert request.status is ImportStatus.DONE
    assert request.outcome is Outcome.STORED


def test_import_follows_the_failure_path_to_removed_with_email_br5_1() -> None:
    request = make_request()

    request.advance(ImportStatus.REMOVING)
    request.finish(Outcome.REMOVED_WITH_EMAIL, "the model   has no\n files")

    assert request.status is ImportStatus.DONE
    assert request.failure_reason == "the model has no files"


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (ImportStatus.DOWNLOADING, ImportStatus.KEEPING),
        (ImportStatus.SCANNING, ImportStatus.DOWNLOADING),
        (ImportStatus.REMOVING, ImportStatus.REMOVING),
        (ImportStatus.DOWNLOADING, ImportStatus.DONE),
    ],
)
def test_a_move_the_spec_does_not_allow_is_refused(
    start: ImportStatus, target: ImportStatus
) -> None:
    request = make_request()
    request.status = start

    with pytest.raises(InvalidTransitionError):
        request.advance(target)

    assert request.status is start


def test_nfr4_1_outcome_is_set_once_and_never_changed() -> None:
    request = make_request()
    request.advance(ImportStatus.REMOVING)
    request.finish(Outcome.REMOVED_EMAIL_FAILED, "email service down")

    with pytest.raises(OutcomeAlreadySetError):
        request.finish(Outcome.REMOVED_WITH_EMAIL)

    assert request.outcome is Outcome.REMOVED_EMAIL_FAILED


def test_the_outcome_must_match_the_status_it_is_set_from() -> None:
    stored_too_early = make_request()
    removed_while_downloading = make_request()

    with pytest.raises(InvalidTransitionError):
        stored_too_early.finish(Outcome.STORED)
    with pytest.raises(InvalidTransitionError):
        removed_while_downloading.finish(Outcome.REMOVED_WITH_EMAIL, "reason")

    keeping = make_request()
    keeping.advance(ImportStatus.SCANNING)
    keeping.advance(ImportStatus.KEEPING)
    with pytest.raises(InvalidTransitionError):
        keeping.finish(Outcome.STORED, "a stored model has no failure reason")


def test_br2_5_a_file_is_complete_only_when_sizes_match() -> None:
    partial = ModelFile(path="a.safetensors", expected_size_bytes=10, received_size_bytes=9)
    whole = ModelFile(path="b.json", expected_size_bytes=10, received_size_bytes=10)
    file_set = ModelFileSet(import_id="imp-1", model_name="o/m", exact_version=VERSION)

    assert not partial.complete and whole.complete
    assert not file_set.download_complete  # no files yet
    file_set.files = [partial, whole]
    assert not file_set.download_complete
    partial.received_size_bytes = 10
    assert file_set.download_complete
    assert file_set.total_size_bytes == 20
    assert file_set.holding_area_path == "holding/imp-1/"
    with pytest.raises(ValueError):
        ModelFile(path="x", expected_size_bytes=-1)


def test_br3_5_a_finding_has_no_attribute_for_the_matched_value() -> None:
    names = {item.name for item in dataclasses.fields(ScanFinding)}

    assert names == {"finding_id", "import_id", "file_name", "place", "kind"}


def test_br3_7_at_most_100_findings_are_recorded_for_one_file() -> None:
    capped = ScannedFile("imp-1", "data.csv", FileClass.SCANNED_TEXT, 100, 5000)

    assert capped.matches_total == 5000
    with pytest.raises(ValueError):
        ScannedFile("imp-1", "data.csv", FileClass.SCANNED_TEXT, 101, 101)
    with pytest.raises(ValueError):
        ScannedFile("imp-1", "data.csv", FileClass.SCANNED_TEXT, 5, 4)


def test_br5_6_an_email_lists_at_most_20_findings() -> None:
    twenty = tuple(make_finding(i) for i in range(20))
    notice = FailureNotice("n-1", "imp-1", ("a@example.com",), "found", True, twenty, 35)

    assert notice.findings_total == 35
    with pytest.raises(ValueError):
        FailureNotice(
            "n-2", "imp-1", ("a@example.com",), "found", True, (*twenty, make_finding(20)), 21
        )


def test_br1_3_final_location_uses_the_lowercase_model_name_br2_2_holding_area() -> None:
    stored = StoredModel("Org/Model", VERSION, NOW)

    assert final_prefix("Org/Model", VERSION) == f"models/org/model/{VERSION}/"
    assert stored.stored_model_key == final_prefix("org/model", VERSION)
    assert holding_prefix("imp-1") == "holding/imp-1/"


def test_record_and_outcome_survive_a_round_trip_through_their_dict_form() -> None:
    record = ImportRecord(
        "imp-1", "org/model", VERSION, ImportStatus.KEEPING, "keep", NOW, NOW, 3, "models/x/y"
    )
    outcome = OutcomeRecord("imp-1", Outcome.REMOVED_WITH_EMAIL, False, NOW, "scan found items")

    assert ImportRecord.from_dict(record.to_dict()) == record
    assert OutcomeRecord.from_dict(outcome.to_dict()) == outcome


# -- u2-import-rules: five outcomes, new moves, new fields (BR6.1 to BR11.3) -----------------------


def make_received(**options: object) -> ImportRequest:
    """A request at the start of the diagram, as the front door of u2 makes it."""
    return ImportRequest(
        import_id=derive_import_id("event-2"),
        model_name="Org/Model",
        exact_version=VERSION,
        sender_email="sender@example.com",
        started_at=NOW,
        status=ImportStatus.RECEIVED,
        **options,  # type: ignore[arg-type]
    )


def test_br10_1_there_are_exactly_five_outcomes() -> None:
    assert {item.value for item in Outcome} == {
        "stored",
        "removed-with-email",
        "removed-email-failed",
        "rejected",
        "refused-already-stored",
    }


def test_br5_1_br7_1_the_new_moves_from_the_start_are_to_downloading_or_removing_only() -> None:
    assert ALLOWED_TRANSITIONS[ImportStatus.RECEIVED] == {
        ImportStatus.DOWNLOADING,
        ImportStatus.REMOVING,
    }
    for target in (ImportStatus.SCANNING, ImportStatus.KEEPING, ImportStatus.DONE):
        request = make_received()
        with pytest.raises(InvalidTransitionError):
            request.advance(target)
        assert request.status is ImportStatus.RECEIVED
    for target in (ImportStatus.DOWNLOADING, ImportStatus.REMOVING):
        request = make_received()
        request.advance(target)
        assert request.status is target
    assert ALLOWED_TRANSITIONS[ImportStatus.DONE] == frozenset()  # done is final


def test_br6_1_a_rejected_event_goes_from_the_start_straight_to_done() -> None:
    request = make_received(rejected_fields=("exactVersion", "senderEmail"))

    request.finish(
        Outcome.REJECTED,
        "the request was rejected",
        email_sent=False,
        recipients_not_reached=("team",),
    )

    assert request.status is ImportStatus.DONE
    assert request.outcome is Outcome.REJECTED
    assert request.rejected_fields == ("exactVersion", "senderEmail")
    assert (request.email_sent, request.recipients_not_reached) == (False, ("team",))


def test_br7_1_an_already_stored_event_goes_from_the_start_straight_to_done() -> None:
    request = make_received()

    request.finish(Outcome.REFUSED_ALREADY_STORED, "this model version is already stored")

    assert (request.status, request.outcome) == (ImportStatus.DONE, Outcome.REFUSED_ALREADY_STORED)


def test_br7_1_br9_6_an_own_earlier_marker_finishes_as_stored_from_the_start() -> None:
    request = make_received()

    request.finish(Outcome.STORED)

    assert (request.status, request.outcome) == (ImportStatus.DONE, Outcome.STORED)
    assert request.email_sent is None  # a stored model sends no email


@pytest.mark.parametrize("status", [ImportStatus.DOWNLOADING, ImportStatus.REMOVING])
@pytest.mark.parametrize("outcome", [Outcome.REJECTED, Outcome.REFUSED_ALREADY_STORED])
def test_br10_1_a_rejected_or_refused_outcome_is_valid_only_from_the_start(
    status: ImportStatus, outcome: Outcome
) -> None:
    request = make_received(rejected_fields=("modelName",))
    request.status = status

    with pytest.raises(InvalidTransitionError):
        request.finish(outcome, "reason")


def test_br10_1_a_removed_outcome_needs_removing_and_stored_has_no_reason_or_email() -> None:
    with pytest.raises(InvalidTransitionError):
        make_received().finish(Outcome.REMOVED_WITH_EMAIL, "reason")
    with pytest.raises(InvalidTransitionError):
        make_received().finish(Outcome.STORED, "a stored model has no reason")
    with pytest.raises(InvalidTransitionError):
        make_received().finish(Outcome.STORED, email_sent=True)


def test_br6_3_rejected_fields_are_set_exactly_when_the_outcome_is_rejected() -> None:
    with pytest.raises(ValueError, match="rejectedFields"):
        make_received().finish(Outcome.REJECTED, "reason")  # no field named
    with pytest.raises(ValueError, match="rejectedFields"):
        make_received(rejected_fields=("modelName",)).finish(Outcome.REFUSED_ALREADY_STORED, "r")


def test_br6_3_br10_3_only_field_names_and_roles_can_be_stored_never_a_value() -> None:
    with pytest.raises(ValueError) as field_error:
        make_received(rejected_fields=("someone@example.com",))
    with pytest.raises(ValueError) as role_error:
        make_received(recipients_not_reached=("someone@example.com",))
    request = make_received()
    with pytest.raises(ValueError) as finish_error:
        request.finish(Outcome.REFUSED_ALREADY_STORED, "r", recipients_not_reached=("a@b.co",))

    for error in (field_error, role_error, finish_error):
        assert "someone" not in str(error.value) and "a@b.co" not in str(error.value)
    assert request.outcome is None  # the refused finish changed nothing


def test_nfr3_20_the_sender_address_is_not_in_the_text_form_of_a_request() -> None:
    request = make_request()

    assert "sender@example.com" not in repr(request)
    assert "sender@example.com" not in str(request)


def test_br8_1_a_request_may_carry_its_deadline_and_it_must_have_a_time_zone() -> None:
    request = make_received(deadline_at=NOW)

    assert request.deadline_at == NOW
    with pytest.raises(ValueError):
        make_received(deadline_at=datetime(2026, 10, 30, 18, 0, 0))


def test_br10_3_br10_4_a_failed_import_keeps_who_was_not_reached() -> None:
    request = make_request()
    request.advance(ImportStatus.REMOVING)

    request.finish(
        Outcome.REMOVED_EMAIL_FAILED,
        "the scan found 1 finding in 1 file",
        email_sent=False,
        recipients_not_reached=("sender", "team"),
    )

    assert request.email_sent is False
    assert request.recipients_not_reached == ("sender", "team")


def test_br10_3_the_outcome_object_holds_the_email_result_as_roles() -> None:
    outcome = OutcomeRecord(
        "imp-1",
        Outcome.REJECTED,
        True,
        NOW,
        "the request was rejected",
        email_sent=False,
        recipients_not_reached=("sender",),
    )

    data = outcome.to_dict()

    assert data["emailSent"] is False
    assert data["recipientsNotReached"] == ["sender"]
    assert OutcomeRecord.from_dict(json.loads(json.dumps(data))) == outcome
    with pytest.raises(ValueError):
        OutcomeRecord("imp-1", Outcome.REJECTED, True, NOW, recipients_not_reached=("x@y.zz",))


def test_nfr4_1_an_outcome_written_by_u1_is_still_readable() -> None:
    old = {
        "importId": "imp-1",
        "outcome": "stored",
        "removalComplete": True,
        "finishedAt": "2026-10-30T12:00:00Z",
        "failureReason": None,
    }

    outcome = OutcomeRecord.from_dict(old)

    assert outcome.email_sent is None and outcome.recipients_not_reached == ()


def test_br9_6_a_stored_outcome_may_say_the_removal_is_not_complete() -> None:
    outcome = OutcomeRecord("imp-1", Outcome.STORED, False, NOW)

    assert OutcomeRecord.from_dict(outcome.to_dict()) == outcome
    assert outcome.to_dict()["emailSent"] is None


def finding_for(name: str = "config.json", index: int = 1) -> ScanFinding:
    return ScanFinding(
        f"f-{index}", "imp-1", name, f"line {index}, columns 5 to 25", FindingKind.TOKEN
    )


def make_full_record(**options: object) -> ImportRecord:
    return ImportRecord(
        "imp-1", "org/model", VERSION, ImportStatus.DONE, "done", NOW, NOW, **options  # type: ignore[arg-type]
    )


def test_br11_1_the_record_carries_the_outcome_the_reason_and_the_event_id() -> None:
    record = make_full_record(
        event_id="evt-1",
        outcome=Outcome.REMOVED_WITH_EMAIL,
        failure_reason="the scan found 2 findings in 1 file",
    )

    data = record.to_dict()
    again = ImportRecord.from_dict(json.loads(json.dumps(data)))

    assert data["eventId"] == "evt-1" and data["outcome"] == "removed-with-email"
    assert again == record


def test_br11_1_a_record_of_a_rejected_event_may_hold_no_model_name_or_version() -> None:
    record = ImportRecord(
        "imp-1", None, None, ImportStatus.DONE, "done", NOW, NOW, outcome=Outcome.REJECTED
    )

    again = ImportRecord.from_dict(json.loads(json.dumps(record.to_dict())))

    assert (again.model_name, again.exact_version) == (None, None)
    assert again == record


def test_nfr3_19_an_event_id_that_is_not_safe_is_replaced_by_the_import_id() -> None:
    odd = "person@example.com secret=abc"

    record = make_full_record(event_id=odd)

    assert record.event_id == "imp-1"
    assert odd not in json.dumps(record.to_dict())


def test_nfr4_8_a_record_written_by_u1_is_still_readable() -> None:
    old = {
        "importId": "imp-1",
        "modelName": "org/model",
        "exactVersion": VERSION,
        "status": "keeping",
        "step": "keep",
        "startedAt": "2026-10-30T12:00:00Z",
        "updatedAt": "2026-10-30T12:00:00Z",
        "filesCopied": 3,
        "lastKeyCopied": "models/x",
    }

    record = ImportRecord.from_dict(old)

    assert (record.outcome, record.findings, record.findings_total) == (None, [], 0)
    assert record.event_id is None


def test_br11_2_a_finding_summary_has_a_cleaned_file_name_a_place_and_a_kind_only() -> None:
    summary = FindingSummary.from_finding(finding_for("bad\nname\x1b" + "x" * 300))

    assert {item.name for item in dataclasses.fields(FindingSummary)} == {
        "file_name",
        "place",
        "kind",
    }
    assert "\n" not in summary.file_name and "\x1b" not in summary.file_name
    assert len(summary.file_name) == 200
    assert summary.to_dict() == {
        "fileName": summary.file_name,
        "place": "line 1, columns 5 to 25",
        "kind": "token",
    }


def test_br11_2_the_record_lists_at_most_200_findings_with_the_total_of_all() -> None:
    record = make_full_record()
    summaries = [FindingSummary.from_finding(finding_for(f"f{i}.json", i)) for i in range(200)]

    record.set_findings(summaries, 5_000)

    data = record.to_dict()
    assert MAX_RECORD_FINDINGS == 200
    assert len(data["findings"]) == 200 and data["findingsTotal"] == 5_000
    assert ImportRecord.from_dict(json.loads(json.dumps(data))) == record
    with pytest.raises(ValueError):
        record.set_findings([*summaries, summaries[0]], 201)
    with pytest.raises(ValueError):
        record.set_findings(summaries[:5], 4)
    assert len(record.findings) == 200  # the refused calls changed nothing


def test_nfr2_10_a_record_with_200_findings_and_1000_files_copied_is_under_100_kb() -> None:
    record = make_full_record(files_copied=1_000, last_key_copied="models/org/model/" + "a" * 200)
    longest_name = "n" * 200
    record.set_findings(
        [
            FindingSummary.from_finding(
                ScanFinding(
                    f"f{i}",
                    "imp-1",
                    longest_name,
                    f"line {i}, columns 1 to 99999",
                    FindingKind.TOKEN,
                )
            )
            for i in range(200)
        ],
        200,
    )

    size = len(json.dumps(record.to_dict(), separators=(",", ":")).encode("utf-8"))

    assert size < 100 * 1024
    assert "keys" not in record.to_dict()  # the list of copied keys is not stored


def test_br9_3_a_leftover_location_is_cleaned_and_a_result_is_complete_only_without_one() -> None:
    leftover = Leftover(
        LeftoverKind.OBJECT, LeftoverArea.FINAL_LOCATION, "models/a/bad\nname" + "x" * 300
    )

    assert "\n" not in leftover.location and len(leftover.location) == 200
    assert RemovalResult("imp-1").removal_complete is True
    incomplete = RemovalResult("imp-1", (leftover,), older_files_removed=2)
    assert incomplete.removal_complete is False
    assert (incomplete.leftovers_total, incomplete.older_files_removed) == (1, 2)
    with pytest.raises(ValueError):
        Leftover(LeftoverKind.OBJECT, LeftoverArea.HOLDING_AREA, "")
    with pytest.raises(ValueError):
        RemovalResult("imp-1", older_files_removed=-1)


def test_br9_4_br9_5_the_kinds_and_areas_are_the_ones_of_the_entities() -> None:
    assert {item.value for item in LeftoverKind} == {
        "object",
        "incomplete-upload",
        "temporary-resource",
    }
    assert {item.value for item in LeftoverArea} == {
        "holding-area",
        "final-location",
        "local-disk",
    }
    assert MAX_LEFTOVERS_LISTED == 20


def test_br9_6_a_result_for_a_stored_model_never_says_removal_complete() -> None:
    result = RemovalResult("imp-1", model_stored=True)

    assert result.removal_complete is False
    assert result.leftovers == ()


def test_br9_6_a_keep_result_says_the_marker_was_written_and_whether_holding_was_emptied() -> None:
    stored = StoredModel("Org/Model", VERSION, NOW)
    leftover = Leftover(LeftoverKind.OBJECT, LeftoverArea.HOLDING_AREA, "holding/imp-1/a.json")

    clean = KeepResult(stored)
    dirty = KeepResult(stored, holding_emptied=False, leftovers=(leftover,))

    assert (clean.marker_written, clean.holding_emptied, clean.leftovers) == (True, True, ())
    assert dirty.holding_emptied is False and dirty.leftovers == (leftover,)
    assert dirty.stored.stored_model_key == final_prefix("org/model", VERSION)


# -- u2-import-rules (Step 7): the last guard of BR9.6 and the new email notice fields ----------


def test_br9_6_stored_is_valid_from_removing_only_as_the_last_guard_of_a_written_marker() -> None:
    plain = make_request()
    plain.advance(ImportStatus.REMOVING)
    guarded = make_request()
    guarded.advance(ImportStatus.REMOVING)

    with pytest.raises(InvalidTransitionError):
        plain.finish(Outcome.STORED)  # without the flag nothing may turn a removal into stored
    guarded.finish(Outcome.STORED, stored_during_removal=True)

    assert plain.outcome is None
    assert (guarded.status, guarded.outcome) == (ImportStatus.DONE, Outcome.STORED)
    other = make_request()
    other.advance(ImportStatus.REMOVING)
    with pytest.raises(InvalidTransitionError):  # the flag does not open any other outcome
        other.finish(Outcome.REJECTED, "r", stored_during_removal=True)


def test_br9_4_br9_5_a_notice_lists_at_most_20_leftovers_and_counts_the_rest() -> None:
    items = tuple(
        Leftover(LeftoverKind.OBJECT, LeftoverArea.HOLDING_AREA, f"holding/imp-1/{n}")
        for n in range(MAX_LEFTOVERS_LISTED)
    )
    base = {"notice_id": "n-1", "import_id": "imp-1", "recipients": ("a",), "reason": "r"}

    notice = FailureNotice(
        **base,  # type: ignore[arg-type]
        removal_complete=False,
        leftovers_listed=items,
        leftovers_total=31,
        older_files_removed=2,
    )

    assert (notice.leftovers_total, notice.older_files_removed) == (31, 2)
    assert notice.kind is NoticeKind.IMPORT_FAILED
    for bad in (
        {"leftovers_listed": (*items, items[0]), "leftovers_total": 21},  # 21 listed
        {"leftovers_listed": items, "leftovers_total": 5},  # total below the listed
        {"older_files_removed": -1},
        {"fields_named": ("someone@example.com",)},  # only field names, never a value
    ):
        with pytest.raises(ValueError):
            FailureNotice(**base, removal_complete=False, **bad)  # type: ignore[arg-type]


def test_br6_3_a_rejection_notice_names_only_the_three_event_fields() -> None:
    notice = FailureNotice(
        "n-1",
        "imp-1",
        ("team@example.com",),
        "the request was rejected",
        True,
        kind=NoticeKind.EVENT_REJECTED,
        fields_named=("modelName", "senderEmail"),
    )

    assert notice.kind.value == "event-rejected" and notice.fields_named == (
        "modelName",
        "senderEmail",
    )
    assert {kind.value for kind in NoticeKind} == {
        "import-failed",
        "event-rejected",
        "already-stored",
    }
