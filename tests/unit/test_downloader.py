"""Tests for downloader.py: BR2.1 to BR2.6, BR1.3 (paths), NFR2.1 (early size checks).

A stand-in model source serves the files, moto stands in for S3, and one test runs the real
Hugging Face client against respx. No test sleeps for real.
"""

import hashlib
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
import respx
from moto import mock_aws

from model_import.deadline import Deadline, DeadlineReached
from model_import.downloader import (
    MAX_FILES,
    MAX_TOTAL_BYTES,
    HoldingAreaError,
    ModelDownloader,
    check_listing,
)
from model_import.hf_client import DownloadedFile, HfClient, RemoteFile
from model_import.models import holding_prefix
from model_import.retry import PermanentDownloadError, Retrier

BUCKET = "holding-bucket"
IMPORT_ID = "imp-abc"
REPO = "org/model"
REVISION = "a" * 40
WEIGHTS = b"0" * 64


class FakeSource:
    """Serves the given files. ``failures`` maps a path to an error raised on download."""

    def __init__(
        self,
        contents: dict[str, bytes],
        failures: dict[str, Exception] | None = None,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.contents = contents
        self.failures = failures or {}
        self.barrier = barrier
        self.downloaded: list[str] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def list_files(self, repo_id: str, revision: str) -> list[RemoteFile]:
        return [RemoteFile(path, len(data)) for path, data in self.contents.items()]

    def download_file(
        self, repo_id: str, revision: str, remote: RemoteFile, destination: Path
    ) -> DownloadedFile:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.downloaded.append(remote.path)
        try:
            if self.barrier is not None:
                self.barrier.wait(timeout=10)
            if remote.path in self.failures:
                raise self.failures[remote.path]
            data = self.contents[remote.path]
            destination.write_bytes(data)
            return DownloadedFile(destination, len(data), hashlib.sha256(data).hexdigest())
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def s3() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def make_downloader(source: Any, s3: Any, tmp_path: Path, max_workers: int = 4) -> ModelDownloader:
    return ModelDownloader(source, s3, BUCKET, work_dir=tmp_path / "work", max_workers=max_workers)


def keys_in_bucket(s3: Any) -> list[str]:
    listing = s3.list_objects_v2(Bucket=BUCKET)
    return sorted(item["Key"] for item in listing.get("Contents", []))


def test_br2_1_br2_2_br2_5_files_go_to_the_holding_area_complete_and_local_copies_are_deleted(
    s3: Any, tmp_path: Path
) -> None:
    contents = {
        "config.json": b'{"a": 1}',
        "sub/model.safetensors": WEIGHTS,
        "README.md": b"hello",
    }
    downloader = make_downloader(FakeSource(contents), s3, tmp_path)

    result = downloader.download(IMPORT_ID, REPO, REVISION)

    assert keys_in_bucket(s3) == sorted(holding_prefix(IMPORT_ID) + path for path in contents)
    assert result.download_complete
    assert {item.path: item.received_size_bytes for item in result.files} == {
        path: len(data) for path, data in contents.items()
    }
    body = s3.get_object(Bucket=BUCKET, Key=holding_prefix(IMPORT_ID) + "sub/model.safetensors")
    assert body["Body"].read() == WEIGHTS
    work = tmp_path / "work"
    assert not work.exists() or list(work.rglob("*")) == []  # no local copy is left


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ([], "no files"),
        ([RemoteFile("config.json", 5), RemoteFile("README.md", 3)], "no .safetensors"),
        ([RemoteFile("pytorch_model.bin", 5)], "no .safetensors"),
    ],
)
def test_br2_6_a_model_with_no_files_or_no_weight_file_fails_before_any_download(
    files: list[RemoteFile], reason: str, s3: Any, tmp_path: Path
) -> None:
    source = FakeSource({item.path: b"x" * item.size for item in files})
    downloader = make_downloader(source, s3, tmp_path)

    with pytest.raises(PermanentDownloadError, match=reason):
        downloader.download(IMPORT_ID, REPO, REVISION)

    assert source.downloaded == []
    assert keys_in_bucket(s3) == []


