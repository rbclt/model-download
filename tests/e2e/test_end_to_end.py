"""End-to-end tests: from an EventBridge-shaped event in the queue to the single outcome.

The real worker loop, coordinator, downloader, Hugging Face client, scanner, custodian, and
notifier run together. Hugging Face is a respx stand-in; S3, SQS, and SES are moto. The planted
secret is built from two parts so that this file holds no complete secret-shaped string.
Rules named: BR1.1, BR2.5, BR4.1, BR4.2, BR5.1, BR5.2, BR5.3, BR5.4 and NFR4.1.
"""

import hashlib
import io
import json
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
import respx
from botocore.exceptions import ClientError
from moto import mock_aws
from moto.core import DEFAULT_ACCOUNT_ID
from moto.ses import ses_backends

from model_import.deadline import REASON_TIME_LIMIT
from model_import.hf_client import HfClient
from model_import.logs import JsonLogger
from model_import.models import ImportStatus, Outcome, derive_import_id, final_prefix
from model_import.notifier import REASON_ALREADY_STORED
from model_import.retry import Retrier
from model_import.settings import Settings
from model_import.store import ImportStore
from model_import.worker import (
    EXIT_OK,
    ImportQueueWorker,
    WorkerConfig,
    build_import_runner,
)

HOLDING = "holding-bucket"
FINAL = "final-bucket"
FROM = "imports@example.com"
TEAM = "team@example.com"
SENDER = "person@example.com"
MODEL = "Org/Model"
VERSION = "b" * 40
EVENT_ID = "e2e-event-1"
SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"  # two parts: the file holds no whole secret
BAD_SENDER = "not an address " + SECRET  # fails the check; planted to prove it is never repeated

HEADER = b'{"a":1}'
WEIGHTS = len(HEADER).to_bytes(8, "little") + HEADER + b"\0" * 8
CLEAN = {"config.json": b'{"layers": 2}\n', "model.safetensors": WEIGHTS}
LEAKY = {"config.json": b'{"key": "' + SECRET.encode() + b'"}\n', "model.safetensors": WEIGHTS}
HF_BASE = "https://huggingface.co"


