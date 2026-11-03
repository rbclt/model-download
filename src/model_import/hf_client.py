"""Hugging Face client: the only module that knows the Hugging Face web addresses.

* File list: ``GET {base}/api/models/{owner}/{name}/tree/{revision}?recursive=true``
* File download: ``GET {base}/{owner}/{name}/resolve/{revision}/{path}``

Rules this module follows:

* BR2.1, NFR3.2: no account and no token. The client sends no ``Authorization`` header, ignores
  the process environment (``trust_env=False``, so ``HF_TOKEN`` style settings and ``.netrc``
  files are never read), and builds its own headers.
* NFR3.13: certificate verification is on and is never switched off. A certificate failure is a
  permanent problem. A SHA-256 hash from the listing is compared after each download.
* NFR1.7: 10 second connect time-out and 60 second read time-out; a download that runs below
  1 MB/s over a 10-minute window fails the import ("the download is too slow").
* BR2.3: a login (401, 403), a missing model or version (404), any other 4xx except 408 and 429,
  and a listing with no size for a file are permanent problems.
* BR2.4: a dropped connection, a time-out, and answers 408, 429, and 5xx are temporary problems
  and are retried by ``retry.Retrier``.
* BR2.5: a file counts as complete only when its received size equals the listed size.
"""

from __future__ import annotations

import hashlib
import re
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import quote

import httpx

from model_import.deadline import Deadline
from model_import.logs import clean_file_name
from model_import.retry import (
    DownloadError,
    PermanentDownloadError,
    Retrier,
    TemporaryDownloadError,
)

BASE_URL = "https://huggingface.co"
CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 60.0
CHUNK_SIZE_BYTES = 8 * 1024 * 1024
MIN_BYTES_PER_SECOND = 1_000_000
SLOW_WINDOW_SECONDS = 600.0
MAX_LISTING_PAGES = 50
USER_AGENT = "model-import-scan/0.1"

_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


class DownloadTooSlowError(PermanentDownloadError):
    """NFR1.7: the download ran below the speed floor. It is not retried."""


@dataclass(frozen=True)
class RemoteFile:
    """One file of the model version, as the listing describes it."""

    path: str
    size: int
    sha256: str | None = None


@dataclass(frozen=True)
class DownloadedFile:
    """A file written completely to local disk."""

    path: Path
    size_bytes: int
    sha256: str


def _certificate_failure(error: BaseException | None) -> bool:
    """NFR3.13: True when a certificate check failed anywhere in the chain of errors.

    Both the ``__cause__`` and the ``__context__`` links are followed, because a library may
    wrap the original error in another one. The marker text of OpenSSL is also accepted.
    """
    pending: list[BaseException] = [error] if error is not None else []
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if "CERTIFICATE_VERIFY_FAILED" in str(current):
            return True
        pending.extend(
            link for link in (current.__cause__, current.__context__) if link is not None
        )
    return False


def _transport_problem(error: httpx.RequestError) -> DownloadError:
    """Turn an HTTP library error into a plain-words problem (BR2.3, BR2.4, NFR3.13)."""
    if _certificate_failure(error):
        return PermanentDownloadError("the certificate of Hugging Face could not be verified")
    if isinstance(error, httpx.TimeoutException):
        return TemporaryDownloadError("the connection to Hugging Face timed out")
    return TemporaryDownloadError("the connection to Hugging Face dropped")


def _check_status(response: httpx.Response) -> None:
    """BR2.3, BR2.4: sort an answer into fine, temporary, or permanent."""
    status = response.status_code
    if 200 <= status < 300:
        return
    if status in (408, 429) or 500 <= status <= 599:
        raise TemporaryDownloadError(
            f"Hugging Face answered {status}", response.headers.get("Retry-After")
        )
    if status in (401, 403):
        raise PermanentDownloadError("the model needs a login, or it is private or gated")
    if status == 404:
        raise PermanentDownloadError("the model or the exact version was not found")
    raise PermanentDownloadError(f"Hugging Face answered {status}")


def _parse_entries(payload: object) -> list[RemoteFile]:
    """Read one page of the listing. Directories are skipped (BR2.3 for a missing size)."""
    if not isinstance(payload, list):
        raise TemporaryDownloadError("the file list from Hugging Face could not be read")
    files: list[RemoteFile] = []
    for entry in payload:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise TemporaryDownloadError("the file list from Hugging Face could not be read")
        if entry.get("type") != "file":
            continue
        files.append(_parse_file(entry))
    return files


def _parse_file(entry: dict[str, Any]) -> RemoteFile:
    path: str = entry["path"]
    lfs = entry.get("lfs") if isinstance(entry.get("lfs"), dict) else {}
    size = lfs.get("size") if _is_size(lfs.get("size")) else entry.get("size")
    if not _is_size(size):
        raise PermanentDownloadError(
            f"the listing gives no size for the file {clean_file_name(path)}"
        )
    oid = lfs.get("oid")
    digest = oid.lower() if isinstance(oid, str) and _SHA256.fullmatch(oid) else None
    return RemoteFile(path=path, size=size, sha256=digest)


