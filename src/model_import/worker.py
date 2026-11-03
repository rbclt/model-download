"""The queue worker: takes one message, runs one import, then asks for the next.

This is the "API / endpoint" layer of the unit. It has no HTTP endpoint: its boundary is the
import queue and the start event of contract C1. The message life cycle follows the table in
``reliability-design.md`` and the scale-in rules follow ``scalability-design.md``:

* Receive one message (long poll) with a visibility time of 15 minutes. Classify it first
  (``classify_message``, BR1.1, BR6.1, BR6.4): a start event and a start event with bad content
  both run under the same supervision (``_process``), with the coordinator's ``run`` or
  ``reject`` as the work to do. A message that is not a start event is logged by the name of the
  envelope field only and its message is deleted at once, with no email and no outcome.
* A message whose ``state/<importId>/outcome.json`` already exists is a finished import
  (for example a crash between the outcome and the delete): delete it, log it, take the next.
* The 6-hour ``Deadline`` of a start event is made when the message is taken off the queue
  (BR8.1), so the wait in the queue never counts.
* While an import runs, the main thread supervises it: every 5 minutes it sets the message
  visibility to 15 minutes again (the heartbeat), renews the ECS task protection, and checks the
  7-hour limit (NFR1.8) and the stop request.
* The message is deleted right after the outcome is written (NFR4.1), and the heartbeat ends
  with the loop that ran it.
* A caught fatal error, SIGTERM during an import, or the 7-hour limit sets the visibility to 0
  first, then ends the program with a non-zero exit code. The next receive moves the message to
  the dead-letter queue (max receive count 1) and the team is alerted (NFR4.2).
* SIGTERM while idle ends the program with exit code 0.
* ECS task protection (8-hour expiry) is set before each receive, renewed every 5 minutes during
  an import, and released after the queue has been empty for 2 minutes. Then the program exits
  with code 0.

The clock, the waiting, the sleep, SQS, ECS and the stop signal are all injectable, so no test
sleeps for real.
"""

from __future__ import annotations

import os
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from types import FrameType
from typing import Any, NamedTuple, Protocol

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from model_import.coordinator import Downloading, ImportCoordinator, ImportResult
from model_import.custodian import FileCustodian
from model_import.deadline import Deadline
from model_import.downloader import ModelDownloader
from model_import.event import NotAStartEvent, RejectedEvent, StartEvent, classify_message
from model_import.hf_client import HfClient
from model_import.logs import JsonLogger, event_reference
from model_import.models import ModelFileSet
from model_import.notifier import FailureNotifier
from model_import.scanner import LeakScanner
from model_import.settings import Settings, SettingsError
from model_import.store import ImportStore

EXIT_OK = 0
EXIT_FATAL = 1  # a caught fatal error
EXIT_SETTINGS = 2  # the environment is missing a setting
EXIT_WATCHDOG = 3  # the 7-hour limit (NFR1.8)
EXIT_STOPPED = 4  # SIGTERM during an import

VISIBILITY_SECONDS = 15 * 60
HEARTBEAT_SECONDS = 5 * 60
HEARTBEAT_RETRY_SECONDS = 30
WATCHDOG_SECONDS = 7 * 60 * 60
PROTECTION_EXPIRY_MINUTES = 8 * 60
PROTECTION_RENEW_SECONDS = 5 * 60
IDLE_EXIT_SECONDS = 2 * 60
RECEIVE_WAIT_SECONDS = 20
DELETE_TRIES = 3

ENV_ECS_METADATA = "ECS_CONTAINER_METADATA_URI_V4"


def _code(error: BaseException) -> str:
    """A short error code for the logs (NFR5.5): never a message from a library."""
    if isinstance(error, ClientError):
        return str(error.response.get("Error", {}).get("Code", "unknown"))
    return type(error).__name__