class FaultyS3:
    """The moto S3 client as the data client, with one fault that a test can switch on.

    ``fail_copy_of`` is the end of a file name: the server-side copy of a key that ends with it
    raises a service error, as a failed move would. Everything else is the real moto client.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self.fail_copy_of: str | None = None

    def copy(self, copy_source: Any, bucket: str, key: str, *args: Any, **kwargs: Any) -> Any:
        if self.fail_copy_of is not None and key.endswith(self.fail_copy_of):
            raise ClientError({"Error": {"Code": "InternalError"}}, "CopyObject")
        return self._client.copy(copy_source, bucket, key, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class Flow:
    """moto S3, SQS, and SES plus the worker wired the way ``main`` wires it."""

    def __init__(self, clock: Any, work_dir: Path) -> None:
        self.clock, self.work_dir = clock, work_dir
        self.s3 = boto3.client("s3", region_name="us-east-1")
        self.data = FaultyS3(self.s3)
        self.s3.create_bucket(Bucket=HOLDING)
        self.s3.create_bucket(Bucket=FINAL)
        ses = boto3.client("ses", region_name="us-east-1")
        ses.verify_email_identity(EmailAddress=FROM)
        self.sqs = boto3.client("sqs", region_name="us-east-1")
        queue_url = self.sqs.create_queue(
            QueueName="import.fifo",
            Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "true"},
        )["QueueUrl"]
        self.settings = Settings(HOLDING, FINAL, queue_url, FROM, TEAM)
        self.store = ImportStore(self.s3, HOLDING)
        self.stream = io.StringIO()
        logger = JsonLogger(self.stream, clock=clock)
        runners = build_import_runner(
            self.settings,
            self.data,
            ses,
            logger=logger,
            clock=clock,
            work_dir=work_dir,
            hf_factory=lambda: HfClient(retrier=Retrier(sleep=lambda seconds: None)),
        )
        polls = [0]

        def bounded_wait(event: threading.Event, seconds: float) -> bool:
            """A real, short wait; a stuck import fails the test after about 60 s, not never."""
            polls[0] += 1
            if polls[0] > 3000:
                raise TimeoutError("the import did not finish")
            return event.wait(seconds)

        self.worker = ImportQueueWorker(
            self.sqs,
            self.store,
            runners.run_import,
            WorkerConfig(
                queue_url=queue_url,
                receive_wait_seconds=0,
                poll_seconds=0.02,  # the import runs on its own thread
                idle_pause_seconds=20,
            ),
            reject_import=runners.reject_import,
            clock=clock,
            wait=bounded_wait,
            sleep=clock.advance,  # idle pauses move the fake clock; nothing sleeps for real
            logger=logger,
        )

    def send_event(self, event_id: str = EVENT_ID, **detail_changes: Any) -> None:
        """Send a start event; ``detail_changes`` replace detail fields (None removes one)."""
        detail: dict[str, Any] = {
            "modelName": MODEL,
            "exactVersion": VERSION,
            "senderEmail": SENDER,
            **detail_changes,
        }
        body = {
            "version": "0",
            "id": event_id,
            "source": "model.import",
            "detail-type": "ModelImportRequested",
            "account": "111122223333",
            "region": "us-east-1",
            "time": "2026-10-30T11:59:00Z",
            "detail": {name: value for name, value in detail.items() if value is not None},
        }
        self.sqs.send_message(
            QueueUrl=self.settings.queue_url,
            MessageBody=json.dumps(body),
            MessageGroupId="imports",
        )

    def run_without_hugging_face(self) -> int:
        """Run the worker with Hugging Face closed: any request to it fails the test."""
        with respx.mock(assert_all_called=False):
            return self.worker.run()

    def run_with_hugging_face(
        self, files: dict[str, bytes], *, during_download: Callable[[], None] | None = None
    ) -> int:
        listing = [
            {
                "type": "file",
                "path": path,
                "size": len(data),
                "lfs": {"oid": hashlib.sha256(data).hexdigest(), "size": len(data)},
            }
            for path, data in files.items()
        ]

        def respond(request: httpx.Request, data: bytes) -> httpx.Response:
            if during_download is not None:
                during_download()  # for example: the clock moves while a file is downloaded
            return httpx.Response(200, content=data)

        with respx.mock(assert_all_called=during_download is None) as router:
            router.get(f"{HF_BASE}/api/models/{MODEL}/tree/{VERSION}").mock(
                return_value=httpx.Response(200, json=listing)
            )
            for path, data in files.items():
                router.get(f"{HF_BASE}/{MODEL}/resolve/{VERSION}/{path}").mock(
                    side_effect=lambda request, data=data: respond(request, data)
                )
            return self.worker.run()

    def keys(self, bucket: str, prefix: str = "") -> list[str]:
        listing = self.s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        return sorted(item["Key"] for item in listing.get("Contents", []))

    def queue_is_empty(self) -> bool:
        attributes = self.sqs.get_queue_attributes(
            QueueUrl=self.settings.queue_url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        return (
            attributes["ApproximateNumberOfMessages"]
            == attributes["ApproximateNumberOfMessagesNotVisible"]
            == "0"
        )


@pytest.fixture
def flow(clock: Any, tmp_path: Path) -> Iterator[Flow]:
    with mock_aws():
        yield Flow(clock, tmp_path / "work")


def emails() -> list[Any]:
    return ses_backends[DEFAULT_ACCOUNT_ID]["us-east-1"].sent_messages


def test_br4_1_a_clean_small_model_goes_from_the_event_to_stored_with_its_marker(
    flow: Flow,
) -> None:
    flow.send_event()

    code = flow.run_with_hugging_face(CLEAN)

    import_id = derive_import_id(EVENT_ID)
    target = final_prefix(MODEL, VERSION)
    assert code == EXIT_OK
    outcome = flow.store.read_outcome(import_id)  # NFR4.1: one outcome object
    assert outcome is not None and outcome.outcome is Outcome.STORED
    assert outcome.removal_complete and outcome.failure_reason is None
    # BR4.1: the files and the marker, written last, are in the final location.
    assert flow.keys(FINAL) == sorted([target + path for path in CLEAN] + [target + "_COMPLETE"])
    marker = json.loads(flow.s3.get_object(Bucket=FINAL, Key=target + "_COMPLETE")["Body"].read())
    assert {item["path"] for item in marker["files"]} == set(CLEAN)
    assert flow.keys(HOLDING, "holding/") == []  # BR2.2: the holding area is empty
    assert emails() == []  # BR5.3: a stored model sends no email
    assert flow.queue_is_empty()  # the message was deleted
    assert list(flow.work_dir.rglob("*.part")) == []  # no half-written local file is left


def test_br5_1_a_model_with_a_planted_secret_is_removed_and_both_recipients_are_emailed(
    flow: Flow,
) -> None:
    flow.send_event()

    code = flow.run_with_hugging_face(LEAKY)

    import_id = derive_import_id(EVENT_ID)
    assert code == EXIT_OK
    outcome = flow.store.read_outcome(import_id)  # NFR4.1
    assert outcome is not None and outcome.outcome is Outcome.REMOVED_WITH_EMAIL
    assert outcome.removal_complete  # BR5.2: everything was removed
    assert outcome.failure_reason == "the scan found 1 finding in 1 file"
    assert flow.keys(FINAL, "models/") == []  # BR4.2: nothing was kept
    assert flow.keys(HOLDING, "holding/") == []  # BR5.2: the holding area is empty
    assert [m.destinations["ToAddresses"] for m in emails()] == [[SENDER], [TEAM]]  # BR5.3
    body = emails()[0].body
    assert "config.json: line 1, columns 10 to 29 (access-key)" in body
    everything = body + emails()[1].body + flow.stream.getvalue()
    assert SECRET not in everything and "AKIA" not in everything  # BR5.4: no value anywhere
    assert flow.queue_is_empty()


# -- u2-import-rules: the rules that finish the import (BR6.1 to BR9.2, BR10.1, BR11.1) ----------


def test_br6_1_a_rejected_event_downloads_nothing_emails_both_and_ends_as_rejected(
    flow: Flow,
) -> None:
    flow.send_event("e2e-bad-1", modelName="../" + SECRET, exactVersion=None)

    code = flow.run_without_hugging_face()  # any request to Hugging Face would fail the test

    import_id = derive_import_id("e2e-bad-1")
    assert code == EXIT_OK
    outcome = flow.store.read_outcome(import_id)  # BR10.1: one outcome object
    assert outcome is not None and outcome.outcome is Outcome.REJECTED
    assert outcome.email_sent is True and outcome.recipients_not_reached == ()
    assert outcome.failure_reason == (
        "the request was rejected; fields that failed: modelName, exactVersion"
    )
    assert flow.keys(FINAL) == [] and flow.keys(HOLDING, "holding/") == []  # nothing was written
    sent = emails()  # BR6.2: the sender's address is well formed, so both are told
    assert [m.destinations["ToAddresses"] for m in sent] == [[SENDER], [TEAM]]
    assert {m.subject for m in sent} == {"Model import rejected: event e2e-bad-1"}  # BR6.3
    assert "modelName" in sent[0].body and "exactVersion" in sent[0].body
    everything = sent[0].body + sent[1].body + flow.stream.getvalue()
    assert SECRET not in everything  # BR6.3: no value that failed is repeated anywhere
    record = flow.store.read_record(import_id)  # BR11.1: written once, after the outcome
    assert record is not None and record.status is ImportStatus.DONE
    assert record.outcome is Outcome.REJECTED
    assert (record.model_name, record.exact_version) == (None, None)
    assert flow.queue_is_empty()


def test_br6_2_a_rejected_event_with_a_bad_sender_address_tells_only_the_team(
    flow: Flow,
) -> None:
    flow.send_event("e2e-bad-2", senderEmail=BAD_SENDER)

    code = flow.run_without_hugging_face()

    outcome = flow.store.read_outcome(derive_import_id("e2e-bad-2"))
    assert code == EXIT_OK
    assert outcome is not None and outcome.outcome is Outcome.REJECTED
    assert [m.destinations["ToAddresses"] for m in emails()] == [[TEAM]]
    assert outcome.email_sent is True
    assert SECRET not in emails()[0].body + flow.stream.getvalue()
    record = flow.store.read_record(derive_import_id("e2e-bad-2"))
    assert record is not None and record.model_name == MODEL  # it passed, so it may be kept


def test_br7_1_a_second_event_for_a_stored_version_is_refused_and_changes_nothing(
    flow: Flow,
) -> None:
    flow.send_event("e2e-first")
    flow.run_with_hugging_face(CLEAN)
    target = final_prefix(MODEL, VERSION)
    keys_before = flow.keys(FINAL)
    marker_before = flow.s3.get_object(Bucket=FINAL, Key=target + "_COMPLETE")["Body"].read()
    assert emails() == []
    flow.send_event("e2e-second")

    code = flow.run_without_hugging_face()  # a refusal downloads nothing

    outcome = flow.store.read_outcome(derive_import_id("e2e-second"))
    assert code == EXIT_OK
    assert outcome is not None and outcome.outcome is Outcome.REFUSED_ALREADY_STORED
    assert outcome.failure_reason == REASON_ALREADY_STORED and outcome.email_sent is True
    assert [m.destinations["ToAddresses"] for m in emails()] == [[SENDER], [TEAM]]  # BR7.2
    assert flow.keys(FINAL) == keys_before  # the stored model is untouched
    assert flow.s3.get_object(Bucket=FINAL, Key=target + "_COMPLETE")["Body"].read() == (
        marker_before
    )
    assert flow.keys(HOLDING, "holding/") == []
    first = flow.store.read_outcome(derive_import_id("e2e-first"))
    assert first is not None and first.outcome is Outcome.STORED
    assert flow.queue_is_empty()


def test_br8_4_a_download_that_reaches_the_6_hour_limit_stops_and_removes_everything(
    flow: Flow,
) -> None:
    flow.send_event()
    passed: list[bool] = []

    def pass_the_time_limit() -> None:
        if not passed:  # once: 6 hours and a minute, still inside the worker's 7-hour limit
            passed.append(True)
            flow.clock.advance(6 * 3600 + 60)

    code = flow.run_with_hugging_face(CLEAN, during_download=pass_the_time_limit)

    outcome = flow.store.read_outcome(derive_import_id(EVENT_ID))
    assert code == EXIT_OK
    assert outcome is not None and outcome.outcome is Outcome.REMOVED_WITH_EMAIL
    assert outcome.failure_reason == REASON_TIME_LIMIT == "the time limit of 6 hours passed"
    assert outcome.removal_complete  # BR8.3: the removal is not cut short by the deadline
    assert flow.keys(FINAL) == [] and flow.keys(HOLDING, "holding/") == []
    assert [m.destinations["ToAddresses"] for m in emails()] == [[SENDER], [TEAM]]
    assert "the time limit of 6 hours passed" in emails()[0].body
    # NFR1.10: the outcome line carries the exact text the DeadlineStopSeconds filter matches.
    lines = [json.loads(line) for line in flow.stream.getvalue().splitlines()]
    (outcome_line,) = [item for item in lines if item["event"] == "import_outcome"]
    assert outcome_line["reason"] == REASON_TIME_LIMIT and outcome_line["totalSeconds"] > 6 * 3600
    assert flow.queue_is_empty()


def test_br9_2_a_failed_move_leaves_nothing_in_the_final_location_and_counts_older_files(
    flow: Flow,
) -> None:
    target = final_prefix(MODEL, VERSION)
    flow.s3.put_object(Bucket=FINAL, Key=target + "old-part.bin", Body=b"left by a crash")
    flow.data.fail_copy_of = "model.safetensors"  # the move breaks at one file
    flow.send_event()

    code = flow.run_with_hugging_face(CLEAN)

    outcome = flow.store.read_outcome(derive_import_id(EVENT_ID))
    assert code == EXIT_OK
    assert outcome is not None and outcome.outcome is Outcome.REMOVED_WITH_EMAIL
    assert outcome.failure_reason == "a file could not be copied to the final location"
    assert outcome.removal_complete  # BR9.3: the proof by listing found nothing left
    assert flow.keys(FINAL) == []  # not a file of the import, not the old debris, no marker
    assert flow.keys(HOLDING, "holding/") == []
    assert [m.destinations["ToAddresses"] for m in emails()] == [[SENDER], [TEAM]]
    assert "1 older unfinished file" in emails()[0].body  # BR9.5
    assert flow.queue_is_empty()
