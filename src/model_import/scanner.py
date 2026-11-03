"""LeakScanner: checks every downloaded file for leaks (BR3.1 to BR3.7, NFR1.9, NFR3.8, NFR3.11).

Files are read from the holding area as bytes. They are never run, imported, or loaded with a
library that can run code (NFR3.11). A finding names the file, the place, and the kind and
never holds the matched value, any part of it, or the text around it (BR3.5): the code that
matches keeps only the position of a match.

Text is read in parts of 1 MiB through an incremental decoder. A line is scanned only when it is
complete, so a match that crosses the border of two parts is found. A line over 1 MiB is cut
into windows of 64 KiB that overlap by 16 KiB, so a long line cannot take unbounded time and a
match across a window border is still found (every pattern is bounded to less than the overlap).
"""

from __future__ import annotations

import codecs
import contextlib
import json
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import IO, Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from model_import.deadline import Deadline
from model_import.models import (
    MAX_FINDINGS_PER_FILE,
    FileClass,
    FindingKind,
    ModelFile,
    ModelFileSet,
    ScanFinding,
    ScannedFile,
    holding_prefix,
)

READ_PART_BYTES = 1024 * 1024  # BR3.3: parts of 1 MiB
MAX_TEXT_FILE_BYTES = 200 * 1024 * 1024  # NFR1.9: 200 MB (counted as 200 MiB)
MAX_SCAN_SECONDS = 5 * 60.0  # NFR1.9
MAX_LINE_CHARS = 1024 * 1024  # NFR1.9: a line over 1 MiB is scanned in windows
WINDOW_CHARS = 64 * 1024  # NFR1.9
WINDOW_OVERLAP_CHARS = 16 * 1024  # longer than any match the patterns below can make
MIN_WEIGHT_HEADER_BYTES = 1  # BR3.2: N must be more than 0
MAX_WEIGHT_HEADER_BYTES = 100_000_000  # BR3.2: N at most 100 million
_CLOCK_CHECK_EVERY_LINES = 1024

WEIGHT_ENDINGS = frozenset({"safetensors"})
DISALLOWED_WEIGHT_ENDINGS = frozenset(
    {"bin", "pt", "pth", "pkl", "pickle", "ckpt", "h5", "onnx", "gguf", "msgpack", "npz"}
)
SCANNED_TEXT_ENDINGS = frozenset(
    {
        "json",
        "jsonl",
        "txt",
        "md",
        "yaml",
        "yml",
        "py",
        "jinja",
        "toml",
        "cfg",
        "ini",
        "csv",
        "tiktoken",
    }
)

PLACE_WHOLE_FILE = "whole file"
PLACE_HEADER = "file header"
PLACE_TIME_LIMIT = "scan time limit of 5 minutes passed"
PLACE_SIZE_LIMIT = "file larger than 200 MB"


# -- file classes (BR3.1) -----------------------------------------------------------------------


def classify(path: str) -> FileClass:
    """BR3.1: give a file one class from the last segment of its path, case ignored.

    The ending is the text after the last dot. A name that starts with a dot and has no other
    dot (``.env``) has no ending, and so does a name that ends in a dot. A file with no ending
    is scanned-text. Only the last ending counts, so ``model.safetensors.index.json`` is json.
    """
    name = path.rsplit("/", 1)[-1].lower()
    ending = name.rsplit(".", 1)[-1] if "." in name.lstrip(".") else ""
    if ending in WEIGHT_ENDINGS:
        return FileClass.WEIGHT
    if ending in DISALLOWED_WEIGHT_ENDINGS:
        return FileClass.DISALLOWED_WEIGHT
    if ending == "" or ending in SCANNED_TEXT_ENDINGS:
        return FileClass.SCANNED_TEXT
    return FileClass.IGNORED


# -- patterns (BR3.3) ---------------------------------------------------------------------------
# The smallest set for the first version: a pattern may be added and none may be removed. Each
# variable part is bounded, so no pattern can match more than the window overlap and none can
# take unbounded time on one line.