def _log_fields(event: StartEvent | RejectedEvent) -> dict[str, object]:
    """NFR3.19, BR6.3: the fields every record of one message carries.

    The importId always; the eventId only in its safe form (``event_reference`` gives the
    importId for an id of an unexpected shape, which the logger then shows as it is); the model
    name and version only when they passed the check. Nothing else of the event: never the
    sender address, never a value that failed.
    """
    fields: dict[str, object] = {
        "importId": event.import_id,
        "eventId": event_reference(event.event_id, event.import_id),
    }
    if event.model_name is not None:
        fields["modelName"] = event.model_name
    if event.exact_version is not None:
        fields["exactVersion"] = event.exact_version
    return fields


# -- stop signal --------------------------------------------------------------------------------


class StopSource(Protocol):
    @property
    def requested(self) -> bool: ...


class StopSignal:
    """Turns SIGTERM (and Ctrl+C) into a flag the loop reads. Tests call ``request`` instead."""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def request(self) -> None:
        self._event.set()

    def install(self) -> None:
        """Register the handlers. Only the main thread of the program may call this."""
        signal.signal(signal.SIGTERM, self._handle)
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, signum: int, frame: FrameType | None) -> None:
        self.request()


# -- ECS task protection (scalability-design.md) ---------------------------------------------------


class TaskProtection(Protocol):
    def protect(self) -> None: ...

    def release(self) -> None: ...


class NullTaskProtection:
    """Used outside ECS (a local run): there is no task to protect."""

    def protect(self) -> None:
        return None

    def release(self) -> None:
        return None


@dataclass(frozen=True)
class TaskIdentity:
    cluster: str
    task_arn: str


def _get_json(url: str) -> Any:
    response = httpx.get(url, timeout=2.0, trust_env=False)
    response.raise_for_status()
    return response.json()


def read_task_identity(
    environ: Mapping[str, str], get_json: Callable[[str], Any] = _get_json
) -> TaskIdentity | None:
    """Read the cluster and task ARN from the ECS task metadata endpoint; None outside ECS."""
    base = environ.get(ENV_ECS_METADATA, "").strip()
    if not base:
        return None
    data = get_json(base.rstrip("/") + "/task")
    return TaskIdentity(cluster=str(data["Cluster"]), task_arn=str(data["TaskARN"]))


class EcsTaskProtection:
    """Sets and releases ECS task scale-in protection with ``UpdateTaskProtection``.

    A failed call is logged and never raised: the import must not stop because protection could
    not be set. The design then relies on the heartbeat and the dead-letter queue (NFR4.2).
    """

    def __init__(
        self,
        ecs_client: Any,
        identity: Callable[[], TaskIdentity | None],
        logger: JsonLogger,
        *,
        expires_minutes: int = PROTECTION_EXPIRY_MINUTES,
    ) -> None:
        self._ecs = ecs_client
        self._identity = identity
        self._log = logger
        self._expires = expires_minutes
        self._known: TaskIdentity | None = None

    def protect(self) -> None:
        self._set(True)

    def release(self) -> None:
        self._set(False)

    def _set(self, enabled: bool) -> None:
        try:
            if self._known is None:
                self._known = self._identity()
            if self._known is None:
                return
            arguments: dict[str, Any] = {
                "cluster": self._known.cluster,
                "tasks": [self._known.task_arn],
                "protectionEnabled": enabled,
            }
            if enabled:
                arguments["expiresInMinutes"] = self._expires
            response = self._ecs.update_task_protection(**arguments)
        except (ClientError, BotoCoreError, httpx.HTTPError, KeyError, ValueError) as error:
            self._log.warning(
                "task_protection_failed",
                step="worker",
                reason="the task protection could not be changed",
                error=_code(error),
            )
            return
        if response.get("failures"):
            self._log.warning(
                "task_protection_failed",
                step="worker",
                reason="ECS did not change the task protection",
            )