def _is_size(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class HfClient:
    """Lists and downloads the files of one public model version.

    Create one client for each import: its ``Retrier`` holds the 30-minute waiting budget of
    that import. The client is safe to use from the download threads of one import.
    """

    def __init__(
        self,
        *,
        retrier: Retrier | None = None,
        base_url: str = BASE_URL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("the Hugging Face address must use https")
        self._base_url = base_url.rstrip("/")
        self._retrier = retrier or Retrier()
        self._clock = clock
        self._http = httpx.Client(
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_SECONDS,
                read=READ_TIMEOUT_SECONDS,
                write=READ_TIMEOUT_SECONDS,
                pool=CONNECT_TIMEOUT_SECONDS,
            ),
            verify=True,
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
        )

    def __enter__(self) -> HfClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- listing ----------------------------------------------------------------------------

    def list_files(
        self, repo_id: str, revision: str, deadline: Deadline | None = None
    ) -> list[RemoteFile]:
        """BR2.1: list the files of ``repo_id`` at the exact ``revision`` with sizes and hashes.

        The ``deadline`` (BR8.2) only limits the waits between tries.
        """
        return self._retrier.call(lambda: self._list_once(repo_id, revision), deadline)

    def _list_once(self, repo_id: str, revision: str) -> list[RemoteFile]:
        page_url = (
            f"{self._base_url}/api/models/{quote(repo_id, safe='/')}"
            f"/tree/{quote(revision, safe='')}"
        )
        params: dict[str, str] | None = {"recursive": "true"}
        files: list[RemoteFile] = []
        for _ in range(MAX_LISTING_PAGES):
            response = self._get(page_url, params)
            try:
                payload = response.json()
            except ValueError as error:
                raise TemporaryDownloadError(
                    "the file list from Hugging Face could not be read"
                ) from error
            files.extend(_parse_entries(payload))
            next_url = self._next_page(response)
            if next_url is None:
                return files
            page_url, params = next_url, None
        raise PermanentDownloadError("the file list of the model is too long")

    def _get(self, url: str, params: dict[str, str] | None) -> httpx.Response:
        try:
            response = self._http.get(url, params=params, headers={"Accept": "application/json"})
        except httpx.RequestError as error:
            raise _transport_problem(error) from error
        _check_status(response)
        return response

    def _next_page(self, response: httpx.Response) -> str | None:
        link = response.links.get("next")
        if not link or not link.get("url"):
            return None
        target = httpx.URL(link["url"])
        if target.scheme != "https" or target.host != httpx.URL(self._base_url).host:
            raise PermanentDownloadError("Hugging Face pointed to an unexpected page address")
        return str(target)

    # -- download ---------------------------------------------------------------------------

    def download_file(
        self,
        repo_id: str,
        revision: str,
        remote: RemoteFile,
        destination: Path,
        deadline: Deadline | None = None,
    ) -> DownloadedFile:
        """BR2.1, BR2.4, BR2.5: download one file to ``destination`` and check it.

        A temporary problem or an incomplete file starts the file again from its start, up to
        3 retries. A partial file is never left behind. With a ``deadline`` (BR8.2) it is checked
        at every chunk written and before every retry, and ``DeadlineReached`` ends the file.
        """
        return self._retrier.call(
            lambda: self._download_once(repo_id, revision, remote, destination, deadline),
            deadline,
        )

    def _download_once(
        self,
        repo_id: str,
        revision: str,
        remote: RemoteFile,
        destination: Path,
        deadline: Deadline | None,
    ) -> DownloadedFile:
        url = (
            f"{self._base_url}/{quote(repo_id, safe='/')}"
            f"/resolve/{quote(revision, safe='')}/{quote(remote.path, safe='/')}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.name + ".part")
        try:
            digest = self._stream_to(url, part, remote, deadline)
            part.replace(destination)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        return DownloadedFile(path=destination, size_bytes=remote.size, sha256=digest)

    def _stream_to(
        self, url: str, part: Path, remote: RemoteFile, deadline: Deadline | None = None
    ) -> str:
        hasher = hashlib.sha256()
        received = 0
        window_start = self._clock()
        window_bytes = 0
        try:
            with self._http.stream("GET", url) as response:
                _check_status(response)
                with part.open("wb") as handle:
                    for chunk in response.iter_bytes(CHUNK_SIZE_BYTES):
                        if deadline is not None:
                            deadline.check()  # BR8.2: at every chunk
                        received += len(chunk)
                        if received > remote.size:
                            raise TemporaryDownloadError("the file is larger than the listing says")
                        handle.write(chunk)
                        hasher.update(chunk)
                        window_bytes += len(chunk)
                        now = self._clock()
                        if now - window_start >= SLOW_WINDOW_SECONDS:
                            if window_bytes < MIN_BYTES_PER_SECOND * (now - window_start):
                                raise DownloadTooSlowError("the download is too slow")
                            window_start, window_bytes = now, 0
        except httpx.RequestError as error:
            raise _transport_problem(error) from error
        if received != remote.size:
            raise TemporaryDownloadError("the file arrived incomplete")
        digest = hasher.hexdigest()
        if remote.sha256 is not None and digest != remote.sha256.lower():
            raise TemporaryDownloadError("the file does not match the hash in the listing")
        return digest
