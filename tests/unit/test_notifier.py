"""Tests for notifier.py with moto SES and S3: BR5.3, BR5.4, BR5.6, NFR3.9, NFR3.14, NFR3.16.

The durable per-day counter lives in a moto S3 bucket. A fake clock sets the day.
"""

import io
import json
import re
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from moto.core import DEFAULT_ACCOUNT_ID
from moto.ses import ses_backends

from model_import.logs import JsonLogger
from model_import.models import FindingKind, ImportRequest, ScanFinding
from model_import.notifier import FailureNotifier, build_subject
from model_import.store import FAILURE_EMAIL_LIMIT, ImportStore, StoreError

BUCKET = "holding-test"
FROM = "imports@example.com"
TEAM = "team@example.com"
SENDER = "person@example.com"
NAME = "Org/Model"
VERSION = "0123456789abcdef" + "0" * 24


class RefusingSes:
    """Wraps a real SES client; refuses the addresses in ``refused`` like a rejecting service."""

    def __init__(self, inner: Any, refused: set[str]) -> None:
        self._inner = inner
        self.refused = refused

    def send_email(self, **arguments: Any) -> Any:
        if set(arguments["Destination"]["ToAddresses"]) & self.refused:
            raise ClientError({"Error": {"Code": "MessageRejected"}}, "SendEmail")
        return self._inner.send_email(**arguments)


@pytest.fixture
def aws() -> Iterator[tuple[Any, Any]]:
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        ses = boto3.client("ses", region_name="us-east-1")
        ses.verify_email_identity(EmailAddress=FROM)
        yield s3, ses


@pytest.fixture
def log_stream() -> io.StringIO:
    return io.StringIO()


def make_notifier(aws: Any, clock: Any, stream: io.StringIO, ses: Any = None) -> FailureNotifier:
    s3, real_ses = aws
    return FailureNotifier(
        ses or real_ses,
        ImportStore(s3, BUCKET),
        FROM,
        TEAM,
        clock=clock,
        logger=JsonLogger(stream, clock=clock),
    )


