"""ImportCoordinator: runs one start event to its single outcome (``functional-spec.md``).

The front door (BR6.1, BR7.1, BR10.1) runs first, in this order:

1. the outcome object of the importId already exists: nothing is done and no email is sent;
2. the content check failed (``reject``): the rejection email, the outcome ``rejected``;
3. the completion marker of the model version is looked for:
   * the check cannot be made: a failed step, nothing to remove, the failure email;
   * the marker holds this import's own importId: an earlier attempt stored the model, so the
     import is finished as ``stored`` after the clean-up of part two (no email);
   * the marker holds another importId, or is damaged: the event is refused (the already-stored
     email, the outcome ``refused-already-stored``);
4. otherwise the import runs.

Allowed moves of a running import (BR5.1, enforced by ``ImportRequest.advance`` and ``finish``):

    received    -> downloading | removing, or done (rejected, refused, own marker)
    downloading -> scanning | removing
    scanning    -> keeping  | removing
    keeping     -> removing (before the marker only), or done with the outcome stored
    removing    -> done     (removed-with-email or removed-email-failed)

The import record is rewritten before each step (NFR4.8). The outcome is created once through
``store.py`` (NFR4.1). A failed step or any finding ends in ``removing``, which runs once: an
error during the removal or the email is recorded and never starts a second removal (BR5.5).

The 6-hour ``Deadline`` (BR8.1) is checked before a move to scanning or keeping, and is handed to
the downloader, the scanner, and the custodian for their own check points (BR8.2). It is not
checked after the marker, in the removal, or in the email (BR8.3). ``DeadlineReached`` is a failed
step with the reason "the time limit of 6 hours passed" (BR8.4).

**The marker is the commit point (BR9.6).** Once ``keep`` returns, ``run.stored`` is set. From
then on nothing can start the removal: an unexpected error goes to the end with the outcome
``stored``, and ``_remove_and_notify`` refuses to run. The custodian adds a last guard: its
``remove`` for an import whose marker was written deletes nothing, and the coordinator then ends
the import as stored.

The end of every import is one function, ``_finish``, in this order: the removal and the email
(already done by the caller), the log line, the outcome object, the final record (BR10.2, NFR4.22).
A write error of the outcome object is the one error that is not caught: with no outcome the
import is not finished, and the worker handles it as a fatal error.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from model_import.custodian import (
    REASON_FINAL_CHECK_FAILED,
    KeepError,
    MarkerCheckError,
    MarkerState,
)
from model_import.deadline import Deadline, DeadlineReached
from model_import.event import RejectedEvent, StartEvent, classify_message
from model_import.logs import JsonLogger, clean_file_name
from model_import.models import (
    EVENT_FIELDS,
    MAX_RECORD_FINDINGS,
    ROLE_SENDER,
    ROLE_TEAM,
    FindingSummary,
    ImportRecord,
    ImportRequest,
    ImportStatus,
    KeepResult,
    Leftover,
    ModelFileSet,
    Outcome,
    OutcomeRecord,
    RemovalResult,
    ScanFinding,
)
from model_import.notifier import (
    REASON_ALREADY_STORED,
    NotificationResult,
    order_findings,
)
from model_import.retry import DownloadError
from model_import.scanner import ScanResult, ScanStorageError
from model_import.store import ImportStore, StoreError

REASON_DOWNLOAD_FAILED = "the download step failed"
REASON_SCAN_FAILED = "the scan step failed"
REASON_KEEP_FAILED = "the step that keeps the files failed"
REASON_RECORD_FAILED = "the import record could not be written"
# NFR3.25: the marker was fetched but is not a valid marker, so the team must look at it.
REASON_MARKER_UNREADABLE = (
    "this model version is already stored; its completion marker could not be read, "
    "so the team should check it"
)
FRONT_DOOR_SECONDS = "frontDoorSeconds"  # NFR1.11
PROOF_SECONDS = "proofSeconds"  # the removal with its proof (NFR1.13)


class Downloading(Protocol):
    def download(
        self,
        import_id: str,
        model_name: str,
        exact_version: str,
        deadline: Deadline | None = None,
    ) -> ModelFileSet: ...


class Scanning(Protocol):
    def scan(self, file_set: ModelFileSet, deadline: Deadline | None = None) -> ScanResult: ...


class Custody(Protocol):
    def read_marker(self, model_name: str, exact_version: str, import_id: str) -> MarkerState: ...

    def finish_own_marker(
        self, import_id: str, model_name: str, exact_version: str
    ) -> KeepResult: ...

    def keep(
        self,
        record: ImportRecord,
        file_set: ModelFileSet,
        scan: ScanResult,
        deadline: Deadline | None = None,
    ) -> KeepResult: ...

    def remove(self, import_id: str, model_name: str, exact_version: str) -> RemovalResult: ...


class Notifying(Protocol):
    def send(
        self,
        request: ImportRequest,
        reason: str,
        removal_complete: bool,
        findings: Sequence[ScanFinding] = (),
        findings_total: int = 0,
        *,
        leftovers: Sequence[Leftover] = (),
        older_files_removed: int = 0,
    ) -> NotificationResult: ...

    def send_rejection(self, event: RejectedEvent) -> NotificationResult: ...

    def send_already_stored(self, request: ImportRequest, reason: str) -> NotificationResult: ...


@dataclass(frozen=True)
class ImportResult:
    """What the worker needs after an import: it is over, and how it ended."""

    import_id: str
    outcome: Outcome
    failure_reason: str | None
    removal_complete: bool
    findings_total: int
    outcome_created: bool  # False when an outcome object already existed (NFR4.1)
    email_sent: bool | None = None  # BR10.4: None when no email was owed
    recipients_not_reached: tuple[str, ...] = ()


class _StepFailed(Exception):
    """A step failed. ``reason`` is plain words (BR5.1)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class _Run:
    request: ImportRequest
    record: ImportRecord
    log: JsonLogger
    started: datetime
    deadline: Deadline
    model_name: str = ""  # empty for a rejected event, which never downloads
    exact_version: str = ""
    scan: ScanResult | None = None
    stored: KeepResult | None = None  # BR9.6: set once the marker write returned success
    removal: RemovalResult | None = None
    removal_complete: bool = True
    email_sent: bool | None = None
    not_reached: tuple[str, ...] = ()
    step_seconds: dict[str, float] = field(default_factory=dict)


