"""Tests for retry.py: retries, waits, Retry-After, 30-minute budget (BR2.4, NFR4.4, NFR4.10)."""

from datetime import timedelta
from typing import Any

import pytest

from model_import.deadline import Deadline, DeadlineReached
from model_import.retry import (
    PermanentDownloadError,
    Retrier,
    RetriesExhaustedError,
    RetryBudget,
    RetryPolicy,
    TemporaryDownloadError,
    parse_retry_after,
)

NOW = 1_000_000.0

# ``clock`` is the datetime clock of ``tests/conftest.py``; the deadline tests below use it.
FakeClock = Any


class Flaky:
    """An operation that fails with the given problems in order, then returns 'ok'."""

    def __init__(self, *problems: Exception) -> None:
        self._problems = list(problems)
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self._problems:
            raise self._problems.pop(0)
        return "ok"


def make_retrier(
    sleeps: list[float], policy: RetryPolicy | None = None, budget: RetryBudget | None = None
) -> Retrier:
    return Retrier(policy, budget, sleep=sleeps.append, clock=lambda: NOW)


def test_the_default_policy_has_the_numbers_of_br2_4_nfr4_4_and_nfr4_10() -> None:
    policy = RetryPolicy()

    assert policy.max_retries == 3
    assert policy.waits_seconds == (5.0, 10.0, 20.0)
    assert policy.budget_seconds == 30 * 60
    assert policy.retry_after_cap_seconds == 60.0


def test_br2_4_a_good_first_try_does_not_wait() -> None:
    sleeps: list[float] = []
    operation = Flaky()

    assert make_retrier(sleeps).call(operation) == "ok"
    assert sleeps == [] and operation.calls == 1


def test_br2_4_a_temporary_problem_waits_5_then_10_seconds_and_then_succeeds() -> None:
    sleeps: list[float] = []
    operation = Flaky(TemporaryDownloadError("dropped"), TemporaryDownloadError("dropped"))

    assert make_retrier(sleeps).call(operation) == "ok"
    assert sleeps == [5.0, 10.0]
    assert operation.calls == 3


def test_br2_4_the_import_fails_after_3_retries_with_waits_5_10_20() -> None:
    sleeps: list[float] = []
    operation = Flaky(*[TemporaryDownloadError("the connection dropped") for _ in range(4)])

    with pytest.raises(RetriesExhaustedError) as raised:
        make_retrier(sleeps).call(operation)

    assert sleeps == [5.0, 10.0, 20.0]
    assert operation.calls == 4
    assert "the connection dropped" in raised.value.reason
    assert "3 retries" in raised.value.reason


def test_br2_3_a_permanent_problem_is_not_retried() -> None:
    sleeps: list[float] = []
    operation = Flaky(PermanentDownloadError("the model was not found"))

    with pytest.raises(PermanentDownloadError):
        make_retrier(sleeps).call(operation)

    assert sleeps == [] and operation.calls == 1


@pytest.mark.parametrize(
    ("header", "expected_wait"),
    [("7", 7.0), ("120", 60.0), ("0", 0.0), (30, 30.0)],
)
def test_nfr4_4_retry_after_is_used_up_to_60_seconds(
    header: str | int, expected_wait: float
) -> None:
    sleeps: list[float] = []
    operation = Flaky(TemporaryDownloadError("busy", header))

    make_retrier(sleeps).call(operation)

    assert sleeps == [expected_wait]


def test_retry_after_given_as_a_date_is_read_against_the_clock() -> None:
    sleeps: list[float] = []
    date_text = "Mon, 12 Jan 1970 13:46:55 GMT"  # 1_000_015 seconds after the epoch
    operation = Flaky(TemporaryDownloadError("busy", date_text))

    make_retrier(sleeps).call(operation)

    assert sleeps == [15.0]


@pytest.mark.parametrize("value", [None, "", "soon", "not a date at all", float("nan")])
def test_parse_retry_after_ignores_what_it_cannot_read(value: str | float | None) -> None:
    assert parse_retry_after(value, NOW) is None


def test_parse_retry_after_never_returns_a_negative_wait() -> None:
    assert parse_retry_after("-5", NOW) == 0.0
    assert parse_retry_after("Thu, 01 Jan 1970 00:00:00 GMT", NOW) == 0.0


