"""Deployment settings for the CDK app, read from CDK context or the environment.

Every setting is named in ``infrastructure-specification.md`` ("Settings the Owner Supplies").
A CDK context value (``cdk deploy -c teamAddress=...``) wins over the environment variable.
A missing or invalid setting raises ``ConfigError`` with every problem named at once, so the
person fixes them in one go. Values are never echoed back in an error message.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from model_import.settings import is_valid_address

DEFAULT_ENVIRONMENT = "staging"
DEFAULT_BUDGET_LIMIT_USD = 100.0

# (context key, environment variable)
KEY_STACK_NAME = ("stackName", "MODEL_IMPORT_STACK_NAME")
KEY_TEAM_ADDRESS = ("teamAddress", "MODEL_IMPORT_TEAM_ADDRESS")
KEY_SENDER_ADDRESS = ("senderAddress", "MODEL_IMPORT_SENDER_ADDRESS")
KEY_BREAK_GLASS = ("breakGlassRoleArns", "MODEL_IMPORT_BREAK_GLASS_ROLE_ARNS")
KEY_BUDGET_LIMIT = ("budgetLimitUsd", "MODEL_IMPORT_BUDGET_LIMIT_USD")
KEY_SMOKE_ROLE = ("smokeCheckRoleArn", "MODEL_IMPORT_SMOKE_CHECK_ROLE_ARN")

_STACK_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")


class ConfigError(ValueError):
    """One or more deployment settings are missing or not valid."""


@dataclass(frozen=True)
class DeploymentConfig:
    stack_name: str
    team_address: str
    sender_address: str
    break_glass_role_arns: tuple[str, ...]
    budget_limit_usd: float
    smoke_check_role_arn: str | None


def _split(value: object) -> list[str]:
    """Accept a list (from cdk.json context) or a comma separated string."""
    if isinstance(value, list | tuple):
        parts = [str(item) for item in value]
    else:
        parts = str(value).split(",")
    return [part.strip() for part in parts if part.strip()]


def load_config(
    context: Callable[[str], object | None],
    environ: Mapping[str, str],
) -> DeploymentConfig:
    """Build the config. ``context`` returns the CDK context value for a key, or None."""

    def read(key: tuple[str, str]) -> object | None:
        value = context(key[0])
        if value is None or (isinstance(value, str) and not value.strip()):
            value = environ.get(key[1])
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    problems: list[str] = []

    def need(key: tuple[str, str]) -> object | None:
        value = read(key)
        if value is None:
            problems.append(f"{key[0]} (context) or {key[1]} (environment) is not set")
        return value

    stack_name = str(read(KEY_STACK_NAME) or DEFAULT_ENVIRONMENT).strip()
    if not _STACK_NAME.fullmatch(stack_name):
        problems.append(f"{KEY_STACK_NAME[0]} must be letters, digits and hyphens")

    addresses: dict[str, str] = {}
    for key in (KEY_TEAM_ADDRESS, KEY_SENDER_ADDRESS):
        raw = need(key)
        if raw is not None:
            if is_valid_address(str(raw).strip()):
                addresses[key[0]] = str(raw).strip()
            else:
                problems.append(f"{key[0]} is not a valid email address")

    raw_roles = need(KEY_BREAK_GLASS)
    roles = tuple(_split(raw_roles)) if raw_roles is not None else ()
    if raw_roles is not None and not roles:
        problems.append(f"{KEY_BREAK_GLASS[0]} must name at least one role")

    limit = DEFAULT_BUDGET_LIMIT_USD
    raw_limit = read(KEY_BUDGET_LIMIT)
    if raw_limit is not None:
        try:
            limit = float(str(raw_limit))
        except ValueError:
            problems.append(f"{KEY_BUDGET_LIMIT[0]} must be a number")
        else:
            if not 0 < limit < 1_000_000:
                problems.append(f"{KEY_BUDGET_LIMIT[0]} must be above 0 and below 1,000,000")

    smoke = read(KEY_SMOKE_ROLE)

    if problems:
        raise ConfigError("deployment settings: " + "; ".join(problems))
    return DeploymentConfig(
        stack_name=stack_name,
        team_address=addresses[KEY_TEAM_ADDRESS[0]],
        sender_address=addresses[KEY_SENDER_ADDRESS[0]],
        break_glass_role_arns=roles,
        budget_limit_usd=limit,
        smoke_check_role_arn=str(smoke).strip() if smoke is not None else None,
    )
