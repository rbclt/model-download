"""FileCustodian: keeps a scanned model, or removes everything of a failed import.

Keep (BR4.1, BR4.2, BR9.1), in this order (``reliability-design.md``, the commit marker):

1. Refuse unless the scan passed (BR4.2) and the final location has no ``_COMPLETE`` marker.
   A marker means the model version is already stored (BR4.1). Unfinished files without a
   marker are from a crashed run and may be overwritten.
2. Copy every file inside S3 (server-side copy) from ``holding/<importId>/`` to
   ``models/<lowercase name>/<version>/``, one at a time, checking the deadline before each file
   (BR8.2). A file above 5 GiB is copied in explicit parts with a check before every part. The
   import record is rewritten after each file.
3. Make the last deadline check, then write the ``_COMPLETE`` marker as the very last object:
   import id, model name, exact version, files with sizes, time.
4. **From the moment the marker write returns success the model is stored (BR9.6).** The holding
   area is deleted, and every error of this clean-up is caught and logged: ``keep`` returns a
   ``KeepResult`` and never raises after the marker. A holding folder that stays only makes
   ``holding_emptied`` False.

Removal (BR5.2, BR9.2, BR9.3) is idempotent and tolerates keys that are already gone. For an
import whose keep step had started writing, it first reads the marker. A marker that holds this
import's id is deleted (3 tries); if it stays, the files stay with it, because a marker is written
only after every file was copied, and the marker is the first Leftover. With no marker, every
object under the model version's folder is deleted, also older unfinished files of earlier
imports (counted, BR9.5). A marker of another import, or one that cannot be read, is left alone.
The holding folder, this import's unfinished uploads, and the local work directory always go.
Afterwards everything is listed again; each entry still there, and each delete that failed, is a
Leftover, and ``removal_complete`` is true only when there is none (BR9.3).

The objects deleted from the final location are the keys this import began to write plus, when
the folder has no marker, the older unfinished files that the listing shows. Nothing is deleted
above or beside the folder ``models/<name>/<version>/`` (its trailing slash keeps a sibling
version or a model with a longer name safe, NFR3.24).

A removal asked for an import whose marker write succeeded does nothing and says so: it returns
a ``RemovalResult`` with ``model_stored`` set, never "removal complete" (NFR4.14).
"""

from __future__ import annotations

import contextlib
import json
import shutil
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from boto3.exceptions import Boto3Error
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import BotoCoreError, ClientError

from model_import.deadline import Deadline
from model_import.logs import JsonLogger, clean_file_name
from model_import.models import (
    ImportRecord,
    KeepResult,
    Leftover,
    LeftoverArea,
    LeftoverKind,
    ModelFileSet,
    RemovalResult,
    StoredModel,
    final_prefix,
    holding_prefix,
    is_valid_import_id,
)
from model_import.scanner import ScanResult
from model_import.store import ImportStore, StoreError

COMPLETE_MARKER = "_COMPLETE"
COPY_PART_BYTES = 32 * 1024 * 1024  # performance-design.md: multipart copy
COPY_PARALLEL_PARTS = 8  # performance-design.md: 8 parallel parts
COPY_SINGLE_LIMIT = 5 * 1024**3  # performance-design.md: above 5 GiB the copy is explicit parts
LARGE_COPY_PART_BYTES = 256 * 1024 * 1024  # one deadline check for each part of a large copy
DELETE_BATCH = 1_000  # performance-design.md: deletes in batches of up to 1,000 keys
MARKER_DELETE_TRIES = 3  # BR9.2: tries to delete the marker
MARKER_DELETE_WAITS = (1.0, 2.0)  # reliability-design.md: seconds between the tries
MAX_MARKER_BYTES = 1024 * 1024  # a marker with 1,000 files is about 100 KB; more is not ours
MAX_LEFTOVER_LOG_RECORDS = 200  # scalability-design.md: the total is always logged
_MISSING_CODES = frozenset({"NoSuchKey", "404", "NotFound"})
_S3_ERRORS = (BotoCoreError, ClientError, Boto3Error)
REASON_FINAL_CHECK_FAILED = "the final location could not be checked"


