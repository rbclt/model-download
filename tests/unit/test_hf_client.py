"""Tests for hf_client.py against a stand-in Hugging Face host (respx).

Covers BR2.1 to BR2.5, NFR3.2, NFR3.13, NFR1.7: listing, stall, slow, bad hash, 404, 429 with
Retry-After, and an interrupted file. Time never passes for real: sleeps are recorded and the
speed clock is a fake.
"""

import hashlib
import ssl
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from model_import import hf_client
from model_import.deadline import Deadline, DeadlineReached
from model_import.hf_client import DownloadTooSlowError, HfClient, RemoteFile
from model_import.retry import (
    PermanentDownloadError,
    Retrier,
    RetriesExhaustedError,
)

BASE = "https://huggingface.co"
REPO = "org/model"
REVISION = "a" * 40
TREE_URL = f"{BASE}/api/models/{REPO}/tree/{REVISION}"
CONTENT = b"weights-bytes-" * 500
CONTENT_SHA256 = hashlib.sha256(CONTENT).hexdigest()


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def client(
    respx_mock: respx.MockRouter, sleeps: list[float], clock: FakeClock
) -> Iterator[HfClient]:
    retrier = Retrier(sleep=sleeps.append, clock=lambda: 1_000_000.0)
    with HfClient(retrier=retrier, clock=clock) as made:
        yield made


def file_url(path: str = "weights.safetensors") -> str:
    return f"{BASE}/{REPO}/resolve/{REVISION}/{path}"


def remote(size: int | None = None, sha256: str | None = CONTENT_SHA256) -> RemoteFile:
    return RemoteFile("weights.safetensors", len(CONTENT) if size is None else size, sha256)


# -- listing (BR2.1, BR2.3, NFR3.2) ---------------------------------------------------------------


def test_br2_1_listing_gives_paths_sizes_and_hashes_of_files_only(
    client: HfClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(TREE_URL, params={"recursive": "true"}).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"type": "directory", "path": "sub", "oid": "d1"},
                {"type": "file", "path": "config.json", "size": 12, "oid": "b" * 40},
                {
                    "type": "file",
                    "path": "sub/model.safetensors",
                    "size": 134,
                    "oid": "c" * 40,
                    "lfs": {"oid": CONTENT_SHA256.upper(), "size": 5000, "pointerSize": 134},
                },
            ],
        )
    )

    files = client.list_files(REPO, REVISION)

    assert files == [
        RemoteFile("config.json", 12, None),
        RemoteFile("sub/model.safetensors", 5000, CONTENT_SHA256),
    ]
    assert route.call_count == 1


