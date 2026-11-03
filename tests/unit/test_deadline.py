"""Tests for deadline.py: the 6-hour deadline with an injected clock (BR8.1, BR8.2, BR8.4)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from model_import.deadline import REASON_TIME_LIMIT, TIME_LIMIT, Deadline, DeadlineReached

START = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


def test_br8_1_the_deadline_is_the_start_plus_6_hours(clock: Any) -> None:
    deadline = Deadline(START, clock)

    assert timedelta(hours=6) == TIME_LIMIT
    assert deadline.at == START + timedelta(hours=6)
    assert deadline.remaining() == timedelta(hours=6)
    assert deadline.remaining_seconds() == 6 * 3600


def test_br8_2_check_passes_before_the_deadline_and_raises_at_it(clock: Any) -> None:
    deadline = Deadline(START, clock)

    clock.advance(6 * 3600 - 1)
    deadline.check()  # one second left: the next piece of work may start
    assert not deadline.reached
    clock.advance(1)  # exactly at the deadline
    with pytest.raises(DeadlineReached):
        deadline.check()
    assert deadline.reached


def test_br8_2_check_keeps_raising_after_the_deadline_and_time_left_is_never_negative(
    clock: Any,
) -> None:
    deadline = Deadline(START, clock)
    clock.advance(7 * 3600)

    with pytest.raises(DeadlineReached):
        deadline.check()
    assert deadline.remaining() < timedelta(0)
    assert deadline.remaining_seconds() == 0.0


def test_br8_4_the_reason_is_the_plain_words_of_the_email(clock: Any) -> None:
    deadline = Deadline(START, clock)
    clock.advance(6 * 3600)

    with pytest.raises(DeadlineReached) as raised:
        deadline.check()

    assert raised.value.reason == REASON_TIME_LIMIT == "the time limit of 6 hours passed"
    assert str(raised.value) == REASON_TIME_LIMIT


def test_br8_1_the_deadline_does_not_move_while_the_clock_does(clock: Any) -> None:
    deadline = Deadline(START, clock)
    first = deadline.at

    clock.advance(3600)

    assert deadline.at == first
    assert deadline.remaining_seconds() == 5 * 3600


def test_a_start_time_without_a_time_zone_or_a_zero_limit_is_refused() -> None:
    with pytest.raises(ValueError):
        Deadline(datetime(2026, 10, 30, 12, 0, 0))
    with pytest.raises(ValueError):
        Deadline(START, limit=timedelta(0))
