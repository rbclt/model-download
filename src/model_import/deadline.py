"""The 6-hour deadline of one import (BR8.1 to BR8.4, NFR1.1, NFR1.10).

One ``Deadline`` is made when the application takes an event off the queue: ``started_at`` plus
6 hours. Waiting in the queue is never part of it. It is passed to the downloader, the scanner,
the custodian, and the retry helper, which call ``check`` at the check points of
``performance-design.md``. ``check`` raises ``DeadlineReached`` when the clock is at or after the
deadline, so the piece of work that is about to start does not start (BR8.2).

The deadline is never checked after the completion marker is written, and never in the removal
or the email (BR8.3). Those places simply do not receive a ``Deadline``.

The clock is injected, so no test waits for real hours.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

REASON_TIME_LIMIT = "the time limit of 6 hours passed"  # BR8.4: the reason in the email
TIME_LIMIT = timedelta(hours=6)  # BR8.1


class DeadlineReached(Exception):
    """BR8.4: the deadline was reached at a check point. ``reason`` is plain words."""

    def __init__(self, reason: str = REASON_TIME_LIMIT) -> None:
        super().__init__(reason)
        self.reason = reason


class Deadline:
    """BR8.1: ``started_at`` plus the time limit, read against an injected clock."""

    def __init__(
        self,
        started_at: datetime,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        limit: timedelta = TIME_LIMIT,
    ) -> None:
        if started_at.tzinfo is None:
            raise ValueError("the start time must carry a time zone")
        if limit <= timedelta(0):
            raise ValueError("the time limit must be more than zero")
        self._at = started_at + limit
        self._clock = clock

    @property
    def at(self) -> datetime:
        """The moment of the deadline (``deadlineAt`` of the import)."""
        return self._at

    def remaining(self) -> timedelta:
        """The time left. Zero or negative when the deadline is reached."""
        return self._at - self._clock()

    def remaining_seconds(self) -> float:
        """The time left in seconds, never below zero."""
        return max(self.remaining().total_seconds(), 0.0)

    @property
    def reached(self) -> bool:
        return self.remaining() <= timedelta(0)

    def check(self) -> None:
        """BR8.2: raise ``DeadlineReached`` when the clock is at or after the deadline."""
        if self.reached:
            raise DeadlineReached(REASON_TIME_LIMIT)
