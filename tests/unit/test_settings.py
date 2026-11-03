"""Tests for settings.py (NFR3.1, NFR3.2, NFR3.14)."""

import pytest

from model_import import settings
from model_import.settings import Settings, SettingsError, is_valid_address


def make_env(**overrides: str) -> dict[str, str]:
    env = {
        settings.ENV_HOLDING_BUCKET: "holding-bucket",
        settings.ENV_FINAL_BUCKET: "final-bucket",
        settings.ENV_QUEUE_URL: "https://sqs.us-east-1.amazonaws.com/123456789012/import-queue.fifo",
        settings.ENV_SENDER_ADDRESS: "imports@example.com",
        settings.ENV_TEAM_ADDRESS: "team@example.com",
    }
    env.update(overrides)
    return env


def test_from_env_reads_all_settings() -> None:
    result = Settings.from_env(make_env())

    assert result.holding_bucket == "holding-bucket"
    assert result.final_bucket == "final-bucket"
    assert result.sender_address == "imports@example.com"
    assert result.team_address == "team@example.com"
    assert result.region == "us-east-1"


def test_from_env_uses_region_from_environment() -> None:
    result = Settings.from_env(make_env(AWS_REGION="eu-west-1"))

    assert result.region == "eu-west-1"


def test_missing_setting_is_named_without_values() -> None:
    env = make_env()
    del env[settings.ENV_QUEUE_URL]

    with pytest.raises(SettingsError) as raised:
        Settings.from_env(env)

    assert settings.ENV_QUEUE_URL in str(raised.value)
    assert "holding-bucket" not in str(raised.value)


def test_blank_setting_counts_as_missing() -> None:
    with pytest.raises(SettingsError, match=settings.ENV_FINAL_BUCKET):
        Settings.from_env(make_env(**{settings.ENV_FINAL_BUCKET: "   "}))


def test_invalid_address_is_rejected_and_not_echoed() -> None:
    bad = "a@example.com, b@example.com"

    with pytest.raises(SettingsError) as raised:
        Settings.from_env(make_env(**{settings.ENV_TEAM_ADDRESS: bad}))

    assert settings.ENV_TEAM_ADDRESS in str(raised.value)
    assert bad not in str(raised.value)


def test_hugging_face_token_in_environment_is_ignored_nfr3_2() -> None:
    """NFR3.2: the settings never carry a Hugging Face token, even when one is set."""
    result = Settings.from_env(make_env(HF_TOKEN="not-a-real-token"))

    assert "not-a-real-token" not in repr(result)
    assert not any("token" in field.lower() for field in result.__dataclass_fields__)


@pytest.mark.parametrize(
    "address",
    ["a@example.com", "first.last+tag@sub.example.org"],
)
def test_valid_addresses_nfr3_14(address: str) -> None:
    assert is_valid_address(address)


@pytest.mark.parametrize(
    "address",
    [
        "",
        "no-at-sign.example.com",
        "a@b",
        "a b@example.com",
        "a@example.com;b@example.com",
        "a@example.com\r\nBcc: x@example.com",
        "x" * 250 + "@example.com",
    ],
)
def test_invalid_addresses_nfr3_14(address: str) -> None:
    assert not is_valid_address(address)


def test_configuration_set_is_optional_and_read_when_set_nfr3_9() -> None:
    """NFR3.9: the SES configuration set name reaches the notifier when the stack sets it."""
    assert Settings.from_env(make_env()).configuration_set is None

    result = Settings.from_env(make_env(**{settings.ENV_CONFIGURATION_SET: "model-import"}))

    assert result.configuration_set == "model-import"


def test_invalid_configuration_set_name_is_refused_without_showing_it() -> None:
    with pytest.raises(SettingsError) as raised:
        Settings.from_env(make_env(**{settings.ENV_CONFIGURATION_SET: "bad name;"}))

    assert settings.ENV_CONFIGURATION_SET in str(raised.value)
    assert "bad name" not in str(raised.value)
