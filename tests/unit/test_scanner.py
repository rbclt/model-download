"""Tests for scanner.py: BR3.1 to BR3.7, NFR1.9, NFR3.8.

Planted secrets are built from two parts in the test, so this file holds no complete
secret-shaped string. Time never passes for real: the clock is a fake.
"""

import io
import json
import struct
from collections.abc import Iterator
from typing import IO, Any

import boto3
import pytest
from moto import mock_aws

from model_import.deadline import Deadline, DeadlineReached
from model_import.models import (
    FileClass,
    FindingKind,
    ModelFile,
    ModelFileSet,
    holding_prefix,
)
from model_import.scanner import (
    MAX_TEXT_FILE_BYTES,
    PLACE_SIZE_LIMIT,
    PLACE_TIME_LIMIT,
    PLACE_WHOLE_FILE,
    WINDOW_CHARS,
    WINDOW_OVERLAP_CHARS,
    FileUnreadableError,
    LeakScanner,
    ScanStorageError,
    classify,
)

IMPORT_ID = "imp-scan"
BUCKET = "holding-bucket"

# Two-part planted values (the test file never holds a whole secret-shaped string).
ACCESS_KEY = "AKI" + "A" + "ABCDEFGHIJKLMNOP"
HF_TOKEN = "hf" + "_" + "a1B2c3D4e5F6g7H8i9J0"
PRIVATE_KEY_LINE = "-----BEGIN " + "RSA PRIVATE KEY-----"
JWT = (
    "ey" + "JhbGciOiJIUzI1NiJ9" + "." + "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "abcDEF123_-xyz"
)
PASSWORD_LINE = "db_" + "password = " + "hunter2"
EMAIL = "jane.doe" + "@" + "example.com"
PHONE_INTL = "+" + "14155550132"
PHONE_US = "(415) " + "555-0132"
GOVERNMENT_ID = "123-45-" + "6789"
STREET = "1600 Pennsylvania " + "Avenue"


