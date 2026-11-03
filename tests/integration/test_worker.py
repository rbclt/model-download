"""Integration tests of the worker loop: BR1.1, NFR1.8, NFR4.1 and NFR4.2.

The worker runs on a moto FIFO queue (with a dead-letter queue and a max receive count of 1, as
in ``infrastructure-specification.md``) and moto S3. The import itself is a stand-in that writes
the outcome the way the coordinator does. Time is a fake clock moved by the fake wait and the fake
sleep, so no test waits for real. The rows of the message life cycle table in
``reliability-design.md`` are covered one by one.
"""

import io
import json
import threading
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from model_import.coordinator import ImportResult
from model_import.deadline import REASON_TIME_LIMIT, Deadline, DeadlineReached
from model_import.event import RejectedEvent, StartEvent
from model_import.logs import JsonLogger
from model_import.models import Outcome, OutcomeRecord, derive_import_id
from model_import.store import ImportStore
from model_import.worker import (
    EXIT_FATAL,
    EXIT_OK,
    EXIT_STOPPED,
    EXIT_WATCHDOG,
    ImportQueueWorker,
    StopSignal,
    WorkerConfig,
)

HOLDING = "holding-bucket"
VERSION = "a" * 40
PLANTED_SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"  # two parts: this file holds no whole secret
PLANTED_ADDRESS = "planted.person" + "@example.invalid"


def start_event(event_id: str = "evt-1") -> dict[str, Any]:
    return {
        "id": event_id,
        "source": "model.import",
        "detail-type": "ModelImportRequested",
        "detail": {
            "modelName": "Org/Model",
            "exactVersion": VERSION,
            "senderEmail": "person@example.com",
        },
    }


class RecordingSqs:
    """The moto SQS client, writing the calls the worker makes into one shared ``order`` list."""

    def __init__(self, client: Any, order: list[str]) -> None:
        self._client, self.order = client, order
        self.receives: list[dict[str, Any]] = []

    def receive_message(self, **arguments: Any) -> Any:
        self.order.append("receive")
        self.receives.append(arguments)
        return self._client.receive_message(**arguments)

    def change_message_visibility(self, **arguments: Any) -> Any:
        self.order.append(f"visibility:{arguments['VisibilityTimeout']}")
        return self._client.change_message_visibility(**arguments)

    def delete_message(self, **arguments: Any) -> Any:
        self.order.append("delete")
        return self._client.delete_message(**arguments)


class RecordingProtection:
    def __init__(self, order: list[str]) -> None:
        self._order = order

    def protect(self) -> None:
        self._order.append("protect")

    def release(self) -> None:
        self._order.append("release")


class FakeImport:
    """An import (or a rejection) that writes the single outcome (NFR4.1) once its gate is open,
    or fails. It is called as the worker calls ``run_import(event, deadline)`` and, for a
    rejected event, ``reject_import(event)``; every call is kept in ``events`` and ``deadlines``.
    """

    def __init__(
        self,
        store: ImportStore,
        order: list[str],
        *,
        block: bool = False,
        error: Exception | None = None,
        outcome: Outcome = Outcome.STORED,
        after_start: Callable[[Deadline | None], None] | None = None,
    ) -> None:
        self.gate, self.finished = threading.Event(), threading.Event()
        self.calls: list[str] = []
        self.events: list[StartEvent | RejectedEvent] = []
        self.deadlines: list[Deadline | None] = []
        self._store, self._order, self._error = store, order, error
        self._outcome, self._after_start = outcome, after_start
        if not block:
            self.gate.set()

    def __call__(
        self, event: StartEvent | RejectedEvent, deadline: Deadline | None = None
    ) -> ImportResult:
        self.calls.append(event.import_id)
        self.events.append(event)
        self.deadlines.append(deadline)
        try:
            if self._error is not None:
                raise self._error
            if self._after_start is not None:
                self._after_start(deadline)
            assert self.gate.wait(10), "the test never opened the gate"
            created = self._store.create_outcome(
                OutcomeRecord(event.import_id, self._outcome, True, datetime.now(UTC))
            )
            self._order.append("outcome")
            return ImportResult(event.import_id, self._outcome, None, True, 0, created)
        finally:
            self.finished.set()


