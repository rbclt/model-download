"""Shared test fixtures. No test calls a real AWS account."""

from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def fake_aws_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every AWS client at fake credentials so a test can never reach a real account."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)


class FakeClock:
    """A clock the test moves by hand. It returns a time zone aware ``datetime``."""

    def __init__(self) -> None:
        self.current = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
