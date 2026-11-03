"""ModelDownloader: list, check, and download a model into the holding area (BR2.1 to BR2.6).

Order of one download (``functional-spec.md`` step 2):

1. List the files of the exact commit (BR2.1) and check the list before any download: it must
   not be empty, every path must be safe and unique (BR1.3), it must hold a ``.safetensors``
   file (BR2.6), and it must hold at most 1,000 files and 20 GB in total (NFR2.1).
2. Download up to 4 files at a time. Each file goes to local disk, is checked for size and
   SHA-256 (BR2.5, NFR3.13, inside ``HfClient``), is uploaded to ``holding/<importId>/<path>``
   (BR2.2), and its local copy is deleted at once. Local disk therefore holds at most the files
   in flight (``performance-design.md``).
3. The first file that fails for good fails the import (BR2.3, BR2.4).

Every failure is a ``DownloadError`` with a plain-words ``reason``. Nothing here holds a secret.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Protocol

from boto3.exceptions import S3UploadFailedError
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import BotoCoreError, ClientError

from model_import.deadline import Deadline
from model_import.event import PathCheckError, check_file_paths
from model_import.hf_client import DownloadedFile, RemoteFile
from model_import.logs import JsonLogger, clean_file_name
from model_import.models import (
    ModelFile,
    ModelFileSet,
    holding_prefix,
)
from model_import.retry import DownloadError, PermanentDownloadError

MAX_PARALLEL_FILES = 4  # performance-design.md
MAX_FILES = 1_000  # NFR2.1
MAX_TOTAL_BYTES = 20 * 1024**3  # NFR2.1: 20 GB, counted as 20 GiB so it fits the 30 GiB disk
WEIGHT_ENDING = ".safetensors"
UPLOAD_PART_BYTES = 32 * 1024 * 1024  # performance-design.md: 32 MiB parts, 4 parts at a time
UPLOAD_PARALLEL_PARTS = 4


class ModelSource(Protocol):
    """What the downloader needs from the Hugging Face client (``HfClient`` fits).

    A source that takes a ``deadline`` keyword (``HfClient`` does) gets the import's ``Deadline``
    when there is one; a source without it is called as in u1.
    """

    def list_files(
        self, repo_id: str, revision: str, deadline: Deadline | None = None
    ) -> list[RemoteFile]: ...

    def download_file(
        self,
        repo_id: str,
        revision: str,
        remote: RemoteFile,
        destination: Path,
        deadline: Deadline | None = None,
    ) -> DownloadedFile: ...


class HoldingAreaError(DownloadError):
    """BR2.2: a file could not be written to the holding area. The import fails."""


def check_listing(files: Sequence[RemoteFile]) -> None:
    """BR1.3, BR2.6, NFR2.1: refuse a bad listing before any download.

    Raises ``PermanentDownloadError`` with a plain reason (BR2.3: no retry).
    """
    if not files:
        raise PermanentDownloadError("the model has no files")
    try:
        check_file_paths(item.path for item in files)
    except PathCheckError as error:
        raise PermanentDownloadError(error.reason) from error
    if not any(item.path.rsplit("/", 1)[-1].lower().endswith(WEIGHT_ENDING) for item in files):
        raise PermanentDownloadError("the model has no .safetensors weight file")
    if len(files) > MAX_FILES:
        raise PermanentDownloadError("the model has more than 1,000 files")
    if sum(item.size for item in files) > MAX_TOTAL_BYTES:
        raise PermanentDownloadError("the model is larger than 20 GB")


class ModelDownloader:
    """BR2.1 to BR2.6: downloads one model version into ``holding/<importId>/``."""

    def __init__(
        self,
        source: ModelSource,
        s3_client: Any,
        holding_bucket: str,
        *,
        work_dir: Path,
        max_workers: int = MAX_PARALLEL_FILES,
        logger: JsonLogger | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._source = source
        self._s3 = s3_client
        self._bucket = holding_bucket
        self._work_dir = work_dir
        self._max_workers = max_workers
        self._log = logger
        self._transfer = TransferConfig(
            multipart_threshold=UPLOAD_PART_BYTES,
            multipart_chunksize=UPLOAD_PART_BYTES,
            max_concurrency=UPLOAD_PARALLEL_PARTS,
        )

    def list_checked(
        self, model_name: str, exact_version: str, deadline: Deadline | None = None
    ) -> list[RemoteFile]:
        """BR2.1, BR2.3, BR2.6, NFR2.1: list the files of the exact commit and check them."""
        if deadline is None:
            files = self._source.list_files(model_name, exact_version)
        else:
            files = self._source.list_files(model_name, exact_version, deadline=deadline)
        check_listing(files)
        return files

    def download(
        self,
        import_id: str,
        model_name: str,
        exact_version: str,
        deadline: Deadline | None = None,
    ) -> ModelFileSet:
        """BR2.1 to BR2.5: list, check, and download every file into the holding area.

        Returns the ``ModelFileSet`` with every file complete (``download_complete`` is True).
        Raises ``DownloadError`` when the listing is refused or a file fails for good; files
        already uploaded stay in the holding area for the removal step (BR5.2).

        With a ``deadline`` (BR8.2) it is checked before every file starts, and handed to the
        source for the check at every chunk and for the retry waits. ``DeadlineReached`` ends the
        download the same way a failed file does: the other downloads stop and the local copies
        are deleted.
        """
        files = self.list_checked(model_name, exact_version, deadline)
        model_files = [ModelFile(path=item.path, expected_size_bytes=item.size) for item in files]
        file_set = ModelFileSet(
            import_id=import_id,
            model_name=model_name,
            exact_version=exact_version,
            files=model_files,
        )
        local_dir = self._work_dir / import_id
        local_dir.mkdir(parents=True, exist_ok=True)
        stop = threading.Event()
        try:
            with ThreadPoolExecutor(
                max_workers=self._max_workers, thread_name_prefix="download"
            ) as pool:
                jobs = [
                    pool.submit(
                        self._fetch_one,
                        import_id,
                        model_name,
                        exact_version,
                        remote,
                        model_file,
                        local_dir / f"file-{index}",
                        stop,
                        deadline,
                    )
                    for index, (remote, model_file) in enumerate(
                        zip(files, model_files, strict=True)
                    )
                ]
                try:
                    for job in as_completed(jobs):
                        job.result()
                except BaseException:
                    stop.set()
                    for job in jobs:
                        job.cancel()
                    raise
        finally:
            shutil.rmtree(local_dir, ignore_errors=True)
        if not file_set.download_complete:  # BR2.5: the scan starts only when this is true
            raise DownloadError("the download did not finish for every file")
        return file_set

    def _fetch_one(
        self,
        import_id: str,
        model_name: str,
        exact_version: str,
        remote: RemoteFile,
        model_file: ModelFile,
        local_path: Path,
        stop: threading.Event,
        deadline: Deadline | None = None,
    ) -> None:
        if stop.is_set():
            return
        try:
            if deadline is None:
                done = self._source.download_file(model_name, exact_version, remote, local_path)
            else:
                deadline.check()  # BR8.2: before each file download
                done = self._source.download_file(
                    model_name, exact_version, remote, local_path, deadline=deadline
                )
            key = holding_prefix(import_id) + remote.path  # BR2.2: only the holding area
            self._upload(local_path, key, done.size_bytes)
        except BaseException:
            stop.set()  # the first file that fails for good ends the other downloads (BR2.4)
            raise
        finally:
            local_path.unlink(missing_ok=True)
        model_file.received_size_bytes = done.size_bytes
        if self._log is not None:
            self._log.info(
                "file_downloaded",
                importId=import_id,
                step="download",
                fileName=clean_file_name(remote.path),
                bytes=done.size_bytes,
            )

    def _upload(self, local_path: Path, key: str, size: int) -> None:
        try:
            self._s3.upload_file(str(local_path), self._bucket, key, Config=self._transfer)
            head = self._s3.head_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError, S3UploadFailedError, OSError) as error:
            raise HoldingAreaError("a file could not be written to the holding area") from error
        if int(head.get("ContentLength", -1)) != size:
            raise HoldingAreaError("a file in the holding area has the wrong size")