@pytest.mark.parametrize(
    "bad_path", ["../escape.json", "/abs.json", "dir\\x.json", "a//b.json", "x/./y.json"]
)
def test_br1_3_an_unsafe_file_path_fails_the_import_before_any_download(
    bad_path: str, s3: Any, tmp_path: Path
) -> None:
    source = FakeSource({"w.safetensors": WEIGHTS, bad_path: b"data"})
    downloader = make_downloader(source, s3, tmp_path)

    with pytest.raises(PermanentDownloadError, match="file path"):
        downloader.download(IMPORT_ID, REPO, REVISION)

    assert source.downloaded == []


def test_br1_3_two_paths_that_differ_only_by_case_fail_the_import() -> None:
    with pytest.raises(PermanentDownloadError, match="same"):
        check_listing([RemoteFile("w.safetensors", 1), RemoteFile("W.SafeTensors", 1)])


def test_nfr2_1_the_limits_are_1000_files_and_20_gb_and_the_edge_is_accepted() -> None:
    weight = RemoteFile("w.safetensors", 1)
    others = [RemoteFile(f"f{i}.json", 1) for i in range(MAX_FILES - 1)]
    check_listing([weight, *others])  # exactly 1,000 files
    check_listing([RemoteFile("w.safetensors", MAX_TOTAL_BYTES)])  # exactly 20 GB

    with pytest.raises(PermanentDownloadError, match="more than 1,000 files"):
        check_listing([weight, *others, RemoteFile("one-more.json", 1)])
    with pytest.raises(PermanentDownloadError, match="larger than 20 GB"):
        check_listing([RemoteFile("w.safetensors", MAX_TOTAL_BYTES + 1)])


def test_br2_1_at_most_4_files_are_downloaded_at_the_same_time(s3: Any, tmp_path: Path) -> None:
    contents = {f"part-{i}.safetensors": WEIGHTS for i in range(8)}
    source = FakeSource(contents, barrier=threading.Barrier(4))
    downloader = make_downloader(source, s3, tmp_path, max_workers=4)

    result = downloader.download(IMPORT_ID, REPO, REVISION)

    assert source.max_active == 4  # four ran together (the barrier needs four) and never more
    assert result.download_complete
    assert len(keys_in_bucket(s3)) == 8


def test_br2_3_br2_4_the_first_failure_ends_the_import_and_the_other_downloads(
    s3: Any, tmp_path: Path
) -> None:
    contents = {f"file-{i}.safetensors": WEIGHTS for i in range(6)}
    failing = "file-0.safetensors"
    source = FakeSource(contents, failures={failing: PermanentDownloadError("model is private")})
    downloader = make_downloader(source, s3, tmp_path, max_workers=1)

    with pytest.raises(PermanentDownloadError, match="private"):
        downloader.download(IMPORT_ID, REPO, REVISION)

    assert source.downloaded == [failing]  # the files after the failure were never started
    assert not (tmp_path / "work" / IMPORT_ID).exists()


def test_br2_2_a_failed_write_to_the_holding_area_fails_the_import(tmp_path: Path) -> None:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")  # the bucket is not created
        source = FakeSource({"w.safetensors": WEIGHTS})
        downloader = make_downloader(source, client, tmp_path)

        with pytest.raises(HoldingAreaError, match="holding area"):
            downloader.download(IMPORT_ID, REPO, REVISION)

    assert list((tmp_path / "work").rglob("*")) == []