class KeepError(Exception):
    """The keep step failed. ``reason`` is plain words and never holds a secret."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MarkerCheckError(KeepError):
    """BR7.1: the check for the marker could not be made (not the same as "no marker").

    The ``HEAD`` or the ``GET`` failed with something other than "not found", for example a
    throttle or a time-out. The caller treats it as a failed step. It is never read as
    ``unreadable`` and never as a refusal, because the marker may be this import's own.
    """

    def __init__(self) -> None:
        super().__init__(REASON_FINAL_CHECK_FAILED)


class MarkerState(StrEnum):
    """What the marker of a model version says to one import (``logical-components.md``)."""

    OWN = "own"  # it holds this import's importId: an earlier attempt of this event stored it
    OTHER = "other"  # it holds another import's importId: the version is already stored
    MISSING = "missing"  # no marker: the version is not stored
    UNREADABLE = "unreadable"  # it exists, but its body is not a valid marker


def _error_code(error: Exception) -> str:
    if isinstance(error, ClientError):
        return str(error.response.get("Error", {}).get("Code", "unknown"))
    return type(error).__name__


def _is_missing(error: ClientError) -> bool:
    status = int(error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
    return _error_code(error) in _MISSING_CODES or status == 404


def classify_marker_body(body: bytes, import_id: str) -> MarkerState:
    """Read the body of a marker that exists: ``own``, ``other``, or ``unreadable`` (BR7.1).

    A body that is too large, is not a JSON object, or has no ``importId`` of the expected
    pattern is ``unreadable``: only another writer can leave such a marker.
    """
    if len(body) > MAX_MARKER_BYTES:
        return MarkerState.UNREADABLE
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        return MarkerState.UNREADABLE
    if not isinstance(parsed, dict):
        return MarkerState.UNREADABLE
    marker_id = parsed.get("importId")
    if not isinstance(marker_id, str) or not is_valid_import_id(marker_id):
        return MarkerState.UNREADABLE
    return MarkerState.OWN if marker_id == import_id else MarkerState.OTHER


def _unique(items: Iterable[Leftover]) -> list[Leftover]:
    """Keep the first of each Leftover, in order (the same entry is found more than once)."""
    seen: set[Leftover] = set()
    unique: list[Leftover] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


class FileCustodian:
    """BR4.1, BR4.2, BR5.2, BR9: keeps a model after a pass, removes everything after a failure."""

    def __init__(
        self,
        s3_client: Any,
        holding_bucket: str,
        final_bucket: str,
        store: ImportStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        logger: JsonLogger | None = None,
        state_client: Any | None = None,
        work_dir: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        single_copy_limit: int = COPY_SINGLE_LIMIT,
        large_copy_part_bytes: int = LARGE_COPY_PART_BYTES,
    ) -> None:
        if single_copy_limit < 1 or large_copy_part_bytes < 1:
            raise ValueError("the copy sizes must be at least 1 byte")
        self._s3 = s3_client
        # The marker is read with the state client: short time-outs, so the front door fails fast
        # (performance-design.md, Client Settings). It defaults to the data client.
        self._state_s3 = state_client if state_client is not None else s3_client
        self._holding = holding_bucket
        self._final = final_bucket
        self._store = store
        self._clock = clock
        self._log = logger
        self._work_dir = work_dir
        self._sleep = sleep
        self._single_copy_limit = single_copy_limit
        self._part_bytes = large_copy_part_bytes
        self._transfer = TransferConfig(
            multipart_threshold=COPY_PART_BYTES,
            multipart_chunksize=COPY_PART_BYTES,
            max_concurrency=COPY_PARALLEL_PARTS,
        )
        # importId -> keys in the final bucket that this import began to write (BR5.2).
        self._written: dict[str, set[str]] = {}
        # Imports whose keep step passed its checks and began to copy: only for these does the
        # removal look at the final location (reliability-design.md).
        self._writing: set[str] = set()
        # Imports whose marker write returned success: the model is stored (NFR4.14).
        self._marker_written: set[str] = set()

    # -- the marker reader (BR7.1) ----------------------------------------------------------

    def read_marker(self, model_name: str, exact_version: str, import_id: str) -> MarkerState:
        """BR7.1: look for the marker of a model version and say what it means to this import.

        One ``HEAD``; only when the marker exists, one ``GET`` of its body. Both go through the
        state client. Raises ``MarkerCheckError`` when a call fails with anything other than
        "not found": that is a failed check, never ``unreadable``.
        """
        key = final_prefix(model_name, exact_version) + COMPLETE_MARKER
        try:
            self._state_s3.head_object(Bucket=self._final, Key=key)
        except ClientError as error:
            if _is_missing(error):
                return MarkerState.MISSING
            raise MarkerCheckError from error
        except BotoCoreError as error:
            raise MarkerCheckError from error
        try:
            response = self._state_s3.get_object(Bucket=self._final, Key=key)
            with contextlib.closing(response["Body"]) as body:
                data = body.read(MAX_MARKER_BYTES + 1)
        except ClientError as error:
            if _is_missing(error):  # removed between the HEAD and the GET
                return MarkerState.MISSING
            raise MarkerCheckError from error
        except BotoCoreError as error:
            raise MarkerCheckError from error
        state = classify_marker_body(data, import_id)
        if state is MarkerState.UNREADABLE and self._log is not None:
            self._log.warning(
                "marker_unreadable",
                importId=import_id,
                step="marker",
                reason="the completion marker exists but could not be read",
            )
        return state

    # -- keep (BR4.1, BR4.2, BR9.1, BR9.6) --------------------------------------------------

    def keep(
        self,
        record: ImportRecord,
        file_set: ModelFileSet,
        scan: ScanResult,
        deadline: Deadline | None = None,
    ) -> KeepResult:
        """BR4.1, BR4.2, BR9.1: copy a model that passed the scan, write the marker last.

        Raises ``KeepError`` with a plain reason, or ``DeadlineReached`` at a check point, only
        before the marker write returns success. Nothing is written to the final location unless
        ``scan.passed`` is True and the download was complete (BR4.2). After the marker this
        method never raises: it returns a ``KeepResult`` (BR9.6).
        """
        import_id = file_set.import_id
        # Only keys written by this keep step are tracked for removal. One import runs at a time,
        # so the tracking of an earlier import is dropped first: a repeat of an event, even one
        # refused as a duplicate, can then never remove what an earlier run stored (BR5.2).
        written: set[str] = set()
        self._written = {import_id: written}
        self._writing = set()
        self._marker_written = set()
        paths = self._check_before_writing(file_set, scan)
        target = final_prefix(file_set.model_name, file_set.exact_version)
        self._refuse_when_stored(target + COMPLETE_MARKER)  # BR4.1: before anything is written

        self._writing.add(import_id)  # from here the removal also looks at the final location
        sizes = {item.path: item.expected_size_bytes for item in file_set.files}
        record.files_copied = 0
        record.last_key_copied = None
        for path in paths:
            if deadline is not None:
                deadline.check()  # BR8.2: before each file is copied
            key = target + path
            written.add(key)  # tracked before the copy, so a failed copy is also removed
            self._copy(holding_prefix(import_id) + path, key, sizes[path], deadline)
            record.files_copied += 1
            record.last_key_copied = key
            record.updated_at = self._clock()
            try:
                self._store.write_record(record)
            except StoreError as error:
                raise KeepError("the import record could not be written") from error
            self._info("file_copied", import_id, path, sizes[path])

        if deadline is not None:
            deadline.check()  # BR8.3: the last check point, just before the marker
        stored_at = self._clock()
        marker_key = target + COMPLETE_MARKER
        written.add(marker_key)  # tracked before the write: it may take effect and still fail
        self._write_marker(marker_key, file_set, sizes, stored_at)
        self._marker_written.add(import_id)  # BR9.6: the model is stored
        return self._after_marker(import_id, file_set.model_name, file_set.exact_version, stored_at)

    def finish_own_marker(self, import_id: str, model_name: str, exact_version: str) -> KeepResult:
        """BR7.1, BR9.6: finish an import whose own earlier attempt already wrote the marker.

        The front door found a marker with this import's importId. The model is stored, so only
        the clean-up of part two remains: delete ``holding/<importId>/``. Like ``keep`` after
        the marker, it never raises.
        """
        self._marker_written.add(import_id)
        return self._after_marker(import_id, model_name, exact_version, self._clock())

    def _check_before_writing(self, file_set: ModelFileSet, scan: ScanResult) -> list[str]:
        if not scan.passed:
            raise KeepError("the scan did not pass, so nothing is moved to the final location")
        if not file_set.download_complete:
            raise KeepError("the download is not complete, so nothing is moved")
        paths = sorted(item.path for item in file_set.files)
        if any(path.casefold() == COMPLETE_MARKER.casefold() for path in paths):
            raise KeepError(f"a file of the model is named {COMPLETE_MARKER}")
        return paths

    def _refuse_when_stored(self, marker_key: str) -> None:
        """BR4.1: a marker in the final location means the version is already stored."""
        try:
            self._s3.head_object(Bucket=self._final, Key=marker_key)
        except ClientError as error:
            if _is_missing(error):
                return
            raise KeepError(REASON_FINAL_CHECK_FAILED) from error
        except BotoCoreError as error:
            raise KeepError(REASON_FINAL_CHECK_FAILED) from error
        raise KeepError("the model version is already in the final location")

    def _copy(self, source_key: str, key: str, size: int, deadline: Deadline | None) -> None:
        try:
            if size > self._single_copy_limit:
                self._copy_in_parts(source_key, key, size, deadline)
            else:
                self._s3.copy(
                    {"Bucket": self._holding, "Key": source_key},
                    self._final,
                    key,
                    Config=self._transfer,
                )
            head = self._s3.head_object(Bucket=self._final, Key=key)
        except _S3_ERRORS as error:
            raise KeepError("a file could not be copied to the final location") from error
        if int(head.get("ContentLength", -1)) != size:
            raise KeepError("a file in the final location has the wrong size")

    def _copy_in_parts(
        self, source_key: str, key: str, size: int, deadline: Deadline | None
    ) -> None:
        """BR8.2: copy a file above 5 GiB part by part, with a deadline check before each part.

        When the deadline is reached, or any part fails, the unfinished upload is aborted before
        the error goes on, so no part stays behind.
        """
        upload_id = self._s3.create_multipart_upload(Bucket=self._final, Key=key)["UploadId"]
        parts: list[dict[str, Any]] = []
        try:
            start, number = 0, 1
            while start < size:
                if deadline is not None:
                    deadline.check()
                end = min(start + self._part_bytes, size) - 1
                response = self._s3.upload_part_copy(
                    Bucket=self._final,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=number,
                    CopySource={"Bucket": self._holding, "Key": source_key},
                    CopySourceRange=f"bytes={start}-{end}",
                )
                parts.append({"ETag": response["CopyPartResult"]["ETag"], "PartNumber": number})
                start, number = end + 1, number + 1
            self._s3.complete_multipart_upload(
                Bucket=self._final,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except BaseException:
            try:
                self._s3.abort_multipart_upload(Bucket=self._final, Key=key, UploadId=upload_id)
            except _S3_ERRORS:
                # The removal aborts every unfinished upload of this import and proves it (BR9.3).
                self._warn("upload_not_aborted", None, step="keep", error="AbortFailed")
            raise

    def _write_marker(
        self, key: str, file_set: ModelFileSet, sizes: dict[str, int], stored_at: datetime
    ) -> None:
        """BR9.1: the importId, the model name and version, the files with sizes, the time.

        Nothing about the sender (NFR3.21).
        """
        body = {
            "importId": file_set.import_id,
            "modelName": file_set.model_name,
            "exactVersion": file_set.exact_version,
            "files": [{"path": path, "sizeBytes": sizes[path]} for path in sorted(sizes)],
            "completedAt": stored_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        try:
            self._s3.put_object(
                Bucket=self._final,
                Key=key,
                Body=json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"),
                ContentType="application/json",
            )
        except _S3_ERRORS as error:
            raise KeepError("the completion marker could not be written") from error

    def _after_marker(
        self, import_id: str, model_name: str, exact_version: str, stored_at: datetime
    ) -> KeepResult:
        """BR9.6: part two of the keep step. No error here removes the model or is raised."""
        stored = StoredModel(model_name, exact_version, stored_at)
        try:
            leftovers, _ = self._clear_prefix(
                self._holding, holding_prefix(import_id), LeftoverArea.HOLDING_AREA
            )
        except Exception as error:  # BR9.6: nothing after the marker may fail the import
            self._error("holding_clean_up_failed", import_id, error)
            leftovers = [
                Leftover(
                    LeftoverKind.TEMPORARY_RESOURCE,
                    LeftoverArea.HOLDING_AREA,
                    holding_prefix(import_id),
                )
            ]
        emptied = not leftovers
        if not emptied and self._log is not None:
            self._log.warning(
                "holding_not_emptied",
                importId=import_id,
                step="keep",
                reason="the holding area could not be fully deleted after the marker",
                leftoversTotal=len(leftovers),
            )
            self._log_leftovers(import_id, leftovers)
        return KeepResult(
            stored=stored,
            marker_written=True,
            holding_emptied=emptied,
            leftovers=tuple(leftovers),
        )

    # -- removal (BR5.2, BR5.5, BR9.2, BR9.3, NFR4.3) ---------------------------------------

    def remove(self, import_id: str, model_name: str, exact_version: str) -> RemovalResult:
        """BR5.2, BR9.2, BR9.3: remove everything this import created, and prove it.

        Safe to run again (NFR4.3). Never raises for a failed delete or a failed listing: they
        become Leftovers and ``removal_complete`` is False. For an import whose marker write
        succeeded it deletes nothing and returns ``model_stored`` (NFR4.14).
        """
        if import_id in self._marker_written:
            self._warn("removal_refused", import_id, step="remove", reason="the model is stored")
            return RemovalResult(import_id, model_stored=True)
        leftovers: list[Leftover] = []
        older_removed = 0
        if import_id in self._writing:  # the keep step had begun to write
            final_leftovers, older_removed = self._remove_final(
                import_id, model_name, exact_version
            )
            leftovers += final_leftovers
        leftovers += self._remove_holding(import_id)
        leftovers += self._remove_local(import_id)
        leftovers = _unique(leftovers)
        if self._log is not None:
            self._log_leftovers(import_id, leftovers)
            self._log.info(
                "removal_finished",
                importId=import_id,
                step="remove",
                removalComplete=not leftovers,
                olderFilesRemoved=older_removed,
                leftoversTotal=len(leftovers),
            )
        return RemovalResult(import_id, tuple(leftovers), older_removed)

    def _remove_final(
        self, import_id: str, model_name: str, exact_version: str
    ) -> tuple[list[Leftover], int]:
        """BR9.2: the marker first, then the folder when it has no marker.

        Returns the Leftovers of the final location and the number of older files removed.
        """
        target = final_prefix(model_name, exact_version)
        marker_key = target + COMPLETE_MARKER
        marker_leftover = Leftover(LeftoverKind.OBJECT, LeftoverArea.FINAL_LOCATION, marker_key)
        try:
            state = self.read_marker(model_name, exact_version, import_id)
        except MarkerCheckError:
            state = MarkerState.UNREADABLE  # it cannot be told: leave it alone and report it
        if state is MarkerState.OTHER:
            self._warn(
                "complete_copy_left_alone",
                import_id,
                step="remove",
                reason="a complete copy of this version was found in the final location",
            )
            return [], 0
        if state is MarkerState.UNREADABLE:
            return [marker_leftover], 0
        if state is MarkerState.OWN and not self._delete_marker(
            marker_key, model_name, exact_version, import_id
        ):
            # BR9.2: the marker stays, so the folder holds a complete model. Keep it whole.
            return [marker_leftover], 0
        written = self._written.get(import_id, set())
        leftovers, older = self._clear_prefix(
            self._final, target, LeftoverArea.FINAL_LOCATION, own_keys=written
        )
        leftovers += self._clear_uploads(
            self._final, target, LeftoverArea.FINAL_LOCATION, only_keys=written
        )
        return leftovers, older

    def _delete_marker(
        self, marker_key: str, model_name: str, exact_version: str, import_id: str
    ) -> bool:
        """BR9.2: delete this import's marker, up to 3 tries. True when it is gone."""
        for attempt in range(MARKER_DELETE_TRIES):
            if not self._delete_keys(self._final, [marker_key]):
                return True
            if attempt < MARKER_DELETE_TRIES - 1:
                self._sleep(MARKER_DELETE_WAITS[min(attempt, len(MARKER_DELETE_WAITS) - 1)])
        try:  # the last delete may have taken effect although it reported an error
            return self.read_marker(model_name, exact_version, import_id) is MarkerState.MISSING
        except MarkerCheckError:
            return False

    def _remove_holding(self, import_id: str) -> list[Leftover]:
        """BR5.2: delete ``holding/<importId>/`` and its unfinished uploads, and prove it."""
        prefix = holding_prefix(import_id)
        leftovers, _ = self._clear_prefix(self._holding, prefix, LeftoverArea.HOLDING_AREA)
        leftovers += self._clear_uploads(self._holding, prefix, LeftoverArea.HOLDING_AREA)
        return leftovers

    def _remove_local(self, import_id: str) -> list[Leftover]:
        """BR5.2, BR9.3: delete the local work directory of this import, then look again."""
        if self._work_dir is None:
            return []
        path = self._work_dir / import_id
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return []
        location = f"{self._work_dir.name}/{import_id}"
        return [Leftover(LeftoverKind.TEMPORARY_RESOURCE, LeftoverArea.LOCAL_DISK, location)]

    # -- deleting and proving ---------------------------------------------------------------

    def _clear_prefix(
        self,
        bucket: str,
        prefix: str,
        area: LeftoverArea,
        *,
        own_keys: set[str] | None = None,
    ) -> tuple[list[Leftover], int]:
        """Delete every key under ``prefix``, then list again as the proof (BR9.3).

        Returns the Leftovers and, when ``own_keys`` is given, the number of keys deleted that
        this import did not write (older unfinished files, BR9.5). A listing that fails is one
        Leftover of kind temporary-resource; the keys this import wrote are still deleted.
        """
        leftovers: list[Leftover] = []
        listed = self._list_keys(bucket, prefix)
        if listed is None:
            leftovers.append(Leftover(LeftoverKind.TEMPORARY_RESOURCE, area, prefix))
            failed = self._delete_keys(bucket, sorted(own_keys or ()))
        else:
            failed = self._delete_keys(bucket, listed)
        older = 0
        if own_keys is not None and listed is not None:
            older = sum(1 for key in listed if key not in own_keys and key not in failed)
        leftovers += [Leftover(LeftoverKind.OBJECT, area, key) for key in sorted(failed)]
        remaining = self._list_keys(bucket, prefix)
        if remaining is None:
            leftovers.append(Leftover(LeftoverKind.TEMPORARY_RESOURCE, area, prefix))
        else:
            leftovers += [Leftover(LeftoverKind.OBJECT, area, key) for key in remaining]
        return _unique(leftovers), older

    def _clear_uploads(
        self,
        bucket: str,
        prefix: str,
        area: LeftoverArea,
        *,
        only_keys: set[str] | None = None,
    ) -> list[Leftover]:
        """Abort this import's unfinished uploads under ``prefix``, then list again (BR9.3)."""
        location = f"{prefix} (incomplete uploads)"
        unchecked = Leftover(LeftoverKind.TEMPORARY_RESOURCE, area, location)
        uploads = self._list_uploads(bucket, prefix, only_keys)
        if uploads is None:
            return [unchecked]
        leftovers = [
            Leftover(LeftoverKind.INCOMPLETE_UPLOAD, area, key)
            for key, upload_id in uploads
            if not self._abort(bucket, key, upload_id)
        ]
        remaining = self._list_uploads(bucket, prefix, only_keys)
        if remaining is None:
            return [*leftovers, unchecked]
        leftovers += [Leftover(LeftoverKind.INCOMPLETE_UPLOAD, area, key) for key, _ in remaining]
        return leftovers

    def _abort(self, bucket: str, key: str, upload_id: str) -> bool:
        try:
            self._s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        except ClientError as error:
            return _error_code(error) == "NoSuchUpload"
        except BotoCoreError:
            return False
        return True

    def _delete_keys(self, bucket: str, keys: Iterable[str]) -> set[str]:
        """Delete keys in batches. Return the keys that could not be deleted."""
        pending = list(keys)
        failed: set[str] = set()
        for start in range(0, len(pending), DELETE_BATCH):
            batch = pending[start : start + DELETE_BATCH]
            try:
                response = self._s3.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
            except _S3_ERRORS:
                failed.update(batch)
                continue
            errors = response.get("Errors", [])
            if any("Key" not in item for item in errors):
                failed.update(batch)  # an error that names no key: count the whole batch
            else:
                failed.update(item["Key"] for item in errors)
        return failed

    def _list_keys(self, bucket: str, prefix: str) -> list[str] | None:
        """List all keys under ``prefix``; None when the listing failed."""
        keys: list[str] = []
        try:
            for page in self._s3.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix=prefix
            ):
                keys.extend(item["Key"] for item in page.get("Contents", []))
        except _S3_ERRORS:
            return None
        return keys

    def _list_uploads(
        self, bucket: str, prefix: str, only_keys: set[str] | None
    ) -> list[tuple[str, str]] | None:
        found: list[tuple[str, str]] = []
        try:
            for page in self._s3.get_paginator("list_multipart_uploads").paginate(
                Bucket=bucket, Prefix=prefix
            ):
                for upload in page.get("Uploads", []):
                    if only_keys is None or upload["Key"] in only_keys:
                        found.append((upload["Key"], upload["UploadId"]))
        except _S3_ERRORS:
            return None
        return found

    # -- logging ----------------------------------------------------------------------------

    def _log_leftovers(self, import_id: str, leftovers: list[Leftover]) -> None:
        """NFR5.14: one record for each Leftover (up to 200); the total is in the next record."""
        if self._log is None:
            return
        for item in leftovers[:MAX_LEFTOVER_LOG_RECORDS]:
            self._log.warning(
                "leftover",
                importId=import_id,
                step="remove",
                kind=item.kind.value,
                place=item.area.value,
                fileName=item.location,
            )

    def _info(self, event: str, import_id: str, path: str, size: int) -> None:
        if self._log is not None:
            self._log.info(
                event, importId=import_id, step="keep", fileName=clean_file_name(path), bytes=size
            )

    def _warn(self, event: str, import_id: str | None, **fields: object) -> None:
        if self._log is not None:
            ids = {} if import_id is None else {"importId": import_id}
            self._log.warning(event, **ids, **fields)

    def _error(self, event: str, import_id: str, error: Exception) -> None:
        if self._log is not None:
            self._log.error(event, importId=import_id, step="keep", error=_error_code(error))