class Timeline:
    """Fake wait and fake sleep: they move the fake clock and never wait for real."""

    def __init__(self, clock: Any) -> None:
        self.clock, self.ticks = clock, 0
        self.import_: FakeImport | None = None
        self.on_tick: Callable[[int], None] | None = None
        self.slept: list[float] = []

    def wait(self, event: threading.Event, seconds: float) -> bool:
        self.clock.advance(seconds)
        self.ticks += 1
        if self.ticks > 50:  # a test that never ends its import fails here instead of hanging
            raise TimeoutError("the supervised import did not end")
        if self.on_tick is not None:
            self.on_tick(self.ticks)
        if self.import_ is not None and self.import_.gate.is_set():
            return event.wait(5.0)  # the import is free to finish: let its thread end
        return event.is_set()

    def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)
        self.slept.append(seconds)


class Env:
    def __init__(self, clock: Any) -> None:
        self.s3 = boto3.client("s3", region_name="us-east-1")
        self.s3.create_bucket(Bucket=HOLDING)
        self.raw_sqs = boto3.client("sqs", region_name="us-east-1")
        dead = self.raw_sqs.create_queue(
            QueueName="import-dlq.fifo", Attributes={"FifoQueue": "true"}
        )["QueueUrl"]
        dead_arn = self.raw_sqs.get_queue_attributes(QueueUrl=dead, AttributeNames=["QueueArn"])[
            "Attributes"
        ]["QueueArn"]
        self.dead_url = dead
        self.queue_url = self.raw_sqs.create_queue(
            QueueName="import.fifo",
            Attributes={
                "FifoQueue": "true",
                "ContentBasedDeduplication": "true",
                "VisibilityTimeout": "900",
                "RedrivePolicy": json.dumps(
                    {"deadLetterTargetArn": dead_arn, "maxReceiveCount": 1}
                ),
            },
        )["QueueUrl"]
        self.order: list[str] = []
        self.sqs = RecordingSqs(self.raw_sqs, self.order)
        self.store = ImportStore(self.s3, HOLDING)
        self.clock = clock
        self.timeline = Timeline(clock)
        self.stream = io.StringIO()
        self.protection = RecordingProtection(self.order)
        self.stop = StopSignal()
        self.poll_seconds = 60.0

    def send(self, body: dict[str, Any] | str) -> None:
        text = body if isinstance(body, str) else json.dumps(body)
        self.raw_sqs.send_message(
            QueueUrl=self.queue_url,
            MessageBody=text,
            MessageGroupId="imports",
            MessageDeduplicationId=uuid.uuid4().hex,  # a resent event is a new message
        )

    def worker(self, import_: FakeImport, reject: FakeImport | None = None) -> ImportQueueWorker:
        self.timeline.import_ = reject if reject is not None else import_
        return ImportQueueWorker(
            self.sqs,
            self.store,
            import_,
            WorkerConfig(
                queue_url=self.queue_url,
                receive_wait_seconds=0,
                poll_seconds=self.poll_seconds,
                idle_pause_seconds=20,
            ),
            reject_import=reject,
            protection=self.protection,
            stop=self.stop,
            clock=self.clock,
            wait=self.timeline.wait,
            sleep=self.timeline.sleep,
            logger=JsonLogger(self.stream, clock=self.clock),
        )

    def count(self, url: str) -> tuple[int, int]:
        attributes = self.raw_sqs.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        return (
            int(attributes["ApproximateNumberOfMessages"]),
            int(attributes["ApproximateNumberOfMessagesNotVisible"]),
        )

    def logged(self, event: str) -> list[dict[str, Any]]:
        lines = [json.loads(line) for line in self.stream.getvalue().splitlines()]
        return [item for item in lines if item["event"] == event]


@pytest.fixture
def env(clock: Any) -> Iterator[Env]:
    with mock_aws():
        yield Env(clock)


def test_nfr4_1_the_message_is_deleted_right_after_the_outcome_and_the_worker_leaves_idle(
    env: Env,
) -> None:
    env.send(start_event())
    import_ = FakeImport(env.store, env.order)

    code = env.worker(import_).run()

    assert code == EXIT_OK
    assert len(import_.calls) == 1
    # Row "Outcome object written": the delete comes right after the outcome, nothing in between.
    assert env.order[env.order.index("outcome") + 1] == "delete"
    assert env.count(env.queue_url) == (0, 0)
    assert env.count(env.dead_url) == (0, 0)
    assert env.sqs.receives[0]["VisibilityTimeout"] == 900  # row "Hard crash": 15 minutes
    assert "visibility:900" not in env.order  # a quick import needs no heartbeat
    assert len(env.logged("message_deleted")) == 1