def test_nfr4_10_after_the_waiting_budget_is_used_the_next_problem_fails_at_once() -> None:
    sleeps: list[float] = []
    policy = RetryPolicy(budget_seconds=12.0)
    budget = RetryBudget(policy.budget_seconds)
    retrier = make_retrier(sleeps, policy, budget)

    with pytest.raises(RetriesExhaustedError) as raised:
        retrier.call(Flaky(*[TemporaryDownloadError("slow answer") for _ in range(4)]))

    assert sleeps == [5.0, 10.0]  # 15 seconds spent, above the 12 second budget
    assert budget.exhausted and budget.spent_seconds == 15.0
    assert "waiting" in raised.value.reason


def test_nfr4_10_the_budget_is_shared_but_the_retry_count_is_for_each_file() -> None:
    sleeps: list[float] = []
    budget = RetryBudget(RetryPolicy().budget_seconds)
    retrier = make_retrier(sleeps, budget=budget)

    retrier.call(Flaky(TemporaryDownloadError("a"), TemporaryDownloadError("a")))
    retrier.call(Flaky(TemporaryDownloadError("b"), TemporaryDownloadError("b")))

    assert sleeps == [5.0, 10.0, 5.0, 10.0]  # the second file starts its waits again
    assert budget.spent_seconds == 30.0


def test_the_30_minute_budget_is_exhausted_by_1800_seconds_of_waiting() -> None:
    budget = RetryBudget(30 * 60.0)

    budget.charge(1799.0)
    assert not budget.exhausted
    budget.charge(1.0)
    assert budget.exhausted


def test_on_retry_reports_the_attempt_the_wait_and_the_reason() -> None:
    seen: list[tuple[int, float, str]] = []
    retrier = Retrier(sleep=lambda _: None, clock=lambda: NOW, on_retry=lambda *a: seen.append(a))

    retrier.call(Flaky(TemporaryDownloadError("timed out")))

    assert seen == [(1, 5.0, "timed out")]


# -- the deadline (u2-import-rules, BR8.2, performance-design.md) ---------------------------------


def make_deadline_retrier(
    clock: FakeClock, sleeps: list[float], budget: RetryBudget | None = None
) -> Retrier:
    """A retrier whose sleep moves the fake clock, so a wait uses up the time left."""

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    return Retrier(budget=budget, sleep=sleep, clock=lambda: NOW)


def test_br8_2_a_wait_is_the_smaller_of_the_plan_and_the_time_left(clock: FakeClock) -> None:
    sleeps: list[float] = []
    budget = RetryBudget(30 * 60.0)
    deadline = Deadline(clock(), clock, limit=timedelta(seconds=8))
    operation = Flaky(TemporaryDownloadError("slow"), TemporaryDownloadError("slow"))

    # The first wait is 5 seconds. The second would be 10 but only 3 seconds are left.
    with pytest.raises(DeadlineReached):
        make_deadline_retrier(clock, sleeps, budget).call(operation, deadline)

    assert sleeps == [5.0, 3.0]
    assert budget.spent_seconds == 8.0  # the budget is charged what was waited, not the plan
    assert operation.calls == 2  # no third try after the time ran out


def test_br8_2_no_time_left_means_no_wait_and_no_retry(clock: FakeClock) -> None:
    sleeps: list[float] = []
    deadline = Deadline(clock(), clock, limit=timedelta(seconds=1))
    clock.advance(1)
    operation = Flaky(TemporaryDownloadError("slow"))

    with pytest.raises(DeadlineReached):
        make_deadline_retrier(clock, sleeps).call(operation, deadline)

    assert sleeps == [] and operation.calls == 1


def test_br8_2_with_time_to_spare_the_waits_are_the_plan_of_br2_4(clock: FakeClock) -> None:
    sleeps: list[float] = []
    deadline = Deadline(clock(), clock)
    operation = Flaky(TemporaryDownloadError("a"), TemporaryDownloadError("b"))

    assert make_deadline_retrier(clock, sleeps).call(operation, deadline) == "ok"

    assert sleeps == [5.0, 10.0]


def test_br8_2_a_permanent_problem_passes_through_even_when_the_deadline_is_reached(
    clock: FakeClock,
) -> None:
    deadline = Deadline(clock(), clock, limit=timedelta(seconds=1))
    clock.advance(5)

    with pytest.raises(PermanentDownloadError):
        make_deadline_retrier(clock, []).call(Flaky(PermanentDownloadError("not found")), deadline)
