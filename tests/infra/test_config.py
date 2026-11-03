"""Tests for infra/config.py: settings from CDK context or the environment."""

from __future__ import annotations

import pytest
from infra.config import ConfigError, load_config

ROLE = "arn:aws:iam::123456789012:role/OwnerAdmin"
ENV = {
    "MODEL_IMPORT_TEAM_ADDRESS": "team@example.com",
    "MODEL_IMPORT_SENDER_ADDRESS": "imports@example.com",
    "MODEL_IMPORT_BREAK_GLASS_ROLE_ARNS": f"{ROLE}, arn:aws:iam::123456789012:role/Other",
}


def no_context(_key: str) -> object | None:
    return None


def test_environment_variables_give_the_defaults() -> None:
    config = load_config(no_context, ENV)

    assert config.stack_name == "staging"
    assert config.budget_limit_usd == 100.0
    assert config.break_glass_role_arns == (ROLE, "arn:aws:iam::123456789012:role/Other")
    assert config.smoke_check_role_arn is None


def test_cdk_context_wins_over_the_environment_and_accepts_a_list() -> None:
    context = {
        "teamAddress": "context@example.com",
        "breakGlassRoleArns": [ROLE],
        "budgetLimitUsd": "250",
        "stackName": "production",
        "smokeCheckRoleArn": "arn:aws:iam::123456789012:role/Smoke",
    }

    config = load_config(context.get, ENV)

    assert config.team_address == "context@example.com"
    assert config.break_glass_role_arns == (ROLE,)
    assert config.budget_limit_usd == 250.0
    assert config.stack_name == "production"
    assert config.smoke_check_role_arn == "arn:aws:iam::123456789012:role/Smoke"


def test_missing_settings_are_all_named_in_one_error() -> None:
    with pytest.raises(ConfigError) as raised:
        load_config(no_context, {})

    message = str(raised.value)
    for name in ("teamAddress", "senderAddress", "breakGlassRoleArns"):
        assert name in message


def test_empty_break_glass_value_is_an_error_not_a_silent_default() -> None:
    with pytest.raises(ConfigError, match="breakGlassRoleArns"):
        load_config(no_context, {**ENV, "MODEL_IMPORT_BREAK_GLASS_ROLE_ARNS": " , "})


def test_invalid_values_are_named_without_showing_them() -> None:
    env = {
        **ENV,
        "MODEL_IMPORT_TEAM_ADDRESS": "bad address",
        "MODEL_IMPORT_BUDGET_LIMIT_USD": "lots",
        "MODEL_IMPORT_STACK_NAME": "bad name!",
    }

    with pytest.raises(ConfigError) as raised:
        load_config(no_context, env)

    message = str(raised.value)
    assert "teamAddress" in message and "budgetLimitUsd" in message and "stackName" in message
    assert "bad address" not in message and "lots" not in message


def test_negative_budget_limit_is_refused() -> None:
    with pytest.raises(ConfigError, match="budgetLimitUsd"):
        load_config(no_context, {**ENV, "MODEL_IMPORT_BUDGET_LIMIT_USD": "-5"})