class FakeReader:
    """Serves files from memory and records how they were read."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.opened: list[str] = []
        self.ranges: list[tuple[str, int, int]] = []

    def read_range(self, path: str, start: int, length: int) -> bytes:
        self.ranges.append((path, start, length))
        if path not in self.files:
            raise FileUnreadableError
        return self.files[path][start : start + length]

    def open(self, path: str) -> IO[bytes]:
        self.opened.append(path)
        if path not in self.files:
            raise FileUnreadableError
        return io.BytesIO(self.files[path])


def file_set(files: dict[str, bytes]) -> ModelFileSet:
    return ModelFileSet(
        import_id=IMPORT_ID,
        model_name="org/model",
        exact_version="a" * 40,
        files=[ModelFile(path, len(data), len(data)) for path, data in files.items()],
    )


def scan(files: dict[str, bytes], **options: Any) -> tuple[Any, FakeReader]:
    reader = FakeReader(files)
    result = LeakScanner(lambda _: reader, **options).scan(file_set(files))
    return result, reader


def safetensors(header: object = None, *, extra: bytes = b"tensor-bytes") -> bytes:
    body = json.dumps({"__metadata__": {"format": "pt"}} if header is None else header).encode()
    return struct.pack("<Q", len(body)) + body + extra


def kinds(result: Any) -> list[FindingKind]:
    return [finding.kind for finding in result.findings]


# -- BR3.1 --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("model.safetensors", FileClass.WEIGHT),
        ("DIR/Model.SafeTensors", FileClass.WEIGHT),
        ("pytorch_model.bin", FileClass.DISALLOWED_WEIGHT),
        ("a/b/model.PKL", FileClass.DISALLOWED_WEIGHT),
        ("weights.gguf", FileClass.DISALLOWED_WEIGHT),
        ("config.json", FileClass.SCANNED_TEXT),
        ("model.safetensors.index.json", FileClass.SCANNED_TEXT),
        ("LICENSE", FileClass.SCANNED_TEXT),
        (".env", FileClass.SCANNED_TEXT),
        ("dir.bin/README", FileClass.SCANNED_TEXT),
        ("tokenizer.model", FileClass.IGNORED),
        ("key.pem", FileClass.IGNORED),
        ("run.sh", FileClass.IGNORED),
        (".env.local", FileClass.IGNORED),
    ],
)
def test_br3_1_a_file_class_comes_from_the_last_segment_and_ending_case_ignored(
    path: str, expected: FileClass
) -> None:
    assert classify(path) is expected


def test_br3_1_br3_6_ignored_files_are_not_read_and_disallowed_weights_are_findings() -> None:
    files = {
        "tokenizer.model": ("key=" + ACCESS_KEY).encode(),
        "private.pem": PRIVATE_KEY_LINE.encode(),
        "pytorch_model.bin": b"\x80\x04pickle",
        "model.safetensors": safetensors(),
    }

    result, reader = scan(files)

    assert kinds(result) == [FindingKind.DISALLOWED_WEIGHT_TYPE]
    assert result.findings[0].file_name == "pytorch_model.bin"
    assert not result.passed
    assert reader.opened == []  # nothing was opened as text
    assert all(path == "model.safetensors" for path, _, _ in reader.ranges)


# -- BR3.2 --------------------------------------------------------------------------------------


def test_br3_2_a_good_header_passes_and_only_the_header_is_read() -> None:
    result, reader = scan({"w.safetensors": safetensors(extra=b"x" * 5000)})

    assert result.passed
    assert [(start, length) for _, start, length in reader.ranges][0] == (0, 8)
    assert reader.opened == []
    assert sum(length for _, _, length in reader.ranges) < 200  # not the 5,000 bytes of tensors


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"short",
        struct.pack("<Q", 0) + b"{}",
        struct.pack("<Q", 100_000_001) + b"{}" * 10,
        struct.pack("<Q", 50) + b'{"a":1}',
        struct.pack("<Q", 4) + b"\xff\xfe\xfd\xfc",
        struct.pack("<Q", 7) + b"{bad:1}",
        struct.pack("<Q", 7) + b"[1,2,3]",
        struct.pack("<Q", 4) + b"null",
    ],
)
def test_br3_2_a_bad_header_is_a_finding(content: bytes) -> None:
    result, _ = scan({"w.safetensors": content})

    assert kinds(result) == [FindingKind.BAD_WEIGHT_HEADER]


def test_br3_2_the_text_inside_the_header_is_not_scanned_for_leaks() -> None:
    header = {"__metadata__": {"author": EMAIL, "key": ACCESS_KEY}}

    result, _ = scan({"w.safetensors": safetensors(header)})

    assert result.passed


# -- BR3.3 and BR3.5 ----------------------------------------------------------------------------

PLANTED = [
    (ACCESS_KEY, FindingKind.ACCESS_KEY),
    (PRIVATE_KEY_LINE, FindingKind.PRIVATE_KEY),
    (HF_TOKEN, FindingKind.TOKEN),
    ("sk" + "-" + "A1b2C3d4E5f6G7h8I9j0", FindingKind.TOKEN),
    (JWT, FindingKind.TOKEN),
    (PASSWORD_LINE, FindingKind.PASSWORD),
    ('"client_' + 'secret": "abc123"', FindingKind.PASSWORD),
    (EMAIL, FindingKind.EMAIL_ADDRESS),
    (PHONE_INTL, FindingKind.PHONE_NUMBER),
    (PHONE_US, FindingKind.PHONE_NUMBER),
    (GOVERNMENT_ID, FindingKind.GOVERNMENT_ID),
    (STREET, FindingKind.STREET_ADDRESS),
]


@pytest.mark.parametrize(("value", "kind"), PLANTED, ids=[kind.value for _, kind in PLANTED])
def test_br3_3_each_kind_is_found_with_its_line_and_columns_and_never_the_value(
    value: str, kind: FindingKind
) -> None:
    text = f"first line is clean\nprefix {value} suffix\n"

    result, _ = scan({"notes.txt": text.encode()})

    assert kind in kinds(result)
    finding = next(item for item in result.findings if item.kind is kind)
    assert finding.place.startswith("line 2, columns ")
    numbers = finding.place.removeprefix("line 2, columns ").split(" to ")
    first, last = int(numbers[0]), int(numbers[1])
    assert 8 <= first <= last <= 7 + len(value)  # inside the planted text, on line 2
    assert value not in repr(finding)
    assert not any(hasattr(finding, name) for name in ("value", "match", "text", "snippet"))


@pytest.mark.parametrize(
    "value",
    [
        "xox" + "b-" + "1234567890-" + "abcdefghij-" + "KLMNOPQRSTuv",
        "sk" + "-proj-" + "A1b2C3d4E5f6_G7h8I9j0K1l2",
        "sk" + "-ant-api03-" + "A1b2C3d4E5f6-G7h8I9j0K1l2",
    ],
    ids=["slack-bot", "openai-project", "anthropic"],
)
def test_br3_3_tokens_with_hyphens_and_underscores_in_the_body_are_found(value: str) -> None:
    result, _ = scan({"notes.txt": f"key {value} end\n".encode()})

    assert kinds(result) == [FindingKind.TOKEN]


def test_br3_3_a_token_body_that_is_too_short_is_not_a_match() -> None:
    short = "sk" + "-proj-" + "A1b2C3d4E5"  # 10 characters after the prefix part

    result, _ = scan({"notes.txt": f"key {short} end\n".encode()})

    assert result.passed


def test_br3_3_clean_text_has_no_finding() -> None:
    clean = (
        'ordinary text, version 1.2.3, numbers 1234567 and 2024-01-15,\n"password": "",\n'
        "risk-assessment-framework, the 5 apples are on the road\n"
    )

    result, _ = scan({"notes.txt": clean.encode()})

    assert result.passed
    assert result.findings == ()


# -- BR3.4 --------------------------------------------------------------------------------------


def test_br3_4_text_may_be_utf8_with_or_without_a_mark_or_utf16_with_a_mark() -> None:
    line = f"contact {EMAIL}"
    files = {
        "a.txt": line.encode("utf-8"),
        "b.txt": b"\xef\xbb\xbf" + line.encode("utf-8"),
        "c.txt": line.encode("utf-16"),  # little-endian with a byte-order mark
        "d.txt": b"\xfe\xff" + line.encode("utf-16-be"),
        "e.txt": "caf\u00e9 \u00fcber \U0001f600 clean".encode(),
    }

    result, _ = scan(files)

    assert sorted(item.file_name for item in result.findings) == [
        "a.txt",
        "b.txt",
        "c.txt",
        "d.txt",
    ]
    assert set(kinds(result)) == {FindingKind.EMAIL_ADDRESS}


@pytest.mark.parametrize(
    "content",
    [b"ok \xff\xfe broken", b"caf\xc3", "caf\u00e9".encode("utf-16-le"), b"\xff\xfeabc"],
)
def test_br3_4_text_that_cannot_be_decoded_is_an_unreadable_file_finding(content: bytes) -> None:
    result, _ = scan({"data.txt": content})

    assert kinds(result) == [FindingKind.UNREADABLE_FILE]


def test_br3_4_utf16_without_a_byte_order_mark_is_an_unreadable_file_finding() -> None:
    result, _ = scan({"data.txt": f"contact {EMAIL}".encode("utf-16-le")})

    assert [(item.kind, item.place) for item in result.findings] == [
        (FindingKind.UNREADABLE_FILE, PLACE_WHOLE_FILE)
    ]


def test_br3_4_a_file_that_cannot_be_opened_is_a_finding_and_the_scan_goes_on() -> None:
    reader = FakeReader({"b.txt": EMAIL.encode()})
    files = file_set({"b.txt": EMAIL.encode()})
    files.files.append(ModelFile("a-missing.json", 10, 10))

    result = LeakScanner(lambda _: reader).scan(files)

    assert [(item.file_name, item.kind) for item in result.findings] == [
        ("a-missing.json", FindingKind.UNREADABLE_FILE),
        ("b.txt", FindingKind.EMAIL_ADDRESS),
    ]


# -- BR3.6 and BR3.7 ----------------------------------------------------------------------------


def test_br3_6_every_file_is_checked_and_all_findings_are_collected() -> None:
    files = {
        "config.json": f'{{"key": "{ACCESS_KEY}"}}'.encode(),
        "pytorch_model.bin": b"pickle",
        "w.safetensors": safetensors(),
        "notes.md": f"mail {EMAIL}\n{GOVERNMENT_ID}\n".encode(),
        "clean.txt": b"nothing here",
    }

    result, _ = scan(files)

    assert [(item.file_name, item.kind) for item in result.findings] == [
        ("config.json", FindingKind.ACCESS_KEY),
        ("notes.md", FindingKind.EMAIL_ADDRESS),
        ("notes.md", FindingKind.GOVERNMENT_ID),
        ("pytorch_model.bin", FindingKind.DISALLOWED_WEIGHT_TYPE),
    ]
    assert result.matches_total == 4
    assert not result.passed
    assert [item.path for item in result.files] == sorted(files)


def test_br3_7_at_most_100_findings_are_recorded_for_one_file_and_the_total_is_kept() -> None:
    many = "".join(f"user{i}@example.com\n" for i in range(250))
    files = {"big.csv": many.encode(), "other.txt": EMAIL.encode()}

    result, _ = scan(files)

    big = next(item for item in result.files if item.path == "big.csv")
    assert (big.findings_recorded, big.matches_total) == (100, 250)
    recorded = [item for item in result.findings if item.file_name == "big.csv"]
    assert [item.place for item in recorded[:2]] == [
        "line 1, columns 1 to 17",
        "line 2, columns 1 to 17",
    ]
    assert recorded[-1].place.startswith("line 100,")  # the first 100, in the order they appear
    assert result.matches_total == 251  # and the other file was still scanned
    assert any(item.file_name == "other.txt" for item in result.findings)


# -- NFR1.9: parts, long lines, and bounds ---------------------------------------------------------


def test_br3_3_a_match_across_the_border_of_two_1_mib_parts_is_found_once() -> None:
    filler = "x" * 99 + "\n"
    head = filler * ((1024 * 1024 - 10) // 100)  # the part border falls inside the next line
    padding = "y" * (1024 * 1024 - 7 - len(head))
    text = head + padding + ACCESS_KEY + " tail\n"
    assert len(head + padding) == 1024 * 1024 - 7  # the key starts 7 bytes before the border

    result, _ = scan({"data.txt": text.encode()})

    assert kinds(result) == [FindingKind.ACCESS_KEY]
    assert result.findings[0].place == (
        f"line {text.count(chr(10), 0, text.index(ACCESS_KEY)) + 1}, "
        f"columns {len(padding) + 1} to {len(padding) + len(ACCESS_KEY)}"
    )


def test_nfr1_9_a_line_over_1_mib_is_scanned_in_overlapping_windows_without_double_reports() -> (
    None
):
    step = WINDOW_CHARS - WINDOW_OVERLAP_CHARS
    offsets = [0, step - 5, step * 2 - len(ACCESS_KEY) + 1, 1024 * 1024 - 8, 1024 * 1024 + 700]
    offsets += [2 * 1024 * 1024 + 3, 2_500_000]
    line = ["a"] * 2_600_000
    for offset in offsets:
        line[offset : offset + len(ACCESS_KEY)] = ACCESS_KEY
    text = "".join(line) + "\nnext line " + EMAIL + "\n"

    result, _ = scan({"huge.json": text.encode()})

    keys = [item for item in result.findings if item.kind is FindingKind.ACCESS_KEY]
    assert [item.place for item in keys] == [
        f"line 1, columns {offset + 1} to {offset + len(ACCESS_KEY)}" for offset in offsets
    ]
    emails = [item for item in result.findings if item.kind is FindingKind.EMAIL_ADDRESS]
    assert [item.place.split(",")[0] for item in emails] == ["line 2"]


def test_nfr1_9_a_text_file_over_200_mb_is_an_unreadable_file_finding_and_is_not_read() -> None:
    reader = FakeReader({})
    huge = ModelFileSet(
        IMPORT_ID, "org/model", "a" * 40, [ModelFile("huge.txt", MAX_TEXT_FILE_BYTES + 1, 0)]
    )

    result = LeakScanner(lambda _: reader).scan(huge)

    assert [(item.kind, item.place) for item in result.findings] == [
        (FindingKind.UNREADABLE_FILE, PLACE_SIZE_LIMIT)
    ]
    assert reader.opened == []


def test_nfr1_9_a_file_that_takes_more_than_5_minutes_is_an_unreadable_file_finding() -> None:
    class JumpingClock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            self.now += 100.0  # every check sees 100 more seconds
            return self.now

    text = ("filler line\n" * 100_000).encode() * 3  # more than one 1 MiB part
    assert len(text) > 3 * 1024 * 1024

    result, _ = scan({"slow.txt": text, "ok.txt": b"fine"}, clock=JumpingClock())

    slow = [item for item in result.findings if item.file_name == "slow.txt"]
    assert [(item.kind, item.place) for item in slow] == [
        (FindingKind.UNREADABLE_FILE, PLACE_TIME_LIMIT)
    ]


# -- S3 reader (moto) ---------------------------------------------------------------------------


@pytest.fixture
def s3() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def put(s3: Any, path: str, data: bytes) -> None:
    s3.put_object(Bucket=BUCKET, Key=holding_prefix(IMPORT_ID) + path, Body=data)


def test_br3_2_br3_3_the_scanner_reads_the_holding_area_in_s3(s3: Any) -> None:
    files = {"w.safetensors": safetensors(extra=b"z" * 3000), "config.json": f"{EMAIL}".encode()}
    for path, data in files.items():
        put(s3, path, data)

    result = LeakScanner.for_s3(s3, BUCKET).scan(file_set(files))

    assert [(item.file_name, item.kind) for item in result.findings] == [
        ("config.json", FindingKind.EMAIL_ADDRESS)
    ]


def test_br3_4_br3_6_a_missing_object_is_a_finding_but_a_storage_failure_fails_the_step(
    s3: Any, tmp_path: Any
) -> None:
    put(s3, "present.txt", b"fine")
    files = file_set({"present.txt": b"fine"})
    files.files.append(ModelFile("gone.txt", 5, 5))

    result = LeakScanner.for_s3(s3, BUCKET).scan(files)
    assert [(item.file_name, item.kind) for item in result.findings] == [
        ("gone.txt", FindingKind.UNREADABLE_FILE)
    ]

    with pytest.raises(ScanStorageError):
        LeakScanner.for_s3(s3, "no-such-bucket").scan(files)


def test_br3_5_nfr3_8_no_finding_or_error_text_holds_a_planted_value() -> None:
    secrets = [ACCESS_KEY, HF_TOKEN, JWT, EMAIL, GOVERNMENT_ID, PHONE_INTL, STREET]
    text = "\n".join(secrets) + "\n" + PASSWORD_LINE + "\n" + PRIVATE_KEY_LINE + "\n"

    result, _ = scan({"leaky.txt": text.encode()})

    assert result.matches_total >= len(secrets)
    everything = (
        repr(result)
        + str(result.findings)
        + json.dumps([vars(f) for f in result.findings], default=str)
    )
    for secret in [*secrets, "hunter2"]:
        assert secret not in everything


# -- the deadline (u2-import-rules, BR8.2, performance-design.md) ---------------------------------


class SlowReader(FakeReader):
    """Opening a file takes the whole deadline: the clock moves 6 hours."""

    def __init__(self, files: dict[str, bytes], clock: Any) -> None:
        super().__init__(files)
        self._clock = clock

    def open(self, path: str) -> IO[bytes]:
        stream = super().open(path)
        self._clock.advance(6 * 3600)
        return stream


def test_br8_2_the_deadline_is_checked_before_each_file_and_ends_the_scan(clock: Any) -> None:
    files = {"a.txt": b"hello", "b.txt": b"world"}
    reader = SlowReader(files, clock)
    deadline = Deadline(clock(), clock)

    with pytest.raises(DeadlineReached):
        LeakScanner(lambda _: reader).scan(file_set(files), deadline)

    assert reader.opened == ["a.txt"]  # b.txt was never opened


def test_br8_2_a_deadline_with_time_left_changes_nothing_in_the_result(clock: Any) -> None:
    files = {"a.txt": EMAIL.encode(), "b.txt": b"world"}
    deadline = Deadline(clock(), clock)

    with_deadline = LeakScanner(lambda _: FakeReader(files)).scan(file_set(files), deadline)
    without = LeakScanner(lambda _: FakeReader(files)).scan(file_set(files))

    assert with_deadline.matches_total == without.matches_total == 1
    assert [f.kind for f in with_deadline.findings] == [f.kind for f in without.findings]


def test_br8_2_a_deadline_already_reached_reads_no_file_at_all(clock: Any) -> None:
    files = {"a.txt": b"hello"}
    reader = FakeReader(files)
    deadline = Deadline(clock(), clock)
    clock.advance(6 * 3600)

    with pytest.raises(DeadlineReached):
        LeakScanner(lambda _: reader).scan(file_set(files), deadline)

    assert reader.opened == [] and reader.ranges == []