# -- the worker ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerConfig:
    queue_url: str
    receive_wait_seconds: int = RECEIVE_WAIT_SECONDS
    visibility_seconds: int = VISIBILITY_SECONDS
    heartbeat_seconds: float = HEARTBEAT_SECONDS
    watchdog_seconds: float = WATCHDOG_SECONDS
    protection_renew_seconds: float = PROTECTION_RENEW_SECONDS
    idle_exit_seconds: float = IDLE_EXIT_SECONDS
    poll_seconds: float = 1.0  # how often the supervisor looks at a running import
    idle_pause_seconds: float = 0.0  # pause after an empty receive (the long poll is the wait)
    error_pause_seconds: float = 10.0  # pause after a failed receive
    max_receive_errors: int = 5


def _wait_for_event(event: threading.Event, seconds: float) -> bool:
    return event.wait(seconds)


class _ImportRun:
    """One import on its own thread, so the main thread can keep the heartbeat and the limit."""

    def __init__(self, work: Callable[[], ImportResult]) -> None:
        self.done = threading.Event()
        self.result: ImportResult | None = None
        self.error: BaseException | None = None
        self._work = work
        # A daemon thread: when the watchdog ends the program, a stuck import cannot hold it.
        self.thread = threading.Thread(target=self._target, name="import", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _target(self) -> None:
        try:
            self.result = self._work()
        except Exception as error:
            self.error = error
        finally:
            self.done.set()


class ImportQueueWorker:
    """BR1.1, NFR1.8, NFR4.2: the queue loop of ``reliability-design.md`` (Message Life Cycle)."""

    def __init__(
        self,
        sqs_client: Any,
        store: ImportStore,
        run_import: Callable[[StartEvent, Deadline], ImportResult],
        config: WorkerConfig,
        *,
        reject_import: Callable[[RejectedEvent], ImportResult] | None = None,
        protection: TaskProtection | None = None,
        stop: StopSource | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        wait: Callable[[threading.Event, float], bool] = _wait_for_event,
        sleep: Callable[[float], None] = time.sleep,
        logger: JsonLogger | None = None,
    ) -> None:
        self._sqs = sqs_client
        self._store = store
        self._run_import = run_import
        self._reject_import = reject_import
        self._config = config
        self._protection: TaskProtection = protection or NullTaskProtection()
        self._stop: StopSource = stop or StopSignal()
        self._clock = clock
        self._wait = wait
        self._sleep = sleep
        self._log = logger or JsonLogger(clock=clock)
        self._active: _ImportRun | None = None

    @property
    def import_abandoned(self) -> bool:
        """True when the program is ending while an import thread is still running."""
        return self._active is not None and self._active.thread.is_alive()

    # -- the loop ---------------------------------------------------------------------------

    def run(self) -> int:
        """Run until the queue has been empty for 2 minutes, or a stop or fatal error. Exit code."""
        self._log.info("worker_started", step="worker")
        try:
            return self._loop()
        except Exception as error:
            self._log.error("worker_failed", step="worker", error=_code(error))
            return EXIT_FATAL

    def _loop(self) -> int:
        idle_since = self._seconds()
        receive_errors = 0
        while True:
            if self._stop.requested:  # SIGTERM while idle: a clean exit
                return self._leave_idle("stopped while idle")
            self._protection.protect()  # before each receive (scalability-design.md)
            try:
                message = self._receive()
            except (ClientError, BotoCoreError) as error:
                receive_errors += 1
                self._log.warning("receive_failed", step="worker", error=_code(error))
                if receive_errors >= self._config.max_receive_errors:
                    return EXIT_FATAL
                self._sleep(self._config.error_pause_seconds)
                continue
            receive_errors = 0
            if message is None:
                if self._seconds() - idle_since >= self._config.idle_exit_seconds:
                    return self._leave_idle("the queue has been empty for 2 minutes")
                if self._config.idle_pause_seconds > 0:
                    self._sleep(self._config.idle_pause_seconds)
                continue
            exit_code = self._handle(message)
            if exit_code is not None:
                return exit_code
            idle_since = self._seconds()

    def _leave_idle(self, reason: str) -> int:
        self._protection.release()
        self._log.info("worker_stopped", step="worker", reason=reason)
        return EXIT_OK

    def _receive(self) -> dict[str, Any] | None:
        response = self._sqs.receive_message(
            QueueUrl=self._config.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=self._config.receive_wait_seconds,
            VisibilityTimeout=self._config.visibility_seconds,
        )
        messages = response.get("Messages") or []
        return dict(messages[0]) if messages else None

    # -- one message ------------------------------------------------------------------------

    def _handle(self, message: dict[str, Any]) -> int | None:
        """BR1.1: check the event first. Return None to take the next message, or an exit code."""
        receipt = message["ReceiptHandle"]
        if self._stop.requested:
            # Received just as the stop came: no import was started, but the message was taken.
            # It is handed back at once; the dead-letter rules then tell the team (NFR4.2).
            return self._abandon(
                receipt, self._log, "stopped before the import started", EXIT_STOPPED
            )
        message_event = classify_message(message.get("Body", ""))
        if isinstance(message_event, NotAStartEvent):
            # BR6.4: the log names the envelope field and never a value; the message goes at once.
            # No email, no outcome. (The words of BR6.4 about dead-letter handling are replaced
            # by logical-components.md, "Decisions That Correct the Functional Design".)
            self._log.warning("event_rejected", step="start", reason=message_event.reason)
            self._delete(receipt, self._log)
            return None
        log = self._log.bind(**_log_fields(message_event))
        work = self._work_for(message_event)
        if work is None:
            return self._abandon(receipt, log, "no function is set to reject an event", EXIT_FATAL)
        try:
            return self._process(receipt, message_event.import_id, work, log)
        except Exception as error:
            return self._abandon(receipt, log, "a fatal error", EXIT_FATAL, error=error)

    def _work_for(self, event: StartEvent | RejectedEvent) -> Callable[[], ImportResult] | None:
        """BR8.1: the work to run for a message. A start event gets its 6-hour Deadline here, at
        the moment it is taken off the queue. A rejected event needs none: it downloads nothing."""
        if isinstance(event, StartEvent):
            return partial(self._run_import, event, Deadline(self._clock(), self._clock))
        if self._reject_import is None:
            return None
        return partial(self._reject_import, event)

    def _process(
        self, receipt: str, import_id: str, work: Callable[[], ImportResult], log: JsonLogger
    ) -> int | None:
        """NFR1.8, NFR4.1, NFR4.2: run one import (or one rejection) under the heartbeat and the
        7-hour limit. ``work`` is the coordinator's ``run`` or ``reject``, already bound to its
        event."""
        if self._store.has_outcome(import_id):
            log.info("message_already_finished", step="start")
            self._delete(receipt, log)
            return None
        log.info("import_received", step="start")
        run = _ImportRun(work)
        self._active = run
        started = self._seconds()
        run.start()
        next_heartbeat = started + self._config.heartbeat_seconds
        next_protection = started + self._config.protection_renew_seconds
        while not self._wait(run.done, self._config.poll_seconds):
            now = self._seconds()
            if self._stop.requested:
                return self._abandon(receipt, log, "stopped during the import", EXIT_STOPPED)
            if now - started >= self._config.watchdog_seconds:  # NFR1.8
                return self._abandon(receipt, log, "the 7-hour limit was reached", EXIT_WATCHDOG)
            if now >= next_heartbeat:
                ok = self._heartbeat(receipt, log)
                step = self._config.heartbeat_seconds if ok else HEARTBEAT_RETRY_SECONDS
                next_heartbeat = now + step
            if now >= next_protection:
                self._protection.protect()
                next_protection = now + self._config.protection_renew_seconds
        if run.error is not None:
            return self._abandon(
                receipt, log, "the import stopped on an error", EXIT_FATAL, error=run.error
            )
        self._delete(receipt, log)  # NFR4.1: the outcome exists, so the message is done
        return None

    # -- queue calls ------------------------------------------------------------------------

    def _heartbeat(self, receipt: str, log: JsonLogger) -> bool:
        try:
            self._sqs.change_message_visibility(
                QueueUrl=self._config.queue_url,
                ReceiptHandle=receipt,
                VisibilityTimeout=self._config.visibility_seconds,
            )
        except (ClientError, BotoCoreError) as error:
            log.warning("heartbeat_failed", step="worker", error=_code(error))
            return False
        return True

    def _delete(self, receipt: str, log: JsonLogger) -> bool:
        error_code = "unknown"
        for _ in range(DELETE_TRIES):
            try:
                self._sqs.delete_message(QueueUrl=self._config.queue_url, ReceiptHandle=receipt)
            except (ClientError, BotoCoreError) as error:
                error_code = _code(error)
                continue
            log.info("message_deleted", step="worker")
            return True
        # The outcome (if any) is already written. The message returns after 15 minutes and goes
        # to the dead-letter queue: the one narrow window that reliability-design.md accepts.
        log.error("message_delete_failed", step="worker", error=error_code)
        return False

    def _abandon(
        self,
        receipt: str,
        log: JsonLogger,
        reason: str,
        exit_code: int,
        *,
        error: BaseException | None = None,
    ) -> int:
        """NFR4.2: set the visibility to 0 first, then leave with a non-zero exit code."""
        try:
            self._sqs.change_message_visibility(
                QueueUrl=self._config.queue_url, ReceiptHandle=receipt, VisibilityTimeout=0
            )
        except (ClientError, BotoCoreError) as visibility_error:
            log.warning("visibility_reset_failed", step="worker", error=_code(visibility_error))
        fields: dict[str, object] = {"step": "worker", "reason": reason}
        if error is not None:
            fields["error"] = _code(error)
        log.error("import_abandoned", **fields)
        return exit_code

    def _seconds(self) -> float:
        return self._clock().timestamp()


# -- wiring ----------------------------------------------------------------------------------------


CLIENT_CONNECT_TIMEOUT_SECONDS = 5  # performance-design.md, Client Settings
CLIENT_READ_TIMEOUT_SECONDS = 10
STATE_CLIENT_ATTEMPTS = 3
MAIL_CLIENT_ATTEMPTS = 2


def _short_call_config(attempts: int) -> Config:
    """Short time-outs and the standard retry mode, for calls that must fail fast."""
    return Config(
        connect_timeout=CLIENT_CONNECT_TIMEOUT_SECONDS,
        read_timeout=CLIENT_READ_TIMEOUT_SECONDS,
        # ``total_max_attempts`` counts the first try; ``max_attempts`` would count only retries.
        retries={"mode": "standard", "total_max_attempts": attempts},
    )


def build_clients(session: boto3.Session) -> tuple[Any, Any, Any]:
    """NFR1.11, NFR1.12: the state client, the data client, and the mail client.

    * State client (S3): the records, outcomes and counters, and the marker ``HEAD`` and ``GET``.
      Connect 5 s, read 10 s, 3 attempts, so the front door fails fast.
    * Data client (S3): downloads, copies, listings, and deletes. SDK defaults, as in u1: these
      calls can take long, and the deadline checks bound the work, not the socket.
    * Mail client (SES): connect 5 s, read 10 s, 2 attempts.

    The queue client is made by the caller with its defaults: its long poll needs a read
    time-out longer than the wait.
    """
    state = session.client("s3", config=_short_call_config(STATE_CLIENT_ATTEMPTS))
    data = session.client("s3")
    mail = session.client("ses", config=_short_call_config(MAIL_CLIENT_ATTEMPTS))
    return state, data, mail


class ImportRunners(NamedTuple):
    """The two functions the worker runs: one for a start event, one for a rejected event."""

    run_import: Callable[[StartEvent, Deadline], ImportResult]
    reject_import: Callable[[RejectedEvent], ImportResult]


class _NoDownloads:
    """The downloader of a rejection: a rejected event never downloads (BR6.1), so a call is a
    bug, and it stops loudly instead of reaching Hugging Face."""

    def download(
        self,
        import_id: str,
        model_name: str,
        exact_version: str,
        deadline: Deadline | None = None,
    ) -> ModelFileSet:
        raise RuntimeError("a rejected event never downloads")


def build_import_runner(
    settings: Settings,
    s3_client: Any,
    ses_client: Any,
    *,
    logger: JsonLogger,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    work_dir: Path | None = None,
    hf_factory: Callable[[], HfClient] = HfClient,
    state_client: Any | None = None,
) -> ImportRunners:
    """Return the two functions the worker runs: ``run_import(event, deadline)`` for a start
    event and ``reject_import(event)`` for a start event with bad content (BR6.1).

    A new Hugging Face client is made for each import because its retry budget of 30 minutes
    belongs to that import (``hf_client.py``); a rejection makes none, because it downloads
    nothing. ``s3_client`` is the data client. ``state_client`` serves the store and the marker
    reader; it defaults to the data client, which is how u1 ran."""
    state = state_client if state_client is not None else s3_client
    store = ImportStore(state, settings.holding_bucket)
    directory = work_dir or Path(tempfile.gettempdir()) / "model-import-work"

    def coordinator_for(downloader: Downloading) -> ImportCoordinator:
        return ImportCoordinator(
            downloader,
            LeakScanner.for_s3(s3_client, settings.holding_bucket),
            FileCustodian(
                s3_client,
                settings.holding_bucket,
                settings.final_bucket,
                store,
                clock=clock,
                logger=logger,
                state_client=state,
                work_dir=directory,
            ),
            FailureNotifier(
                ses_client,
                store,
                settings.sender_address,
                settings.team_address,
                clock=clock,
                logger=logger,
                configuration_set=settings.configuration_set,
            ),
            store,
            clock=clock,
            logger=logger,
        )

    def run_import(event: StartEvent, deadline: Deadline) -> ImportResult:
        with hf_factory() as hf:
            downloader = ModelDownloader(
                hf, s3_client, settings.holding_bucket, work_dir=directory, logger=logger
            )
            return coordinator_for(downloader).run(event, deadline)

    def reject_import(event: RejectedEvent) -> ImportResult:
        return coordinator_for(_NoDownloads()).reject(event)

    return ImportRunners(run_import, reject_import)


def main(environ: Mapping[str, str] | None = None) -> int:
    """The entry point of ``model-import-worker`` and ``python -m model_import.worker``."""
    env = os.environ if environ is None else environ
    logger = JsonLogger()
    try:
        settings = Settings.from_env(env)
    except SettingsError as error:
        logger.error("worker_not_started", step="start", reason=str(error))
        return EXIT_SETTINGS
    session = boto3.Session(region_name=settings.region)
    state_client, s3_client, ses_client = build_clients(session)
    ecs_client = session.client("ecs")
    protection: TaskProtection = (
        EcsTaskProtection(ecs_client, lambda: read_task_identity(env), logger)
        if env.get(ENV_ECS_METADATA)
        else NullTaskProtection()
    )
    stop = StopSignal()
    stop.install()
    runners = build_import_runner(
        settings, s3_client, ses_client, logger=logger, state_client=state_client
    )
    worker = ImportQueueWorker(
        session.client("sqs"),
        ImportStore(state_client, settings.holding_bucket),
        runners.run_import,
        WorkerConfig(queue_url=settings.queue_url),
        reject_import=runners.reject_import,
        protection=protection,
        stop=stop,
        logger=logger,
    )
    code = worker.run()
    if worker.import_abandoned:
        # A stuck import thread must not hold the program open: end it now.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