class ImportCoordinator:
    """BR1.1, BR1.2, BR5.1, BR6.1, BR7.1: runs the steps of one start event, in order."""

    def __init__(
        self,
        downloader: Downloading,
        scanner: Scanning,
        custodian: Custody,
        notifier: Notifying,
        store: ImportStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        logger: JsonLogger | None = None,
    ) -> None:
        self._downloader = downloader
        self._scanner = scanner
        self._custodian = custodian
        self._notifier = notifier
        self._store = store
        self._clock = clock
        self._log = logger or JsonLogger(clock=clock)

    # -- start (BR1.1, BR1.2, BR6.1) --------------------------------------------------------

    def handle(self, body: str | bytes | Mapping[str, Any]) -> ImportResult | None:
        """BR1.1, BR6.1, BR6.4: classify a queue message and handle it.

        A start event runs; a start event with bad content is rejected with an email; a message
        that is not a start event is logged by field name and left alone (``None``).
        """
        message = classify_message(body)
        if isinstance(message, StartEvent):
            return self.run(message)
        if isinstance(message, RejectedEvent):
            return self.reject(message)
        self._log.warning("event_rejected", step="start", reason=message.reason)
        return None

    def reject(self, event: RejectedEvent) -> ImportResult:
        """BR6.1 to BR6.3: nothing is downloaded; one email; the outcome ``rejected``."""
        started = self._clock()
        run = self._new_run(
            event.to_import_request(started), started, Deadline(started, self._clock)
        )
        existing = self._existing_outcome(run)
        if existing is not None:
            return existing
        reason = _rejection_reason(event.rejected_fields)
        run.log.warning(
            "event_rejected", step="start", reason=_fields_reason(event.rejected_fields)
        )
        owed = (ROLE_TEAM, ROLE_SENDER) if event.sender_email else (ROLE_TEAM,)
        self._send_email(run, lambda: self._notifier.send_rejection(event), owed)
        self._lap(run, FRONT_DOOR_SECONDS, started)
        return self._finish(run, Outcome.REJECTED, reason, True)

    def run(self, event: StartEvent, deadline: Deadline | None = None) -> ImportResult:
        """BR1.2, BR5.1: run one accepted start event to its single outcome.

        ``deadline`` is made by the caller when the event is taken off the queue (BR8.1); without
        one it starts at the moment this method begins.
        """
        started = self._clock()
        deadline = deadline or Deadline(started, self._clock)
        request = event.to_import_request(started, deadline_at=deadline.at)  # BR1.2
        run = self._new_run(request, started, deadline)
        finished = self._front_door(run)
        if finished is not None:
            return finished
        try:
            self._open_import(run)
            file_set = self._download(run)
            self._scan(run, file_set)
            self._keep(run, file_set)
        except _StepFailed as failed:
            return self._remove_and_notify(run, failed.reason)
        except Exception as error:
            if run.stored is None:
                raise
            # BR9.6: an unexpected error after the marker never undoes the model.
            run.log.error("step_failed", step="done", error=type(error).__name__)
        return self._finish_stored(run)

    # -- the front door (BR6.1, BR7.1, BR10.1) ----------------------------------------------

    def _front_door(self, run: _Run) -> ImportResult | None:
        """The outcome, then the marker. Returns the result when the import ends here."""
        existing = self._existing_outcome(run)
        if existing is not None:
            return existing
        try:
            marker = self._custodian.read_marker(
                run.model_name, run.exact_version, run.request.import_id
            )
        except Exception as error:
            reason = (
                error.reason if isinstance(error, MarkerCheckError) else REASON_FINAL_CHECK_FAILED
            )
            if not isinstance(error, MarkerCheckError):
                run.log.error("step_failed", step="start", error=type(error).__name__)
            self._lap(run, FRONT_DOOR_SECONDS, run.started)
            # BR7.1: the check could not be made. Nothing was created, so nothing is removed.
            return self._remove_and_notify(run, reason, nothing_to_remove=True)
        self._lap(run, FRONT_DOOR_SECONDS, run.started)
        if marker is MarkerState.OWN:
            return self._finish_own_marker(run)
        if marker in {MarkerState.OTHER, MarkerState.UNREADABLE}:
            return self._refuse(run, unreadable=marker is MarkerState.UNREADABLE)
        return None

    def _existing_outcome(self, run: _Run) -> ImportResult | None:
        """BR10.1: a message received again after its outcome exists does nothing."""
        try:
            outcome = self._store.read_outcome(run.request.import_id)
        except StoreError:
            return None  # the conditional create of the outcome still protects it
        if outcome is None:
            return None
        self._log.bind(**self._ids(run.request)).info("outcome_already_set", step="start")
        return ImportResult(
            import_id=outcome.import_id,
            outcome=outcome.outcome,
            failure_reason=outcome.failure_reason,
            removal_complete=outcome.removal_complete,
            findings_total=0,
            outcome_created=False,
            email_sent=outcome.email_sent,
            recipients_not_reached=outcome.recipients_not_reached,
        )

    def _refuse(self, run: _Run, *, unreadable: bool) -> ImportResult:
        """BR7.1, BR7.2: the version is already stored. Nothing is downloaded or written."""
        reason = REASON_MARKER_UNREADABLE if unreadable else REASON_ALREADY_STORED
        run.log.warning("refused_already_stored", step="start", reason=reason)
        self._send_email(
            run,
            lambda: self._notifier.send_already_stored(run.request, reason),
            (ROLE_SENDER, ROLE_TEAM),
        )
        return self._finish(run, Outcome.REFUSED_ALREADY_STORED, reason, True)

    def _finish_own_marker(self, run: _Run) -> ImportResult:
        """BR7.1, BR9.6: an earlier attempt of this event stored the model; finish it, no email."""
        run.log.info(
            "own_marker_found", step="start", reason="an earlier attempt of this event stored it"
        )
        try:
            run.stored = self._custodian.finish_own_marker(
                run.request.import_id, run.model_name, run.exact_version
            )
        except Exception as error:  # the custodian does not raise here; a wrong call is still safe
            run.log.error("step_failed", step="keep", error=type(error).__name__)
            run.removal_complete = False
            return self._finish(run, Outcome.STORED, None, False)
        return self._finish_stored(run)

    # -- the steps --------------------------------------------------------------------------

    def _open_import(self, run: _Run) -> None:
        """BR7.1, BR8.1: the model is not stored; the import starts, in status downloading."""
        run.request.advance(ImportStatus.DOWNLOADING)
        run.record.status = run.request.status
        run.log.info("import_started", status=run.request.status.value, step="download")

    def _download(self, run: _Run) -> ModelFileSet:
        """BR2.1 to BR2.6: status downloading until every file is complete."""
        self._save(run, "download")
        started = self._clock()
        try:
            file_set = self._downloader.download(
                run.request.import_id, run.model_name, run.exact_version, run.deadline
            )
        except DeadlineReached as error:
            raise _StepFailed(error.reason) from error
        except DownloadError as error:
            raise _StepFailed(error.reason) from error
        except Exception as error:
            run.log.error("step_failed", step="download", error=type(error).__name__)
            raise _StepFailed(REASON_DOWNLOAD_FAILED) from error
        self._lap(run, "downloadSeconds", started)
        if not file_set.download_complete:  # BR2.5: the scan starts only when this is true
            raise _StepFailed("the download did not finish for every file")
        return file_set

    def _scan(self, run: _Run, file_set: ModelFileSet) -> None:
        """BR3.6: any finding, or a scan that breaks, is a failed step."""
        self._check_deadline(run)  # BR8.2: before a move to a new status
        self._move(run, ImportStatus.SCANNING, "scan")
        started = self._clock()
        try:
            result = self._scanner.scan(file_set, run.deadline)
        except DeadlineReached as error:
            raise _StepFailed(error.reason) from error
        except ScanStorageError as error:
            raise _StepFailed(error.reason) from error
        except Exception as error:
            run.log.error("step_failed", step="scan", error=type(error).__name__)
            raise _StepFailed(REASON_SCAN_FAILED) from error
        self._lap(run, "scanSeconds", started)
        run.scan = result
        self._log_findings(run, result)
        self._record_findings(run, result)
        if not result.passed:
            files = sum(1 for item in result.files if item.matches_total)
            raise _StepFailed(
                f"the scan found {_count(result.matches_total, 'finding')} "
                f"in {_count(files, 'file')}"
            )

    def _keep(self, run: _Run, file_set: ModelFileSet) -> None:
        """BR4.1, BR4.2, BR9.1: the keep step starts only after a scan that passed."""
        self._check_deadline(run)  # BR8.2: before a move to a new status
        self._move(run, ImportStatus.KEEPING, "keep")
        started = self._clock()
        scan = run.scan
        if scan is None:  # cannot happen: _scan stores the result before _keep runs
            raise _StepFailed(REASON_KEEP_FAILED)
        try:
            run.stored = self._custodian.keep(run.record, file_set, scan, run.deadline)
        except DeadlineReached as error:
            raise _StepFailed(error.reason) from error
        except KeepError as error:
            raise _StepFailed(error.reason) from error
        except Exception as error:
            run.log.error("step_failed", step="keep", error=type(error).__name__)
            raise _StepFailed(REASON_KEEP_FAILED) from error
        self._lap(run, "keepSeconds", started)

    def _check_deadline(self, run: _Run) -> None:
        try:
            run.deadline.check()
        except DeadlineReached as error:
            raise _StepFailed(error.reason) from error

    # -- removal and email (BR5.1, BR5.2, BR5.3, BR5.5, BR9) --------------------------------

    def _remove_and_notify(
        self, run: _Run, reason: str, *, nothing_to_remove: bool = False
    ) -> ImportResult:
        """BR5.1: stop, remove everything once, send the email once, and finish.

        Refuses to run when the marker was written (BR9.6): the import ends as stored.
        """
        if run.stored is not None:
            return self._finish_stored(run)
        run.log.warning("import_failed", step="remove", reason=reason)
        self._move(run, ImportStatus.REMOVING, "remove", strict=False)
        if not nothing_to_remove:
            started = self._clock()
            run.removal = self._remove(run)
            self._lap(run, PROOF_SECONDS, started)
            if run.removal is not None and run.removal.model_stored:
                # The last guard of BR9.6: the custodian saw that the marker was written.
                run.log.error("step_failed", step="remove", error="ModelStored")
                run.removal_complete = False
                return self._finish(run, Outcome.STORED, None, False, stored_during_removal=True)
            run.removal_complete = run.removal is not None and run.removal.removal_complete
        self._notify(run, reason)
        outcome = Outcome.REMOVED_WITH_EMAIL if run.email_sent else Outcome.REMOVED_EMAIL_FAILED
        return self._finish(run, outcome, reason, run.removal_complete)

    def _remove(self, run: _Run) -> RemovalResult | None:
        """BR5.2, BR5.5: one removal. An error makes removalComplete False and never repeats."""
        try:
            return self._custodian.remove(run.request.import_id, run.model_name, run.exact_version)
        except Exception as error:
            run.log.error("step_failed", step="remove", error=type(error).__name__)
            return None

    def _notify(self, run: _Run, reason: str) -> None:
        """BR5.3, BR5.5, BR9.4, BR9.5: send the failure email and record who was reached."""
        findings = run.scan.findings if run.scan is not None else ()
        total = run.scan.matches_total if run.scan is not None else 0
        removal = run.removal
        leftovers = removal.leftovers if removal is not None else ()
        older = removal.older_files_removed if removal is not None else 0
        self._send_email(
            run,
            lambda: self._notifier.send(
                run.request,
                reason,
                run.removal_complete,
                findings,
                total,
                leftovers=leftovers,
                older_files_removed=older,
            ),
            (ROLE_SENDER, ROLE_TEAM),
        )

    def _send_email(
        self,
        run: _Run,
        send: Callable[[], NotificationResult],
        owed_roles: tuple[str, ...],
    ) -> None:
        """BR10.3, BR10.4: send, and set ``emailSent`` and the roles not reached in the run.

        No exception from the notifier reaches ``_finish``: it counts as every owed email not
        accepted. One ``email_not_sent`` record is written for each role not reached, with the
        kind of problem and never an address.
        """
        try:
            result = send()
        except Exception as error:
            run.log.error("step_failed", step="email", error=type(error).__name__)
            run.email_sent = False
            run.not_reached = owed_roles
            for role in owed_roles:
                self._log_not_sent(run, role, "service-error")
            return
        run.email_sent = result.email_sent
        run.not_reached = result.roles_not_reached
        for role, problem in result.not_reached:
            self._log_not_sent(run, role, problem)
        if not result.any_sent:
            run.log.error("email_failed", step="email", reason="no recipient was sent the email")

    @staticmethod
    def _log_not_sent(run: _Run, role: str, problem: str) -> None:
        run.log.warning(
            "email_not_sent",
            step="email",
            reason="the email was not accepted",
            recipientsNotReached=role,
            emailProblem=problem,
        )

    # -- finish -----------------------------------------------------------------------------

    def _finish_stored(self, run: _Run) -> ImportResult:
        """BR9.6: the marker was written, so the outcome is stored, whatever happened after it."""
        stored = run.stored
        complete = stored.holding_emptied if stored is not None else False
        run.removal_complete = complete
        return self._finish(run, Outcome.STORED, None, complete)

    def _finish(
        self,
        run: _Run,
        outcome: Outcome,
        reason: str | None,
        removal_complete: bool,
        *,
        stored_during_removal: bool = False,
    ) -> ImportResult:
        """NFR4.1, NFR4.22, BR10.2, BR11.1: the one end of every import, in a fixed order.

        The caller has already removed and sent the email. Here: set the outcome on the request,
        write the log line, create the outcome object once, and only then write the final record.
        """
        request, record = run.request, run.record
        request.finish(
            outcome,
            reason,
            email_sent=run.email_sent,
            recipients_not_reached=run.not_reached,
            stored_during_removal=stored_during_removal,
        )
        finished = self._clock()
        total = run.scan.matches_total if run.scan is not None else 0
        older = run.removal.older_files_removed if run.removal is not None else 0
        leftovers = _leftovers_total(run)
        seconds = {name: round(value, 3) for name, value in run.step_seconds.items()}
        fields: dict[str, object] = {
            "status": request.status.value,
            "step": "done",
            "outcome": outcome.value,
            "removalComplete": removal_complete,
            "findingsTotal": total,
            "olderFilesRemoved": older,
            "leftoversTotal": leftovers,
            "totalSeconds": round((finished - run.started).total_seconds(), 3),
            **seconds,
        }
        if request.failure_reason is not None:
            fields["reason"] = request.failure_reason
        if request.email_sent is not None:
            fields["emailSent"] = request.email_sent
        if request.recipients_not_reached:
            fields["recipientsNotReached"] = ",".join(request.recipients_not_reached)
        run.log.info("import_outcome", **fields)  # BR10.2: before the object, so a stop is safe
        created = self._store.create_outcome(
            OutcomeRecord(
                import_id=request.import_id,
                outcome=outcome,
                removal_complete=removal_complete,
                finished_at=finished,
                failure_reason=request.failure_reason,
                email_sent=request.email_sent,
                recipients_not_reached=request.recipients_not_reached,
            )
        )
        if not created:
            self._log.bind(**self._ids(request)).info("outcome_already_set", step="done")
        record.status, record.step, record.updated_at = request.status, "done", finished
        record.outcome, record.failure_reason = outcome, request.failure_reason
        self._write_record_quietly(run)  # BR11.1: only after the outcome object exists
        return ImportResult(
            import_id=request.import_id,
            outcome=outcome,
            failure_reason=request.failure_reason,
            removal_complete=removal_complete,
            findings_total=total,
            outcome_created=created,
            email_sent=request.email_sent,
            recipients_not_reached=request.recipients_not_reached,
        )

    # -- helpers ----------------------------------------------------------------------------

    @staticmethod
    def _ids(request: ImportRequest) -> dict[str, object]:
        ids: dict[str, object] = {"importId": request.import_id}
        if request.event_id is not None:
            ids["eventId"] = request.event_id
        return ids

    def _new_run(self, request: ImportRequest, started: datetime, deadline: Deadline) -> _Run:
        record = ImportRecord(
            import_id=request.import_id,
            model_name=request.model_name,
            exact_version=request.exact_version,
            status=request.status,
            step="start",
            started_at=started,
            updated_at=started,
            event_id=request.event_id,
        )
        context = self._ids(request)
        if request.model_name is not None:  # only a value that passed the check (BR6.3)
            context["modelName"] = request.model_name
        if request.exact_version is not None:
            context["exactVersion"] = request.exact_version
        return _Run(
            request,
            record,
            self._log.bind(**context),
            started,
            deadline,
            model_name=request.model_name or "",
            exact_version=request.exact_version or "",
        )

    def _move(self, run: _Run, to: ImportStatus, step: str, *, strict: bool = True) -> None:
        run.request.advance(to)  # BR5.1: only the allowed transitions
        run.record.status = run.request.status
        run.log.info("status_changed", status=to.value, step=step)
        self._save(run, step, strict=strict)

    def _save(self, run: _Run, step: str, *, strict: bool = True) -> None:
        """NFR4.8: rewrite the import record before a step starts."""
        run.record.step = step
        run.record.updated_at = self._clock()
        if strict:
            try:
                self._store.write_record(run.record)
            except StoreError as error:
                run.log.error("step_failed", step=step, error="StoreError")
                raise _StepFailed(REASON_RECORD_FAILED) from error
        else:
            self._write_record_quietly(run)

    def _write_record_quietly(self, run: _Run) -> None:
        """Rewrite the record where a failure must not stop the import (removal, done)."""
        try:
            self._store.write_record(run.record)
        except StoreError:
            run.log.error("record_not_written", step=run.record.step, error="StoreError")

    def _lap(self, run: _Run, name: str, started: datetime) -> None:
        run.step_seconds[name] = (self._clock() - started).total_seconds()

    def _record_findings(self, run: _Run, result: ScanResult) -> None:
        """BR11.2: the first 200 findings (cleaned name, place, kind) and the total, no value."""
        if not result.findings and not result.matches_total:
            return
        ordered = order_findings(result.findings)[:MAX_RECORD_FINDINGS]
        run.record.set_findings(
            [FindingSummary.from_finding(item) for item in ordered],
            max(result.matches_total, len(ordered)),
        )

    def _log_findings(self, run: _Run, result: ScanResult) -> None:
        """BR5.6: every finding goes to the logs; BR3.7: a capped file also logs its total."""
        for finding in result.findings:
            run.log.info(
                "finding",
                step="scan",
                fileName=clean_file_name(finding.file_name),
                place=finding.place,
                kind=finding.kind.value,
            )
        for item in result.files:
            if item.matches_total > item.findings_recorded:
                run.log.info(
                    "findings_capped",
                    step="scan",
                    fileName=clean_file_name(item.path),
                    matchesTotal=item.matches_total,
                )


def _leftovers_total(run: _Run) -> int:
    if run.removal is not None:
        return run.removal.leftovers_total
    if run.stored is not None:
        return len(run.stored.leftovers)
    return 0


def _fields_reason(names: tuple[str, ...]) -> str:
    """The log sentence for a rejected event: field names only, never a value (BR6.3)."""
    ordered = [name for name in EVENT_FIELDS if name in names]
    if len(ordered) == 1:
        return f"the event field {ordered[0]} is missing or not valid"
    return f"the event fields {', '.join(ordered)} are missing or not valid"


def _rejection_reason(names: tuple[str, ...]) -> str:
    ordered = [name for name in EVENT_FIELDS if name in names]
    return "the request was rejected; fields that failed: " + ", ".join(ordered)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")