def test_nfr4_2_a_message_whose_outcome_exists_is_deleted_and_not_run_again(env: Env) -> None:
    env.send(start_event())
    first = FakeImport(env.store, env.order)
    env.worker(first).run()  # the import finishes; its outcome object exists
    env.send(start_event())  # the same event comes back (a crash before the delete)
    again = FakeImport(env.store, env.order)
    env.order.clear()

    code = env.worker(again).run()

    assert code == EXIT_OK
    assert again.calls == []  # row "Message received": no second import
    assert env.order.count("delete") == 1 and "outcome" not in env.order
    assert env.count(env.queue_url) == (0, 0)
    assert len(env.logged("message_already_finished")) == 1


def test_br6_4_a_message_that_is_not_a_start_event_is_deleted_at_once_and_only_the_field_is_logged(
    env: Env,
) -> None:
    # Changed from u1 (BR6.1, BR6.4): a start event with bad content is now rejected with an
    # outcome and an email, so this test sends only messages that are not start events.
    wrong_source = start_event("evt-bad")
    wrong_source["source"] = "secret-value.source"
    env.send(wrong_source)
    env.send("this is not json at all")
    import_, reject = FakeImport(env.store, env.order), FakeImport(env.store, env.order)

    code = env.worker(import_, reject).run()

    assert code == EXIT_OK
    assert import_.calls == [] and reject.calls == []  # row "Event fails the check": nothing starts
    assert env.order.count("delete") == 2 and "outcome" not in env.order
    assert env.count(env.queue_url) == (0, 0) and env.count(env.dead_url) == (0, 0)
    reasons = [item["reason"] for item in env.logged("event_rejected")]
    assert reasons == [
        "the event field source is missing or not valid",
        "the event field body is missing or not valid",
    ]
    assert "secret-value" not in env.stream.getvalue()


def test_nfr1_8_the_heartbeat_renews_visibility_and_protection_every_5_minutes(env: Env) -> None:
    env.send(start_event())
    import_ = FakeImport(env.store, env.order, block=True)
    env.timeline.on_tick = lambda tick: import_.gate.set() if tick == 11 else None

    code = env.worker(import_).run()

    assert code == EXIT_OK
    first = env.order.index("receive")
    during = env.order[first + 1 : env.order.index("delete")]
    # Ticks of 60 s: renewals at 300 s and 600 s, each the visibility reset and the protection.
    assert during.count("visibility:900") == 2
    assert during.count("protect") == 2
    after_delete = env.order[env.order.index("delete") + 1 :]
    assert "visibility:900" not in after_delete  # the heartbeat ended with the import
    assert env.timeline.ticks == 11


def test_nfr1_8_the_7_hour_watchdog_sets_visibility_0_first_then_exits_non_zero(env: Env) -> None:
    env.send(start_event())
    import_ = FakeImport(env.store, env.order, block=True)
    env.poll_seconds = 3600.0
    worker = env.worker(import_)

    code = worker.run()

    assert code == EXIT_WATCHDOG and code != 0
    assert env.timeline.ticks == 7  # 7 x 3,600 s: not before 7 hours
    assert env.order[-1] == "visibility:0"  # the last thing done is the release of the message
    assert "delete" not in env.order and "outcome" not in env.order
    assert worker.import_abandoned  # the stuck import thread is left behind for the exit
    assert env.logged("import_abandoned")[0]["reason"] == "the 7-hour limit was reached"
    import_.gate.set()  # let the stand-in thread end
    assert import_.finished.wait(5)


def test_nfr4_2_a_fatal_error_sets_visibility_0_exits_non_zero_and_the_queue_moves_it_on(
    env: Env,
) -> None:
    env.send(start_event())
    import_ = FakeImport(env.store, env.order, error=RuntimeError("boom with detail"))

    code = env.worker(import_).run()

    assert code == EXIT_FATAL and code != 0
    assert env.order[-1] == "visibility:0" and "delete" not in env.order
    entry = env.logged("import_abandoned")[0]
    assert entry["error"] == "RuntimeError" and "boom" not in env.stream.getvalue()
    # The next receive finds the message again; with max receive count 1 it goes to the DLQ.
    assert env.raw_sqs.receive_message(QueueUrl=env.queue_url).get("Messages") is None
    assert env.count(env.dead_url)[0] == 1


def test_nfr4_2_sigterm_during_an_import_sets_visibility_0_and_exits_non_zero(env: Env) -> None:
    env.send(start_event())
    import_ = FakeImport(env.store, env.order, block=True)
    env.timeline.on_tick = lambda tick: env.stop.request() if tick == 2 else None
    worker = env.worker(import_)

    code = worker.run()

    assert code == EXIT_STOPPED and code != 0
    assert env.order[-1] == "visibility:0" and "delete" not in env.order
    assert env.timeline.ticks == 2
    import_.gate.set()
    assert import_.finished.wait(5)