def records(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def sent_messages() -> list[Any]:
    return ses_backends[DEFAULT_ACCOUNT_ID]["us-east-1"].sent_messages


def make_request(clock: Any, sender: str = SENDER) -> ImportRequest:
    return ImportRequest("imp-1", NAME, VERSION, sender, clock())


def finding(path: str, place: str, kind: FindingKind = FindingKind.EMAIL_ADDRESS) -> ScanFinding:
    return ScanFinding(f"f-{path}-{place}", "imp-1", path, place, kind)


def test_br5_3_one_separate_email_per_recipient_with_the_contract_c3_fields(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)
    found = [finding("config.json", "line 12, columns 5 to 30", FindingKind.ACCESS_KEY)]

    reason = "the scan found 1 finding in 1 file"

    result = notifier.send(make_request(clock), reason, True, found, 1)

    assert (result.sent, result.not_sent, result.any_sent) == (2, 0, True)
    assert result.notice.sent_at == clock()
    messages = sent_messages()
    assert [m.destinations["ToAddresses"] for m in messages] == [[SENDER], [TEAM]]
    assert {m.source for m in messages} == {FROM}
    subject = "Model import removed-with-email: Org/Model 0123456789ab"
    assert {m.subject for m in messages} == {subject}
    assert build_subject(NAME, VERSION) == messages[0].subject
    assert messages[0].body == (
        "modelName: Org/Model\n"
        f"exactVersion: {VERSION}\n"
        "reason: the scan found 1 finding in 1 file\n"
        "removalComplete: yes\n"
        "\n"
        "findings:\n"
        "1. config.json: line 12, columns 5 to 30 (access-key)\n"
        "1 findings in total\n"
    )


def test_br5_6_lists_at_most_20_findings_in_numeric_order_and_the_total_matches(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)
    found = [finding("b.json", f"line {n}, columns 1 to 9") for n in range(25, 0, -1)]
    for place in ("line 100, columns 2 to 9", "line 9, columns 7 to 9", "line 9, columns 3 to 5"):
        found.append(finding("a.json", place))
    found.append(finding("a.json", "whole file"))

    result = notifier.send(make_request(clock), "the scan found findings", False, found, 240)

    body = sent_messages()[0].body
    lines = [line for line in body.splitlines() if re.match(r"\d+\. ", line)]
    assert len(lines) == 20 == len(result.notice.findings_listed)
    assert [line.split(": ", 1)[1].split(" (")[0] for line in lines[:5]] == [
        "whole file",
        "line 9, columns 3 to 5",
        "line 9, columns 7 to 9",
        "line 100, columns 2 to 9",
        "line 1, columns 1 to 9",
    ]
    assert lines[5].startswith("6. b.json: line 2,")  # numbers, not text order (2 before 10)
    assert body.endswith("240 findings in total\n")
    assert "removalComplete: no" in body


def test_br5_4_file_names_are_cleaned_and_a_finding_line_holds_no_value(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)
    odd_name = "bad\nname\x1b[31m" + "x" * 300
    found = [finding(odd_name, "line 1, columns 1 to 20", FindingKind.TOKEN)]

    notifier.send(make_request(clock), "the scan found 1 finding in 1 file", True, found, 1)

    body = sent_messages()[0].body
    finding_lines = [line for line in body.splitlines() if line.startswith("1. ")]
    assert len(finding_lines) == 1  # the line break in the name did not make a second line
    name = finding_lines[0].split(": line 1", 1)[0][3:]
    assert name.startswith("bad?name?[31m") and len(name) == 200
    assert finding_lines[0].endswith("(token)")
    assert not hasattr(ScanFinding, "value")  # a finding has no attribute for the matched value


def test_nfr3_16_nfr3_18_the_31st_failure_email_of_a_day_is_not_sent_and_logs_the_limit(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    # u2-import-rules (NFR3.18) amends NFR3.16: the failure emails stop at 30 a day, not 50.
    notifier = make_notifier(aws, clock, log_stream)
    store = ImportStore(aws[0], BUCKET)
    day = clock().date()
    for _ in range(FAILURE_EMAIL_LIMIT - 1):
        store.reserve_email_attempt(day)

    result = notifier.send(make_request(clock), "the download step failed", True)

    assert (result.sent, result.not_sent) == (1, 1)  # the 30th is sent, the 31st is not
    assert [m.destinations["ToAddresses"] for m in sent_messages()] == [[SENDER]]
    limit_records = [r for r in records(log_stream) if r["event"] == "email_limit_reached"]
    assert len(limit_records) == 1
    assert store.read_email_count(day) == FAILURE_EMAIL_LIMIT == 30
    clock.advance(24 * 3600)  # the next day starts a new count
    assert notifier.send(make_request(clock), "the download step failed", True).sent == 2


def test_nfr3_9_a_refused_recipient_does_not_stop_the_other_one(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    refusing = RefusingSes(aws[1], {SENDER})
    notifier = make_notifier(aws, clock, log_stream, ses=refusing)

    result = notifier.send(make_request(clock), "the download step failed", True)

    assert (result.sent, result.not_sent, result.any_sent) == (1, 1, True)
    assert [m.destinations["ToAddresses"] for m in sent_messages()] == [[TEAM]]
    failed = [r for r in records(log_stream) if r["event"] == "email_send_failed"]
    assert [r["error"] for r in failed] == ["MessageRejected"]
    assert ImportStore(aws[0], BUCKET).read_email_count(clock().date()) == 2  # every attempt


def test_br5_5_an_email_that_cannot_be_sent_is_reported_to_the_caller(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    refusing = RefusingSes(aws[1], {SENDER, TEAM})
    notifier = make_notifier(aws, clock, log_stream, ses=refusing)

    result = notifier.send(make_request(clock), "the download step failed", True)

    assert (result.sent, result.not_sent, result.any_sent) == (0, 2, False)
    assert result.notice.sent_at is None
    assert sent_messages() == []
    assert all("@" not in line for line in log_stream.getvalue().splitlines())  # no address logged


def test_nfr3_14_the_same_address_for_both_recipients_gets_one_email(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)

    result = notifier.send(make_request(clock, sender=TEAM.upper()), "the scan broke", True)

    assert (result.sent, result.not_sent) == (1, 0)
    assert len(sent_messages()) == 1


def test_nfr3_16_when_the_counter_cannot_be_used_nothing_is_sent(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    class BrokenStore:
        def reserve_email_attempt(self, *args: Any, **kwargs: Any) -> Any:
            raise StoreError("the counter cannot be read")

    notifier = FailureNotifier(
        aws[1],
        BrokenStore(),  # type: ignore[arg-type]
        FROM,
        TEAM,
        clock=clock,
        logger=JsonLogger(log_stream, clock=clock),
    )

    result = notifier.send(make_request(clock), "the scan broke", True)

    assert (result.sent, result.not_sent) == (0, 2)
    assert sent_messages() == []


def test_nfr3_9_the_configuration_set_name_is_sent_with_each_email(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    """NFR3.9: bounces and complaints reach the alert topic only through the configuration set."""

    class RecordingSes:
        def __init__(self, inner: Any) -> None:
            self.inner = inner
            self.calls: list[dict[str, Any]] = []

        def send_email(self, **arguments: Any) -> Any:
            self.calls.append(arguments)
            return self.inner.send_email(**arguments)

    s3, real_ses = aws
    real_ses.create_configuration_set(ConfigurationSet={"Name": "model-import"})
    recording = RecordingSes(real_ses)
    notifier = FailureNotifier(
        recording,
        ImportStore(s3, BUCKET),
        FROM,
        TEAM,
        clock=clock,
        logger=JsonLogger(log_stream, clock=clock),
        configuration_set="model-import",
    )

    result = notifier.send(make_request(clock), "the scan found 1 finding in 1 file", True, [], 0)

    assert result.sent == 2
    assert [call["ConfigurationSetName"] for call in recording.calls] == ["model-import"] * 2


# -- u2-import-rules: rejection email, already-stored email, counters, per-role result --------

from model_import.event import RejectedEvent, classify_message  # noqa: E402
from model_import.models import (  # noqa: E402
    Leftover,
    LeftoverArea,
    LeftoverKind,
    NoticeKind,
    Outcome,
)
from model_import.notifier import (  # noqa: E402
    PROBLEM_DAILY_LIMIT,
    PROBLEM_SERVICE_ERROR,
    build_rejection_subject,
    select_leftovers,
)
from model_import.store import NOTICE_COUNTER, NOTICE_EMAIL_LIMIT  # noqa: E402

EVENT_ID = "11111111-2222-3333-4444-555555555555"
SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"  # two parts: this file holds no whole secret
PLANTED_ADDRESS = "planted.person" + "@example.invalid"


def rejected_event(**detail: object) -> RejectedEvent:
    body: dict[str, Any] = {
        "id": EVENT_ID,
        "source": "model.import",
        "detail-type": "ModelImportRequested",
        "time": "2026-10-30T11:59:00Z",
        "detail": {
            "modelName": "not a valid name",  # a rejected event, unless a test changes it
            "exactVersion": VERSION,
            "senderEmail": SENDER,
            **detail,
        },
    }
    result = classify_message(body)
    assert isinstance(result, RejectedEvent)
    return result


def bodies() -> list[str]:
    return [m.body for m in sent_messages()]


def test_br6_2_br6_3_the_rejection_email_goes_to_both_and_names_fields_never_values(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)
    event = rejected_event(modelName="bad/" + SECRET + "/x", exactVersion=None)

    result = notifier.send_rejection(event)

    assert (result.sent, result.not_sent, result.email_sent) == (2, 0, True)
    assert result.notice.kind is NoticeKind.EVENT_REJECTED
    messages = sent_messages()
    assert [m.destinations["ToAddresses"] for m in messages] == [[SENDER], [TEAM]]
    assert {m.subject for m in messages} == {f"Model import rejected: event {EVENT_ID}"}
    assert build_rejection_subject(EVENT_ID) == messages[0].subject
    assert messages[0].body == (
        "The request was rejected and nothing was downloaded.\n"
        "\n"
        "fields that failed:\n"
        "- modelName: not in the expected form\n"
        "- exactVersion: missing\n"
        "\n"
        f"eventId: {EVENT_ID}\n"
        "eventTime: 2026-10-30T11:59:00Z\n"
    )
    assert SECRET not in "".join(bodies()) + log_stream.getvalue()  # BR6.3: no value repeated
    assert SENDER not in "".join(bodies())  # nor a value that passed, such as the sender


def test_br6_2_a_rejected_event_without_a_usable_sender_tells_only_the_team(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)

    for bad in (None, "a@b@example.invalid", PLANTED_ADDRESS + ",x@example.com"):
        sent_messages().clear()
        result = notifier.send_rejection(rejected_event(senderEmail=bad))

        assert [m.destinations["ToAddresses"] for m in sent_messages()] == [[TEAM]]
        assert result.email_sent is True  # BR10.4: every email that had to go out was accepted
    assert PLANTED_ADDRESS not in "".join(bodies()) + log_stream.getvalue()


def test_nfr3_19_an_event_id_of_an_unexpected_form_is_replaced_by_the_import_id(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)
    body = {
        "id": "evt 1 " + SECRET,
        "source": "model.import",
        "detail-type": "ModelImportRequested",
        "detail": {"modelName": "bad"},
    }
    event = classify_message(body)
    assert isinstance(event, RejectedEvent)

    notifier.send_rejection(event)

    message = sent_messages()[0]
    assert message.subject == f"Model import rejected: event {event.import_id}"
    assert f"importId: {event.import_id}" in message.body and "eventId:" not in message.body
    assert "eventTime: not available" in message.body  # BR6.4: the time is optional
    assert SECRET not in message.subject + message.body + log_stream.getvalue()
    assert sorted(item.split(": ")[0] for item in message.body.splitlines() if ": " in item) == [
        "- exactVersion",
        "- modelName",
        "- senderEmail",
        "eventTime",
        "importId",
    ]


def test_br7_2_the_already_stored_email_goes_to_both_with_the_model_the_reason_and_the_place(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)

    result = notifier.send_already_stored(make_request(clock))

    assert (result.sent, result.email_sent, result.notice.kind) == (
        2,
        True,
        NoticeKind.ALREADY_STORED,
    )
    messages = sent_messages()
    assert [m.destinations["ToAddresses"] for m in messages] == [[SENDER], [TEAM]]
    assert {m.subject for m in messages} == {
        "Model import refused-already-stored: Org/Model 0123456789ab"
    }
    assert messages[0].body == (
        "modelName: Org/Model\n"
        f"exactVersion: {VERSION}\n"
        "reason: this model version is already stored\n"
        f"finalLocation: models/org/model/{VERSION}/\n"
    )
    assert "removal" not in messages[0].body  # nothing was downloaded, so nothing was removed


def test_nfr3_18_rejection_and_already_stored_emails_use_the_notices_counter_up_to_20(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)
    store = ImportStore(aws[0], BUCKET)
    day = clock().date()
    for _ in range(NOTICE_EMAIL_LIMIT - 1):
        store.reserve_email_attempt(day, NOTICE_EMAIL_LIMIT, counter=NOTICE_COUNTER)

    result = notifier.send_already_stored(make_request(clock))

    assert (result.sent, result.not_sent) == (1, 1)  # the 20th is sent, the 21st is not
    assert store.read_email_count(day, NOTICE_COUNTER) == NOTICE_EMAIL_LIMIT == 20
    assert store.read_email_count(day) == 0  # the failure emails keep all of their 30
    limit = [r for r in records(log_stream) if r["event"] == "email_limit_reached"]
    assert [(r["kind"], r["emailsSentToday"]) for r in limit] == [("notice", 20)]
    assert result.not_reached == (("team", PROBLEM_DAILY_LIMIT),)
    # A rejection email is refused by the same counter, while a failure email still goes out.
    assert notifier.send_rejection(rejected_event()).sent == 0
    assert notifier.send(make_request(clock), "the download step failed", True).sent == 2


def test_nfr3_18_a_full_failure_counter_does_not_stop_the_rejection_email(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)
    store = ImportStore(aws[0], BUCKET)
    for _ in range(FAILURE_EMAIL_LIMIT):
        store.reserve_email_attempt(clock().date())

    failure = notifier.send(make_request(clock), "the download step failed", True)
    rejection = notifier.send_rejection(rejected_event())

    assert failure.sent == 0 and failure.recipients[0].problem == PROBLEM_DAILY_LIMIT
    assert rejection.sent == 2
    kinds = [r["kind"] for r in records(log_stream) if r["event"] == "email_limit_reached"]
    assert kinds == ["failure", "failure"]


def test_br10_3_br10_4_the_result_names_the_role_not_reached_and_the_kind_of_problem(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream, ses=RefusingSes(aws[1], {SENDER}))

    result = notifier.send(make_request(clock), "the download step failed", True)

    assert (result.sent, result.not_sent) == (1, 1)
    assert result.any_sent is True and result.email_sent is False  # BR10.4: not every email
    assert result.roles_not_reached == ("sender",)
    assert result.not_reached == (("sender", PROBLEM_SERVICE_ERROR),)
    assert [m.destinations["ToAddresses"] for m in sent_messages()] == [[TEAM]]  # stays sent
    assert SENDER not in log_stream.getvalue()  # BR10.3: never an address


def test_br10_3_when_the_counter_fails_the_problem_is_a_service_error_for_each_role(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    class BrokenStore:
        def reserve_email_attempt(self, *args: Any, **kwargs: Any) -> Any:
            raise StoreError("the counter cannot be read")

    notifier = FailureNotifier(
        aws[1],
        BrokenStore(),  # type: ignore[arg-type]
        FROM,
        TEAM,
        clock=clock,
        logger=JsonLogger(log_stream, clock=clock),
    )

    result = notifier.send_rejection(rejected_event())

    assert (result.sent, result.email_sent) == (0, False)
    assert result.roles_not_reached == ("sender", "team")
    assert {problem for _, problem in result.not_reached} == {PROBLEM_SERVICE_ERROR}


def test_br10_4_one_address_for_both_roles_gets_one_email_that_stands_for_both(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    refusing = RefusingSes(aws[1], {TEAM.upper()})  # the address as the sender wrote it
    notifier = make_notifier(aws, clock, log_stream, ses=refusing)

    result = notifier.send(make_request(clock, sender=TEAM.upper()), "the scan broke", True)

    assert (result.sent, result.not_sent, result.email_sent) == (0, 1, False)
    assert result.roles_not_reached == ("sender", "team")  # one email, both roles unreached


def leftover(location: str, area: LeftoverArea = LeftoverArea.FINAL_LOCATION) -> Leftover:
    return Leftover(LeftoverKind.OBJECT, area, location)


def test_br9_4_the_failure_email_lists_20_leftovers_in_area_then_location_with_the_total(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)
    found = [leftover(f"holding/imp-1/file-{n:02d}", LeftoverArea.HOLDING_AREA) for n in range(15)]
    found += [leftover(f"models/org/model/{VERSION}/part-{n:02d}") for n in range(15, 0, -1)]
    found += [Leftover(LeftoverKind.TEMPORARY_RESOURCE, LeftoverArea.LOCAL_DISK, "work/imp-1")]

    result = notifier.send(make_request(clock), "the scan broke", False, leftovers=found)

    body = sent_messages()[0].body
    lines = [line for line in body.splitlines() if re.match(r"\d+\. (object|temporary)", line)]
    assert len(lines) == 20 == len(result.notice.leftovers_listed)
    assert lines[0] == f"1. object, final-location: models/org/model/{VERSION}/part-01"
    assert lines[15].startswith("16. object, holding-area: holding/imp-1/file-00")  # area order
    assert "31 leftovers in total" in body and "removalComplete: no" in body
    assert "the team should remove these by hand" in body
    assert result.notice.leftovers_total == 31
    assert select_leftovers(found) == result.notice.leftovers_listed


def test_br9_4_br9_5_the_marker_leftover_adds_a_note_and_older_files_are_counted(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)
    marker = leftover(f"models/org/model/{VERSION}/_COMPLETE")

    notifier.send(
        make_request(clock), "the move failed", False, leftovers=[marker], older_files_removed=3
    )
    notifier.send(make_request(clock), "the move failed", True, older_files_removed=1)

    first, second = sent_messages()[0].body, sent_messages()[2].body
    assert "the final location may hold a complete copy of this version" in first
    assert (
        "3 older unfinished files of an earlier import of this version were also removed" in first
    )
    assert "leftovers in total" not in first  # one leftover: no total line
    assert "1 older unfinished file of an earlier import of this version was also removed" in second
    assert "leftovers" not in second and "note:" not in second  # complete: nothing is listed


def test_br9_4_a_leftover_location_is_cleaned_before_it_reaches_the_email(
    aws: Any, clock: Any, log_stream: io.StringIO
) -> None:
    notifier = make_notifier(aws, clock, log_stream)
    odd = leftover("holding/imp-1/bad\nname\x1b[31m" + "x" * 300, LeftoverArea.HOLDING_AREA)

    notifier.send(make_request(clock), "the scan broke", False, leftovers=[odd])

    lines = [x for x in sent_messages()[0].body.splitlines() if x.startswith("1. object")]
    assert len(lines) == 1  # the line break did not make a second line
    assert "bad?name?[31m" in lines[0] and len(lines[0].split(": ", 1)[1]) == 200


def test_br10_3_an_outcome_name_in_the_subject_follows_the_kind_of_email() -> None:
    assert build_subject(NAME, VERSION) == "Model import removed-with-email: Org/Model 0123456789ab"
    assert (
        build_subject(NAME, VERSION, Outcome.REFUSED_ALREADY_STORED)
        == "Model import refused-already-stored: Org/Model 0123456789ab"
    )