_STREET_WORDS = "Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Boulevard|Blvd"

PATTERNS: tuple[tuple[FindingKind, re.Pattern[str]], ...] = (
    (FindingKind.ACCESS_KEY, re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}")),
    (
        FindingKind.PRIVATE_KEY,
        re.compile(r"-----BEGIN (?:[A-Z0-9]{1,16} ){0,4}PRIVATE KEY-----"),
    ),
    (
        FindingKind.TOKEN,
        re.compile(
            r"(?:hf_|ghp_|github_pat_|xoxb-|xoxp-|(?<![A-Za-z0-9])sk-)[A-Za-z0-9_-]{20,512}"
            r"|eyJ[A-Za-z0-9_-]{5,4000}\.[A-Za-z0-9_-]{5,4000}\.[A-Za-z0-9_-]{1,4000}"
        ),
    ),
    (
        FindingKind.PASSWORD,
        re.compile(
            r"(?:password|passwd|pwd|secret|api_key)[\w.-]{0,40}[\"']?[ \t]{0,20}[=:][ \t]{0,20}"
            r"[\"']?[^\s\"',;]",
            re.IGNORECASE,
        ),
    ),
    (
        FindingKind.EMAIL_ADDRESS,
        re.compile(r"[A-Za-z0-9._%+-]{1,64}@(?:[A-Za-z0-9-]{1,63}\.){1,6}[A-Za-z]{2,24}"),
    ),
    (
        FindingKind.PHONE_NUMBER,
        re.compile(
            r"(?<![\w+])\+\d{8,15}(?!\d)"
            r"|(?<![\d-])(?:\(\d{3}\) ?|\d{3}[-. ])\d{3}[-. ]\d{4}(?![\d-])"
        ),
    ),
    (FindingKind.GOVERNMENT_ID, re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")),
    (
        FindingKind.STREET_ADDRESS,
        re.compile(
            rf"\b\d{{1,6}}[ \t]{{1,5}}(?:(?-i:[A-Z])[A-Za-z']{{0,30}}[ \t]{{1,5}}){{1,4}}"
            rf"(?:{_STREET_WORDS})\b",
            re.IGNORECASE,
        ),
    ),
)
_KIND_RANK = {kind: rank for rank, (kind, _) in enumerate(PATTERNS)}


# -- reading ------------------------------------------------------------------------------------


class FileUnreadableError(Exception):
    """One file cannot be opened or read (BR3.4). The scan records a finding and goes on."""


class ScanStorageError(RuntimeError):
    """BR3.6: the storage failed in a way that is not about one file. The step fails."""

    def __init__(self, reason: str = "the files to scan could not be read from storage") -> None:
        super().__init__(reason)
        self.reason = reason


class FileReader(Protocol):
    """Reads the bytes of a downloaded file by its path in the model."""

    def read_range(self, path: str, start: int, length: int) -> bytes: ...

    def open(self, path: str) -> IO[bytes]: ...


_FILE_SPECIFIC_CODES = frozenset({"NoSuchKey", "404", "NotFound", "InvalidRange", "416"})


class S3HoldingReader:
    """Reads files from ``holding/<importId>/`` in the holding bucket."""

    def __init__(self, s3_client: Any, bucket: str, import_id: str) -> None:
        self._s3 = s3_client
        self._bucket = bucket
        self._prefix = holding_prefix(import_id)

    def read_range(self, path: str, start: int, length: int) -> bytes:
        if length <= 0:
            return b""
        response = self._get(path, f"bytes={start}-{start + length - 1}")
        with contextlib.closing(response["Body"]) as body:
            return _read_exact(body, length)

    def open(self, path: str) -> IO[bytes]:
        return self._get(path, None)["Body"]

    def _get(self, path: str, byte_range: str | None) -> dict[str, Any]:
        arguments: dict[str, str] = {"Bucket": self._bucket, "Key": self._prefix + path}
        if byte_range is not None:
            arguments["Range"] = byte_range
        try:
            return self._s3.get_object(**arguments)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in _FILE_SPECIFIC_CODES:
                raise FileUnreadableError from error
            raise ScanStorageError from error
        except BotoCoreError as error:
            raise ScanStorageError from error


def _read_exact(stream: IO[bytes], length: int) -> bytes:
    """Read ``length`` bytes, or fewer only at the end of the stream."""
    parts: list[bytes] = []
    remaining = length
    while remaining > 0:
        part = stream.read(remaining)
        if not part:
            break
        parts.append(part)
        remaining -= len(part)
    return b"".join(parts)


# -- result -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanResult:
    """BR3.6: the result for every file. Any match fails the import."""

    files: tuple[ScannedFile, ...]
    findings: tuple[ScanFinding, ...]

    @property
    def matches_total(self) -> int:
        """BR3.7: the count of all matches, including those beyond 100 for one file."""
        return sum(item.matches_total for item in self.files)

    @property
    def passed(self) -> bool:
        return self.matches_total == 0


class _LimitError(Exception):
    """The scan of one file passed a bound of NFR1.9. ``place`` says which."""

    def __init__(self, place: str) -> None:
        super().__init__(place)
        self.place = place


class _FileRecorder:
    """Collects the findings of one file and applies the cap of BR3.7."""

    def __init__(self, scanner: LeakScanner, import_id: str, path: str) -> None:
        self._scanner = scanner
        self._import_id = import_id
        self._path = path
        self.recorded: list[ScanFinding] = []
        self.matches_total = 0

    def add(self, kind: FindingKind, place: str) -> None:
        self.matches_total += 1
        if len(self.recorded) < MAX_FINDINGS_PER_FILE:
            self.recorded.append(
                ScanFinding(
                    finding_id=self._scanner.next_finding_id(self._import_id),
                    import_id=self._import_id,
                    file_name=self._path,
                    place=place,
                    kind=kind,
                )
            )


class LeakScanner:
    """BR3.6: checks every file of a downloaded model and collects all findings."""

    def __init__(
        self,
        reader_for: Callable[[str], FileReader],
        *,
        clock: Callable[[], float] = time.monotonic,
        max_scan_seconds: float = MAX_SCAN_SECONDS,
        max_text_bytes: int = MAX_TEXT_FILE_BYTES,
    ) -> None:
        self._reader_for = reader_for
        self._clock = clock
        self._max_seconds = max_scan_seconds
        self._max_text_bytes = max_text_bytes
        self._counter = 0

    @classmethod
    def for_s3(cls, s3_client: Any, bucket: str, **options: Any) -> LeakScanner:
        """Build a scanner that reads the holding area of an import from S3."""
        return cls(lambda import_id: S3HoldingReader(s3_client, bucket, import_id), **options)

    def next_finding_id(self, import_id: str) -> str:
        self._counter += 1
        return f"{import_id}-f{self._counter}"

    def scan(self, file_set: ModelFileSet, deadline: Deadline | None = None) -> ScanResult:
        """BR3.6: scan every file; all findings are collected, none stops the scan early.

        Raises ``ScanStorageError`` when the storage fails in a way that is not about one file.
        With a ``deadline`` (BR8.2) it is checked before each file; ``DeadlineReached`` ends the
        scan. The scan of one file keeps its own limit of 5 minutes (NFR1.9).
        """
        reader = self._reader_for(file_set.import_id)
        scanned: list[ScannedFile] = []
        findings: list[ScanFinding] = []
        for model_file in sorted(file_set.files, key=lambda item: item.path):
            if deadline is not None:
                deadline.check()  # BR8.2: before each file is scanned
            recorder = _FileRecorder(self, file_set.import_id, model_file.path)
            file_class = classify(model_file.path)
            self._scan_one(reader, model_file, file_class, recorder)
            scanned.append(
                ScannedFile(
                    import_id=file_set.import_id,
                    path=model_file.path,
                    file_class=file_class,
                    findings_recorded=len(recorder.recorded),
                    matches_total=recorder.matches_total,
                )
            )
            findings.extend(recorder.recorded)
        return ScanResult(files=tuple(scanned), findings=tuple(findings))

    def _scan_one(
        self,
        reader: FileReader,
        model_file: ModelFile,
        file_class: FileClass,
        recorder: _FileRecorder,
    ) -> None:
        if file_class is FileClass.IGNORED:
            return
        if file_class is FileClass.DISALLOWED_WEIGHT:
            recorder.add(FindingKind.DISALLOWED_WEIGHT_TYPE, PLACE_WHOLE_FILE)
            return
        try:
            if file_class is FileClass.WEIGHT:
                self._check_weight_header(reader, model_file, recorder)
            else:
                self._scan_text(reader, model_file, recorder)
        except FileUnreadableError:
            recorder.add(FindingKind.UNREADABLE_FILE, PLACE_WHOLE_FILE)
        except _LimitError as limit:
            recorder.add(FindingKind.UNREADABLE_FILE, limit.place)

    # -- weight header (BR3.2) ---------------------------------------------------------------

    def _check_weight_header(
        self, reader: FileReader, model_file: ModelFile, recorder: _FileRecorder
    ) -> None:
        """BR3.2: read only the header of a weight file and check it. Nothing else is read."""
        if not _header_is_valid(reader, model_file.path, model_file.expected_size_bytes):
            recorder.add(FindingKind.BAD_WEIGHT_HEADER, PLACE_HEADER)

    # -- text (BR3.3, BR3.4, BR3.5, BR3.7, NFR1.9) ----------------------------------------------

    def _scan_text(
        self, reader: FileReader, model_file: ModelFile, recorder: _FileRecorder
    ) -> None:
        """BR3.3, BR3.4: scan a text file for the kinds of BR3.3 (unreadable is a finding)."""
        if model_file.expected_size_bytes > self._max_text_bytes:
            raise _LimitError(PLACE_SIZE_LIMIT)
        deadline = self._clock() + self._max_seconds
        state = _TextState()
        total_bytes = 0
        with contextlib.closing(reader.open(model_file.path)) as stream:
            first = _read_exact(stream, READ_PART_BYTES)
            if _looks_like_utf16_without_mark(first):
                raise FileUnreadableError  # BR3.4: a file that cannot be checked is a finding
            decoder = _decoder_for(first)
            part = first
            while True:
                total_bytes += len(part)
                if total_bytes > self._max_text_bytes:
                    raise _LimitError(PLACE_SIZE_LIMIT)
                self._feed(decoder, part, state, recorder, deadline)
                if len(part) < READ_PART_BYTES:
                    break
                part = _read_exact(stream, READ_PART_BYTES)
        self._feed(decoder, b"", state, recorder, deadline, final=True)

    def _feed(
        self,
        decoder: codecs.IncrementalDecoder,
        part: bytes,
        state: _TextState,
        recorder: _FileRecorder,
        deadline: float,
        *,
        final: bool = False,
    ) -> None:
        try:
            text = decoder.decode(part, final=final)
        except UnicodeDecodeError as error:
            raise FileUnreadableError from error
        if self._clock() > deadline:
            raise _LimitError(PLACE_TIME_LIMIT)
        lines = (state.carry + text).split("\n")
        state.carry = lines.pop()  # the last piece may be a line that is not complete yet
        for line in lines:
            self._scan_line(line, state, recorder)
            state.line_number += 1
            state.column_base = 0
            if state.line_number % _CLOCK_CHECK_EVERY_LINES == 0 and self._clock() > deadline:
                raise _LimitError(PLACE_TIME_LIMIT)
        while len(state.carry) > MAX_LINE_CHARS:  # NFR1.9: a line over 1 MiB is not held whole
            cut = len(state.carry) - WINDOW_OVERLAP_CHARS
            self._scan_line(state.carry, state, recorder, report_to=cut)
            state.carry = state.carry[cut:]
            state.column_base += cut
            if self._clock() > deadline:
                raise _LimitError(PLACE_TIME_LIMIT)
        if final:
            self._scan_line(state.carry, state, recorder)
            state.carry = ""

    def _scan_line(
        self,
        line: str,
        state: _TextState,
        recorder: _FileRecorder,
        *,
        report_to: int | None = None,
    ) -> None:
        """Scan one line (or the start of a long one) in windows; report matches that start
        before ``report_to`` (the whole line when it is None)."""
        limit = len(line) if report_to is None else report_to
        for start, end, kind in _matches_in_windows(line, limit):
            first_column = state.column_base + start + 1
            last_column = state.column_base + end
            recorder.add(kind, f"line {state.line_number}, columns {first_column} to {last_column}")


@dataclass
class _TextState:
    carry: str = ""
    line_number: int = 1
    column_base: int = 0


def _looks_like_utf16_without_mark(first_bytes: bytes) -> bool:
    """BR3.4: NUL bytes in the first 4 KiB and no UTF-16 byte-order mark mean the file is
    probably UTF-16 without a mark. It would be read as UTF-8 and hide a secret, so it is
    treated as a file that cannot be checked."""
    if first_bytes[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return False
    return b"\x00" in first_bytes[:4096]


def _decoder_for(first_bytes: bytes) -> codecs.IncrementalDecoder:
    """BR3.4: UTF-8 (with or without a byte-order mark), or UTF-16 with a byte-order mark."""
    if first_bytes[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return codecs.getincrementaldecoder("utf-16")(errors="strict")
    return codecs.getincrementaldecoder("utf-8-sig")(errors="strict")


def _matches_in_windows(line: str, report_to: int) -> Iterator[tuple[int, int, FindingKind]]:
    """Yield (start, end, kind) of matches that start before ``report_to``, in order.

    Windows of 64 KiB overlap by 16 KiB. A match is reported by the window where it starts
    before the window's last 16 KiB (or by the last window), so no match is reported twice and
    a match across a window border is found whole.
    """
    step = WINDOW_CHARS - WINDOW_OVERLAP_CHARS
    position = 0
    while position < max(report_to, 1):
        window_end = position + WINDOW_CHARS
        is_last = window_end >= len(line)
        report_until = report_to if is_last else min(position + step, report_to)
        window = line[position:window_end]
        found: list[tuple[int, int, int, FindingKind]] = []
        for kind, pattern in PATTERNS:
            for match in pattern.finditer(window):
                start = position + match.start()
                if start >= report_until:
                    break
                found.append((start, position + match.end(), _KIND_RANK[kind], kind))
        found.sort(key=lambda item: (item[0], item[2]))
        for start, end, _, kind in found:
            yield start, end, kind
        if is_last:
            return
        position += step


def _header_is_valid(reader: FileReader, path: str, size: int) -> bool:
    """BR3.2: the first 8 bytes give the header length N (little-endian); the next N bytes must
    be UTF-8 text that parses as one JSON object. The rest of the file is not read."""
    if size < 8:
        return False
    length_bytes = reader.read_range(path, 0, 8)
    if len(length_bytes) != 8:
        return False
    header_length = int.from_bytes(length_bytes, "little")
    if not MIN_WEIGHT_HEADER_BYTES <= header_length <= MAX_WEIGHT_HEADER_BYTES:
        return False
    if size < 8 + header_length:
        return False
    header = reader.read_range(path, 8, header_length)
    if len(header) != header_length:
        return False
    try:
        parsed = json.loads(header.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return False
    return isinstance(parsed, dict)
