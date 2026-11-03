"""Tests for coordinator.py: BR1.1, BR1.2, BR3.6, BR4.3, BR5.1, BR5.5 and NFR4.1.

The coordinator runs with the real scanner, custodian, notifier, and store on moto S3 and SES.
A stand-in downloader puts files into the holding area. Forced failures come from stand-ins that
raise, from an existing marker in the final location, and from a rejecting email service.
"""

import io
import json
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from moto.core import DEFAULT_ACCOUNT_ID
from moto.ses import ses_backends

from model_import.coordinator import (
    REASON_DOWNLOAD_FAILED,
    REASON_KEEP_FAILED,
    REASON_MARKER_UNREADABLE,
    REASON_SCAN_FAILED,
    ImportCoordinator,
)
from model_import.custodian import COMPLETE_MARKER, FileCustodian, RemovalResult
from model_import.deadline import REASON_TIME_LIMIT, Deadline, DeadlineReached
from model_import.event import StartEvent, classify_message
from model_import.logs import JsonLogger
from model_import.models import (
    ImportRecord,
    ImportStatus,
    ModelFile,
    ModelFileSet,
    Outcome,
    OutcomeRecord,
    derive_import_id,
    final_prefix,
    holding_prefix,
)
from model_import.notifier import FailureNotifier
from model_import.retry import PermanentDownloadError
from model_import.scanner import LeakScanner, ScanStorageError
from model_import.store import NOTICE_COUNTER, NOTICE_EMAIL_LIMIT, ImportStore, StoreError

HOLDING = "holding-bucket"
FINAL = "final-bucket"
FROM = "imports@example.com"
TEAM = "team@example.com"
SENDER = "person@example.com"
NAME = "Org/Model"
VERSION = "a" * 40
TARGET = final_prefix(NAME, VERSION)
SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"  # built from two parts: the file holds no whole secret

HEADER = b'{"a":1}'
WEIGHTS = len(HEADER).to_bytes(8, "little") + HEADER + b"\0" * 8
CLEAN = {"config.json": b'{"layers": 2}\n', "model.safetensors": WEIGHTS}
LEAKY = {"config.json": b'{"key": "' + SECRET.encode() + b'"}\n', "model.safetensors": WEIGHTS}


def start_event(event_id: str = "evt-1", model: str = NAME) -> dict[str, Any]:
    return {
        "id": event_id,
        "source": "model.import",
        "detail-type": "ModelImportRequested",
        "detail": {"modelName": model, "exactVersion": VERSION, "senderEmail": SENDER},
    }


class StubDownloader:
    """Puts files in the holding area, or fails after writing one of them."""

    def __init__(self, s3: Any, files: dict[str, bytes], error: Exception | None = None) -> None:
        self._s3, self._files, self._error = s3, files, error
        self.calls = 0
        self.deadlines: list[Any] = []

    def download(
        self, import_id: str, model_name: str, exact_version: str, deadline: Any = None
    ) -> ModelFileSet:
        self.calls += 1
        self.deadlines.append(deadline)
        for path, data in self._files.items():
            self._s3.put_object(Bucket=HOLDING, Key=holding_prefix(import_id) + path, Body=data)
            if self._error is not None:
                raise self._error
        files = [ModelFile(path, len(data), len(data)) for path, data in self._files.items()]
        return ModelFileSet(import_id, model_name, exact_version, files)


class MarkerAppearsDownloader(StubDownloader):
    """Downloads, and a complete copy of the same version appears in the final location."""

    def download(
        self, import_id: str, model_name: str, exact_version: str, deadline: Any = None
    ) -> ModelFileSet:
        file_set = super().download(import_id, model_name, exact_version, deadline)
        self._s3.put_object(Bucket=FINAL, Key=TARGET + COMPLETE_MARKER, Body=b"{}")
        return file_set


class BrokenScanner:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def scan(self, file_set: ModelFileSet, deadline: Any = None) -> Any:
        raise self._error


class CountingCustodian(FileCustodian):
    """The real custodian that counts removals and can be told to fail."""

    keep_error: Exception | None = None
    remove_error: Exception | None = None
    keep_error_after_marker: Exception | None = None  # raised after the real keep wrote the marker
    after_keep: Callable[[], None] | None = None
    removals = 0

    def keep(
        self, record: ImportRecord, file_set: ModelFileSet, scan: Any, deadline: Any = None
    ) -> Any:
        if self.keep_error is not None:
            raise self.keep_error
        result = super().keep(record, file_set, scan, deadline)
        if self.keep_error_after_marker is not None:
            raise self.keep_error_after_marker
        if self.after_keep is not None:
            self.after_keep()
        return result

    def remove(self, import_id: str, model_name: str, exact_version: str) -> RemovalResult:
        self.removals += 1
        if self.remove_error is not None:
            raise self.remove_error
        return super().remove(import_id, model_name, exact_version)


class RejectingSes:
    def send_email(self, **arguments: Any) -> Any:
        raise ClientError({"Error": {"Code": "MessageRejected"}}, "SendEmail")