def test_nfr4_2_sigterm_while_idle_exits_zero_without_taking_a_message(env: Env) -> None:
    env.send(start_event())
    env.stop.request()
    import_ = FakeImport(env.store, env.order)

    code = env.worker(import_).run()

    assert code == EXIT_OK
    assert "receive" not in env.order and import_.calls == []
    assert env.order == ["release"]  # protection is let go on the way out
    assert env.count(env.queue_url) == (1, 0)  # the waiting message stays for the next worker


def test_nfr1_8_protection_is_set_before_each_receive_and_released_after_2_empty_minutes(
    env: Env,
) -> None:
    import_ = FakeImport(env.store, env.order)

    code = env.worker(import_).run()

    assert code == EXIT_OK
    assert env.order == ["protect", "receive"] * 7 + ["release"]
    # Empty receives 20 s apart: the 7th starts at 120 s, the first moment the queue has been
    # empty for 2 minutes. The program then exits 0.
    assert env.timeline.slept == [20.0] * 6
    assert import_.calls == []


# -- u2-import-rules: the rejected event, the deadline, and the routing (BR6.1, BR6.4, BR8.1) -----


def rejected_event(event_id: str = "evt-rej") -> dict[str, Any]:
    """A start event whose model name fails the check; the sender and the version pass."""
    body = start_event(event_id)
    body["detail"]["modelName"] = "../" + PLANTED_SECRET
    body["detail"]["senderEmail"] = PLANTED_ADDRESS
    return body


def test_br6_1_a_rejected_event_runs_the_reject_path_and_its_message_goes_after_the_outcome(
    env: Env,
) -> None:
    env.send(rejected_event())
    import_ = FakeImport(env.store, env.order)
    reject = FakeImport(env.store, env.order, outcome=Outcome.REJECTED)

    code = env.worker(import_, reject).run()

    import_id = derive_import_id("evt-rej")
    assert code == EXIT_OK
    assert import_.calls == [] and reject.calls == [import_id]  # nothing is imported
    assert isinstance(reject.events[0], RejectedEvent)
    assert reject.events[0].rejected_fields == ("modelName",)
    assert reject.deadlines == [None]  # a rejection downloads nothing, so it has no deadline
    assert env.order[env.order.index("outcome") + 1] == "delete"  # NFR4.1, as for an import
    outcome = env.store.read_outcome(import_id)
    assert outcome is not None and outcome.outcome is Outcome.REJECTED
    assert env.count(env.queue_url) == (0, 0) and env.count(env.dead_url) == (0, 0)


def test_br6_3_the_log_of_a_rejected_event_holds_the_ids_and_only_the_fields_that_passed(
    env: Env,
) -> None:
    env.send(rejected_event())
    reject = FakeImport(env.store, env.order, outcome=Outcome.REJECTED)

    env.worker(FakeImport(env.store, env.order), reject).run()

    received = env.logged("import_received")[0]
    assert received["importId"] == derive_import_id("evt-rej")
    assert received["eventId"] == "evt-rej"
    assert received["exactVersion"] == VERSION  # it passed the check
    assert "modelName" not in received  # it failed, so it is not bound
    text = env.stream.getvalue()
    assert PLANTED_SECRET not in text and PLANTED_ADDRESS not in text


def test_br10_1_a_rejected_message_received_again_after_its_outcome_does_nothing(
    env: Env,
) -> None:
    env.send(rejected_event())
    first = FakeImport(env.store, env.order, outcome=Outcome.REJECTED)
    env.worker(FakeImport(env.store, env.order), first).run()
    env.send(rejected_event())  # the same event again (a crash before the delete)
    again = FakeImport(env.store, env.order, outcome=Outcome.REJECTED)
    env.order.clear()

    code = env.worker(FakeImport(env.store, env.order), again).run()

    assert code == EXIT_OK
    assert again.calls == []  # the rejection is not run twice, so no second email
    assert env.order.count("delete") == 1 and "outcome" not in env.order
    assert len(env.logged("message_already_finished")) == 1
    assert env.count(env.queue_url) == (0, 0)