def test_br2_1_nfr3_2_no_token_is_sent_and_token_settings_are_ignored(
    client: HfClient, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_token = "hf" + "_" + "A" * 30
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        monkeypatch.setenv(name, fake_token)
    route = respx_mock.get(TREE_URL).mock(return_value=httpx.Response(200, json=[]))

    client.list_files(REPO, REVISION)

    request = route.calls.last.request
    assert "authorization" not in request.headers
    assert fake_token not in str(request.headers)
    assert fake_token not in str(request.url)


def test_the_client_uses_the_timeouts_of_nfr1_7_and_refuses_a_plain_http_address() -> None:
    with HfClient() as made:
        timeout = made._http.timeout
        assert (timeout.connect, timeout.read) == (10.0, 60.0)
    with pytest.raises(ValueError):
        HfClient(base_url="http://huggingface.co")


def test_the_listing_follows_the_next_page_link(
    client: HfClient, respx_mock: respx.MockRouter
) -> None:
    next_page = f"{TREE_URL}?recursive=true&cursor=abc"

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "abc":
            return httpx.Response(200, json=[{"type": "file", "path": "two.json", "size": 2}])
        return httpx.Response(
            200,
            json=[{"type": "file", "path": "one.json", "size": 1}],
            headers={"Link": f'<{next_page}>; rel="next"'},
        )

    respx_mock.get(TREE_URL).mock(side_effect=answer)

    assert [item.path for item in client.list_files(REPO, REVISION)] == ["one.json", "two.json"]


def test_a_next_page_on_another_host_is_refused(
    client: HfClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(TREE_URL).mock(
        return_value=httpx.Response(
            200, json=[], headers={"Link": '<https://evil.example.com/page2>; rel="next"'}
        )
    )

    with pytest.raises(PermanentDownloadError, match="unexpected page address"):
        client.list_files(REPO, REVISION)


def test_br2_3_a_missing_model_or_commit_fails_at_once_without_a_retry(
    client: HfClient, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.get(TREE_URL).mock(return_value=httpx.Response(404))

    with pytest.raises(PermanentDownloadError, match="was not found"):
        client.list_files(REPO, REVISION)

    assert route.call_count == 1 and sleeps == []


@pytest.mark.parametrize("status", [401, 403])
def test_br2_3_a_login_or_gated_model_fails_at_once(
    client: HfClient, respx_mock: respx.MockRouter, sleeps: list[float], status: int
) -> None:
    respx_mock.get(TREE_URL).mock(return_value=httpx.Response(status))

    with pytest.raises(PermanentDownloadError, match="login"):
        client.list_files(REPO, REVISION)

    assert sleeps == []


def test_br2_3_a_listing_with_no_size_for_a_file_fails_at_once(
    client: HfClient, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    respx_mock.get(TREE_URL).mock(
        return_value=httpx.Response(200, json=[{"type": "file", "path": "bad\nname.json"}])
    )

    with pytest.raises(PermanentDownloadError) as raised:
        client.list_files(REPO, REVISION)

    assert "no size" in raised.value.reason and "\n" not in raised.value.reason
    assert sleeps == []


def test_br2_4_a_429_with_retry_after_waits_that_long_then_succeeds(
    client: HfClient, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    respx_mock.get(TREE_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(429, headers={"Retry-After": "900"}),
            httpx.Response(200, json=[{"type": "file", "path": "a.json", "size": 3}]),
        ]
    )

    files = client.list_files(REPO, REVISION)

    assert [item.path for item in files] == ["a.json"]
    assert sleeps == [7.0, 60.0]  # Retry-After is used, capped at 60 seconds


def test_br2_4_server_errors_are_retried_3_times_and_then_fail_the_import(
    client: HfClient, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.get(TREE_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(RetriesExhaustedError, match="503"):
        client.list_files(REPO, REVISION)

    assert route.call_count == 4
    assert sleeps == [5.0, 10.0, 20.0]


# -- download (BR2.4, BR2.5, NFR3.13, NFR1.7) -----------------------------------------------------


def test_br2_1_a_file_is_downloaded_through_a_redirect_and_checked(
    client: HfClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    path = "dir with space/weights.safetensors"
    quoted = "dir%20with%20space/weights.safetensors"
    respx_mock.get(file_url(quoted)).mock(
        return_value=httpx.Response(302, headers={"Location": "https://cdn.example.com/blob"})
    )
    respx_mock.get("https://cdn.example.com/blob").mock(
        return_value=httpx.Response(200, content=CONTENT)
    )
    destination = tmp_path / "work" / "weights.safetensors"

    result = client.download_file(
        REPO, REVISION, RemoteFile(path, len(CONTENT), CONTENT_SHA256), destination
    )

    assert destination.read_bytes() == CONTENT
    assert (result.size_bytes, result.sha256) == (len(CONTENT), CONTENT_SHA256)
    assert list(tmp_path.rglob("*.part")) == []


def test_br2_5_an_interrupted_file_is_downloaded_again_from_its_start(
    client: HfClient, respx_mock: respx.MockRouter, sleeps: list[float], tmp_path: Path
) -> None:
    respx_mock.get(file_url()).mock(
        side_effect=[
            httpx.Response(200, content=CONTENT[:100]),  # arrives short
            httpx.Response(200, content=CONTENT),
        ]
    )
    destination = tmp_path / "weights.safetensors"

    client.download_file(REPO, REVISION, remote(), destination)

    assert destination.read_bytes() == CONTENT
    assert sleeps == [5.0]


def test_br2_5_a_file_that_never_arrives_whole_is_not_kept(
    client: HfClient, respx_mock: respx.MockRouter, sleeps: list[float], tmp_path: Path
) -> None:
    route = respx_mock.get(file_url()).mock(return_value=httpx.Response(200, content=CONTENT[:10]))
    destination = tmp_path / "weights.safetensors"

    with pytest.raises(RetriesExhaustedError, match="incomplete"):
        client.download_file(REPO, REVISION, remote(), destination)

    assert route.call_count == 4 and sleeps == [5.0, 10.0, 20.0]
    assert list(tmp_path.iterdir()) == []


def test_nfr3_13_a_wrong_hash_counts_as_an_incomplete_file(
    client: HfClient, respx_mock: respx.MockRouter, sleeps: list[float], tmp_path: Path
) -> None:
    corrupted = bytes(reversed(CONTENT))  # same size, different bytes
    respx_mock.get(file_url()).mock(
        side_effect=[httpx.Response(200, content=corrupted), httpx.Response(200, content=CONTENT)]
    )
    destination = tmp_path / "weights.safetensors"

    client.download_file(REPO, REVISION, remote(), destination)

    assert destination.read_bytes() == CONTENT
    assert sleeps == [5.0]


def test_nfr3_13_a_file_that_always_fails_the_hash_check_is_never_kept(
    client: HfClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.get(file_url()).mock(return_value=httpx.Response(200, content=bytes(len(CONTENT))))

    with pytest.raises(RetriesExhaustedError, match="hash"):
        client.download_file(REPO, REVISION, remote(), tmp_path / "weights.safetensors")

    assert list(tmp_path.iterdir()) == []


def test_br2_4_a_stalled_connection_times_out_and_the_file_is_tried_again(
    client: HfClient, respx_mock: respx.MockRouter, sleeps: list[float], tmp_path: Path
) -> None:
    respx_mock.get(file_url()).mock(
        side_effect=[
            httpx.ReadTimeout("no data for 60 seconds"),
            httpx.RemoteProtocolError("connection reset"),
            httpx.Response(200, content=CONTENT),
        ]
    )

    client.download_file(REPO, REVISION, remote(), tmp_path / "weights.safetensors")

    assert sleeps == [5.0, 10.0]


def test_br2_3_a_download_that_answers_404_fails_at_once(
    client: HfClient, respx_mock: respx.MockRouter, sleeps: list[float], tmp_path: Path
) -> None:
    respx_mock.get(file_url()).mock(return_value=httpx.Response(404))

    with pytest.raises(PermanentDownloadError):
        client.download_file(REPO, REVISION, remote(), tmp_path / "weights.safetensors")

    assert sleeps == [] and list(tmp_path.iterdir()) == []


def test_br2_5_a_file_bigger_than_the_listing_is_refused(
    client: HfClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.get(file_url()).mock(return_value=httpx.Response(200, content=CONTENT))

    with pytest.raises(RetriesExhaustedError, match="larger"):
        client.download_file(REPO, REVISION, remote(size=len(CONTENT) - 1), tmp_path / "w.bin")

    assert list(tmp_path.iterdir()) == []


def _paced_body(clock: FakeClock, pieces: int, jump_before: int) -> Iterator[bytes]:
    for index in range(pieces):
        if index == jump_before:
            clock.now += 601.0  # a 10-minute window passes
        yield b"x" * 1000


def test_nfr1_7_a_download_below_the_speed_floor_fails_and_is_not_retried(
    client: HfClient,
    respx_mock: respx.MockRouter,
    sleeps: list[float],
    clock: FakeClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hf_client, "CHUNK_SIZE_BYTES", 1000)
    monkeypatch.setattr(hf_client, "MIN_BYTES_PER_SECOND", 10)  # floor: 6,010 bytes in 601 s
    route = respx_mock.get(file_url()).mock(
        side_effect=lambda request: httpx.Response(200, content=_paced_body(clock, 9, 2))
    )

    with pytest.raises(DownloadTooSlowError, match="too slow"):
        client.download_file(REPO, REVISION, remote(9000, None), tmp_path / "w.bin")

    assert route.call_count == 1 and sleeps == []
    assert list(tmp_path.iterdir()) == []


def test_nfr1_7_a_download_above_the_speed_floor_is_not_stopped(
    client: HfClient,
    respx_mock: respx.MockRouter,
    clock: FakeClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hf_client, "CHUNK_SIZE_BYTES", 1000)
    monkeypatch.setattr(hf_client, "MIN_BYTES_PER_SECOND", 10)
    respx_mock.get(file_url()).mock(
        side_effect=lambda request: httpx.Response(200, content=_paced_body(clock, 9, 7))
    )

    result = client.download_file(REPO, REVISION, remote(9000, None), tmp_path / "w.bin")

    assert result.size_bytes == 9000


def test_nfr3_13_a_certificate_failure_is_permanent_and_not_retried(
    client: HfClient, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed")
        except ssl.SSLCertVerificationError as cause:
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]") from cause

    respx_mock.get(TREE_URL).mock(side_effect=fail)

    with pytest.raises(PermanentDownloadError, match="certificate"):
        client.list_files(REPO, REVISION)

    assert sleeps == []


# -- the deadline (u2-import-rules, BR8.2, performance-design.md) ---------------------------------


def ticking_deadline(seconds_allowed: int) -> Deadline:
    """A deadline whose fake clock moves one second at every read, so each check uses one."""
    start = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
    reads = {"count": 0}

    def tick() -> datetime:
        reads["count"] += 1
        return start + timedelta(seconds=reads["count"])

    return Deadline(start, tick, limit=timedelta(seconds=seconds_allowed))


def test_br8_2_the_deadline_is_checked_at_every_chunk_and_ends_the_file_without_a_retry(
    client: HfClient,
    respx_mock: respx.MockRouter,
    sleeps: list[float],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hf_client, "CHUNK_SIZE_BYTES", 100)  # the content arrives in many chunks
    route = respx_mock.get(file_url()).mock(return_value=httpx.Response(200, content=CONTENT))
    destination = tmp_path / "weights.safetensors"

    with pytest.raises(DeadlineReached):
        client.download_file(REPO, REVISION, remote(), destination, ticking_deadline(3))

    assert route.call_count == 1  # a deadline is not a temporary problem: no retry
    assert sleeps == []
    assert list(tmp_path.iterdir()) == []  # neither the file nor its .part file is left


def test_br8_2_a_download_with_time_left_is_the_same_as_one_without_a_deadline(
    client: HfClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.get(file_url()).mock(return_value=httpx.Response(200, content=CONTENT))
    destination = tmp_path / "weights.safetensors"

    result = client.download_file(REPO, REVISION, remote(), destination, ticking_deadline(10_000))

    assert destination.read_bytes() == CONTENT and result.sha256 == CONTENT_SHA256


def test_br8_2_a_listing_that_fails_with_the_deadline_reached_does_not_wait_or_retry(
    client: HfClient, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.get(TREE_URL, params={"recursive": "true"}).mock(
        return_value=httpx.Response(503)
    )

    with pytest.raises(DeadlineReached):
        client.list_files(REPO, REVISION, ticking_deadline(1))

    assert route.call_count == 1 and sleeps == []