class RecordingStore(ImportStore):
    """Remembers (status, step) of every record written by the coordinator."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.writes: list[tuple[str, str]] = []

    def write_record(self, record: ImportRecord) -> None:
        self.writes.append((record.status.value, record.step))
        super().write_record(record)


class Setup:
    def __init__(self, s3: Any, ses: Any, clock: Any, stream: io.StringIO) -> None:
        self.s3, self.stream = s3, stream
        self.store = RecordingStore(s3, HOLDING)
        self.logger = JsonLogger(stream, clock=clock)
        self.custodian = CountingCustodian(
            s3, HOLDING, FINAL, ImportStore(s3, HOLDING), clock=clock, logger=self.logger
        )
        self.notifier_ses: Any = ses
        self.downloader: Any = StubDownloader(s3, CLEAN)
        self.scanner: Any = LeakScanner.for_s3(s3, HOLDING)
        self.clock = clock

    def use_custodian(self, s3_like: Any, state: Any = None) -> CountingCustodian:
        """Replace the custodian by one whose S3 calls go through a stand-in client."""
        self.custodian = CountingCustodian(
            s3_like,
            HOLDING,
            FINAL,
            ImportStore(self.s3, HOLDING),
            clock=self.clock,
            logger=self.logger,
            state_client=state,
            sleep=lambda seconds: None,
        )
        return self.custodian

    def coordinator(self, cls: type[ImportCoordinator] = ImportCoordinator) -> ImportCoordinator:
        notifier = FailureNotifier(
            self.notifier_ses,
            ImportStore(self.s3, HOLDING),
            FROM,
            TEAM,
            clock=self.clock,
            logger=self.logger,
        )
        return cls(
            self.downloader,
            self.scanner,
            self.custodian,
            notifier,
            self.store,
            clock=self.clock,
            logger=self.logger,
        )

    def logged(self, event: str) -> list[dict[str, Any]]:
        lines = [json.loads(line) for line in self.stream.getvalue().splitlines()]
        return [item for item in lines if item["event"] == event]

    def keys(self, bucket: str, prefix: str = "") -> list[str]:
        listing = self.s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        return sorted(item["Key"] for item in listing.get("Contents", []))


@pytest.fixture
def setup(clock: Any) -> Iterator[Setup]:
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=HOLDING)
        s3.create_bucket(Bucket=FINAL)
        ses = boto3.client("ses", region_name="us-east-1")
        ses.verify_email_identity(EmailAddress=FROM)
        yield Setup(s3, ses, clock, io.StringIO())


def emails() -> list[Any]:
    return ses_backends[DEFAULT_ACCOUNT_ID]["us-east-1"].sent_messages


def test_br4_3_a_clean_model_is_kept_with_outcome_stored_and_no_email(setup: Setup) -> None:
    result = setup.coordinator().handle(start_event())

    assert result is not None
    assert result.import_id == derive_import_id("evt-1")  # BR1.2: the importId follows the event
    assert (result.outcome, result.failure_reason, result.outcome_created) == (
        Outcome.STORED,
        None,
        True,
    )
    assert emails() == []
    assert setup.keys(FINAL) == sorted([TARGET + path for path in CLEAN] + [TARGET + "_COMPLETE"])
    assert setup.keys(HOLDING, holding_prefix(result.import_id)) == []
    stored = setup.store.read_outcome(result.import_id)
    assert stored is not None and stored.outcome is Outcome.STORED
    record = setup.store.read_record(result.import_id)
    assert record is not None and record.status is ImportStatus.DONE


def test_br5_1_a_finding_removes_everything_and_emails_both_without_the_value(
    setup: Setup,
) -> None:
    setup.downloader = StubDownloader(setup.s3, LEAKY)

    result = setup.coordinator().handle(start_event())

    assert result is not None
    assert result.outcome is Outcome.REMOVED_WITH_EMAIL  # BR5.3
    assert result.failure_reason == "the scan found 1 finding in 1 file"
    assert (result.findings_total, result.removal_complete) == (1, True)
    assert setup.keys(FINAL) == []  # BR4.2: nothing was kept
    assert setup.keys(HOLDING, holding_prefix(result.import_id)) == []
    assert [m.destinations["ToAddresses"] for m in emails()] == [[SENDER], [TEAM]]
    body = emails()[0].body
    assert "config.json: line 1, columns 10 to 29 (access-key)" in body
    everything = body + setup.stream.getvalue()
    assert SECRET not in everything and "AKIA" not in everything  # BR3.5, BR5.4
    assert [r["kind"] for r in setup.logged("finding")] == ["access-key"]  # BR5.6: in the logs


def test_br5_1_records_are_rewritten_before_each_step_and_moves_are_allowed_ones(
    setup: Setup,
) -> None:
    setup.coordinator().handle(start_event("evt-clean"))
    clean_writes, setup.store.writes = setup.store.writes, []
    setup.downloader = StubDownloader(setup.s3, LEAKY)

    # BR7.1: another model, because the clean one is stored now and its version would be refused.
    setup.coordinator().handle(start_event("evt-leaky", model="Org/Other"))

    assert clean_writes == [
        ("downloading", "download"),
        ("scanning", "scan"),
        ("keeping", "keep"),
        ("done", "done"),
    ]
    assert setup.store.writes == [
        ("downloading", "download"),
        ("scanning", "scan"),
        ("removing", "remove"),
        ("done", "done"),
    ]
    statuses = [r["status"] for r in setup.logged("status_changed")]
    assert statuses == ["scanning", "keeping", "scanning", "removing"]


FAILURES = {
    "download-permanent": (REASON_DOWNLOAD_FAILED, "the model needs a login"),
    "download-unexpected": (REASON_DOWNLOAD_FAILED, REASON_DOWNLOAD_FAILED),
    "scan-storage": (None, "the files to scan could not be read from storage"),
    "scan-unexpected": (None, REASON_SCAN_FAILED),
    # BR4.1: the second line of defence. The front door of BR7.1 sees no marker; one appears
    # while the import runs, and the keep step still refuses to write beside it.
    "keep-duplicate": (None, "the model version is already in the final location"),
    "keep-unexpected": (None, REASON_KEEP_FAILED),
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_br5_1_a_failed_step_goes_to_removing_once_then_done_with_one_email(
    setup: Setup, case: str
) -> None:
    """BR5.1, BR3.6: a failed download, scan, or keep step ends the same way."""
    expected_reason = FAILURES[case][1]
    existing = []
    if case == "download-permanent":
        setup.downloader = StubDownloader(
            setup.s3, CLEAN, error=PermanentDownloadError(expected_reason)
        )
    elif case == "download-unexpected":
        setup.downloader = StubDownloader(setup.s3, CLEAN, error=RuntimeError("boom"))
    elif case == "scan-storage":
        setup.scanner = BrokenScanner(ScanStorageError())
    elif case == "scan-unexpected":
        setup.scanner = BrokenScanner(ValueError("boom"))
    elif case == "keep-duplicate":
        setup.downloader = MarkerAppearsDownloader(setup.s3, CLEAN)
        existing = [TARGET + COMPLETE_MARKER]
    else:
        setup.custodian.keep_error = RuntimeError("boom")

    result = setup.coordinator().handle(start_event())

    assert result is not None
    assert result.outcome is Outcome.REMOVED_WITH_EMAIL
    assert result.failure_reason == expected_reason
    assert setup.custodian.removals == 1  # one removal, never two
    assert setup.keys(FINAL) == existing  # nothing new; an earlier model is untouched
    assert setup.keys(HOLDING, holding_prefix(result.import_id)) == []
    assert len(emails()) == 2 and f"reason: {expected_reason}" in emails()[0].body
    outcome = setup.store.read_outcome(result.import_id)
    assert outcome is not None and outcome.outcome is Outcome.REMOVED_WITH_EMAIL


def test_br5_5_an_error_during_removal_is_recorded_and_the_email_still_goes_out(
    setup: Setup,
) -> None:
    setup.downloader = StubDownloader(setup.s3, LEAKY)
    setup.custodian.remove_error = RuntimeError("the delete broke")

    result = setup.coordinator().handle(start_event())

    assert result is not None
    assert setup.custodian.removals == 1  # the error did not start a second removal
    assert (result.outcome, result.removal_complete) == (Outcome.REMOVED_WITH_EMAIL, False)
    assert "removalComplete: no" in emails()[0].body
    record = setup.store.read_record(result.import_id)
    assert record is not None and record.status is ImportStatus.DONE


@pytest.mark.parametrize("how", ["service-rejects", "notifier-raises"])
def test_br5_5_an_email_that_cannot_be_sent_ends_as_removed_email_failed(
    setup: Setup, how: str
) -> None:
    setup.downloader = StubDownloader(setup.s3, LEAKY)
    setup.notifier_ses = RejectingSes()
    coordinator = setup.coordinator()
    if how == "notifier-raises":

        class RaisingNotifier:
            def send(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("the mail code broke")

        coordinator._notifier = RaisingNotifier()  # type: ignore[assignment]

    result = coordinator.handle(start_event())

    assert result is not None
    assert result.outcome is Outcome.REMOVED_EMAIL_FAILED
    assert setup.custodian.removals == 1
    assert setup.keys(HOLDING, holding_prefix(result.import_id)) == []
    outcome = setup.store.read_outcome(result.import_id)
    assert outcome is not None and outcome.outcome is Outcome.REMOVED_EMAIL_FAILED
    record = setup.store.read_record(result.import_id)
    assert record is not None and record.status is ImportStatus.DONE


def test_nfr4_1_the_outcome_is_written_once_and_a_repeat_deletes_nothing_stored(
    setup: Setup,
) -> None:
    first = setup.coordinator().handle(start_event())
    second = setup.coordinator().handle(start_event())  # the same event again

    assert first is not None and second is not None
    assert second.import_id == first.import_id
    assert (first.outcome, first.outcome_created) == (Outcome.STORED, True)
    # BR10.1: the outcome object exists, so the repeat does nothing and reports that outcome
    # (u1 ran the import again and failed at the keep step; BR7.1 and BR10.1 end it at the door).
    assert second.outcome is Outcome.STORED
    assert second.outcome_created is False
    assert setup.downloader.calls == 1 and len(emails()) == 0
    stored = setup.store.read_outcome(first.import_id)
    assert stored is not None and stored.outcome is Outcome.STORED  # the first outcome stays
    assert TARGET + COMPLETE_MARKER in setup.keys(FINAL)  # the repeat deleted nothing stored
    assert len(setup.keys(FINAL)) == len(CLEAN) + 1
    assert setup.logged("outcome_already_set")


def test_br1_1_br6_1_an_event_that_fails_the_check_starts_nothing_and_logs_only_the_field(
    setup: Setup,
) -> None:
    bad = start_event(model="bad name!!/x")

    result = setup.coordinator().handle(bad)

    # u2 (BR6.1): the event is rejected with an email as well; u1 only logged it and dropped it.
    assert result is not None and result.outcome is Outcome.REJECTED
    assert setup.downloader.calls == 0
    # Nothing is written to the holding area or the final location; only state/ gets the outcome.
    assert setup.keys(HOLDING, "holding/") == [] and setup.keys(FINAL) == []
    rejected = setup.logged("event_rejected")
    assert [r["reason"] for r in rejected] == ["the event field modelName is missing or not valid"]
    assert "bad name" not in setup.stream.getvalue()


# =============================================================================================
# u2-import-rules, Step 7/8: the front door, the five outcomes, the deadline, the commit marker
# =============================================================================================

PLANTED_ADDRESS = "planted.person" + "@example.invalid"
OTHER_IMPORT = "imp-other"
HOUR = 3600


def server_error(operation: str = "Call") -> ClientError:
    return ClientError(
        {"Error": {"Code": "InternalError"}, "ResponseMetadata": {"HTTPStatusCode": 500}}, operation
    )


class FaultyS3:
    """Forwards to a real client and fails the calls a test chooses (the custodian's client)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.copies = 0
        self.fail_copy_at: int | None = None
        self.fail_delete_prefix: str | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def copy(self, source: Any, bucket: str, key: str, **options: Any) -> Any:
        self.copies += 1
        if self.copies == self.fail_copy_at:
            raise server_error("Copy")
        return self._inner.copy(source, bucket, key, **options)

    def delete_objects(self, **arguments: Any) -> Any:
        keys = [item["Key"] for item in arguments["Delete"]["Objects"]]
        prefix = self.fail_delete_prefix
        refused = [key for key in keys if prefix is not None and key.startswith(prefix)]
        if not refused:
            return self._inner.delete_objects(**arguments)
        rest = [{"Key": key} for key in keys if key not in refused]
        if rest:
            self._inner.delete_objects(Bucket=arguments["Bucket"], Delete={"Objects": rest})
        return {"Errors": [{"Key": k, "Code": "InternalError", "Message": "no"} for k in refused]}


class FailingMarkerCheck:
    """The state client of the marker reader: the HEAD fails with a server error."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def head_object(self, **arguments: Any) -> Any:
        raise server_error("HeadObject")

    def get_object(self, **arguments: Any) -> Any:
        return self._inner.get_object(**arguments)


class RefusingSomeSes:
    """Wraps a real SES client; refuses the addresses in ``refused`` like a rejecting service."""

    def __init__(self, inner: Any, refused: set[str]) -> None:
        self._inner, self.refused = inner, refused

    def send_email(self, **arguments: Any) -> Any:
        if set(arguments["Destination"]["ToAddresses"]) & self.refused:
            raise ClientError({"Error": {"Code": "MessageRejected"}}, "SendEmail")
        return self._inner.send_email(**arguments)


class SlowDownloader(StubDownloader):
    """Downloads, and then the clock passes the deadline before the download call returns."""

    def __init__(self, s3: Any, files: dict[str, bytes], clock: Any) -> None:
        super().__init__(s3, files)
        self._clock = clock

    def download(
        self, import_id: str, model_name: str, exact_version: str, deadline: Any = None
    ) -> ModelFileSet:
        super().download(import_id, model_name, exact_version, deadline)
        self._clock.advance(6 * HOUR)
        deadline.check()  # the check of the downloader at the next chunk or file
        raise AssertionError("the deadline must have been reached")


class SlowScanner:
    """The real scanner; the clock passes the deadline while it works."""

    def __init__(self, inner: Any, clock: Any) -> None:
        self._inner, self._clock = inner, clock

    def scan(self, file_set: ModelFileSet, deadline: Any = None) -> Any:
        result = self._inner.scan(file_set, deadline)
        self._clock.advance(6 * HOUR)
        return result


class CheckingStore(RecordingStore):
    """Remembers whether the log line was already written when the outcome object is created."""

    def __init__(self, s3: Any, bucket: str, stream: io.StringIO) -> None:
        super().__init__(s3, bucket)
        self._stream = stream
        self.line_first: list[bool] = []
        self.outcome_exists_at_record_write: list[bool] = []

    def create_outcome(self, outcome: OutcomeRecord) -> bool:
        self.line_first.append('"event":"import_outcome"' in self._stream.getvalue())
        return super().create_outcome(outcome)

    def write_record(self, record: ImportRecord) -> None:
        if record.status is ImportStatus.DONE:
            self.outcome_exists_at_record_write.append(self.has_outcome(record.import_id))
        super().write_record(record)


class RacingStore(RecordingStore):
    """Another writer creates the outcome object just before this import does."""

    def create_outcome(self, outcome: OutcomeRecord) -> bool:
        first = OutcomeRecord(outcome.import_id, Outcome.STORED, True, outcome.finished_at)
        super().create_outcome(first)
        return super().create_outcome(outcome)


class BreaksAfterKeep(ImportCoordinator):
    """A coordinator with an unexpected error right after the keep step returned."""

    def _lap(self, run: Any, name: str, started: Any) -> None:
        if name == "keepSeconds":
            raise RuntimeError("an unexpected error after the marker")
        super()._lap(run, name, started)


def state_text(setup: Setup) -> str:
    """Every object under state/ as one text (records, outcomes, counters)."""
    return "\n".join(
        setup.s3.get_object(Bucket=HOLDING, Key=key)["Body"].read().decode()
        for key in setup.keys(HOLDING, "state/")
    )


def email_text() -> str:
    return "\n".join(f"{m.subject}\n{m.body}" for m in emails())


def put_marker(setup: Setup, body: bytes | None = None) -> bytes:
    data = body if body is not None else json.dumps({"importId": OTHER_IMPORT}).encode()
    setup.s3.put_object(Bucket=FINAL, Key=TARGET + COMPLETE_MARKER, Body=data)
    return data


def final_marker(setup: Setup) -> bytes:
    return setup.s3.get_object(Bucket=FINAL, Key=TARGET + COMPLETE_MARKER)["Body"].read()


def the_line(setup: Setup, event: str) -> dict[str, Any]:
    lines = setup.logged(event)
    assert len(lines) == 1, f"expected one {event} line, found {len(lines)}"
    return lines[0]


# -- BR6: a rejected event ---------------------------------------------------------------------


def test_br6_1_br6_2_br11_1_a_bad_event_is_rejected_with_two_emails_and_one_record_at_the_end(
    setup: Setup,
) -> None:
    result = setup.coordinator().handle(start_event(model="bad name!!/x"))

    assert result is not None and result.outcome is Outcome.REJECTED
    assert (result.email_sent, result.recipients_not_reached) == (True, ())
    assert result.removal_complete is True and result.outcome_created is True
    assert [m.destinations["ToAddresses"] for m in emails()] == [[SENDER], [TEAM]]
    assert emails()[0].subject == "Model import rejected: event evt-1"
    assert "- modelName: not in the expected form" in emails()[0].body
    assert setup.downloader.calls == 0  # nothing is downloaded (BR6.1)
    assert setup.keys(HOLDING, "holding/") == [] and setup.keys(FINAL) == []
    outcome = setup.store.read_outcome(result.import_id)
    assert outcome is not None and outcome.outcome is Outcome.REJECTED
    assert (outcome.email_sent, outcome.recipients_not_reached) == (True, ())
    assert outcome.failure_reason == "the request was rejected; fields that failed: modelName"
    record = setup.store.read_record(result.import_id)
    assert record is not None and record.status is ImportStatus.DONE
    assert record.outcome is Outcome.REJECTED and record.event_id == "evt-1"
    assert (record.model_name, record.exact_version) == (None, VERSION)  # only what passed
    assert setup.store.writes == [("done", "done")]  # BR11.1: written once, at the end
    day = setup.clock().date()
    assert setup.store.read_email_count(day, NOTICE_COUNTER) == 2  # NFR3.18: the notices counter
    assert setup.store.read_email_count(day) == 0


def test_br6_2_a_rejected_event_with_a_bad_sender_tells_only_the_team_and_names_each_field(
    setup: Setup,
) -> None:
    event = start_event()
    event["detail"]["senderEmail"] = "not an address"
    del event["detail"]["exactVersion"]

    result = setup.coordinator().handle(event)

    assert result is not None and result.outcome is Outcome.REJECTED
    assert [m.destinations["ToAddresses"] for m in emails()] == [[TEAM]]
    assert "- exactVersion: missing" in emails()[0].body
    assert "- senderEmail: not in the expected form" in emails()[0].body
    record = setup.store.read_record(result.import_id)
    assert record is not None and (record.model_name, record.exact_version) == (NAME, None)
    assert result.email_sent is True  # every email that had to go out was accepted


def test_br6_4_a_message_that_is_not_a_start_event_is_logged_by_field_and_left_alone(
    setup: Setup,
) -> None:
    for body in ({"id": "evt-9", "source": "other"}, "not json at all"):
        assert setup.coordinator().handle(body) is None

    reasons = [r["reason"] for r in setup.logged("event_rejected")]
    assert reasons == [
        "the event field source is missing or not valid",
        "the event field body is missing or not valid",
    ]
    assert emails() == [] and setup.keys(HOLDING) == []  # no email, no outcome, no record


def test_br10_3_a_rejection_email_the_service_refuses_is_recorded_and_the_outcome_stays_rejected(
    setup: Setup,
) -> None:
    setup.notifier_ses = RejectingSes()

    result = setup.coordinator().handle(start_event(model="bad name!!/x"))

    assert result is not None and result.outcome is Outcome.REJECTED
    assert (result.email_sent, result.recipients_not_reached) == (False, ("sender", "team"))
    outcome = setup.store.read_outcome(result.import_id)
    assert outcome is not None and outcome.email_sent is False
    assert outcome.recipients_not_reached == ("sender", "team")
    not_sent = setup.logged("email_not_sent")
    assert [(r["recipientsNotReached"], r["emailProblem"]) for r in not_sent] == [
        ("sender", "service-error"),
        ("team", "service-error"),
    ]
    line = the_line(setup, "import_outcome")
    assert (line["emailSent"], line["recipientsNotReached"]) == (False, "sender,team")
    assert SENDER not in setup.stream.getvalue() and TEAM not in setup.stream.getvalue()


def test_br10_3_the_daily_limit_of_notices_is_a_recorded_problem_not_a_failed_import(
    setup: Setup,
) -> None:
    for _ in range(NOTICE_EMAIL_LIMIT):
        setup.store.reserve_email_attempt(
            setup.clock().date(), NOTICE_EMAIL_LIMIT, counter=NOTICE_COUNTER
        )

    result = setup.coordinator().handle(start_event(model="bad name!!/x"))

    assert result is not None and result.outcome is Outcome.REJECTED
    assert (result.email_sent, emails()) == (False, [])
    assert {r["emailProblem"] for r in setup.logged("email_not_sent")} == {"daily-limit"}
    assert [r["kind"] for r in setup.logged("email_limit_reached")] == ["notice", "notice"]


def test_br10_1_br10_5_the_same_bad_event_again_does_nothing_and_sends_no_second_email(
    setup: Setup,
) -> None:
    first = setup.coordinator().handle(start_event(model="bad name!!/x"))
    second = setup.coordinator().handle(start_event(model="bad name!!/x"))

    assert first is not None and second is not None
    assert second.import_id == first.import_id  # BR10.5: the same eventId, the same importId
    assert (first.outcome_created, second.outcome_created) == (True, False)
    assert second.outcome is Outcome.REJECTED
    assert len(emails()) == 2  # only the first time
    assert len(setup.logged("outcome_already_set")) == 1


def test_nfr3_19_nfr3_20_nothing_planted_in_a_bad_event_reaches_a_log_a_record_or_an_email(
    setup: Setup,
) -> None:
    event = {
        "id": "evt 1 " + SECRET,  # an id of an unexpected form: the importId stands for it
        "source": "model.import",
        "detail-type": "ModelImportRequested",
        "time": SECRET,
        "detail": {
            "modelName": "bad/" + SECRET + "/x",
            "exactVersion": SECRET,
            "senderEmail": PLANTED_ADDRESS + " " + SECRET,
            "note": SECRET,
        },
    }

    result = setup.coordinator().handle(event)

    assert result is not None and result.outcome is Outcome.REJECTED
    everything = setup.stream.getvalue() + state_text(setup) + email_text()
    assert SECRET not in everything and PLANTED_ADDRESS not in everything
    assert "- modelName" in email_text() and f"importId: {result.import_id}" in email_text()
    assert [m.destinations["ToAddresses"] for m in emails()] == [[TEAM]]


def test_nfr3_20_the_sender_address_is_in_no_log_record_state_object_or_marker(
    setup: Setup,
) -> None:
    """A planted address on a stored import and on a failed import: only emails hold it."""
    stored_event = start_event("evt-a")
    stored_event["detail"]["senderEmail"] = PLANTED_ADDRESS
    leaky_event = start_event("evt-b", model="Org/Other")
    leaky_event["detail"]["senderEmail"] = PLANTED_ADDRESS
    stored = setup.coordinator().handle(stored_event)
    setup.downloader = StubDownloader(setup.s3, LEAKY)
    failed = setup.coordinator().handle(leaky_event)

    assert stored is not None and stored.outcome is Outcome.STORED
    assert failed is not None and failed.outcome is Outcome.REMOVED_WITH_EMAIL
    marker = final_marker(setup).decode()
    quiet = marker + setup.stream.getvalue() + state_text(setup)
    assert PLANTED_ADDRESS not in quiet and SECRET not in quiet + email_text()
    # The address is used for the one send that needs it, and nowhere else.
    assert [m.destinations["ToAddresses"] for m in emails()] == [[PLANTED_ADDRESS], [TEAM]]
    assert PLANTED_ADDRESS not in email_text()


# -- BR7: a model that is already stored ---------------------------------------------------------


def test_br7_1_br7_2_a_marker_of_another_import_refuses_the_event_before_any_download(
    setup: Setup,
) -> None:
    body = put_marker(setup)

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.REFUSED_ALREADY_STORED
    assert result.failure_reason == "this model version is already stored"
    assert setup.downloader.calls == 0
    assert setup.keys(FINAL) == [TARGET + COMPLETE_MARKER] and final_marker(setup) == body
    assert setup.keys(HOLDING, "holding/") == []
    assert [m.destinations["ToAddresses"] for m in emails()] == [[SENDER], [TEAM]]
    assert f"finalLocation: {TARGET}" in emails()[0].body
    assert emails()[0].subject.startswith("Model import refused-already-stored: Org/Model ")
    outcome = setup.store.read_outcome(result.import_id)
    assert outcome is not None and outcome.outcome is Outcome.REFUSED_ALREADY_STORED
    assert (outcome.email_sent, outcome.removal_complete) == (True, True)
    assert setup.store.writes == [("done", "done")]  # BR11.1: one record, at the end
    assert setup.custodian.removals == 0  # nothing was created, so nothing is removed


def test_br7_1_a_second_event_for_a_version_the_first_stored_is_refused_when_its_turn_comes(
    setup: Setup,
) -> None:
    first = setup.coordinator().handle(start_event("evt-1"))
    second = setup.coordinator().handle(start_event("evt-2"))  # the same model and version

    assert first is not None and second is not None
    assert (first.outcome, second.outcome) == (Outcome.STORED, Outcome.REFUSED_ALREADY_STORED)
    assert setup.downloader.calls == 1
    assert len(setup.keys(FINAL)) == len(CLEAN) + 1  # the first model is untouched


@pytest.mark.parametrize("damage", [b"not json", b"[1, 2]", b'{"files": []}', b'{"importId": 7}'])
def test_br7_1_nfr3_25_a_marker_that_cannot_be_read_refuses_the_event_and_is_left_alone(
    setup: Setup, damage: bytes
) -> None:
    put_marker(setup, damage)

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.REFUSED_ALREADY_STORED
    assert result.failure_reason == REASON_MARKER_UNREADABLE
    assert final_marker(setup) == damage and setup.downloader.calls == 0
    assert f"reason: {REASON_MARKER_UNREADABLE}" in emails()[0].body
    unreadable = the_line(setup, "marker_unreadable")
    assert damage.decode() not in json.dumps(unreadable)  # no content of the marker in the log


def test_br7_1_a_check_of_the_marker_that_cannot_be_made_is_a_failed_step_not_a_refusal(
    setup: Setup,
) -> None:
    custodian = setup.use_custodian(setup.s3, state=FailingMarkerCheck(setup.s3))

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.REMOVED_WITH_EMAIL
    assert result.failure_reason == "the final location could not be checked"
    assert setup.downloader.calls == 0 and custodian.removals == 0  # nothing to remove
    assert [m.destinations["ToAddresses"] for m in emails()] == [[SENDER], [TEAM]]
    assert "reason: the final location could not be checked" in emails()[0].body
    assert setup.keys(FINAL) == []
    assert setup.store.writes == [("removing", "remove"), ("done", "done")]  # record: removing


def test_br7_1_br9_6_a_redriven_message_finds_its_own_marker_and_finishes_as_stored_without_mail(
    setup: Setup,
) -> None:
    first = setup.coordinator().handle(start_event())
    assert first is not None and first.outcome is Outcome.STORED
    # The process stopped after the marker and before the outcome object; the holding folder
    # still has a file, and the message was sent again from the dead-letter queue.
    setup.s3.delete_object(Bucket=HOLDING, Key=ImportStore.outcome_key(first.import_id))
    setup.s3.put_object(
        Bucket=HOLDING, Key=holding_prefix(first.import_id) + "left.bin", Body=b"left"
    )
    emails_before = len(emails())

    again = setup.coordinator().handle(start_event())

    assert again is not None and again.outcome is Outcome.STORED
    assert (again.outcome_created, again.email_sent, again.failure_reason) == (True, None, None)
    assert again.removal_complete is True
    assert setup.downloader.calls == 1  # no second download
    assert len(emails()) == emails_before == 0
    assert setup.keys(HOLDING, holding_prefix(first.import_id)) == []  # BR9.6: the clean-up ran
    assert len(setup.keys(FINAL)) == len(CLEAN) + 1 and setup.custodian.removals == 0
    assert the_line(setup, "own_marker_found")["step"] == "start"
    outcome = setup.store.read_outcome(first.import_id)
    assert outcome is not None and outcome.outcome is Outcome.STORED and outcome.email_sent is None


def test_br7_1_files_without_a_marker_do_not_count_as_stored_and_are_overwritten(
    setup: Setup,
) -> None:
    setup.s3.put_object(Bucket=FINAL, Key=TARGET + "config.json", Body=b"old and unfinished")

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.STORED
    body = setup.s3.get_object(Bucket=FINAL, Key=TARGET + "config.json")["Body"].read()
    assert body == CLEAN["config.json"] and setup.downloader.calls == 1


# -- BR8: the time limit ---------------------------------------------------------------------------


def test_br8_1_the_deadline_is_six_hours_from_the_start_or_the_one_the_worker_made(
    setup: Setup,
) -> None:
    event = classify_message(start_event("evt-1"))
    other = classify_message(start_event("evt-2", model="Org/Other"))
    assert isinstance(event, StartEvent) and isinstance(other, StartEvent)
    begin = setup.clock()

    setup.coordinator().run(event)
    made_by_the_worker = Deadline(setup.clock(), setup.clock)
    setup.clock.advance(5 * HOUR)  # the event waited, or the worker took time before the call
    setup.coordinator().run(other, made_by_the_worker)

    assert setup.downloader.deadlines[0].at == begin + timedelta(hours=6)
    assert setup.downloader.deadlines[1] is made_by_the_worker  # BR8.1: queue time never counts


def test_br8_2_br8_4_br8_3_a_deadline_reached_in_the_download_fails_the_import_and_removes_all(
    setup: Setup,
) -> None:
    setup.downloader = SlowDownloader(setup.s3, CLEAN, setup.clock)

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.REMOVED_WITH_EMAIL
    assert result.failure_reason == REASON_TIME_LIMIT == "the time limit of 6 hours passed"
    assert setup.keys(HOLDING, "holding/") == [] and setup.keys(FINAL) == []
    assert setup.custodian.removals == 1  # BR8.3: the removal and the email are not stopped
    assert f"reason: {REASON_TIME_LIMIT}" in emails()[0].body and len(emails()) == 2
    assert ("scanning", "scan") not in setup.store.writes
    assert the_line(setup, "import_outcome")["reason"] == REASON_TIME_LIMIT


def test_br8_2_the_deadline_is_checked_before_the_move_to_a_new_status(setup: Setup) -> None:
    setup.scanner = SlowScanner(setup.scanner, setup.clock)

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.REMOVED_WITH_EMAIL
    assert result.failure_reason == REASON_TIME_LIMIT
    assert ("keeping", "keep") not in setup.store.writes  # the move to keeping was never made
    assert ("removing", "remove") in setup.store.writes
    assert setup.keys(FINAL) == [] and setup.keys(HOLDING, "holding/") == []


def test_br8_2_a_deadline_reached_inside_the_keep_step_fails_the_import_with_the_same_reason(
    setup: Setup,
) -> None:
    setup.custodian.keep_error = DeadlineReached()

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.REMOVED_WITH_EMAIL
    assert result.failure_reason == REASON_TIME_LIMIT and setup.custodian.removals == 1
    assert setup.keys(FINAL) == []


def test_br8_3_br9_6_the_deadline_never_undoes_a_model_whose_marker_is_written(
    setup: Setup,
) -> None:
    setup.custodian.after_keep = lambda: setup.clock.advance(7 * HOUR)  # long after the deadline

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.STORED and emails() == []
    assert TARGET + COMPLETE_MARKER in setup.keys(FINAL) and setup.custodian.removals == 0


# -- BR9: all or nothing, and the marker as the commit point ---------------------------------------


def test_br9_2_br9_3_br9_5_a_failed_move_removes_the_copies_and_older_files_and_says_how_many(
    setup: Setup,
) -> None:
    faulty = FaultyS3(setup.s3)
    faulty.fail_copy_at = 2  # the second of two files cannot be copied
    custodian = setup.use_custodian(faulty)
    setup.s3.put_object(Bucket=FINAL, Key=TARGET + "old-1.bin", Body=b"old")
    setup.s3.put_object(Bucket=FINAL, Key=TARGET + "old-2.bin", Body=b"old")

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.REMOVED_WITH_EMAIL
    assert result.removal_complete is True and custodian.removals == 1
    assert result.failure_reason == "a file could not be copied to the final location"
    assert setup.keys(FINAL) == [] and setup.keys(HOLDING, "holding/") == []  # nothing is left
    body = emails()[0].body
    assert "removalComplete: yes" in body
    assert "2 older unfinished files of an earlier import of this version were also removed" in body
    line = the_line(setup, "import_outcome")
    assert (line["olderFilesRemoved"], line["leftoversTotal"]) == (2, 0)
    assert line["removalComplete"] is True


def test_br9_3_br9_4_br11_3_a_delete_that_fails_is_a_leftover_in_the_email_and_the_records_stay(
    setup: Setup,
) -> None:
    faulty = FaultyS3(setup.s3)
    faulty.fail_delete_prefix = "holding/"
    setup.use_custodian(faulty)
    setup.downloader = StubDownloader(setup.s3, LEAKY)

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.REMOVED_WITH_EMAIL
    assert result.removal_complete is False
    body = emails()[0].body
    assert "removalComplete: no" in body and "the team should remove these by hand" in body
    prefix = holding_prefix(result.import_id)
    assert f"1. object, holding-area: {prefix}config.json" in body
    assert len(setup.logged("leftover")) == 2
    assert the_line(setup, "import_outcome")["leftoversTotal"] == 2
    outcome = setup.store.read_outcome(result.import_id)
    assert outcome is not None and outcome.removal_complete is False
    # BR11.3: the removal never touches state/; the record and the outcome are still there.
    assert setup.keys(HOLDING, f"state/{result.import_id}/") == sorted(
        [ImportStore.record_key(result.import_id), ImportStore.outcome_key(result.import_id)]
    )


def test_br9_6_an_unexpected_error_right_after_the_marker_never_removes_the_model(
    setup: Setup,
) -> None:
    result = setup.coordinator(BreaksAfterKeep).handle(start_event())

    assert result is not None and result.outcome is Outcome.STORED
    assert result.failure_reason is None and result.removal_complete is True
    assert setup.custodian.removals == 0 and emails() == []  # the removal is never called
    assert setup.keys(FINAL) == sorted([TARGET + p for p in CLEAN] + [TARGET + COMPLETE_MARKER])
    assert any(r["error"] == "RuntimeError" for r in setup.logged("step_failed"))
    outcome = setup.store.read_outcome(result.import_id)
    assert outcome is not None and outcome.outcome is Outcome.STORED


def test_br9_6_nfr4_14_if_keep_raises_after_the_marker_the_last_guard_ends_the_import_as_stored(
    setup: Setup,
) -> None:
    setup.custodian.keep_error_after_marker = RuntimeError("raised after the marker")

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.STORED
    assert result.removal_complete is False  # what happened after the marker is not known
    assert emails() == []  # the remove found a stored model: no failure email
    assert setup.keys(FINAL) == sorted([TARGET + p for p in CLEAN] + [TARGET + COMPLETE_MARKER])
    assert the_line(setup, "removal_refused")["reason"] == "the model is stored"
    record = setup.store.read_record(result.import_id)
    assert record is not None and record.status is ImportStatus.DONE
    assert record.outcome is Outcome.STORED
    outcome = setup.store.read_outcome(result.import_id)
    assert outcome is not None and outcome.outcome is Outcome.STORED


def test_br9_6_a_holding_folder_that_stays_after_the_marker_is_stored_with_removal_incomplete(
    setup: Setup,
) -> None:
    faulty = FaultyS3(setup.s3)
    faulty.fail_delete_prefix = "holding/"
    custodian = setup.use_custodian(faulty)

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.STORED
    assert result.removal_complete is False and emails() == []  # no email for a stored model
    assert custodian.removals == 0  # the model stays, and the removal is never started
    assert TARGET + COMPLETE_MARKER in setup.keys(FINAL)
    assert the_line(setup, "holding_not_emptied")["leftoversTotal"] == 2
    line = the_line(setup, "import_outcome")
    assert (line["removalComplete"], line["leftoversTotal"]) == (False, 2)
    outcome = setup.store.read_outcome(result.import_id)
    assert outcome is not None and outcome.outcome is Outcome.STORED
    assert outcome.removal_complete is False


# -- BR10: one outcome, one log line, the email result ---------------------------------------------


def test_br10_2_nfr4_22_br11_1_the_log_line_comes_before_the_outcome_and_the_record_after_it(
    setup: Setup,
) -> None:
    store = CheckingStore(setup.s3, HOLDING, setup.stream)
    setup.store = store
    setup.downloader = StubDownloader(setup.s3, LEAKY)

    result = setup.coordinator().handle(start_event())

    assert result is not None
    assert store.line_first == [True]  # the line was written before the outcome object
    assert store.outcome_exists_at_record_write == [True]  # the record shows done only after it
    line = the_line(setup, "import_outcome")
    assert line["importId"] == result.import_id and line["eventId"] == "evt-1"
    assert (line["outcome"], line["reason"]) == (
        "removed-with-email",
        "the scan found 1 finding in 1 file",
    )
    assert (line["removalComplete"], line["emailSent"], line["findingsTotal"]) == (True, True, 1)
    assert (line["olderFilesRemoved"], line["leftoversTotal"]) == (0, 0)
    for duration in ("frontDoorSeconds", "downloadSeconds", "scanSeconds", "proofSeconds"):
        assert duration in line
    assert "totalSeconds" in line and "senderEmail" not in line
    assert SENDER not in setup.stream.getvalue() and SECRET not in setup.stream.getvalue()


def test_br10_2_an_outcome_that_already_exists_is_not_changed_and_one_line_says_so(
    setup: Setup,
) -> None:
    setup.store = RacingStore(setup.s3, HOLDING)
    setup.downloader = StubDownloader(setup.s3, LEAKY)

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome_created is False
    already = the_line(setup, "outcome_already_set")
    assert already["importId"] == result.import_id and already["eventId"] == "evt-1"
    assert "reason" not in already and "outcome" not in already
    stored = setup.store.read_outcome(result.import_id)
    assert stored is not None and stored.outcome is Outcome.STORED  # the first one stays
    assert len(setup.logged("import_outcome")) == 1


def test_br10_3_br10_4_one_refused_recipient_makes_the_outcome_removed_email_failed(
    setup: Setup,
) -> None:
    setup.downloader = StubDownloader(setup.s3, LEAKY)
    setup.notifier_ses = RefusingSomeSes(setup.notifier_ses, {SENDER})

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.REMOVED_EMAIL_FAILED
    assert (result.email_sent, result.recipients_not_reached) == (False, ("sender",))
    assert [m.destinations["ToAddresses"] for m in emails()] == [[TEAM]]  # the other one stays sent
    assert setup.custodian.removals == 1 and setup.keys(HOLDING, "holding/") == []
    outcome = setup.store.read_outcome(result.import_id)
    assert outcome is not None and outcome.outcome is Outcome.REMOVED_EMAIL_FAILED
    assert (outcome.email_sent, outcome.recipients_not_reached) == (False, ("sender",))
    only = the_line(setup, "email_not_sent")
    assert (only["recipientsNotReached"], only["emailProblem"]) == ("sender", "service-error")
    assert SENDER not in setup.stream.getvalue()


def test_br10_1_every_event_ends_in_exactly_one_of_the_five_outcomes(setup: Setup) -> None:
    def outcome_of(event: dict[str, Any]) -> Outcome:
        result = setup.coordinator().handle(event)  # a new coordinator takes the current stand-ins
        assert result is not None
        return result.outcome

    stored = outcome_of(start_event("e1", model="Org/A"))
    setup.downloader = StubDownloader(setup.s3, LEAKY)
    removed = outcome_of(start_event("e2", model="Org/B"))
    rejected = outcome_of(start_event("e3", model="bad!!/x"))
    refused = outcome_of(start_event("e4", model="Org/A"))  # Org/A was stored by e1
    setup.notifier_ses = RejectingSes()
    unmailed = outcome_of(start_event("e5", model="Org/C"))

    assert [stored, removed, rejected, refused, unmailed] == [
        Outcome.STORED,
        Outcome.REMOVED_WITH_EMAIL,
        Outcome.REJECTED,
        Outcome.REFUSED_ALREADY_STORED,
        Outcome.REMOVED_EMAIL_FAILED,
    ]
    assert len([k for k in setup.keys(HOLDING, "state/") if k.endswith("outcome.json")]) == 5


# -- BR11: the record of every import --------------------------------------------------------------


def test_br11_1_br11_2_the_record_lists_the_first_200_findings_and_the_total_without_values(
    setup: Setup,
) -> None:
    def lines() -> bytes:  # 70 planted keys a file, none of them a whole secret in this file
        keys = [f'  "name{n}": "AKIA' + "Q" * 15 + f'{chr(65 + n % 26)}",' for n in range(70)]
        return ("{\n" + "\n".join(keys) + '\n  "end": 1\n}\n').encode()

    files = {"a.json": lines(), "b.json": lines(), "c.json": lines()}
    setup.downloader = StubDownloader(setup.s3, {**files, "model.safetensors": WEIGHTS})

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.REMOVED_WITH_EMAIL
    total = result.findings_total
    assert total >= 210  # more than the 200 the record keeps
    record = setup.store.read_record(result.import_id)
    assert record is not None and record.findings_total == total and len(record.findings) == 200
    names = [item.file_name for item in record.findings]
    assert names == sorted(names) and names[0] == "a.json"  # BR11.2: file path order
    assert {item.kind.value for item in record.findings} == {"access-key"}
    raw = state_text(setup)
    assert "AKIA" not in raw and "QQQQ" not in raw  # BR3.5: kind, place, file name only
    body = emails()[0].body
    assert f"{total} findings in total" in body and body.count(" (access-key)") == 20
    assert len(setup.logged("finding")) == total  # BR5.6: the full list stays in the log
    assert the_line(setup, "import_outcome")["findingsTotal"] == total


def test_br11_1_the_record_of_a_stored_import_has_the_outcome_and_no_findings(
    setup: Setup,
) -> None:
    result = setup.coordinator().handle(start_event())

    assert result is not None
    record = setup.store.read_record(result.import_id)
    assert record is not None and record.status is ImportStatus.DONE
    assert (record.outcome, record.findings, record.findings_total) == (Outcome.STORED, [], 0)
    assert record.event_id == "evt-1" and record.failure_reason is None


def test_br10_1_br8_1_a_redriven_message_with_no_marker_starts_again_with_a_new_deadline(
    setup: Setup,
) -> None:
    """The first attempt stopped while keeping: no marker, no outcome, some files and a holding
    leftover. The person sends the message again; it runs from the start."""
    import_id = derive_import_id("evt-1")
    setup.s3.put_object(Bucket=FINAL, Key=TARGET + "config.json", Body=b"half written")
    setup.s3.put_object(Bucket=HOLDING, Key=holding_prefix(import_id) + "junk.bin", Body=b"x")
    setup.clock.advance(3 * HOUR)  # the first attempt, and the dead-letter queue, took a while
    begin = setup.clock()

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.STORED
    assert setup.downloader.calls == 1 and emails() == []
    assert setup.downloader.deadlines[0].at == begin + timedelta(hours=6)  # new startedAt
    assert setup.keys(HOLDING, holding_prefix(import_id)) == []
    body = setup.s3.get_object(Bucket=FINAL, Key=TARGET + "config.json")["Body"].read()
    assert body == CLEAN["config.json"]


def test_br7_1_an_unexpected_error_in_the_marker_check_is_a_failed_step_with_a_plain_reason(
    setup: Setup,
) -> None:
    def explode(*_: Any, **__: Any) -> Any:
        raise RuntimeError("secret=should-not-be-logged")

    setup.custodian.read_marker = explode  # type: ignore[method-assign]

    result = setup.coordinator().handle(start_event())

    assert result is not None and result.outcome is Outcome.REMOVED_WITH_EMAIL
    assert result.failure_reason == "the final location could not be checked"
    assert setup.downloader.calls == 0 and setup.custodian.removals == 0
    assert "should-not-be-logged" not in setup.stream.getvalue()
    assert [r["error"] for r in setup.logged("step_failed")] == ["RuntimeError"]


def test_br10_1_when_the_outcome_cannot_be_read_at_the_door_the_conditional_create_still_guards(
    setup: Setup,
) -> None:
    class UnreadableOutcomeStore(RecordingStore):
        def read_outcome(self, import_id: str) -> Any:
            raise StoreError("the outcome cannot be read")

    setup.store = UnreadableOutcomeStore(setup.s3, HOLDING)

    first = setup.coordinator().handle(start_event())
    again = setup.coordinator().handle(start_event())  # nothing stopped this one at the door

    assert first is not None and again is not None
    assert (first.outcome_created, again.outcome_created) == (True, False)
    # The marker door found the import's own marker: stored again, no email, and the conditional
    # create left the first outcome as it was.
    assert again.outcome is Outcome.STORED and emails() == []
    assert setup.downloader.calls == 1


def test_br9_6_a_clean_up_that_raises_for_an_own_marker_still_ends_as_stored(
    setup: Setup,
) -> None:
    class BrokenFinish(CountingCustodian):
        def finish_own_marker(self, *args: Any) -> Any:
            raise RuntimeError("the clean-up broke")

    first = setup.coordinator().handle(start_event())
    assert first is not None
    setup.s3.delete_object(Bucket=HOLDING, Key=ImportStore.outcome_key(first.import_id))
    broken = BrokenFinish(
        setup.s3, HOLDING, FINAL, ImportStore(setup.s3, HOLDING), clock=setup.clock
    )
    setup.custodian = broken

    again = setup.coordinator().handle(start_event())

    assert again is not None and again.outcome is Outcome.STORED
    assert again.removal_complete is False and emails() == [] and broken.removals == 0
    assert len(setup.keys(FINAL)) == len(CLEAN) + 1  # the model stays


def test_nfr4_1_an_unexpected_error_before_the_marker_is_not_swallowed(setup: Setup) -> None:
    class BreakingStore(RecordingStore):
        def write_record(self, record: ImportRecord) -> None:
            if record.status is ImportStatus.SCANNING:
                raise RuntimeError("a fault the coordinator does not know")
            super().write_record(record)

    setup.store = BreakingStore(setup.s3, HOLDING)

    with pytest.raises(RuntimeError):
        setup.coordinator().handle(start_event())  # the worker handles it as a fatal error

    assert setup.store.read_outcome(derive_import_id("evt-1")) is None  # and no outcome exists