def test_nfr1_8_a_rejection_runs_under_the_same_7_hour_watchdog_as_an_import(env: Env) -> None:
    env.send(rejected_event())
    reject = FakeImport(env.store, env.order, block=True, outcome=Outcome.REJECTED)
    env.poll_seconds = 3600.0
    worker = env.worker(FakeImport(env.store, env.order), reject)

    code = worker.run()

    assert code == EXIT_WATCHDOG
    assert env.timeline.ticks == 7
    assert env.order[-1] == "visibility:0" and "delete" not in env.order
    assert env.logged("import_abandoned")[0]["reason"] == "the 7-hour limit was reached"
    reject.gate.set()
    assert reject.finished.wait(5)


def test_nfr4_2_a_rejection_that_fails_on_an_error_is_handed_back_like_a_failed_import(
    env: Env,
) -> None:
    env.send(rejected_event())
    reject = FakeImport(env.store, env.order, error=RuntimeError("boom " + PLANTED_SECRET))

    code = env.worker(FakeImport(env.store, env.order), reject).run()

    assert code == EXIT_FATAL
    assert env.order[-1] == "visibility:0" and "delete" not in env.order
    assert env.logged("import_abandoned")[0]["error"] == "RuntimeError"
    assert PLANTED_SECRET not in env.stream.getvalue()
    # The next receive finds the message again; with max receive count 1 it goes to the DLQ.
    assert env.raw_sqs.receive_message(QueueUrl=env.queue_url).get("Messages") is None
    assert env.count(env.dead_url)[0] == 1


def test_br6_1_without_a_reject_function_the_message_is_handed_back_not_dropped(
    env: Env,
) -> None:
    env.send(rejected_event())
    import_ = FakeImport(env.store, env.order)

    code = env.worker(import_).run()  # no reject function was given to the worker

    assert code == EXIT_FATAL
    assert import_.calls == []
    assert "delete" not in env.order and env.order[-1] == "visibility:0"
    assert env.logged("import_abandoned")[0]["reason"] == ("no function is set to reject an event")


def test_br8_1_the_deadline_is_made_when_the_event_is_taken_off_the_queue_not_when_it_was_sent(
    env: Env,
) -> None:
    env.send(start_event())
    env.clock.advance(3 * 3600)  # the event waited 3 hours in the queue
    taken = env.clock.current
    import_ = FakeImport(env.store, env.order)

    code = env.worker(import_).run()

    assert code == EXIT_OK
    deadline = import_.deadlines[0]
    assert isinstance(deadline, Deadline)
    assert deadline.at == taken + timedelta(hours=6)  # the wait in the queue did not count
    assert env.logged("import_received")[0]["importId"] == derive_import_id("evt-1")


def test_nfr1_1_an_import_that_runs_past_its_deadline_is_ended_by_the_import_not_the_worker(
    env: Env,
) -> None:
    env.send(start_event())

    def pass_the_deadline(deadline: Deadline | None) -> None:
        # The import notices the deadline itself (BR8.2) and ends with its outcome; the worker's
        # 7-hour watchdog is one hour later and must not fire.
        assert deadline is not None
        env.clock.advance(6 * 3600 + 120)
        with pytest.raises(DeadlineReached) as reached:
            deadline.check()
        assert reached.value.reason == REASON_TIME_LIMIT

    import_ = FakeImport(
        env.store, env.order, outcome=Outcome.REMOVED_WITH_EMAIL, after_start=pass_the_deadline
    )

    code = env.worker(import_).run()

    assert code == EXIT_OK
    assert env.order[env.order.index("outcome") + 1] == "delete"
    assert env.logged("import_abandoned") == []
    assert env.count(env.queue_url) == (0, 0) and env.count(env.dead_url) == (0, 0)


def test_nfr3_19_an_event_id_of_an_unexpected_shape_is_replaced_by_the_import_id_in_the_log(
    env: Env,
) -> None:
    unsafe = "evt unsafe " + PLANTED_SECRET  # a space is not in the safe pattern
    env.send(start_event(unsafe))
    import_ = FakeImport(env.store, env.order)

    env.worker(import_).run()

    import_id = derive_import_id(unsafe)
    received = env.logged("import_received")[0]
    assert received["importId"] == import_id and received["eventId"] == import_id
    assert PLANTED_SECRET not in env.stream.getvalue()


def test_br1_1_a_start_event_log_holds_the_ids_the_model_and_the_version_but_no_address(
    env: Env,
) -> None:
    env.send(start_event("evt-ok.1:a"))
    import_ = FakeImport(env.store, env.order)

    env.worker(import_).run()

    received = env.logged("import_received")[0]
    assert received["eventId"] == "evt-ok.1:a"
    assert received["modelName"] == "Org/Model" and received["exactVersion"] == VERSION
    assert "person@example.com" not in env.stream.getvalue()
    assert isinstance(import_.events[0], StartEvent)
