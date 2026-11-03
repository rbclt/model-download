"""Retry helper for Hugging Face requests (BR2.4, NFR4.4, NFR4.10).

Rules (``reliability-design.md``):

* 3 retries for each file (each ``Retrier.call`` is one file or one listing).
* Waits of 5, 10, and 20 seconds between tries.
* When the answer gives a ``Retry-After`` time, it is used instead, up to 60 seconds.
* One budget for the whole import adds up every wait. After 30 minutes of waiting, the next
  temporary problem fails the import at once.
* With a ``Deadline`` (u2, BR8.2) a wait never runs past the deadline, and no retry starts after it.

The sleep and the clock are injectable, so no test ever sleeps for real.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import TypeVar

from model_import.deadline import Deadline

T = TypeVar("T")


class DownloadError(Exception):
    """A problem with Hugging Face. ``reason`` is plain words and never holds a secret."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TemporaryDownloadError(DownloadError):
    """A problem that may pass on its own (BR2.4). ``retry_after`` is the raw header or seconds."""

    def __init__(self, reason: str, retry_after: str | float | None = None) -> None:
        super().__init__(reason)
        self.retry_after = retry_after


class PermanentDownloadError(DownloadError):
    """A problem that will not pass on its own: the import fails at once (BR2.3)."""


class RetriesExhaustedError(DownloadError):
    """The retries or the time budget ran out. The import fails (BR2.4)."""


@dataclass(frozen=True)
class RetryPolicy:
    """The numbers of BR2.4 and NFR4.4."""

    max_retries: int = 3
    waits_seconds: tuple[float, ...] = (5.0, 10.0, 20.0)
    budget_seconds: float = 30 * 60.0
    retry_after_cap_seconds: float = 60.0


class RetryBudget:
    """NFR4.10: adds up all waits of one import. Safe to share between download threads."""

    def __init__(self, limit_seconds: float) -> None:
        self._limit = limit_seconds
        self._spent = 0.0
        self._lock = threading.Lock()

    @property
    def spent_seconds(self) -> float:
        with self._lock:
            return self._spent

    @property
    def exhausted(self) -> bool:
        return self.spent_seconds >= self._limit

    def charge(self, seconds: float) -> None:
        with self._lock:
            self._spent += seconds


def parse_retry_after(value: str | float | None, now_epoch_seconds: float) -> float | None:
    """Read a ``Retry-After`` value (seconds or an HTTP date) as seconds to wait.

    Returns None when the value is missing or cannot be read.
    """
    if value is None:
        return None
    if isinstance(value, int | float):
        return max(float(value), 0.0) if math.isfinite(value) else None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            moment = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return max(moment.timestamp() - now_epoch_seconds, 0.0)
    return max(seconds, 0.0) if math.isfinite(seconds) else None


class Retrier:
    """Runs an operation again after a temporary problem (BR2.4)."""

    def __init__(
        self,
        policy: RetryPolicy | None = None,
        budget: RetryBudget | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        on_retry: Callable[[int, float, str], None] | None = None,
    ) -> None:
        self._policy = policy or RetryPolicy()
        self._budget = budget or RetryBudget(self._policy.budget_seconds)
        self._sleep = sleep
        self._clock = clock
        self._on_retry = on_retry

    @property
    def budget(self) -> RetryBudget:
        return self._budget

    def call(self, operation: Callable[[], T], deadline: Deadline | None = None) -> T:
        """BR2.4: run ``operation``; retry it after a temporary problem.

        A permanent problem passes through at once (BR2.3). After 3 retries, or when the budget
        of the import is used up, ``RetriesExhaustedError`` is raised.

        With a ``deadline`` (BR8.2): no retry starts when no time is left (``DeadlineReached`` is
        raised instead of waiting), and a wait is the smaller of the planned wait and the time
        left. The deadline is checked again after the wait, before the retry runs.
        """
        retries = 0
        while True:
            try:
                return operation()
            except TemporaryDownloadError as problem:
                if retries >= self._policy.max_retries:
                    raise RetriesExhaustedError(
                        f"{problem.reason}; gave up after {retries} retries"
                    ) from problem
                if self._budget.exhausted:
                    minutes = round(self._policy.budget_seconds / 60)
                    raise RetriesExhaustedError(
                        f"{problem.reason}; the {minutes} minutes of waiting are used up"
                    ) from problem
                if deadline is not None:
                    deadline.check()  # BR8.2: no time left means no wait and no retry
                wait = self._wait_for(retries, problem.retry_after)
                if deadline is not None:
                    wait = min(wait, deadline.remaining_seconds())
                self._budget.charge(wait)
                retries += 1
                if self._on_retry is not None:
                    self._on_retry(retries, wait, problem.reason)
                self._sleep(wait)
                if deadline is not None:
                    deadline.check()  # BR8.2: the wait may have used the last of the time

    def _wait_for(self, retries_so_far: int, retry_after: str | float | None) -> float:
        given = parse_retry_after(retry_after, self._clock())
        if given is not None:
            return min(given, self._policy.retry_after_cap_seconds)
        waits = self._policy.waits_seconds
        return waits[min(retries_so_far, len(waits) - 1)]