def test_br2_4_br2_5_a_file_is_retried_from_its_start_then_kept_with_the_real_client(
    s3: Any, tmp_path: Path
) -> None:
    content = b"real-weights" * 100
    base = "https://huggingface.co"
    sleeps: list[float] = []
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{base}/api/models/{REPO}/tree/{REVISION}").mock(
            return_value=httpx.Response(
                200, json=[{"type": "file", "path": "w.safetensors", "size": len(content)}]
            )
        )
        route = router.get(f"{base}/{REPO}/resolve/{REVISION}/w.safetensors")
        route.side_effect = [
            httpx.Response(503),
            httpx.Response(200, content=content[:100]),  # incomplete: size does not match
            httpx.Response(200, content=content),
        ]
        with HfClient(retrier=Retrier(sleep=sleeps.append)) as hf:
            downloader = make_downloader(hf, s3, tmp_path)
            result = downloader.download(IMPORT_ID, REPO, REVISION)

    assert route.call_count == 3
    assert sleeps == [5.0, 10.0]
    assert result.download_complete
    stored = s3.get_object(Bucket=BUCKET, Key=holding_prefix(IMPORT_ID) + "w.safetensors")
    assert stored["Body"].read() == content


# -- the deadline (u2-import-rules, BR8.2, performance-design.md) ---------------------------------


class DeadlineSource(FakeSource):
    """A source that takes the deadline, as ``HfClient`` does. It records what it was given."""

    def __init__(self, *args: Any, after_download: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.seen: list[Deadline | None] = []
        self._after_download = after_download

    def list_files(
        self, repo_id: str, revision: str, deadline: Deadline | None = None
    ) -> list[RemoteFile]:
        self.seen.append(deadline)
        return super().list_files(repo_id, revision)

    def download_file(
        self,
        repo_id: str,
        revision: str,
        remote: RemoteFile,
        destination: Path,
        deadline: Deadline | None = None,
    ) -> DownloadedFile:
        self.seen.append(deadline)
        result = super().download_file(repo_id, revision, remote, destination)
        if self._after_download is not None:
            self._after_download(remote.path)
        return result


def test_br8_2_the_deadline_goes_to_the_listing_and_to_every_file(
    s3: Any, tmp_path: Path, clock: Any
) -> None:
    deadline = Deadline(clock(), clock)
    source = DeadlineSource({"w.safetensors": WEIGHTS, "config.json": b"{}"})

    result = make_downloader(source, s3, tmp_path).download(IMPORT_ID, REPO, REVISION, deadline)

    assert result.download_complete
    assert source.seen == [deadline] * 3  # the listing and two files


def test_br8_2_a_download_without_a_deadline_calls_the_source_as_in_u1(
    s3: Any, tmp_path: Path
) -> None:
    # FakeSource has no deadline argument: it would fail if the downloader passed one.
    source = FakeSource({"w.safetensors": WEIGHTS})

    assert make_downloader(source, s3, tmp_path).download(IMPORT_ID, REPO, REVISION).files


def test_br8_2_a_reached_deadline_stops_before_the_first_file_is_requested(
    s3: Any, tmp_path: Path, clock: Any
) -> None:
    deadline = Deadline(clock(), clock)
    clock.advance(6 * 3600)
    source = DeadlineSource({"w.safetensors": WEIGHTS, "config.json": b"{}"})

    with pytest.raises(DeadlineReached):
        make_downloader(source, s3, tmp_path).download(IMPORT_ID, REPO, REVISION, deadline)

    assert source.downloaded == []
    assert keys_in_bucket(s3) == []


def test_br8_2_the_deadline_between_two_files_stops_the_next_file_and_clears_local_disk(
    s3: Any, tmp_path: Path, clock: Any
) -> None:
    deadline = Deadline(clock(), clock)
    contents = {f"part-{i}.safetensors": WEIGHTS for i in range(3)}
    source = DeadlineSource(contents, after_download=lambda _: clock.advance(6 * 3600))

    with pytest.raises(DeadlineReached):
        make_downloader(source, s3, tmp_path, max_workers=1).download(
            IMPORT_ID, REPO, REVISION, deadline
        )

    assert source.downloaded == ["part-0.safetensors"]  # the files after it never started
    assert keys_in_bucket(s3) == [holding_prefix(IMPORT_ID) + "part-0.safetensors"]
    assert not (tmp_path / "work" / IMPORT_ID).exists()
