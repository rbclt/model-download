"""Tests for the helpers of worker.py: task protection, the stop signal, and the entry point.

NFR4.2 and NFR1.8 are covered by the loop tests in ``tests/integration/test_worker.py``; these
tests cover the parts that need no queue. No test calls AWS or the ECS metadata address.
"""

import io
import json
import signal
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from model_import.deadline import Deadline
from model_import.event import RejectedEvent, StartEvent, classify_message
from model_import.logs import JsonLogger
from model_import.models import Outcome, derive_import_id, final_prefix
from model_import.settings import Settings
from model_import.store import ImportStore
from model_import.worker import (
    ENV_ECS_METADATA,
    EXIT_FATAL,
    EXIT_SETTINGS,
    MAIL_CLIENT_ATTEMPTS,
    STATE_CLIENT_ATTEMPTS,
    EcsTaskProtection,
    ImportQueueWorker,
    StopSignal,
    TaskIdentity,
    WorkerConfig,
    _NoDownloads,
    build_clients,
    build_import_runner,
    main,
    read_task_identity,
)

IDENTITY = TaskIdentity("cluster-1", "arn:aws:ecs:us-east-1:111122223333:task/cluster-1/abc")


class StubEcs:
    def __init__(self, error: Exception | None = None, failures: list[Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._error, self._failures = error, failures or []

    def update_task_protection(self, **arguments: Any) -> dict[str, Any]:
        self.calls.append(arguments)
        if self._error is not None:
            raise self._error
        return {"failures": self._failures}


def logged(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_nfr1_8_task_protection_is_set_for_8_hours_and_released_for_the_task_of_this_cluster() -> (
    None
):
    ecs, stream = StubEcs(), io.StringIO()
    fetches: list[int] = []
    protection = EcsTaskProtection(ecs, lambda: fetches.append(1) or IDENTITY, JsonLogger(stream))

    protection.protect()
    protection.protect()
    protection.release()

    assert ecs.calls[0] == {
        "cluster": "cluster-1",
        "tasks": [IDENTITY.task_arn],
        "protectionEnabled": True,
        "expiresInMinutes": 480,
    }
    assert ecs.calls[2] == {
        "cluster": "cluster-1",
        "tasks": [IDENTITY.task_arn],
        "protectionEnabled": False,
    }
    assert len(fetches) == 1  # the task identity is read once


def test_nfr4_2_a_protection_error_is_logged_with_its_code_and_never_stops_the_import() -> None:
    stream = io.StringIO()
    failing = StubEcs(error=ClientError({"Error": {"Code": "AccessDeniedException"}}, "Update"))
    refused = StubEcs(failures=[{"reason": "MISSING"}])

    EcsTaskProtection(failing, lambda: IDENTITY, JsonLogger(stream)).protect()
    EcsTaskProtection(refused, lambda: IDENTITY, JsonLogger(stream)).protect()

    events = logged(stream)
    assert [item["event"] for item in events] == ["task_protection_failed"] * 2
    assert events[0]["error"] == "AccessDeniedException"


def test_nfr1_8_outside_ecs_there_is_no_task_to_protect_and_inside_it_is_read_from_metadata() -> (
    None
):
    urls: list[str] = []

    def get_json(url: str) -> dict[str, str]:
        urls.append(url)
        return {"Cluster": "cluster-1", "TaskARN": IDENTITY.task_arn}

    assert read_task_identity({}, get_json) is None
    found = read_task_identity({ENV_ECS_METADATA: "http://169.254.170.2/v4/abc/"}, get_json)

    assert found == IDENTITY
    assert urls == ["http://169.254.170.2/v4/abc/task"]
    assert EcsTaskProtection(StubEcs(), lambda: None, JsonLogger(io.StringIO())).protect() is None


def test_nfr4_2_the_stop_signal_is_a_flag_set_by_sigterm(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers: dict[int, Any] = {}
    monkeypatch.setattr(
        signal, "signal", lambda number, handler: handlers.update({number: handler})
    )
    stop = StopSignal()

    stop.install()
    assert stop.requested is False
    handlers[signal.SIGTERM](signal.SIGTERM, None)

    assert stop.requested is True
    assert set(handlers) == {signal.SIGTERM, signal.SIGINT}


def test_nfr4_2_five_failed_receives_in_a_row_end_the_worker_with_a_non_zero_code() -> None:
    sleeps: list[float] = []

    class DownQueue:
        def receive_message(self, **arguments: Any) -> Any:
            raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "ReceiveMessage")

    stream = io.StringIO()
    worker = ImportQueueWorker(
        DownQueue(),
        ImportStore(None, "bucket"),
        lambda event, deadline: pytest.fail("no import should start"),
        WorkerConfig(queue_url="q", receive_wait_seconds=0),
        sleep=sleeps.append,
        logger=JsonLogger(stream),
    )

    code = worker.run()

    assert code == EXIT_FATAL
    assert sleeps == [10.0] * 4  # a pause after each failed receive except the last
    assert [item["error"] for item in logged(stream) if item["event"] == "receive_failed"] == [
        "ServiceUnavailable"
    ] * 5


def test_nfr3_2_the_entry_point_names_missing_settings_and_exits_2_without_starting() -> None:
    code = main({"HF_TOKEN": "ignored-on-purpose"})  # no setting at all; HF_TOKEN is never read

    assert code == EXIT_SETTINGS


# -- the three clients (u2-import-rules, NFR1.11, NFR1.12, performance-design.md) -----------------


def test_nfr1_11_the_state_and_mail_clients_fail_fast_and_the_data_client_keeps_the_defaults() -> (
    None
):
    session = boto3.Session(region_name="us-east-1")

    state, data, mail = build_clients(session)

    assert (state.meta.config.connect_timeout, state.meta.config.read_timeout) == (5, 10)
    assert (mail.meta.config.connect_timeout, mail.meta.config.read_timeout) == (5, 10)
    assert state.meta.config.retries["mode"] == "standard"
    assert mail.meta.config.retries["mode"] == "standard"
    assert state.meta.config.retries["total_max_attempts"] == STATE_CLIENT_ATTEMPTS == 3
    assert mail.meta.config.retries["total_max_attempts"] == MAIL_CLIENT_ATTEMPTS == 2
    assert (data.meta.config.connect_timeout, data.meta.config.read_timeout) == (60, 60)
    assert data is not state  # the data client is a client of its own
    assert (state.meta.service_model.service_name, data.meta.service_model.service_name) == (
        "s3",
        "s3",
    )
    assert mail.meta.service_model.service_name == "ses"


# -- the two runners (u2-import-rules, BR6.1, BR7.1, NFR1.11) ----------------------------------


class RecordingS3:
    """A moto S3 client that writes the name of every call into ``calls``."""

    def __init__(self, client: Any, calls: list[str]) -> None:
        self._client, self._calls = client, calls

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._client, name)
        if not callable(target):
            return target

        def call(*args: Any, **kwargs: Any) -> Any:
            self._calls.append(name)
            return target(*args, **kwargs)

        return call


def test_nfr1_11_the_runner_gives_the_state_client_to_the_store_and_the_marker_reader() -> None:
    event_id = "runner-event-1"
    version = "c" * 40
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="holding-bucket")
        s3.create_bucket(Bucket="final-bucket")
        boto3.client("ses", region_name="us-east-1").verify_email_identity(
            EmailAddress="imports@example.com"
        )
        # A marker of another import: the front door refuses, so no download is needed.
        s3.put_object(
            Bucket="final-bucket",
            Key=final_prefix("Org/Model", version) + "_COMPLETE",
            Body=json.dumps({"importId": derive_import_id("another-event")}).encode(),
        )
        state_calls: list[str] = []
        data_calls: list[str] = []
        runners = build_import_runner(
            Settings("holding-bucket", "final-bucket", "queue", "imports@example.com", "t@e.com"),
            RecordingS3(s3, data_calls),
            boto3.client("ses", region_name="us-east-1"),
            logger=JsonLogger(io.StringIO()),
            hf_factory=lambda: nullcontext(object()),  # type: ignore[arg-type,return-value]
            state_client=RecordingS3(s3, state_calls),
        )
        event = classify_message(
            {
                "id": event_id,
                "source": "model.import",
                "detail-type": "ModelImportRequested",
                "detail": {
                    "modelName": "Org/Model",
                    "exactVersion": version,
                    "senderEmail": "person@example.com",
                },
            }
        )
        assert isinstance(event, StartEvent)

        result = runners.run_import(event, Deadline(datetime.now(UTC)))

    assert result.outcome is Outcome.REFUSED_ALREADY_STORED
    assert "head_object" in state_calls  # the marker reader used the state client
    assert "put_object" in state_calls  # the store (outcome, record, counters) used it too
    assert data_calls == []  # nothing was downloaded, copied, listed, or deleted


def test_br6_1_the_reject_runner_never_makes_a_hugging_face_client_and_ends_as_rejected() -> None:
    def no_hugging_face() -> Any:
        raise AssertionError("a rejection must not make a Hugging Face client")

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="holding-bucket")
        boto3.client("ses", region_name="us-east-1").verify_email_identity(
            EmailAddress="imports@example.com"
        )
        runners = build_import_runner(
            Settings("holding-bucket", "final-bucket", "queue", "imports@example.com", "t@e.com"),
            s3,
            boto3.client("ses", region_name="us-east-1"),
            logger=JsonLogger(io.StringIO()),
            hf_factory=no_hugging_face,
        )
        event = classify_message(
            {
                "id": "runner-event-2",
                "source": "model.import",
                "detail-type": "ModelImportRequested",
                "detail": {"modelName": "bad", "exactVersion": "main"},
            }
        )
        assert isinstance(event, RejectedEvent)

        result = runners.reject_import(event)

    assert result.outcome is Outcome.REJECTED


def test_br6_1_the_downloader_of_a_rejection_is_a_guard_that_stops_any_call() -> None:
    with pytest.raises(RuntimeError, match="never downloads"):
        _NoDownloads().download("import", "Org/Model", "a" * 40)
